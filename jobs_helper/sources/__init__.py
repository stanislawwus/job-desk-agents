"""Source registry. This mini-project has exactly one source: Djinni."""

from __future__ import annotations

import re

from jobs_helper.models import Job, SearchQuery, SourceName
from jobs_helper.sources.base import Source, SourceError
from jobs_helper.sources.djinni import DjinniSource

PREFIX_TO_SOURCE: dict[str, SourceName] = {"djinni": "djinni"}
OTHER_SITES = re.compile(r"(dou\.ua|happymonday|linkedin\.com|work\.ua|robota\.ua|^dou:|^hm:|^li:|^ext:)", re.I)
DJINNI_URL = re.compile(r"^(https?://)?(www\.)?djinni\.co/jobs/", re.I)

_instances: dict[SourceName, Source] = {}


def get_source(name: SourceName = "djinni") -> Source:
    if name not in _instances:
        _instances[name] = DjinniSource()
    return _instances[name]


def normalize_job_id(raw: str) -> str:
    """'djinni:851711-slug', '851711-slug' or a djinni.co/jobs/... link -> 'djinni:851711-slug'.
    Anything from another site raises SourceError('unsupported_source')."""
    s = (raw or "").strip()
    if OTHER_SITES.search(s):
        raise SourceError("unsupported_source", f"'{s}' is not a Djinni vacancy. This assistant works only with Djinni.",
                          "Find a similar vacancy on Djinni with search_jobs.")
    if DJINNI_URL.search(s):
        s = "djinni:" + DJINNI_URL.sub("", s).split("?")[0].strip("/")
    elif s.startswith("http"):
        raise SourceError("unsupported_source", f"'{s}' is not a Djinni vacancy link. This assistant works only with Djinni.",
                          "Use a djinni.co/jobs/... link or a job_id from search_jobs.")
    elif re.fullmatch(r"\d+-[\w-]+", s):
        s = "djinni:" + s
    return s


def search_all(q: SearchQuery) -> tuple[list[Job], dict[str, str]]:
    """Kept with the original signature: (jobs, {source: error})."""
    try:
        return get_source().search(q), {}
    except Exception as e:  # noqa: BLE001 — reported, not raised
        return [], {"djinni": f"{type(e).__name__}: {e}"}


def get_job(job_id: str) -> Job:
    job_id = normalize_job_id(job_id)
    if not job_id.startswith("djinni:"):
        raise SourceError("invalid_job_id", f"'{job_id}' is not a Djinni id.",
                          "Djinni ids look like 'djinni:851711-junior-manual-qa-engineer'.")
    return get_source().get(job_id)
