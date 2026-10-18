"""MCP server `jobdesk` (stdio). Three tools: search_jobs, get_job, apply.

Run it:      uv run python -m jobdesk.server
Inspect it:  AGENT_ROLE=single_agent npx @modelcontextprotocol/inspector uv run python -m jobdesk.server

Environment:
  AGENT_ROLE       scout | reviewer | single_agent | viewer (default). `apply` works only for reviewer / single_agent.
  JOBDESK_DATA     directory with jobs.json                (default: ./data)
  JOBDESK_WORKDIR  where applications.csv and mcp_calls.jsonl are written (default: ./workspace)
  JOBDESK_TODAY    fixed date for applied_at (YYYY-MM-DD), for reproducible runs

The server never crashes on a bad call: every failure comes back as {"error", "message", "hint", "retryable"}.
Every call is appended to mcp_calls.jsonl together with the caller's role: an audit log the evaluation reads.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from jobdesk.store import Desk, err

ROOT = Path(__file__).resolve().parent.parent
ROLE = os.environ.get("AGENT_ROLE", "viewer")
_desk: Desk | None = None

mcp = MCPServer(
    name="jobdesk",
    instructions="Vacancy search and applications for one candidate. search_jobs and get_job only read; "
                 "apply submits an application and is allowed for the Reviewer role only.",
)


def desk() -> Desk:
    """Created lazily, so a bad configuration becomes a tool error instead of a dead server."""
    global _desk
    if _desk is None:
        _desk = Desk(Path(os.environ.get("JOBDESK_DATA", ROOT / "data")),
                     Path(os.environ.get("JOBDESK_WORKDIR", ROOT / "workspace")),
                     ROLE, os.environ.get("JOBDESK_TODAY"))
    return _desk


def _audit(tool: str, args: dict, result: dict, started: float) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "role": ROLE, "tool": tool,
             "args": {k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v) for k, v in args.items()},
             "ok": "error" not in result, "error": result.get("error"), "status": result.get("status"),
             "application_id": result.get("application_id"), "ms": round((time.perf_counter() - started) * 1000)}
    try:
        workdir = Path(os.environ.get("JOBDESK_WORKDIR", ROOT / "workspace"))
        workdir.mkdir(parents=True, exist_ok=True)
        with (workdir / "mcp_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # auditing must never break a tool call


def _call(tool: str, args: dict, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn(desk())
    except Exception as e:  # noqa: BLE001  the server must survive a bad call and a bad config
        result = err("internal_error", f"{type(e).__name__}: {e}",
                     "Check the arguments and retry once; if it fails again, report it to the user.", True)
    _audit(tool, args, result, started)
    return result


@mcp.tool()
def search_jobs(
    keywords: Annotated[str, Field(description="Role and key skills, e.g. 'python django'")],
    remote: Annotated[bool | None, Field(description="true: only remote vacancies")] = None,
    min_salary: Annotated[int | None, Field(description="Monthly USD; drops vacancies whose known top salary is lower")] = None,
    max_experience_years: Annotated[float | None, Field(description="Candidate's experience; drops vacancies that require more")] = None,
    limit: Annotated[int, Field(description="Max results, 1..20")] = 8,
) -> dict[str, Any]:
    """READ-ONLY. Search vacancies in the listing index. Returns short summaries with job_id, stack, salary range and
    required years (null = not published). The index can be stale: it does NOT show whether a vacancy is still open;
    call get_job for that."""
    args = {"keywords": keywords, "remote": remote, "min_salary": min_salary,
            "max_experience_years": max_experience_years, "limit": limit}
    return _call("search_jobs", args, lambda d: d.search_jobs(**args))


@mcp.tool()
def get_job(
    job_id: Annotated[str, Field(description="Vacancy id from search_jobs, e.g. 'J-101'")],
) -> dict[str, Any]:
    """READ-ONLY. Full live record of one vacancy: description, requirements, stack, salary, work mode and `status`
    (open | closed). Always check `status` before drafting or applying."""
    return _call("get_job", {"job_id": job_id}, lambda d: d.get_job(job_id))


@mcp.tool()
def apply(
    job_id: Annotated[str, Field(description="Vacancy id, e.g. 'J-101'")],
    cover_letter: Annotated[str, Field(description="Cover letter, 200-1800 characters, ONLY facts from the candidate's real CV")],
    answers: Annotated[dict[str, str] | None, Field(description="Answers to form questions returned earlier as needs_input: {question_id: text}")] = None,
) -> dict[str, Any]:
    """STATE-CHANGING. Submits an application and appends it to applications.csv. Allowed for the Reviewer role only.
    Success: {status: 'submitted', application_id}. Failure is a structured error with a hint: forbidden, unknown_job,
    job_closed (with similar_open_jobs), already_applied, cover_letter_too_short / _too_long, needs_input (with the
    form's questions), human_only_question, answer_too_short, invalid_input."""
    args = {"job_id": job_id, "cover_letter": cover_letter, "answers": answers}
    return _call("apply", args, lambda d: d.apply(job_id, cover_letter, answers))


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
