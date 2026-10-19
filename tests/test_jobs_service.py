"""The `jobs` service (Djinni only) tested directly: search, get, and every rule of `apply`."""

import pytest
import yaml

from jobs_helper.mcp_jobs.service import JobsService
from jobs_helper.tracker import Tracker

UAPP = "djinni:851711-junior-manual-qa-engineer"
INPOLIUM = "djinni:853773-junior-manual-qa-engineer-mobile-web"
LETTER = ("I am a manual QA engineer with 1.5 years of commercial experience testing iOS and Android apps and web "
          "applications. I wrote about 300 test cases in TestRail and test the REST API with Postman before every release.")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_TODAY", "2026-10-18")
    monkeypatch.delenv("JOBS_CLOSED", raising=False)
    monkeypatch.delenv("JOBS_DRY_RUN", raising=False)
    return tmp_path


def svc(role="reviewer"):
    return JobsService(role=role, tracker=Tracker())


def test_search_is_djinni_only(env):
    res = svc().search_jobs("manual qa", remote=True)
    assert res["count"] > 0 and res["data_source"].startswith("snapshot:")
    assert all(j["job_id"].startswith("djinni:") and j["url"].startswith("https://djinni.co/") for j in res["jobs"])
    assert all(j["work_mode"] not in ("onsite", "hybrid") for j in res["jobs"])
    assert svc().search_jobs("")["error"] == "invalid_argument"
    assert svc().search_jobs("qa", limit=50)["error"] == "invalid_argument"


def test_get_job_is_lean(env):
    j = svc().get_job("https://djinni.co/jobs/851711-junior-manual-qa-engineer/")
    assert j["id"] == UAPP and j["is_open"] is True and "Postman" in j["description"]
    assert not {"fit", "warnings", "apply_target", "apply_url"} & set(j)      # no extra payload for the agents
    assert svc().get_job("851711")["id"] == UAPP
    assert svc().get_job("djinni:1-removed")["error"] == "job_not_found"
    assert svc().get_job("https://jobs.dou.ua/companies/a/vacancies/1/")["error"] == "unsupported_source"


def test_apply_writes_one_row_and_documents(env):
    res = svc().apply(UAPP, LETTER)
    assert res["ok"] and res["status"] == "submitted" and res["application_id"] == "A-0001"
    rows = Tracker().rows()
    assert len(rows) == 1 and rows[0]["submitted_by"] == "reviewer" and rows[0]["url"].endswith("851711-junior-manual-qa-engineer/")
    ws = env / "ws"
    assert (ws / rows[0]["cover_path"]).read_text(encoding="utf-8").startswith("I am a manual QA")
    assert "Iryna Bondar" in (ws / rows[0]["cv_path"]).read_text(encoding="utf-8")   # the candidate's own CV, unchanged


def test_apply_with_tailored_cv(env):
    from jobs_helper import config
    original = (config.DEFAULT_CANDIDATE / "cv.md").read_text(encoding="utf-8")
    tailored = original.replace("## Skills", "## Skills for mobile QA")
    res = svc().apply(UAPP, LETTER, tailored)
    assert res["ok"] and res["cv"] == "tailored"
    row = Tracker().rows()[0]
    assert row["cv"] == "tailored" and "Skills for mobile QA" in (env / "ws" / row["cv_path"]).read_text(encoding="utf-8")


def test_tailored_cv_cannot_add_facts(env):
    from jobs_helper import config
    original = (config.DEFAULT_CANDIDATE / "cv.md").read_text(encoding="utf-8")
    lie = original.replace("1.5 years of commercial experience", "3 years of commercial experience").replace("Scrum.", "Scrum, Appium.")
    res = svc().apply(UAPP, LETTER, lie)
    assert res["error"] == "unverified_facts" and res["retryable"]
    assert res["unverified"] == {"numbers": ["3"], "tools": ["appium"]}
    assert svc().apply(UAPP, LETTER, "short CV")["error"] == "cv_invalid"
    assert svc().apply(UAPP, LETTER, original.replace("Iryna", "Anna").replace("iryna", "anna"))["error"] == "cv_invalid"
    assert Tracker().rows() == []                                     # nothing was written


def test_scout_cannot_apply(env):
    assert svc("scout").apply(UAPP, LETTER)["error"] == "forbidden"
    assert Tracker().rows() == []


def test_errors(env, monkeypatch):
    s = svc()
    assert s.apply(UAPP, "short")["error"] == "cover_letter_too_short"
    assert s.apply(UAPP, "x" * 2000)["error"] == "cover_letter_too_long"
    assert s.apply("https://jobs.dou.ua/companies/a/vacancies/1/", LETTER)["error"] == "unsupported_source"
    assert s.apply("djinni:1-removed", LETTER)["error"] == "job_not_found"
    monkeypatch.setenv("JOBS_CLOSED", INPOLIUM)
    closed = svc().apply(INPOLIUM, LETTER)
    assert closed["error"] == "job_closed" and closed["retryable"] is False and closed["similar_open_jobs"]
    assert svc().get_job(INPOLIUM)["is_open"] is False
    assert Tracker().rows() == []


def test_duplicate_is_refused(env):
    first = svc().apply(UAPP, LETTER)
    dup = svc().apply("851711", LETTER)
    assert dup["error"] == "already_applied" and dup["application_id"] == first["application_id"]


def test_policies_and_dry_run(env, monkeypatch):
    (env / "settings.yaml").write_text(yaml.safe_dump({"submit_policy": {"djinni": "manual"}}), encoding="utf-8")
    assert svc().apply(UAPP, LETTER)["status"] == "awaiting_user"
    monkeypatch.setenv("JOBS_DRY_RUN", "1")
    assert svc().apply("djinni:853744-junior-manual-qa", LETTER)["status"] == "prepared"


def test_expired_vacancy(env, monkeypatch):
    monkeypatch.setenv("JOBS_TODAY", "2026-12-01")      # every vacancy in the snapshot is valid through 2026-11-15
    assert svc().apply(UAPP, LETTER)["error"] == "job_closed"


def test_djinni_form_needs_contacts_from_the_profile(env, monkeypatch):
    cand = env / "cand"
    cand.mkdir()
    (cand / "candidate.yaml").write_text(yaml.safe_dump({"personal": {"first_name": "Test", "last_name": "User"}}), encoding="utf-8")
    (cand / "cv.md").write_text("# Test User\nManual QA.", encoding="utf-8")
    monkeypatch.setenv("JOBS_CANDIDATE", str(cand / "candidate.yaml"))
    res = svc().apply(UAPP, LETTER)
    assert res["error"] == "candidate_profile_incomplete" and res["retryable"] is False
    assert any(q["label"] == "Електронна пошта" for q in res["questions"])
    assert Tracker().rows() == []
