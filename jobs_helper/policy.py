"""Submit policy for Djinni chosen by the human (settings.yaml) and the candidate profile (candidate.yaml).

The agent never chooses how an application is sent: the MCP server reads these files itself.
Only Djinni.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml

from jobs_helper import config

Policy = Literal["manual", "confirm", "auto"]
POLICIES: tuple[Policy, ...] = ("manual", "confirm", "auto")
SITES = ("djinni",)
DEFAULTS: dict[str, Policy] = {"djinni": "confirm"}

def settings_path() -> Path:
    return Path(os.environ.get("JOBS_SETTINGS", config.HOME / "settings.yaml"))


def candidate_path() -> Path:
    return Path(os.environ.get("JOBS_CANDIDATE", config.DEFAULT_CANDIDATE / "candidate.yaml"))


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def load_policies() -> dict[str, Policy]:
    """settings.yaml (chosen by the human) > JOBS_POLICY_DJINNI > default 'confirm'."""
    raw = _load_yaml(settings_path()).get("submit_policy", {}) or {}
    out = {}
    for site in SITES:
        value = raw.get(site) if raw.get(site) in POLICIES else os.environ.get(f"JOBS_POLICY_{site.upper()}", "").strip().lower()
        out[site] = value if value in POLICIES else DEFAULTS[site]
    return out


def dry_run() -> bool:
    return os.environ.get("JOBS_DRY_RUN", "").lower() in ("1", "true", "yes")


def load_candidate() -> dict | None:
    data = _load_yaml(candidate_path())
    links = data.get("links") or {}
    for k in ("linkedin", "github", "portfolio"):
        v = str(links.get(k) or "").strip()
        if v and not v.startswith(("http://", "https://")) and "." in v:
            links[k] = "https://" + v.lstrip("/")
    return data or None


CV_NAMES = ("cv.md", "cv.txt")


def cv_file(profile: dict | None = None) -> Path | None:
    """The candidate's own CV that is attached to the Djinni form: cv_file from candidate.yaml, else cv.md next to it."""
    profile = profile if profile is not None else (load_candidate() or {})
    base = candidate_path().parent
    if profile.get("cv_file"):
        p = Path(str(profile["cv_file"])).expanduser()
        p = p if p.is_absolute() else base / p
        return p if p.exists() else None
    return next((base / n for n in CV_NAMES if (base / n).exists()), None)


def current_cv_text(profile: dict | None = None) -> str:
    path = cv_file(profile)
    return path.read_text(errors="replace", encoding="utf-8").strip() if path else ""
