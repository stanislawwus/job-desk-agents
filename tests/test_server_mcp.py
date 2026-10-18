"""The `jobs` MCP server tested the way an agent uses it: a real MCP client over stdio against a real subprocess."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
UAPP = "djinni:851711-junior-manual-qa-engineer"
INPOLIUM = "djinni:853773-junior-manual-qa-engineer-mobile-web"
LETTER = ("I am a manual QA engineer with 1.5 years of commercial experience testing iOS and Android apps and web "
          "applications. I wrote about 300 test cases in TestRail and test the REST API with Postman before every release.")


def _run(tmp: Path, role: str, calls: list[tuple[str, dict]], closed: str = ""):
    params = StdioServerParameters(command=sys.executable, args=["-m", "jobs_helper.mcp_jobs.server"], cwd=str(ROOT),
                                   env={"AGENT_ROLE": role, "JOBS_WORKSPACE": str(tmp), "JOBS_SETTINGS": str(tmp / "s.yaml"),
                                        "JOBS_TODAY": "2026-10-18", "JOBS_CLOSED": closed, "PYTHONPATH": str(ROOT)})

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            return tools, [json.loads((await s.call_tool(n, a)).content[0].text) for n, a in calls]
    return asyncio.run(go())


def test_exactly_three_tools(tmp_path):
    assert _run(tmp_path, "scout", [])[0] == ["apply", "get_job", "search_jobs"]


def test_scout_reads_but_cannot_write(tmp_path):
    _, (found, job, denied) = _run(tmp_path, "scout", [("search_jobs", {"keywords": "qa", "remote": True}),
                                                       ("get_job", {"job_id": UAPP}), ("apply", {"job_id": UAPP, "cover_letter": LETTER})])
    assert found["count"] > 0 and job["is_open"] and denied["error"] == "forbidden"
    assert not (tmp_path / "applications.csv").exists()


def test_reviewer_writes_a_row(tmp_path):
    _, (res,) = _run(tmp_path, "reviewer", [("apply", {"job_id": UAPP, "cover_letter": LETTER})])
    assert res["status"] == "submitted"
    lines = (tmp_path / "applications.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and UAPP in lines[1] and "reviewer" in lines[1]


def test_errors_are_structured_and_the_server_survives(tmp_path):
    _, res = _run(tmp_path, "reviewer", [
        ("apply", {"job_id": INPOLIUM, "cover_letter": LETTER}),
        ("apply", {"job_id": "https://jobs.dou.ua/companies/a/vacancies/1/", "cover_letter": LETTER}),
        ("apply", {"job_id": UAPP, "cover_letter": "short"}),
        ("search_jobs", {"keywords": " "}),
        ("get_job", {"job_id": UAPP}),
    ], closed=INPOLIUM)
    closed, other, short, bad, alive = res
    assert (closed["error"], other["error"], short["error"], bad["error"]) == ("job_closed", "unsupported_source",
                                                                               "cover_letter_too_short", "invalid_argument")
    assert all(e["message"] and e["hint"] and "retryable" in e for e in (closed, other, short, bad))
    assert alive["id"] == UAPP


def test_audit_log_records_roles(tmp_path):
    _run(tmp_path, "scout", [("apply", {"job_id": UAPP, "cover_letter": LETTER}), ("get_job", {"job_id": UAPP})])
    log = [json.loads(x) for x in (tmp_path / "mcp_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["role"], e["tool"], e["error"]) for e in log] == [("scout", "apply", "forbidden"), ("scout", "get_job", None)]
