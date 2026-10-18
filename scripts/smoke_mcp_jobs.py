"""Smoke test of the `jobs` MCP server through a real MCP stdio client (no direct imports of the service).

    uv run python scripts/smoke_mcp_jobs.py        # Djinni snapshot + mock Djinni form, temp workspace
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
LETTER = ("Hello! I am a manual QA engineer with 1.5 years of commercial experience testing iOS and Android apps and web "
          "applications. I wrote about 300 test cases in TestRail and test the REST API with Postman.")


def show(title: str, res) -> dict:
    data = json.loads(res.content[0].text) if res.content else {}
    print(f"\n> {title}  -> {data.get('error') or data.get('status') or str(data.get('count', '')) + ' jobs'}")
    print("  " + json.dumps(data, ensure_ascii=False)[:300])
    return data


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="jobs_smoke_"))
    env = {**os.environ, "AGENT_ROLE": "reviewer", "JOBS_WORKSPACE": str(tmp), "JOBS_SETTINGS": str(tmp / "settings.yaml"),
           "JOBS_TODAY": "2026-10-18", "JOBS_CLOSED": "djinni:853773-junior-manual-qa-engineer-mobile-web"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "jobs_helper.mcp_jobs.server"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        print("tools:", [t.name for t in (await s.list_tools()).tools])
        show("search_jobs('manual qa', remote)", await s.call_tool("search_jobs", {"keywords": "manual qa", "remote": True}))
        show("get_job(djinni.co link)", await s.call_tool("get_job", {"job_id": "https://djinni.co/jobs/851711-junior-manual-qa-engineer/"}))
        show("apply(UAPP.AI)", await s.call_tool("apply", {"job_id": "djinni:851711-junior-manual-qa-engineer", "cover_letter": LETTER}))
        show("apply(UAPP.AI) again", await s.call_tool("apply", {"job_id": "djinni:851711-junior-manual-qa-engineer", "cover_letter": LETTER}))
        show("apply(closed Inpolium)", await s.call_tool("apply", {"job_id": "djinni:853773-junior-manual-qa-engineer-mobile-web", "cover_letter": LETTER}))
        show("apply(DOU link)", await s.call_tool("apply", {"job_id": "https://jobs.dou.ua/companies/a/vacancies/1/", "cover_letter": LETTER}))
    print(f"\napplications.csv:\n{(tmp / 'applications.csv').read_text(encoding='utf-8')}")


if __name__ == "__main__":
    asyncio.run(main())
