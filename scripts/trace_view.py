"""Turns a trace.jsonl into something readable.

  uv run python scripts/trace_view.py runs/<run>/trace.jsonl            # coloured timeline in the terminal
  uv run python scripts/trace_view.py runs/<run>/trace.jsonl --html out.html   # one self-contained HTML page
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobdesk.tracing import format_event, load_trace, short  # noqa: E402

COLORS = {"scout": "#0e7490", "reviewer": "#7e22ce", "single_agent": "#b45309", "orchestrator": "#1d4ed8", None: "#334155"}


def to_html(events: list[dict]) -> str:
    rows = []
    for e in events:
        k, out = e["kind"], e.get("output")
        if k == "tool_call":
            body = f"<b>{html.escape(e['tool'])}</b>({html.escape(short(e.get('input'), 400))})"
        elif k == "tool_result":
            cls = "err" if e.get("error") else "ok"
            body = f"<span class={cls}>{'✗ ' + html.escape(e['error']) if e.get('error') else '✓'} {html.escape(e['tool'])}</span> {html.escape(short(out, 400))}"
        elif k == "assistant_text":
            body = f"<i>{html.escape(short(e.get('text'), 500))}</i>"
        elif k == "handoff":
            body = f"⇢ {e.get('src')} → {e.get('dst')}: {html.escape(short(e.get('summary'), 300))}"
        else:
            rest = {x: y for x, y in e.items() if x not in ("ts", "t", "run_id", "kind", "agent", "structured", "usage")}
            body = f"<b>{k}</b> {html.escape(short(rest, 300))}"
        c = COLORS.get(e.get("agent"), "#334155")
        rows.append(f"<tr><td class=t>{e['t']:.1f}s</td><td><span class=tag style='background:{c}'>{e.get('agent') or 'run'}</span></td><td>{body}</td></tr>")
    return ("<!doctype html><meta charset=utf-8><title>trace</title><style>body{font:14px system-ui;margin:24px;color:#0f172a}"
            "table{border-collapse:collapse;width:100%}td{padding:4px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top}"
            ".t{color:#64748b;white-space:nowrap}.tag{color:#fff;padding:1px 8px;border-radius:9px;font-size:12px}.ok{color:#15803d}.err{color:#b91c1c}"
            "</style><h2>Agent trace</h2><table>" + "".join(rows) + "</table>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", type=Path)
    ap.add_argument("--html", type=Path)
    a = ap.parse_args()
    events = load_trace(a.trace)
    if a.html:
        a.html.write_text(to_html(events), encoding="utf-8")
        print(f"written {a.html}")
    else:
        for e in events:
            print(format_event(e))


if __name__ == "__main__":
    main()
