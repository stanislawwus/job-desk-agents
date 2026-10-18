"""Run the assistant on one request (Djinni only).

  uv run python -m jobs_helper.agents.run "Apply to the 2 best remote manual QA vacancies on Djinni for me."
  uv run python -m jobs_helper.agents.run --mode single --seed djinni:851711-junior-manual-qa-engineer "..."
  uv run python -m jobs_helper.agents.run --snapshot live "Find 3 remote QA vacancies on Djinni, do not apply."
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from jobs_helper import config
from jobs_helper.agents.orchestrator import run_request
from jobs_helper.tracker import Tracker

DEFAULT_TODAY = "2026-10-18"


def seed_application(tr: Tracker, candidate: str, job_id: str) -> str:
    """An application the candidate sent before the run (for the duplicate case)."""
    from jobs_helper.snapshot import SnapshotStore
    job = SnapshotStore(config.DEFAULT_SNAPSHOT).jobs.get(job_id)
    app_id = tr.next_id()
    tr.add(application_id=app_id, candidate=candidate, job_id=job_id, source="djinni", target="djinni",
           company=job.company if job else "", title=job.title if job else "", url=job.url if job else "",
           status="submitted", policy="manual", submitted_by="seed", created_at="2026-10-10T10:00:00+00:00")
    return app_id


def main() -> None:
    p = argparse.ArgumentParser(prog="jobs_helper.agents.run")
    p.add_argument("request")
    p.add_argument("--candidate", type=Path, default=config.DEFAULT_CANDIDATE, help="directory with cv.md and candidate.yaml")
    p.add_argument("--mode", choices=["multi", "single"], default="multi")
    p.add_argument("--snapshot", default=None, help="snapshot path (default: the Djinni snapshot) or 'live' for djinni.co")
    p.add_argument("--seed", action="append", default=[], metavar="JOB_ID", help="the candidate already applied here (repeatable)")
    p.add_argument("--closed", action="append", default=[], metavar="JOB_ID", help="treat as closed on Djinni (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="fill the form but send nothing (status prepared)")
    p.add_argument("--model", help="Claude model, e.g. claude-sonnet-5-5; default: Claude Code default")
    p.add_argument("--today", help=f"fixed date YYYY-MM-DD; default {DEFAULT_TODAY} for the snapshot, the real date for live")
    args = p.parse_args()

    from datetime import datetime
    run_dir = config.ROOT / "runs" / f"{datetime.now():%Y%m%d_%H%M%S}_{args.mode}"
    ws = run_dir / "workspace"
    if args.seed:
        import yaml
        profile = yaml.safe_load((args.candidate / "candidate.yaml").read_text(encoding="utf-8"))
        name = f"{profile['personal']['first_name']} {profile['personal']['last_name']}"
        tr = Tracker(ws)
        for jid in args.seed:
            seed_application(tr, name, jid)
    today = args.today or (None if args.snapshot == "live" else DEFAULT_TODAY)
    result = asyncio.run(run_request(args.request, args.candidate, args.mode, snapshot=args.snapshot, dry_run=args.dry_run,
                                     model=args.model, today=today, workspace=ws, run_name=run_dir.name, closed=args.closed))
    print("\n" + "=" * 80 + "\n" + result["final_answer"])
    if result.get("questions_for_candidate"):
        print("\nПитання до кандидата:\n- " + "\n- ".join(result["questions_for_candidate"]))
    print("\n" + "=" * 80 + "\napplications.csv (пише лише apply)")
    path = ws / "applications.csv"
    for r in (csv.DictReader(path.open(encoding="utf-8")) if path.exists() else []):
        print(f"  {r['application_id']}  {r['title']} @ {r['company']}  [{r['submitted_by']}, {r['status']}]  {r['url']}")
    if not path.exists():
        print("  (файлу немає: жодного відгуку не записано)")
    print(f"\nТрейс: {result['run_dir']}/trace.jsonl")


if __name__ == "__main__":
    main()
