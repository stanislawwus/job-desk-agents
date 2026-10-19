"""Apply backend contract: take a prepared request, fill the Djinni form, act on the policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from jobs_helper.models import Job


@dataclass
class ApplyRequest:
    job: Job
    target: str                 # always 'djinni' in this mini-project
    profile: dict               # candidate.yaml
    cv_text: str                # the CV attached to the form: tailored to the vacancy or the candidate's own
    cover_letter: str
    policy: str                 # manual | confirm | auto
    dry_run: bool
    cv_path: str | None = None


@dataclass
class ApplyOutcome:
    status: str                 # prepared | awaiting_user | submitted | declined | needs_input
    filled: dict[str, str] = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    message: str = ""


class ApplyBackend(Protocol):
    def apply(self, req: ApplyRequest) -> ApplyOutcome: ...
