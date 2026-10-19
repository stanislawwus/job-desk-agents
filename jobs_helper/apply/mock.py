"""Mock apply backend for the Djinni form (schema from the stage-1 recon of jobs_helper_MCP), without touching djinni.co.
It is the only backend of this mini-project: `apply` fills the form, acts on the submit policy and the tracker row is the
state change. Results never depend on page markup and no real application is sent.

Djinni form (from the site recon in jobs_helper_MCP): message to the recruiter, email (required), name, and a LinkedIn profile or a CV.

Env:
  JOBS_MOCK_CONFIRM   answer of the simulated human for the 'confirm' policy: yes | no (default yes)
"""

from __future__ import annotations

import os

from jobs_helper.apply.backend import ApplyOutcome, ApplyRequest
from jobs_helper.apply.fields import FormField as F
from jobs_helper.apply.planner import plan_fill

DJINNI_FORM: list[F] = [F("message", "Повідомлення", "textarea", True), F("email", "Електронна пошта", "email", True),
                        F("name", "Ваше ім'я", "text", True), F("linkedin", "LinkedIn профіль", "text"),
                        F("cv", "Резюме", "file", True)]


class MockBackend:
    def apply(self, req: ApplyRequest) -> ApplyOutcome:
        plan = plan_fill(DJINNI_FORM, req.profile, {}, req.job.source, req.cv_text, req.cover_letter)
        if not plan.complete:
            return ApplyOutcome("needs_input", filled=plan.origin, questions=plan.questions,
                                message=f"{len(plan.questions)} required field(s) of the Djinni form are empty.")
        if req.dry_run:
            return ApplyOutcome("prepared", filled=plan.origin, message="Djinni form filled (dry run): nothing was sent.")
        if req.policy == "manual":
            return ApplyOutcome("awaiting_user", filled=plan.origin,
                                message="Djinni form filled and left open; the candidate presses 'Відгукнутися' themselves.")
        if req.policy == "confirm" and os.environ.get("JOBS_MOCK_CONFIRM", "yes").lower() not in ("yes", "y", "1", "true"):
            return ApplyOutcome("declined", filled=plan.origin, message="The candidate declined this submission.")
        return ApplyOutcome("submitted", filled=plan.origin, message="Application recorded (mock Djinni form).")
