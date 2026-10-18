"""Business logic behind the `jobs` MCP tools, Djinni only. Kept free of MCP so it can be tested directly.

Trimmed from jobs_helper_MCP for the mini-project, so the agents get small inputs and small outputs:
- one source (Djinni), no DOU / Happy Monday / LinkedIn / ATS / browser;
- `apply` takes only the message to the recruiter: the candidate's own CV is attached as is, agents never rewrite it;
- `get_job` returns the vacancy itself, without fit scores, red flags or activity logs.

Every failure is returned (never raised) as:
    {"error": <code>, "message": <what happened>, "hint": <what to do next>, "retryable": bool, ...extra}
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from jobs_helper import config
from jobs_helper import policy as pol
from jobs_helper.apply.backend import ApplyBackend, ApplyRequest
from jobs_helper.apply.mock import MockBackend
from jobs_helper.models import Job, SearchQuery
from jobs_helper.snapshot import SnapshotStore
from jobs_helper.sources import get_job as live_get_job
from jobs_helper.sources import normalize_job_id, search_all
from jobs_helper.sources.base import SourceError
from jobs_helper.tracker import Tracker

WRITER_ROLES = {"reviewer", "single_agent", "manual"}
COVER_MIN, COVER_MAX = 200, 1800
DESCRIPTION_MAX = 3000


def err(code: str, message: str, hint: str = "", retryable: bool = False, **extra) -> dict:
    return {"error": code, "message": message, "hint": hint, "retryable": retryable, **extra}


class LiveProvider:
    name = "live:djinni.co"

    def search(self, q: SearchQuery) -> tuple[list[Job], dict]:
        return search_all(q)

    def get(self, job_id: str) -> Job:
        return live_get_job(job_id)


class SnapshotProvider:
    def __init__(self, path: Path):
        self.store = SnapshotStore(path)
        self.name = f"snapshot:{path.name}"

    def search(self, q: SearchQuery) -> tuple[list[Job], dict]:
        return self.store.search(q), {}

    def get(self, job_id: str) -> Job:
        return self.store.get(job_id)


def provider_from_env():
    """JOBS_SNAPSHOT=<path> (default: the frozen Djinni snapshot) or JOBS_SNAPSHOT=live for djinni.co right now."""
    snap = os.environ.get("JOBS_SNAPSHOT", str(config.DEFAULT_SNAPSHOT))
    if snap == "live":
        return LiveProvider()
    path = Path(snap)
    if not path.exists():
        raise RuntimeError(f"JOBS_SNAPSHOT={snap!r}: snapshot file not found")
    return SnapshotProvider(path)


def today() -> date:
    return date.fromisoformat(os.environ["JOBS_TODAY"]) if os.environ.get("JOBS_TODAY") else date.today()


def closed_overlay() -> set[str]:
    """JOBS_CLOSED: Djinni ids to treat as closed since the snapshot was taken (a test fixture)."""
    return {x.strip() for x in os.environ.get("JOBS_CLOSED", "").split(",") if x.strip()}


class JobsService:
    def __init__(self, provider=None, backend: ApplyBackend | None = None, tracker: Tracker | None = None,
                 role: str | None = None):
        self.provider = provider or provider_from_env()
        self.backend = backend or MockBackend()
        self.tracker = tracker or Tracker()
        self.role = role or os.environ.get("AGENT_ROLE", "manual")
        self.closed = closed_overlay()

    # ------------------------------------------------------------ search_jobs
    def search_jobs(self, keywords: str, remote: bool | None = None, min_salary: int | None = None,
                    max_experience_years: float | None = None, limit: int = 10) -> dict:
        if not keywords or not keywords.strip():
            return err("invalid_argument", "keywords is empty.", "Pass a role and key skills, e.g. 'manual qa' or 'python'.", True)
        if not 1 <= limit <= 20:
            return err("invalid_argument", f"limit={limit} is out of range.", "Use 1..20.", True)
        if min_salary is not None and min_salary < 0:
            return err("invalid_argument", "min_salary must be >= 0.", "Monthly USD, e.g. 1000.", True)
        q = SearchQuery(keywords=keywords, remote=remote, min_salary=min_salary, max_experience_years=max_experience_years,
                        limit=limit)
        jobs, errors = self.provider.search(q)
        rows = [j.short() for j in jobs]
        out = {"count": len(rows), "data_source": self.provider.name, "jobs": rows,
               "note": "Fields are parsed from Djinni and can be wrong (e.g. work_mode); read the description with get_job. "
                       "The listing does not show whether a vacancy is still open: get_job shows is_open."}
        if errors:
            out["source_errors"] = errors
        if not rows:
            out["hint"] = "Nothing matched. Try broader keywords (a Djinni category such as 'qa' or 'python') or fewer filters."
        return out

    # ------------------------------------------------------------ get_job
    def get_job(self, job_id: str) -> dict:
        try:
            job = self.provider.get(job_id)
        except SourceError as e:
            return err(e.code, e.message, e.hint, e.code in ("invalid_job_id", "source_unavailable", "job_not_found"))
        data = job.model_dump(exclude={"fetched_at", "details_loaded", "apply_type", "apply_url", "cities", "source"})
        if len(data["description"]) > DESCRIPTION_MAX:
            data["description"] = data["description"][:DESCRIPTION_MAX] + "…"
        data["is_open"] = self._is_open(job)
        if not data["is_open"]:
            data["similar_open_jobs"] = self._similar(job)
        return data

    # ------------------------------------------------------------ apply
    def apply(self, job_id: str, cover_letter: str) -> dict:
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' may not submit applications.",
                       "Only the reviewer agent calls apply; hand the draft over instead.")
        cover = (cover_letter or "").strip()
        if len(cover) < COVER_MIN:
            return err("cover_letter_too_short", f"The message has {len(cover)} chars, minimum is {COVER_MIN}.",
                       "Write 3-6 sentences specific to this vacancy, only with facts from the CV.", True)
        if len(cover) > COVER_MAX:
            return err("cover_letter_too_long", f"The message has {len(cover)} chars, maximum is {COVER_MAX}.",
                       "Keep it under ~250 words.", True)
        try:
            job = self.provider.get(job_id)
        except SourceError as e:
            return err(e.code, e.message, e.hint, e.code == "invalid_job_id")
        if not self._is_open(job):
            similar = self._similar(job)
            alt = ", ".join(f"{s['job_id']} ({s['title']}, {s['company']})" for s in similar) or "none found"
            return err("job_closed", f"{job.id} ({job.title}, {job.company}) is closed on Djinni.",
                       f"Do not retry this vacancy. Similar open jobs: {alt}", False, similar_open_jobs=similar)
        profile = pol.load_candidate()
        name = _full_name(profile)
        if not profile or not name:
            return err("candidate_profile_missing", "candidate.yaml is missing or has no name.",
                       "Ask the candidate to fill candidate.yaml; never invent personal data.")
        prev = self.tracker.find_active(name, job.id)
        if prev:
            return err("already_applied", f"{name} already applied to {job.id} on {prev['created_at'][:10]} "
                       f"({prev['application_id']}, status {prev['status']}).", "Do not re-submit; pick another job.", False,
                       application_id=prev["application_id"])
        cv_path = pol.cv_file(profile)
        outcome = self.backend.apply(ApplyRequest(job=job, target="djinni", profile=profile,
                                                  cv_text=pol.current_cv_text(profile), cover_letter=cover,
                                                  policy=pol.load_policies()["djinni"], dry_run=pol.dry_run(),
                                                  cv_path=str(cv_path) if cv_path else None))
        if outcome.status == "needs_input":
            return err("candidate_profile_incomplete", outcome.message,
                       "These Djinni form fields come only from the candidate profile: ask the candidate, never invent "
                       "contact details or a CV.", False, questions=outcome.questions)
        dry = pol.dry_run()
        app_id = self.tracker.next_id()
        cv_saved, cover_saved = self.tracker.save_documents(app_id, pol.current_cv_text(profile), cover)
        self.tracker.add(application_id=app_id, candidate=name, job_id=job.id, source="djinni", target="djinni",
                         company=job.company or "", title=job.title, url=job.url, status=outcome.status,
                         policy="dry_run" if dry else pol.load_policies()["djinni"], cv_path=cv_saved,
                         cover_path=cover_saved, submitted_by=self.role)
        return {"ok": True, "application_id": app_id, "status": outcome.status, "job_id": job.id, "url": job.url,
                "policy": "dry_run" if dry else pol.load_policies()["djinni"], "message": outcome.message}

    # ------------------------------------------------------------ helpers
    def _is_open(self, job: Job) -> bool:
        if job.id in self.closed:
            return False
        if not job.valid_through:
            return True
        try:
            return date.fromisoformat(job.valid_through[:10]) >= today()
        except ValueError:
            return True

    def _similar(self, job: Job, k: int = 3) -> list[dict]:
        try:
            found, _ = self.provider.search(SearchQuery(keywords=" ".join(job.tags) or job.title, limit=k + 5))
        except Exception:  # noqa: BLE001 — a hint is optional
            return []
        return [{"job_id": j.id, "title": j.title, "company": j.company} for j in found if j.id != job.id and self._is_open(j)][:k]


def _full_name(profile: dict | None) -> str:
    p = (profile or {}).get("personal") or {}
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


__all__ = ["JobsService", "err", "normalize_job_id"]
