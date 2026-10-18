"""Domain rules of the desk, tested without MCP."""

import json
from pathlib import Path

import pytest

from jobdesk.store import Desk, normalize_job_id

DATA = Path(__file__).resolve().parent.parent / "data"
LETTER = "x" * 300


@pytest.fixture
def desk(tmp_path):
    return Desk(DATA, tmp_path, "reviewer", "2026-10-18")


def test_normalize_job_id():
    assert [normalize_job_id(x) for x in ("j101", "J-101", " 101 ", "J_7")] == ["J-101", "J-101", "J-101", "J-7"]


def test_search_ranks_title_and_stack_above_description(desk):
    ids = [j["job_id"] for j in desk.search_jobs("python django", remote=True)["jobs"]]
    assert ids[0] in {"J-108", "J-109", "J-103"} and "J-104" not in ids   # J-104 is on-site


def test_search_filters(desk):
    res = desk.search_jobs("python", max_experience_years=3.5, limit=20)["jobs"]
    assert res and all(j["experience_years_min"] is None or j["experience_years_min"] <= 3.5 for j in res)
    assert "J-106" not in [j["job_id"] for j in res]                       # needs 5+ years
    rich = desk.search_jobs("python", min_salary=7000)["jobs"]
    assert all(j["salary_usd"] is None or int(j["salary_usd"].split("-")[1]) >= 7000 for j in rich)


def test_search_does_not_reveal_status_but_get_job_does(desk):
    res = desk.search_jobs("django")["jobs"]
    assert "J-109" in [j["job_id"] for j in res] and all("status" not in j for j in res)
    assert desk.get_job("J-109")["status"] == "closed"


def test_empty_search_is_an_error_with_a_hint(desk):
    assert desk.search_jobs("")["error"] == "invalid_input"
    assert "hint" in desk.search_jobs("zzzzqqq")


def test_closed_job_suggests_similar_open_ones(desk):
    e = desk.apply("J-109", LETTER)
    assert e["error"] == "job_closed" and all(s["job_id"] != "J-109" for s in e["similar_open_jobs"])
    assert all(desk.jobs[s["job_id"]]["status"] == "open" for s in e["similar_open_jobs"])


def test_form_questions_flow(desk):
    e = desk.apply("J-101", LETTER)
    assert e["error"] == "needs_input" and e["retryable"]
    ok = desk.apply("J-101", LETTER, {"why_looking": "I want to grow into bigger systems.",
                                      "achievement": "I cut API latency from 800 ms to 320 ms."})
    assert ok["status"] == "submitted"
    stored = desk.applications()[0]
    assert set(json.loads(stored["answers"])) == {"why_looking", "achievement"}


def test_human_only_question_is_never_accepted(desk):
    e = desk.apply("J-112", LETTER)
    assert e["error"] == "needs_input" and e["questions"][0]["human_only"] is True
    refused = desk.apply("J-112", LETTER, {"relatives": "No, none at all."})
    assert refused["error"] == "human_only_question" and refused["retryable"] is False
    assert desk.applications() == []


def test_read_only_roles_cannot_write(tmp_path):
    for role in ("scout", "viewer", ""):
        assert Desk(DATA, tmp_path, role).apply("J-103", LETTER)["error"] == "forbidden"
    assert not (tmp_path / "applications.csv").exists()


def test_ids_continue_after_existing_rows(desk):
    assert desk.apply("J-103", LETTER)["application_id"] == "APP-0001"
    assert desk.apply("J-121", LETTER)["application_id"] == "APP-0002"


def test_bad_types_do_not_crash(desk):
    assert desk.apply(None, LETTER)["error"] == "invalid_input"              # type: ignore[arg-type]
    assert desk.apply("J-103", 123)["error"] == "invalid_input"              # type: ignore[arg-type]
    assert desk.apply("J-103", LETTER, {"a": 1})["error"] == "invalid_input"  # type: ignore[dict-item]
