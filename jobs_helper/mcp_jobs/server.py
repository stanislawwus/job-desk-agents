"""MCP server `jobs` (stdio), Djinni only. Exactly three tools: search_jobs, get_job, apply.

Run:      uv run python -m jobs_helper.mcp_jobs.server
Inspect:  AGENT_ROLE=single_agent npx @modelcontextprotocol/inspector uv run python -m jobs_helper.mcp_jobs.server

Env: AGENT_ROLE (scout | reviewer | single_agent | manual), JOBS_SNAPSHOT (path, default: data/snapshots/2026-10-17_djinni.json,
     or `live` for djinni.co), JOBS_WORKSPACE, JOBS_CANDIDATE, JOBS_SETTINGS, JOBS_TODAY, JOBS_DRY_RUN, JOBS_CLOSED.
"""

from __future__ import annotations

import json
import os
import time
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from jobs_helper.mcp_jobs.service import JobsService, err
from jobs_helper.models import now_iso
from jobs_helper.tracker import workspace

_service: JobsService | None = None
_config_error: str | None = None


def get_service() -> JobsService:
    """Created lazily: a misconfiguration becomes a tool error instead of a crashed server."""
    global _service, _config_error
    if _service is None:
        try:
            _service = JobsService()
        except Exception as e:  # noqa: BLE001
            _config_error = f"{type(e).__name__}: {e}"
            raise
    return _service


mcp = MCPServer(
    name="jobs",
    instructions=(
        "Djinni (djinni.co) vacancies for one candidate: search, read details, apply with a message to the recruiter. "
        "Only Djinni is supported. How an application is sent (manual / confirm / auto) is chosen by the candidate "
        "and is not controlled by the caller."
    ),
)


def _audit(tool: str, args: dict, result: dict, started: float) -> None:
    short = {k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v) for k, v in args.items()}
    entry = {"ts": now_iso(), "role": os.environ.get("AGENT_ROLE", "manual"), "tool": tool, "args": short, "ok": "error" not in result,
             "error": result.get("error"), "status": result.get("status"),
             "application_id": result.get("application_id"), "ms": round((time.perf_counter() - started) * 1000)}
    try:
        with (workspace() / "mcp_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _safe(tool: str, args: dict, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn(get_service())
    except Exception as e:  # the server must never crash on bad input or a flaky site
        if _config_error:
            result = err("server_misconfigured", _config_error, "The operator must fix JOBS_* settings; do not retry.")
        else:
            result = err("internal_error", f"{type(e).__name__}: {e}", "Retry once; if it fails again, report it.", True)
    _audit(tool, args, result, started)
    return result


@mcp.tool()
def search_jobs(
    keywords: Annotated[str, Field(description="Role and key skills, e.g. 'manual qa' or 'python django'")],
    remote: Annotated[bool | None, Field(description="True: drop vacancies marked onsite / hybrid")] = None,
    min_salary: Annotated[int | None, Field(description="Monthly USD; drops vacancies whose known salary is lower")] = None,
    max_experience_years: Annotated[float | None, Field(description="Candidate's experience; drops vacancies requiring more")] = None,
    limit: Annotated[int, Field(description="Max results, 1..20")] = 10,
) -> dict[str, Any]:
    """READ-ONLY. Search Djinni vacancies. Returns short cards: job_id ('djinni:<number>-<slug>'), title, company,
    category, work mode, seniority, required years, English, salary (null = not published) and the Djinni URL.
    Fields are parsed from Djinni and can be wrong; the listing does not say whether a vacancy is open: use get_job."""
    args = {"keywords": keywords, "remote": remote, "min_salary": min_salary,
            "max_experience_years": max_experience_years, "limit": limit}
    return _safe("search_jobs", args, lambda svc: svc.search_jobs(**args))


@mcp.tool()
def get_job(
    job_id: Annotated[str, Field(description="Djinni job_id from search_jobs, or a djinni.co/jobs/... link")],
) -> dict[str, Any]:
    """READ-ONLY. The Djinni vacancy: description (the source of truth for requirements and work mode), experience,
    English, salary, valid_through and is_open (with similar_open_jobs when it is closed)."""
    return _safe("get_job", {"job_id": job_id}, lambda svc: svc.get_job(job_id))


@mcp.tool()
def apply(
    job_id: Annotated[str, Field(description="Djinni job_id or djinni.co/jobs/... link")],
    cover_letter: Annotated[str, Field(description="Message to the recruiter, 200-1800 chars, ONLY facts from the candidate's CV")],
    cv_markdown: Annotated[str | None, Field(description="The candidate's CV tailored to this vacancy, markdown, 400-6000 chars: "
                                                         "the original CV reordered, shortened and rephrased, nothing added. "
                                                         "Omit to attach the original CV")] = None,
) -> dict[str, Any]:
    """APPLY to a Djinni vacancy (state-changing). Fills the Djinni form with the candidate profile (name, email,
    LinkedIn), the tailored CV (or the candidate's own CV) and this message, acts on the candidate's submit policy and
    adds a row to the applications tracker (workspace/applications.csv). Returns status submitted | awaiting_user |
    prepared | declined, or a structured error: forbidden, unsupported_source, invalid_job_id, job_not_found, job_closed
    (with similar_open_jobs), already_applied, cover_letter_too_short / _too_long, cv_invalid, unverified_facts (the CV
    has numbers or tools that the original CV does not), candidate_profile_incomplete."""
    args = {"job_id": job_id, "cover_letter": cover_letter, "cv_markdown": cv_markdown}
    return _safe("apply", args, lambda svc: svc.apply(job_id, cover_letter, cv_markdown))


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
