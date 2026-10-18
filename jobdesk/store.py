"""Domain logic of the job desk. No MCP and no LLM in here, so every rule can be unit-tested directly.

Three operations:
  search_jobs  read-only, searches a (possibly stale) listing index; it does NOT say whether a vacancy is still open
  get_job      read-only, the live record of one vacancy, including `status`
  apply        STATE-CHANGING: appends one row to applications.csv

Every failure is returned as a structured error the agent can act on, never raised:
  {"error": <code>, "message": <what happened>, "hint": <what to do next>, "retryable": <bool>, ...extra}
"""

from __future__ import annotations

import csv
import difflib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

WRITER_ROLES = frozenset({"reviewer", "single_agent"})  # the only roles allowed to call apply
LETTER_MIN, LETTER_MAX = 200, 1800                      # cover letter length in characters
ANSWER_MIN = 10
CSV_FIELDS = ["application_id", "job_id", "title", "company", "applied_at", "role", "cover_letter", "answers"]


def err(code: str, message: str, hint: str = "", retryable: bool = False, **extra: Any) -> dict[str, Any]:
    return {"error": code, "message": message, "hint": hint, "retryable": retryable, **extra}


def normalize_job_id(raw: Any) -> str:
    """'j101', 'J-101', ' 101 ' -> 'J-101'. Anything else is returned stripped and upper-cased."""
    s = str(raw).strip().upper()
    m = re.fullmatch(r"J?[-_ ]?(\d{1,4})", s)
    return f"J-{m.group(1)}" if m else s


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", text.lower().replace("/", " "))


class Desk:
    def __init__(self, data_dir: Path, workdir: Path, role: str = "viewer", today: str | None = None):
        self.role = role
        self.today = today or date.today().isoformat()
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.workdir / "applications.csv"
        jobs = json.loads((Path(data_dir) / "jobs.json").read_text(encoding="utf-8"))
        self.jobs: dict[str, dict] = {j["id"]: j for j in jobs}

    # ------------------------------------------------------------------ read-only

    def search_jobs(self, keywords: str, remote: bool | None = None, min_salary: int | None = None,
                    max_experience_years: float | None = None, limit: int = 8) -> dict[str, Any]:
        if not isinstance(keywords, str) or not keywords.strip():
            return err("invalid_input", "`keywords` must be a non-empty string.",
                       "Pass a role and key skills, e.g. 'python django'.", True)
        limit = max(1, min(int(limit), 20))
        words = _tokens(keywords)
        scored = []
        for j in self.jobs.values():
            title, stack, desc = set(_tokens(j["title"])), {t for s in j["stack"] for t in _tokens(s)}, set(_tokens(j["description"]))
            score = sum(3 * (w in title) + 2 * (w in stack) + (w in desc) for w in words)
            if score == 0:
                continue
            if remote is True and not j["remote"]:
                continue
            if min_salary is not None and j["salary_usd_max"] is not None and j["salary_usd_max"] < min_salary:
                continue
            if max_experience_years is not None and j["experience_years_min"] is not None \
                    and j["experience_years_min"] > max_experience_years:
                continue
            scored.append((-score, j["id"], j))
        scored.sort(key=lambda x: (x[0], x[1]))
        out = [self._summary(j) for _, _, j in scored[:limit]]
        res: dict[str, Any] = {
            "jobs": out, "total_matches": len(scored),
            "note": "Listing index can be stale: it does not show whether a vacancy is still open. "
                    "Call get_job to see the live status.",
        }
        if not out:
            res["hint"] = "No matches. Try fewer or broader keywords, or relax remote / salary / experience filters."
        return res

    def get_job(self, job_id: str) -> dict[str, Any]:
        jid = normalize_job_id(job_id)
        job = self.jobs.get(jid)
        if job is None:
            return self._unknown_job(jid)
        public = {k: v for k, v in job.items() if k != "questions"}  # form questions are only revealed by apply
        return public

    # ------------------------------------------------------------------ state-changing

    def apply(self, job_id: str, cover_letter: str, answers: dict[str, str] | None = None) -> dict[str, Any]:
        # 1. who is calling
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' is read-only and may not submit applications.",
                       "Hand your draft to the Reviewer; only the Reviewer (or a single agent) can call apply.", False)
        # 2. shape of the input
        if not isinstance(job_id, str) or not job_id.strip():
            return err("invalid_input", "`job_id` must be a non-empty string like 'J-101'.", "Take the id from search_jobs.", True)
        if not isinstance(cover_letter, str):
            return err("invalid_input", "`cover_letter` must be a string.", "Pass the letter text.", True)
        if answers is not None and (not isinstance(answers, dict) or not all(isinstance(v, str) for v in answers.values())):
            return err("invalid_input", "`answers` must be an object {question_id: text}.", "Use the ids from needs_input.", True)
        # 3. does the vacancy exist, is it open, did we already apply
        jid = normalize_job_id(job_id)
        job = self.jobs.get(jid)
        if job is None:
            return self._unknown_job(jid)
        if job["status"] != "open":
            return err("job_closed", f"{jid} ({job['title']}, {job['company']}) was closed on {job.get('closed_on', 'an earlier date')}.",
                       "Do not retry this vacancy. Pick another one from similar_open_jobs or your backups.", False,
                       job_id=jid, similar_open_jobs=self._similar_open(job))
        prev = next((r for r in self.applications() if r["job_id"] == jid), None)
        if prev:
            return err("already_applied", f"An application for {jid} already exists ({prev['application_id']}, {prev['applied_at']}).",
                       "Do not send a duplicate. Choose another vacancy.", False,
                       job_id=jid, application_id=prev["application_id"])
        # 4. the letter
        n = len(cover_letter.strip())
        if n < LETTER_MIN:
            return err("cover_letter_too_short", f"Cover letter has {n} characters, minimum is {LETTER_MIN}.",
                       "Expand it with real facts from the CV (no invented ones) and call apply again.", True, job_id=jid)
        if n > LETTER_MAX:
            return err("cover_letter_too_long", f"Cover letter has {n} characters, maximum is {LETTER_MAX}.",
                       "Shorten it and call apply again.", True, job_id=jid)
        # 5. the application form's own questions
        answers = {k: v.strip() for k, v in (answers or {}).items()}
        questions = job.get("questions", [])
        human = [q for q in questions if q["human_only"]]
        given_human = [q["id"] for q in human if answers.get(q["id"])]
        if given_human:
            return err("human_only_question", f"Question(s) {given_human} can only be answered by the candidate.",
                       "Remove these answers, do not guess; report the question to the candidate.", False, job_id=jid, question_ids=given_human)
        missing = [q for q in questions if q["id"] not in answers]
        if missing:
            return err("needs_input", f"The application form of {jid} has {len(missing)} required question(s).",
                       "Answer questions with human_only=false using ONLY the CV or the candidate profile, then call apply again "
                       "with answers={id: text}. Questions with human_only=true go to the candidate: do not guess.",
                       True, job_id=jid,
                       questions=[{"id": q["id"], "question": q["question"], "human_only": q["human_only"]} for q in missing])
        short = [q["id"] for q in questions if len(answers[q["id"]]) < ANSWER_MIN]
        if short:
            return err("answer_too_short", f"Answers {short} are shorter than {ANSWER_MIN} characters.", "Write a real answer.", True, job_id=jid)
        # 6. all checks passed: write
        row = {
            "application_id": f"APP-{len(self.applications()) + 1:04d}", "job_id": jid, "title": job["title"],
            "company": job["company"], "applied_at": self.today, "role": self.role, "cover_letter": cover_letter.strip(),
            "answers": json.dumps({q["id"]: answers[q["id"]] for q in questions}, ensure_ascii=False),
        }
        self._append(row)
        return {"status": "submitted", "application_id": row["application_id"], "job_id": jid,
                "title": job["title"], "company": job["company"], "applied_at": row["applied_at"]}

    # ------------------------------------------------------------------ helpers

    def applications(self) -> list[dict[str, str]]:
        if not self.csv_path.exists():
            return []
        with self.csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _append(self, row: dict[str, str]) -> None:
        new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

    @staticmethod
    def _summary(j: dict) -> dict[str, Any]:
        lo, hi = j["salary_usd_min"], j["salary_usd_max"]
        return {"job_id": j["id"], "title": j["title"], "company": j["company"], "remote": j["remote"], "city": j["city"],
                "salary_usd": f"{lo}-{hi}" if lo is not None and hi is not None else None,
                "experience_years_min": j["experience_years_min"], "stack": j["stack"], "posted": j["posted"]}

    def _unknown_job(self, jid: str) -> dict[str, Any]:
        close = difflib.get_close_matches(jid, list(self.jobs), n=3, cutoff=0.5)
        return err("unknown_job", f"No vacancy with id '{jid}'.", "Use an id returned by search_jobs." +
                   (f" Did you mean: {', '.join(close)}?" if close else ""), True, did_you_mean=close)

    def _similar_open(self, job: dict) -> list[dict[str, Any]]:
        mine = set(job["stack"])
        cands = [(len(mine & set(j["stack"])), j) for j in self.jobs.values() if j["status"] == "open" and j["id"] != job["id"]]
        cands = [c for c in sorted(cands, key=lambda x: (-x[0], x[1]["id"])) if c[0] > 0][:3]
        return [{"job_id": j["id"], "title": j["title"], "company": j["company"]} for _, j in cands]
