"""The MCP server tested the way an agent uses it: a real MCP client over stdio against a real subprocess."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
LONG_LETTER = ("I have 3.5 years of commercial Python and Django experience building REST APIs with Django REST Framework "
               "and PostgreSQL. At my current job I cut the p95 latency of the orders API from 800 ms to 320 ms with Redis "
               "caching and moved a nightly report to Celery. I would like to bring the same care to your team.")


def _params(workdir: Path, role: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "jobdesk.server"],
        env={"AGENT_ROLE": role, "JOBDESK_DATA": str(ROOT / "data"), "JOBDESK_WORKDIR": str(workdir),
             "JOBDESK_TODAY": "2026-10-18", "PYTHONPATH": str(ROOT)})


def _run(workdir: Path, role: str, calls: list[tuple[str, dict]]):
    """Opens one session, performs the calls in order, returns (tool names, parsed results)."""
    async def go():
        async with stdio_client(_params(workdir, role)) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                out = []
                for name, args in calls:
                    res = await session.call_tool(name, args)
                    out.append(json.loads(res.content[0].text))
                return tools, out
    return asyncio.run(go())


def test_server_lists_exactly_three_tools(tmp_path):
    tools, _ = _run(tmp_path, "scout", [])
    assert sorted(tools) == ["apply", "get_job", "search_jobs"]


def test_search_and_get_job_work_for_scout(tmp_path):
    _, (found, job, closed) = _run(tmp_path, "scout", [
        ("search_jobs", {"keywords": "python django", "remote": True, "limit": 5}),
        ("get_job", {"job_id": "J-101"}),
        ("get_job", {"job_id": "J-109"}),
    ])
    assert found["jobs"] and all(j["remote"] for j in found["jobs"])
    assert job["status"] == "open" and "questions" not in job   # form questions stay hidden until apply
    assert closed["status"] == "closed"


def test_scout_cannot_write_and_nothing_is_written(tmp_path):
    _, (res,) = _run(tmp_path, "scout", [("apply", {"job_id": "J-103", "cover_letter": LONG_LETTER})])
    assert res["error"] == "forbidden" and res["retryable"] is False
    assert not (tmp_path / "applications.csv").exists()


def test_reviewer_applies_and_row_is_written(tmp_path):
    _, (res,) = _run(tmp_path, "reviewer", [("apply", {"job_id": "J-103", "cover_letter": LONG_LETTER})])
    assert res["status"] == "submitted" and res["application_id"] == "APP-0001"
    rows = (tmp_path / "applications.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2 and "J-103" in rows[1] and "reviewer" in rows[1]


def test_errors_are_structured_and_the_server_survives(tmp_path):
    _, results = _run(tmp_path, "reviewer", [
        ("apply", {"job_id": "J-109", "cover_letter": LONG_LETTER}),   # closed
        ("apply", {"job_id": "J-999", "cover_letter": LONG_LETTER}),   # unknown
        ("apply", {"job_id": "J-103", "cover_letter": "too short"}),   # bad letter
        ("apply", {"job_id": "J-101", "cover_letter": LONG_LETTER}),   # form has questions
        ("search_jobs", {"keywords": "   "}),                          # bad input
        ("get_job", {"job_id": "J-103"}),                              # the server is still alive afterwards
    ])
    closed, unknown, short, needs, bad, alive = results
    assert closed["error"] == "job_closed" and closed["similar_open_jobs"] and closed["retryable"] is False
    assert unknown["error"] == "unknown_job"
    assert short["error"] == "cover_letter_too_short" and short["retryable"] is True
    assert needs["error"] == "needs_input" and {q["id"] for q in needs["questions"]} == {"why_looking", "achievement"}
    assert bad["error"] == "invalid_input"
    assert alive["id"] == "J-103"
    for e in (closed, unknown, short, needs, bad):
        assert e["message"] and e["hint"], "every error must say what happened and what to do next"


def test_duplicate_is_refused(tmp_path):
    _, (first, second) = _run(tmp_path, "reviewer", [
        ("apply", {"job_id": "J-103", "cover_letter": LONG_LETTER}),
        ("apply", {"job_id": "j103", "cover_letter": LONG_LETTER}),    # the id is normalised
    ])
    assert first["status"] == "submitted"
    assert second["error"] == "already_applied" and second["application_id"] == first["application_id"]
    assert len((tmp_path / "applications.csv").read_text(encoding="utf-8").splitlines()) == 2


def test_audit_log_records_role_and_outcome(tmp_path):
    _run(tmp_path, "scout", [("apply", {"job_id": "J-103", "cover_letter": LONG_LETTER}), ("get_job", {"job_id": "J-103"})])
    log = [json.loads(x) for x in (tmp_path / "mcp_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["role"], e["tool"], e["ok"], e["error"]) for e in log] == [
        ("scout", "apply", False, "forbidden"), ("scout", "get_job", True, None)]
