"""Offline tests for the Djinni parser and the heuristics (fixtures saved from real Djinni pages on 2026-10-17)."""

from pathlib import Path

import feedparser
import pytest

from jobs_helper import extract
from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources import base, normalize_job_id
from jobs_helper.sources.djinni import DjinniSource

F = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- heuristics

@pytest.mark.parametrize("text,expected", [
    ("5+ years of professional Python\n2+ years with Spark\nAt least 1 year of AWS", 5.0),
    ("Досвід роботи від 3 років з Django", 3.0),
    ("We have been 15 years on the market. 2+ years of experience required", 2.0),
    ("no numbers here", None),
])
def test_guess_experience(text, expected):
    assert extract.guess_experience_years(text) == expected


@pytest.mark.parametrize("title,expected", [
    ("Middle/Senior Python Engineer", "middle"),
    ("Senior React Engineer", "senior"),
    ("Tech Lead (Python / FastAPI)", "lead"),
    ("Python Developer", "unknown"),
])
def test_guess_seniority(title, expected):
    assert extract.guess_seniority(title) == expected


def test_guess_english():
    assert extract.guess_english("Requirements: English — Upper-Intermediate") == "B2"
    assert extract.guess_english("Англійська B1 – Середній") == "B1"


def test_relevance_ignores_generic_words():
    react = extract.relevance("react developer", "React Developer", [], "")
    java = extract.relevance("react developer", "Java Developer", [], "")
    assert react > 0 and java == 0


# ---------------------------------------------------------------- Djinni

def test_djinni_feed_and_page():
    feed = feedparser.parse((F / "djinni_feed.xml").read_bytes())
    jobs = [DjinniSource()._from_entry(e) for e in feed.entries]
    assert len(jobs) == 100 and all(j.id.startswith("djinni:") for j in jobs)
    job = DjinniSource()._from_page("djinni:853661-middle-python-developer", "https://djinni.co/jobs/853661-middle-python-developer/",
                                    (F / "djinni_page.html").read_text())
    assert job.company and job.details_loaded and job.apply_type == "on_site"
    assert job.experience_years is not None and job.valid_through


# ---------------------------------------------------------------- filters

def _job(**kw) -> Job:
    return Job(**({"id": "djinni:1-python-developer", "source": "djinni", "url": "u", "title": "Python Developer"} | kw))


def test_filters_keep_unknowns_drop_contradictions():
    q = SearchQuery(keywords="python", remote=True, min_salary=3000, max_experience_years=3)
    assert base.passes_filters(_job(), q)                                   # all unknown -> keep
    assert not base.passes_filters(_job(work_mode="onsite"), q)
    assert not base.passes_filters(_job(salary_min=1500, salary_max=2500), q)
    assert not base.passes_filters(_job(experience_years=5), q)
    assert base.passes_filters(_job(work_mode="remote", salary_max=4000, experience_years=3), q)


# ---------------------------------------------------------------- Djinni ids: only Djinni is accepted

@pytest.mark.parametrize("raw", ["djinni:851711-junior-manual-qa-engineer", "851711-junior-manual-qa-engineer",
                                 "https://djinni.co/jobs/851711-junior-manual-qa-engineer/",
                                 "djinni.co/jobs/851711-junior-manual-qa-engineer?utm_source=x"])
def test_djinni_ids_and_links(raw):
    assert normalize_job_id(raw) == "djinni:851711-junior-manual-qa-engineer"


@pytest.mark.parametrize("raw", ["https://jobs.dou.ua/companies/acme/vacancies/376945/", "dou:acme/376945", "hm:2049225",
                                 "https://www.linkedin.com/jobs/view/123", "https://example.com/careers/qa"])
def test_other_sites_are_refused(raw):
    with pytest.raises(base.SourceError) as e:
        normalize_job_id(raw)
    assert e.value.code == "unsupported_source"
