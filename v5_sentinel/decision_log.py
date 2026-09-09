"""Persistent, append-only decision log. Confirmed with the user
2026-09-09, after repeated "why didn't this flip fire a trade" questions
couldn't be answered with certainty from anything on disk -- every
decision Trend Manager's own run_once() already computes and prints
live (M3 event validity + parent bias state, watch-zone actions,
owner-flip reversals, ICT Guard blocks) now ALSO gets written here, one
record per decision, so a later "why" question is answerable from this
file instead of a console print already scrolled off a window nothing
captures.

Format: JSON Lines (one compact JSON object per line, newline-delimited,
UTF-8) -- append-only, never rewritten, so a crash mid-write can corrupt
at most the partial last line, never anything already flushed. Every
record carries "ts" (wall-clock unix time) and "kind" (the decision
type) plus whatever fields that kind needs -- deliberately free-form
beyond that rather than one rigid schema, since new decision kinds get
added over time (this file is meant to grow with whatever main.py's own
run_once() later needs explained, not just today's set).

Not wired into a rotation/trim scheme -- same "grows forever, gitignored,
regenerated at runtime" convention every other state file in this project
already uses; if it ever gets unwieldy, trimming is a separate, later
concern, not something this module does on its own.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator


def log(path: str, kind: str, **fields: Any) -> None:
    """Appends one decision record. Never raises -- a logging failure
    must never take the trading loop down with it, same fail-soft
    contract this project's alert senders (_send_alert/_send_critical_alert)
    already use."""
    record = {"ts": int(time.time()), "kind": kind, **fields}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[V5S-DECISIONLOG] write failed: {exc!r} -- record was: {record}")


def read_all(path: str) -> list[dict]:
    """Every record in the log, oldest first. A malformed trailing line
    (e.g. a crash mid-write) is skipped, not fatal to the rest."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_since(path: str, since_ts: int) -> list[dict]:
    """Every record with ts >= since_ts, oldest first -- the common case
    for "what happened today" style questions."""
    return [r for r in read_all(path) if r.get("ts", 0) >= since_ts]
