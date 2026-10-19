"""Runs the 5 test requests (Djinni) through the two-agent system and the single-agent baseline and grades them.

  uv run python -m evaluation.run_eval                              # 5 cases x {multi, single} x 3 repeats
  uv run python -m evaluation.run_eval --cases c1_best_qa --modes multi --repeat 1

Output: runs/eval_<timestamp>/ (per-run trace.jsonl, workspace, result.json) and
        evaluation/results/<timestamp>/{results.json, report.md}
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import statistics as st
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from evaluation.graders import GRADERS, RunOutcome
from jobs_helper import config
from jobs_helper.agents.orchestrator import run_request
from jobs_helper.agents.run import seed_application
from jobs_helper.agents.tracing import load_trace
from jobs_helper.models import Job
from jobs_helper.tracker import Tracker

HERE = Path(__file__).resolve().parent


def seed_workspace(ws: Path, candidate_dir: Path, seed: list[str]) -> set[str]:
    ws.mkdir(parents=True, exist_ok=True)
    if not seed:
        return set()
    profile = yaml.safe_load((candidate_dir / "candidate.yaml").read_text(encoding="utf-8"))
    name = f"{profile['personal']['first_name']} {profile['personal']['last_name']}"
    tr = Tracker(ws)
    return {seed_application(tr, name, jid) for jid in seed}


def metrics(run_dir: Path) -> dict:
    trace = load_trace(run_dir / "trace.jsonl")
    ends = [e for e in trace if e["kind"] == "agent_end"]
    tools = [e for e in trace if e["kind"] == "tool_result"]
    return {
        "seconds": trace[-1]["t"] if trace else None,
        "agent_runs": len(ends),
        "turns": sum(e.get("num_turns") or 0 for e in ends),
        "tool_calls": len(tools),
        "tool_errors": dict(Counter(e["error"] for e in tools if e.get("error"))),
        "cost_usd": round(sum(e.get("cost_usd") or 0 for e in ends), 4),
        "output_tokens": sum(((e.get("usage") or {}).get("output_tokens") or 0) for e in ends),
        "models": sorted({e.get("model") for e in trace if e["kind"] == "agent_init" and e.get("model")}),
    }


def reviewer_edits(o: RunOutcome) -> list[dict]:
    """Changes the Reviewer made to the Scout's message and CV before submitting (multi mode only)."""
    all_drafts = [d for rnd in o.result.get("rounds", []) for d in ((rnd.get("scout") or {}).get("structured") or {}).get("drafts", [])]
    drafts = {d["job_id"]: d["cover_letter"] for d in all_drafts}
    cvs = {d["job_id"]: d.get("cv_markdown", "") for d in all_drafts}
    out = []
    for r in o.successful:
        if r["job_id"] not in drafts:
            continue
        a, b = drafts[r["job_id"]].split(), o.doc(r).split()
        ops = [{"op": t, "scout": " ".join(a[i1:i2]), "reviewer": " ".join(b[j1:j2])}
               for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes() if t != "equal"]
        a, b = cvs[r["job_id"]].split(), o.doc(r, "cv").split()
        cv_ops = [{"op": t, "scout": " ".join(a[i1:i2]), "reviewer": " ".join(b[j1:j2])}
                  for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes() if t != "equal"]
        out.append({"job_id": r["job_id"], "changes": ops, "cv_changes": cv_ops})
    return out


async def one(case: dict, mode: str, rep: int, spec: dict, jobs: dict, out_dir: Path, model: str | None,
              sem: asyncio.Semaphore, quiet: bool) -> dict:
    async with sem:
        name = f"{case['id']}__{mode}__r{rep}"
        cand = config.ROOT / "data" / "candidates" / case["candidate"]
        ws = out_dir / name / "workspace"
        try:
            seeded = seed_workspace(ws, cand, case.get("seed", []))
            res = await run_request(case["request"], cand, mode, runs_root=out_dir, run_name=name,
                                    snapshot=str((config.ROOT / spec["snapshot"]).resolve()), model=model,
                                    verbose=not quiet, workspace=ws, today=spec["today"], closed=case.get("closed", []))
            o = RunOutcome(ws, res, jobs, seeded, cand)
            g = GRADERS[case["grader"]](o, case.get("params", {}))
            grade = {"passed": g.passed, "reason": g.reason, "details": g.details}
            edits = reviewer_edits(o) if mode == "multi" else []
            submitted = len(o.successful)
        except Exception as e:  # noqa: BLE001  a crashed run is a failed run, not a crashed evaluation
            res, grade, edits, submitted = {"final_answer": ""}, {"passed": False, "reason": f"run crashed: {type(e).__name__}: {e}", "details": {}}, [], 0
        try:
            m = metrics(out_dir / name)
        except (OSError, ValueError) as e:
            m = {"metrics_error": str(e)}
        row = {"case": case["id"], "mode": mode, "rep": rep, **grade, **m, "submitted": submitted,
               "reviewer_edits": edits, "final_answer": res.get("final_answer", "")}
        print(f"  {'PASS' if row['passed'] else 'FAIL'}  {name:34} {m.get('seconds') or 0:5.0f}s  {row['reason'][:120]}", flush=True)
        return row


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", help="comma separated ids; default all")
    p.add_argument("--modes", default="multi,single")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--model", help="default: `model` from cases.yaml")
    p.add_argument("--verbose", action="store_true", help="live log of every run (noisy with concurrency)")
    p.add_argument("--regrade", metavar="RESULTS_DIR", help="re-grade finished runs of evaluation/results/<stamp> (after a grader fix); no agent is run")
    args = p.parse_args()
    if args.regrade:
        return regrade(Path(args.regrade))

    spec = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    snapshot = (config.ROOT / spec["snapshot"]).resolve()
    jobs = {j["id"]: Job(**j) for j in json.loads(snapshot.read_text(encoding="utf-8"))["jobs"]}
    cases = [c for c in spec["cases"] if not args.cases or c["id"] in args.cases.split(",")]
    modes = args.modes.split(",")
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    out_dir = config.ROOT / "runs" / f"eval_{stamp}"
    sem = asyncio.Semaphore(args.concurrency)
    print(f"{len(cases)} cases x {len(modes)} modes x {args.repeat} repeats, concurrency {args.concurrency}")
    results = await asyncio.gather(*[one(c, m, r, spec, jobs, out_dir, args.model or spec.get("model"), sem, not args.verbose)
                                     for c in cases for m in modes for r in range(1, args.repeat + 1)])
    results = sorted(results, key=lambda r: ([c["id"] for c in cases].index(r["case"]), modes.index(r["mode"]), r["rep"]))
    res_dir = HERE / "results" / stamp
    res_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(results, ensure_ascii=False, indent=1).replace(str(config.ROOT) + "/", "")
    (res_dir / "results.json").write_text(text, encoding="utf-8")
    (res_dir / "report.md").write_text(render_report(results, cases, modes, spec, stamp), encoding="utf-8")
    print(f"\nReport: {res_dir / 'report.md'}")


def regrade(res_dir: Path) -> None:
    """Grades the recorded runs again with the current graders; the agents are not called. Keeps the old report."""
    spec = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    jobs = {j["id"]: Job(**j) for j in json.loads((config.ROOT / spec["snapshot"]).read_text(encoding="utf-8"))["jobs"]}
    cases = {c["id"]: c for c in spec["cases"]}
    results = json.loads((res_dir / "results.json").read_text(encoding="utf-8"))
    stamp = res_dir.name
    for r in results:
        run_dir = config.ROOT / "runs" / f"eval_{stamp}" / f"{r['case']}__{r['mode']}__r{r['rep']}"
        ws, cand = run_dir / "workspace", config.ROOT / "data" / "candidates" / cases[r["case"]]["candidate"]
        seeded = {x["application_id"] for x in Tracker(ws).rows() if x["submitted_by"] == "seed"}
        res = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        g = GRADERS[cases[r["case"]]["grader"]](RunOutcome(ws, res, jobs, seeded, cand), cases[r["case"]].get("params", {}))
        if g.passed != r["passed"]:
            print(f"  changed: {r['case']} {r['mode']} r{r['rep']}: {r['reason'][:90]} -> {g.reason[:90]}")
        r.update(passed=g.passed, reason=g.reason, details=g.details)
    old = res_dir / "report.md"
    if old.exists() and not (res_dir / "report_before_regrade.md").exists():
        old.rename(res_dir / "report_before_regrade.md")
    (res_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    modes = [m for m in ("multi", "single") if any(r["mode"] == m for r in results)]
    (res_dir / "report.md").write_text(render_report(results, [c for c in spec["cases"] if any(r["case"] == c["id"] for r in results)],
                                                     modes, spec, stamp), encoding="utf-8")
    print(f"Re-graded: {res_dir / 'report.md'}")


def render_report(results: list[dict], cases: list[dict], modes: list[str], spec: dict, stamp: str) -> str:
    by = lambda c, m: [r for r in results if r["case"] == c and r["mode"] == m]  # noqa: E731
    models = sorted({x for r in results for x in r.get("models", [])})
    lines = [f"# Результати оцінки {stamp}", "",
             f"Модель: `{', '.join(models)}`; знімок `{spec['snapshot']}`; дата {spec['today']}; "
             f"повторів на кейс і режим: {max(r['rep'] for r in results)}.", "",
             "| Кейс | Пастка | " + " | ".join(modes) + " |", "|---|---|" + "---|" * len(modes)]
    for c in cases:
        cells = []
        for m in modes:
            rs = by(c["id"], m)
            ok = sum(r["passed"] for r in rs)
            cells.append(f"{'✅' if ok == len(rs) else ('❌' if ok == 0 else '⚠️')} {ok}/{len(rs)}")
        lines.append(f"| {c['id']} | {c.get('trap', '')} | " + " | ".join(cells) + " |")
    lines.append("| **Разом** | | " + " | ".join(f"**{sum(r['passed'] for r in results if r['mode'] == m)}/"
                                                  f"{sum(1 for r in results if r['mode'] == m)}**" for m in modes) + " |")
    lines += ["", "## Ціна (на один запуск)", "", "| | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    rows = [("медіанний час, с", lambda rs: f"{st.median([r.get('seconds') or 0 for r in rs]):.0f}"),
            ("викликів інструментів", lambda rs: f"{st.mean([r.get('tool_calls') or 0 for r in rs]):.1f}"),
            ("ходів моделі", lambda rs: f"{st.mean([r.get('turns') or 0 for r in rs]):.1f}"),
            ("вихідних токенів", lambda rs: f"{st.mean([r.get('output_tokens') or 0 for r in rs]):.0f}"),
            ("вартість, $ (еквівалент API)", lambda rs: f"{st.mean([r.get('cost_usd') or 0 for r in rs]):.3f}")]
    for label, f in rows:
        lines.append(f"| {label} | " + " | ".join(f([r for r in results if r["mode"] == m]) for m in modes) + " |")
    codes = sorted({k for r in results for k in (r.get("tool_errors") or {})})
    lines += ["", "## Помилки MCP-сервера під час запусків", "", "| код | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    lines += [f"| `{c}` | " + " | ".join(str(sum((r.get("tool_errors") or {}).get(c, 0) for r in results if r["mode"] == m))
                                       for m in modes) + " |" for c in codes] or ["| — | " + " | ".join("0" for _ in modes) + " |"]
    if "multi" in modes:
        multi = [r for r in results if r["mode"] == "multi"]
        changed = [e for r in multi for e in r["reviewer_edits"] if e["changes"]]
        cv_changed = [e for r in multi for e in r["reviewer_edits"] if e.get("cv_changes")]
        total = sum(r["submitted"] for r in multi)
        lines += ["", "## Чи змінював щось Reviewer", "",
                  f"Reviewer змінив лист Scout у **{len(changed)} з {total}** поданих відгуків, а CV у **{len(cv_changed)} з {total}**."]
        for r in multi:
            for e in r["reviewer_edits"]:
                for kind, key in (("лист", "changes"), ("CV", "cv_changes")):
                    for ch in e.get(key, []):
                        lines.append(f"- {r['case']} r{r['rep']} `{e['job_id']}` ({kind}): «{ch['scout'][:140]}» → «{ch['reviewer'][:140]}»")
    lines += ["", "## Деталі", ""]
    for r in results:
        lines.append(f"- **{r['case']} / {r['mode']} / r{r['rep']}**: {'PASS' if r['passed'] else 'FAIL'}: {r['reason']} "
                     f"(помилки інструментів: {', '.join(f'{k}×{v}' for k, v in (r.get('tool_errors') or {}).items()) or '—'})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    r = main()
    if asyncio.iscoroutine(r):
        asyncio.run(r)
