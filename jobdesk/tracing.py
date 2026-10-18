"""Tracing of an agent run. Every event goes to <run_dir>/trace.jsonl and, optionally, to a coloured console log.

Span structure:  run -> agent (one per round) -> tool_call / tool_result,  plus `handoff` between agents.
Event kinds: run_start, agent_start, agent_init, tool_call, tool_result, assistant_text, agent_end, handoff, error, run_end.
Every record has: ts, t (seconds since run start), run_id, kind, agent, plus kind-specific fields.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLORS = {"scout": "\033[36m", "reviewer": "\033[35m", "single_agent": "\033[33m", "orchestrator": "\033[34m"}
RESET, DIM, BOLD, RED, GREEN, YELLOW = "\033[0m", "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m"


def short(value: Any, n: int = 150) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Tracer:
    def __init__(self, run_dir: Path, run_id: str, verbose: bool = True):
        self.run_id, self.verbose = run_id, verbose
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "trace.jsonl"
        self.t0 = time.perf_counter()

    def event(self, kind: str, agent: str | None = None, **data: Any) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "t": round(time.perf_counter() - self.t0, 2),
               "run_id": self.run_id, "kind": kind, "agent": agent, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if self.verbose:
            print(format_event(rec), file=sys.stderr, flush=True)


def format_event(r: dict) -> str:
    c = COLORS.get(r.get("agent") or "", BOLD)
    tag = f"{c}{BOLD}[{r['agent'] or 'run'}]{RESET}"
    k, out = r["kind"], r.get("output")
    if k == "run_start":
        line = f"{BOLD}▶ {r.get('mode')} run{RESET}  “{short(r.get('request'), 110)}”"
    elif k == "agent_start":
        line = f"{c}▶ start{RESET} round {r.get('round', 1)}  tools={r.get('tools')}"
    elif k == "tool_call":
        line = f"→ {BOLD}{r['tool']}{RESET}({short(r.get('input'), 130)})"
    elif k == "tool_result" and r.get("error"):
        line = f"{RED}✗ {r['tool']} → {r['error']}{RESET}  {short(out.get('message') if isinstance(out, dict) else out, 140)}"
    elif k == "tool_result" and isinstance(out, dict) and out.get("status"):
        line = f"{GREEN}✓ {r['tool']} → {out['status']}{RESET} {out.get('application_id', '')} {out.get('job_id', '')}"
    elif k == "tool_result":
        line = f"{GREEN}✓ {r['tool']}{RESET} {DIM}{short(out, 120)}{RESET}"
    elif k == "assistant_text":
        line = f"{DIM}💬 {short(r.get('text'), 170)}{RESET}"
    elif k == "agent_end":
        err = f" {RED}{r.get('subtype')}{RESET}" if r.get("is_error") else ""
        line = f"{c}■ end{RESET} turns={r.get('num_turns')} tool_calls={r.get('tool_calls')} {(r.get('duration_ms') or 0) / 1000:.1f}s{err}"
    elif k == "handoff":
        line = f"{YELLOW}⇢ {r.get('src')} → {r.get('dst')}{RESET}  {short(r.get('summary'), 140)}"
    elif k == "run_end":
        line = f"{BOLD}■ run finished{RESET} in {r['t']:.0f}s"
    else:
        line = f"{k} {short({x: y for x, y in r.items() if x not in ('ts', 't', 'run_id', 'kind', 'agent')})}"
    return f"{DIM}{r['t']:6.1f}s{RESET} {tag} {line}"


def load_trace(path: Path) -> list[dict]:
    # split on "\n" only: str.splitlines() also breaks on U+2028, which can appear inside vacancy text
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").split("\n") if x.strip()]
