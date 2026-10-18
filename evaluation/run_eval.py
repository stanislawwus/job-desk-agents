"""Runs the 5 test requests through the two-agent system AND the single-agent baseline, grades every run, writes a report.

  uv run python -m evaluation.run_eval                      # 5 cases x {multi, single} x 3 repeats
  uv run python -m evaluation.run_eval --repeat 1 --cases c5 --modes multi
Output: evaluation/results/<stamp>/{results.json, report.md}; full traces of every run: runs/eval_<stamp>/<case>_<mode>_r<n>/
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics as st
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from evaluation.graders import GRADERS, Run
from jobdesk.orchestrator import DEFAULT_TODAY, run_request
from jobdesk.runtime import ROOT

DATA = ROOT / "data"
JOBS = {j["id"]: j for j in json.loads((DATA / "jobs.json").read_text(encoding="utf-8"))}
CV = (DATA / "candidate" / "cv.md").read_text(encoding="utf-8")
PROFILE = yaml.safe_load((DATA / "candidate" / "profile.yaml").read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").split("\n") if x.strip()] if path.exists() else []


def load_run(case_id: str, result: dict, seeded: list[str]) -> Run:
    ws = Path(result["workspace"])
    rows = list(csv.DictReader((ws / "applications.csv").open(encoding="utf-8", newline=""))) if (ws / "applications.csv").exists() else []
    return Run(case_id, result, rows, _read_jsonl(ws / "mcp_calls.jsonl"), JOBS, CV, PROFILE, set(seeded))


def reviewer_edits(result: dict, run: Run) -> tuple[int, int]:
    """(submitted letters that differ from the Scout's draft, submitted letters). Only meaningful in multi mode."""
    drafts = {}
    for a in result.get("agents", []):
        for d in ((a.get("structured") or {}).get("drafts") or []):
            drafts[d["job_id"]] = " ".join(d["cover_letter"].split())
    new = run.new_rows
    return sum(1 for r in new if r["job_id"] in drafts and " ".join(r["cover_letter"].split()) != drafts[r["job_id"]]), len(new)


async def one(case: dict, mode: str, rep: int, stamp: str, model: str | None, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.perf_counter()
        name = f"eval_{stamp}/{case['id']}_{mode}_r{rep}"
        try:
            result = await run_request(case["request"], mode, seed=case["seed_applied"], model=model, run_name=name, verbose=False)
            crashed = None
        except Exception as e:  # noqa: BLE001  a crashed run is a failed run, not a crashed evaluation
            result, crashed = {"final_answer": "", "recommended_job_ids": [], "agents": [], "workspace": str(ROOT / "runs" / name / "workspace"),
                               "run_dir": str(ROOT / "runs" / name)}, f"{type(e).__name__}: {e}"
        wall = time.perf_counter() - t0
        run = load_run(case["id"], result, case["seed_applied"])
        problems = GRADERS[case["id"]](run)
        if crashed or result.get("error"):
            problems.insert(0, f"run failed: {crashed or result['error']}")
        agents = result.get("agents", [])
        edits, submitted = reviewer_edits(result, run) if mode == "multi" else (0, len(run.new_rows))
        print(f"  {'PASS' if not problems else 'FAIL'}  {case['id']} {mode:6} r{rep}  {wall:5.0f}s  {'; '.join(problems)[:140]}", flush=True)
        return {
            "case": case["id"], "mode": mode, "rep": rep, "passed": not problems, "problems": problems,
            "wall_s": round(wall, 1), "models": sorted({a.get("model") for a in agents if a.get("model")}),
            "cost_usd": round(sum(a.get("cost_usd") or 0 for a in agents), 4),
            "turns": sum(a.get("num_turns") or 0 for a in agents),
            "tool_calls": sum(len(a.get("tool_calls", [])) for a in agents),
            "output_tokens": sum(((a.get("usage") or {}).get("output_tokens") or 0) for a in agents),
            "errors_seen": dict(Counter(c["error"] for c in run.audit if c.get("error"))),
            "applications_written": [r["job_id"] for r in run.new_rows],
            "reviewer_edits": edits, "submitted": submitted, "run_dir": str(result.get("run_dir", "")),
            "final_answer": result.get("final_answer", ""),
        }


def report(results: list[dict], cases: list[dict], modes: list[str], stamp: str, model: str | None, repeat: int) -> str:
    med = lambda xs: st.median(xs) if xs else 0  # noqa: E731
    by = lambda c, m: [r for r in results if r["case"] == c and r["mode"] == m]  # noqa: E731
    L = [f"# Evaluation report {stamp}", "",
         f"- model: `{', '.join(sorted({m for r in results for m in r['models']})) or model}`; repeats per case and mode: {repeat}; "
         f"fixed date {DEFAULT_TODAY}; fictional candidate and vacancies from `data/`", "",
         "## Accuracy (runs that passed all checks of the case)", "",
         "| case | trap | " + " | ".join(modes) + " |", "|---|---|" + "---|" * len(modes)]
    for c in cases:
        L.append(f"| {c['id']} {c['title']} | {c['trap']} | " + " | ".join(
            f"{sum(r['passed'] for r in by(c['id'], m))}/{len(by(c['id'], m))}" for m in modes) + " |")
    L.append("| **total** | | " + " | ".join(f"**{sum(r['passed'] for r in results if r['mode'] == m)}/{sum(1 for r in results if r['mode'] == m)}**" for m in modes) + " |")
    L += ["", "## Cost of the second agent (per run)", "", "| | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    rows = [("median wall time, s", lambda rs: f"{med([r['wall_s'] for r in rs]):.0f}"),
            ("tool calls (mean)", lambda rs: f"{st.mean([r['tool_calls'] for r in rs]):.1f}" if rs else "-"),
            ("model turns (mean)", lambda rs: f"{st.mean([r['turns'] for r in rs]):.1f}" if rs else "-"),
            ("output tokens (mean)", lambda rs: f"{st.mean([r['output_tokens'] for r in rs]):.0f}" if rs else "-"),
            ("cost, USD (mean)", lambda rs: f"${st.mean([r['cost_usd'] for r in rs]):.3f}" if rs else "-")]
    for label, f in rows:
        L.append(f"| {label} | " + " | ".join(f([r for r in results if r["mode"] == m]) for m in modes) + " |")
    L += ["", "## Errors returned by the MCP server during the runs (and how often)", "", "| error code | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    codes = sorted({k for r in results for k in r["errors_seen"]})
    for code in codes:
        L.append(f"| `{code}` | " + " | ".join(str(sum(r["errors_seen"].get(code, 0) for r in results if r["mode"] == m)) for m in modes) + " |")
    if "multi" in modes:
        multi = [r for r in results if r["mode"] == "multi"]
        L += ["", f"## Did the Reviewer change anything?", "",
              f"The Reviewer changed the Scout's cover letter before submitting in **{sum(r['reviewer_edits'] for r in multi)} of "
              f"{sum(r['submitted'] for r in multi)}** submitted applications."]
    L += ["", "## Failed runs", ""]
    failed = [r for r in results if not r["passed"]]
    L += [f"- **{r['case']} / {r['mode']} / r{r['rep']}**: " + "; ".join(r["problems"]) + f"  (trace: `{Path(r['run_dir']).name}`)" for r in failed] or ["None."]
    return "\n".join(L) + "\n"


async def main_async(a) -> None:
    cases = [c for c in yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8")) if not a.cases or c["id"] in a.cases]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sem = asyncio.Semaphore(a.concurrency)
    print(f"{len(cases)} cases x {len(a.modes)} modes x {a.repeat} repeats = {len(cases) * len(a.modes) * a.repeat} runs, concurrency {a.concurrency}")
    results = await asyncio.gather(*[one(c, m, r, stamp, a.model, sem) for c in cases for m in a.modes for r in range(1, a.repeat + 1)])
    results = sorted(results, key=lambda r: (r["case"], r["mode"], r["rep"]))
    out = ROOT / "evaluation" / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report(results, cases, a.modes, stamp, a.model, a.repeat), encoding="utf-8")
    print(f"\nreport: {out / 'report.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--modes", nargs="+", default=["multi", "single"], choices=["multi", "single"])
    ap.add_argument("--cases", nargs="*", default=None, help="subset, e.g. c1 c5")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
