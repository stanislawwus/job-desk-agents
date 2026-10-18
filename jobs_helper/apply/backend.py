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
    cv_text: str                # the candidate's own CV (attached as is, never rewritten by an agent)
    cover_letter: str
    policy: str                 # manual | confirm | auto
    dry_run: bool
    cv_path: str | None = None


@dataclass
class ApplyOutcome:
    status: str                 # prepared | awaiting_user | submitted | declined | needs_input | failed
    filled: dict[str, str] = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    screenshot: str | None = None
    message: str = ""
    error: dict | None = None   # structured error for the agent (code/message/hint/retryable)
    extra: dict = field(default_factory=dict)   # e.g. url / page_excerpt / confirmed after submit, value adjustments
    documents: dict[str, str] = field(default_factory=dict)   # files uploaded to the form: {'cv': path, 'cover_letter': path}


class ApplyBackend(Protocol):
    def apply(self, req: ApplyRequest) -> ApplyOutcome: ...
