"""Two ways to run the same request:

  multi   Scout (search_jobs, get_job) -> Reviewer (get_job, apply), with at most one revision round
  single  one agent with all three tools: the baseline of the comparison

The orchestrator is plain Python (no LLM). Each run gets its own directory:
  runs/<run>/trace.jsonl      every event of the run
  runs/<run>/result.json      final structured answer
  runs/<run>/workspace/       applications.csv (the only thing `apply` writes) and mcp_calls.jsonl (server-side audit)
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from jobdesk.prompts import (REVIEWER_PROMPT, REVIEWER_SCHEMA, SCOUT_PROMPT, SCOUT_SCHEMA, SINGLE_PROMPT, SINGLE_SCHEMA)
from jobdesk.runtime import ROOT, AgentResult, AgentSpec, RunContext, run_agent
from jobdesk.store import CSV_FIELDS
from jobdesk.tracing import Tracer

SCOUT = AgentSpec("scout", SCOUT_PROMPT, ["search_jobs", "get_job"], SCOUT_SCHEMA)
REVIEWER = AgentSpec("reviewer", REVIEWER_PROMPT, ["get_job", "apply"], REVIEWER_SCHEMA)
SINGLE = AgentSpec("single_agent", SINGLE_PROMPT, ["search_jobs", "get_job", "apply"], SINGLE_SCHEMA, max_turns=35)
MAX_ROUNDS = 2
DEFAULT_TODAY = "2026-10-18"


def candidate_block(candidate_dir: Path) -> str:
    cv = (candidate_dir / "cv.md").read_text(encoding="utf-8")
    profile = yaml.safe_load((candidate_dir / "profile.yaml").read_text(encoding="utf-8"))
    return (f"## Candidate CV (the only source of facts)\n\n{cv}\n\n"
            f"## Candidate profile\n\n```yaml\n{yaml.safe_dump(profile, allow_unicode=True)}```")


def seed_applications(workspace: Path, job_ids: list[str], data_dir: Path, today: str) -> None:
    """Pre-existing applications (the candidate applied to these before the run); used to test duplicate handling."""
    if not job_ids:
        return
    jobs = {j["id"]: j for j in json.loads((data_dir / "jobs.json").read_text(encoding="utf-8"))}
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "applications.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for i, jid in enumerate(job_ids, 1):
            w.writerow({"application_id": f"APP-{i:04d}", "job_id": jid, "title": jobs[jid]["title"], "company": jobs[jid]["company"],
                        "applied_at": "2026-10-01", "role": "seed", "cover_letter": "(application sent earlier by the candidate)", "answers": "{}"})


def make_context(run_dir: Path, run_id: str, *, data_dir: Path, today: str, model: str | None, verbose: bool,
                 seed: list[str]) -> RunContext:
    run_dir = run_dir.resolve()
    workspace = run_dir / "workspace"
    seed_applications(workspace, seed, data_dir, today)
    env = {"JOBDESK_DATA": str(data_dir.resolve()), "JOBDESK_WORKDIR": str(workspace), "JOBDESK_TODAY": today}
    return RunContext(run_dir=run_dir, tracer=Tracer(run_dir, run_id, verbose), server_env=env, model=model)


async def run_multi(request: str, candidate_dir: Path, ctx: RunContext) -> dict:
    cand = candidate_block(candidate_dir)
    feedback, scout_res, reviewer_res, rounds = "", None, None, []
    for round_no in range(1, MAX_ROUNDS + 1):
        scout_prompt = f"{cand}\n\n## User request\n\n{request}"
        if feedback:
            scout_prompt += f"\n\n## Reviewer feedback on your previous drafts\n\n{feedback}"
        scout_res = await run_agent(SCOUT, scout_prompt, ctx, round_no)
        if scout_res.mcp_failed:
            return _mcp_failure(scout_res)
        drafts = scout_res.structured or {"drafts": [], "notes_for_reviewer": "scout returned no structured output", "raw": scout_res.text}
        ctx.tracer.event("handoff", "orchestrator", src="scout", dst="reviewer", round=round_no,
                         summary=f"{len(drafts.get('drafts', []))} drafts, submit_requested={drafts.get('submit_requested')}",
                         drafts=[d.get("job_id") for d in drafts.get("drafts", [])])
        reviewer_prompt = (f"## User request\n\n{request}\n\n{cand}\n\n"
                           f"## Scout drafts (round {round_no})\n\n```json\n{json.dumps(drafts, ensure_ascii=False, indent=1)}\n```")
        reviewer_res = await run_agent(REVIEWER, reviewer_prompt, ctx, round_no)
        if reviewer_res.mcp_failed:
            return _mcp_failure(reviewer_res)
        rounds.append({"scout": _summary(scout_res), "reviewer": _summary(reviewer_res)})
        verdict = reviewer_res.structured or {}
        if not verdict.get("needs_revision") or round_no == MAX_ROUNDS:
            break
        feedback = verdict.get("feedback_for_scout", "")
        ctx.tracer.event("handoff", "orchestrator", src="reviewer", dst="scout", round=round_no, summary=feedback)
    verdict = (reviewer_res.structured if reviewer_res else None) or {}
    return {"final_answer": verdict.get("final_answer") or (reviewer_res.text if reviewer_res else ""),
            "recommended_job_ids": verdict.get("recommended_job_ids", []),
            "questions_for_candidate": verdict.get("questions_for_candidate", []),
            "decisions": verdict.get("decisions", []), "rounds": rounds,
            "agents": [r for rd in rounds for r in rd.values()]}


async def run_single(request: str, candidate_dir: Path, ctx: RunContext) -> dict:
    res = await run_agent(SINGLE, f"{candidate_block(candidate_dir)}\n\n## User request\n\n{request}", ctx)
    if res.mcp_failed:
        return _mcp_failure(res)
    out = res.structured or {}
    return {"final_answer": out.get("final_answer") or res.text, "recommended_job_ids": out.get("recommended_job_ids", []),
            "questions_for_candidate": out.get("questions_for_candidate", []), "rounds": [{"single_agent": _summary(res)}],
            "agents": [_summary(res)]}


async def run_request(request: str, mode: str = "multi", *, candidate_dir: Path | None = None, data_dir: Path | None = None,
                      runs_root: Path | None = None, run_name: str | None = None, seed: list[str] | None = None,
                      today: str = DEFAULT_TODAY, model: str | None = None, verbose: bool = True) -> dict:
    data_dir = data_dir or ROOT / "data"
    candidate_dir = candidate_dir or data_dir / "candidate"
    run_id = uuid.uuid4().hex[:8]
    run_dir = (runs_root or ROOT / "runs") / (run_name or f"{datetime.now():%Y%m%d_%H%M%S}_{mode}_{run_id}")
    ctx = make_context(run_dir, run_id, data_dir=data_dir, today=today, model=model, verbose=verbose, seed=seed or [])
    ctx.tracer.event("run_start", None, mode=mode, request=request, seed=seed or [])
    result = await (run_multi if mode == "multi" else run_single)(request, candidate_dir, ctx)
    result |= {"mode": mode, "run_dir": str(ctx.run_dir), "workspace": ctx.server_env["JOBDESK_WORKDIR"]}
    ctx.tracer.event("run_end", None, final_answer=result["final_answer"])
    (ctx.run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result


def _mcp_failure(r: AgentResult) -> dict:
    return {"final_answer": "The jobdesk MCP server did not start; nothing was done. Run `uv run pytest` to check the server.",
            "recommended_job_ids": [], "questions_for_candidate": [], "error": "mcp_failed", "rounds": [{"failed": _summary(r)}], "agents": []}


def _summary(r: AgentResult) -> dict:
    d = asdict(r)
    d.pop("text", None)
    d["tool_calls"] = [{"tool": c["tool"], "error": c["output"].get("error") if isinstance(c["output"], dict) else None}
                       for c in r.tool_calls]
    return d
