"""Frozen copies of live vacancies. Evaluations always run on a snapshot, because live data
changes every day; the snapshot store offers the same search/get interface as live sources."""

from __future__ import annotations

import json
from pathlib import Path

from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources import PREFIX_TO_SOURCE, normalize_job_id
from jobs_helper.sources.base import SourceError, rank


class SnapshotStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.created_at = data.get("created_at")
        self.jobs: dict[str, Job] = {j["id"]: Job(**j) for j in data["jobs"]}

    def search(self, q: SearchQuery) -> list[Job]:
        pool = [j for j in self.jobs.values() if j.source in q.sources]
        return rank(pool, q)

    def get(self, job_id: str) -> Job:
        job_id = normalize_job_id(job_id)
        if job_id.isdigit():  # a bare Djinni number: look it up
            job_id = next((j for j in self.jobs if j.startswith(f"djinni:{job_id}-")), job_id)
        if job_id.split(":", 1)[0] not in PREFIX_TO_SOURCE or ":" not in job_id:
            raise SourceError("invalid_job_id", f"'{job_id}' is not a Djinni id.",
                              "Djinni ids look like 'djinni:851711-junior-manual-qa-engineer'.")
        if job_id not in self.jobs:
            raise SourceError("job_not_found", f"{job_id} is not in snapshot {self.path.name}.",
                              "Use search_jobs to find vacancies available in this snapshot.")
        return self.jobs[job_id]
