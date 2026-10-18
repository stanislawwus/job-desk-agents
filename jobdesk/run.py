"""CLI: run one request through the two-agent system (or the single-agent baseline) and show what changed.

  uv run python -m jobdesk.run "Find the best remote Python backend job for me and apply to it"
  uv run python -m jobdesk.run --mode single --seed J-108 "Apply to the 2 best remote Python/Django jobs"
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from jobdesk.orchestrator import DEFAULT_TODAY, run_request


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request")
    ap.add_argument("--mode", choices=["multi", "single"], default="multi")
    ap.add_argument("--seed", action="append", default=[], metavar="JOB_ID",
                    help="the candidate already applied to this job before the run (repeatable)")
    ap.add_argument("--today", default=DEFAULT_TODAY)
    ap.add_argument("--model", default=None)
    ap.add_argument("--quiet", action="store_true", help="no live console log")
    a = ap.parse_args()

    result = asyncio.run(run_request(a.request, a.mode, seed=a.seed, today=a.today, model=a.model, verbose=not a.quiet))

    print("\n" + "=" * 78 + "\nFINAL ANSWER\n" + "=" * 78)
    print(result["final_answer"])
    if result.get("questions_for_candidate"):
        print("\nQuestions for the candidate:")
        for q in result["questions_for_candidate"]:
            print(f"  - {q}")

    csv_path = Path(result["workspace"]) / "applications.csv"
    print("\n" + "=" * 78 + "\napplications.csv (the only file `apply` writes)\n" + "=" * 78)
    if csv_path.exists():
        for r in csv.DictReader(csv_path.open(encoding="utf-8", newline="")):
            print(f"  {r['application_id']}  {r['job_id']}  {r['title']} @ {r['company']}  [{r['role']}]  {r['applied_at']}")
    else:
        print("  (no file: no application was written)")
    print(f"\ntrace: {result['run_dir']}/trace.jsonl   (view: uv run python scripts/trace_view.py {result['run_dir']}/trace.jsonl)")


if __name__ == "__main__":
    main()
