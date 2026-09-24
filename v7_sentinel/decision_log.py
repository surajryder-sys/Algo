"""Persistent, append-only decision log. Ported verbatim from
v5_sentinel/decision_log.py (2026-09-18) -- takes `path` as an explicit
argument already, so a caller passing a per-symbol path (via
config.state_file_for()) gets per-symbol logs with no change needed
here.

Every decision a component computes and prints live (signal validity,
ICT Guard blocks, etc.) also gets written here, one record per decision,
so a later "why didn't this fire" question is answerable from this file
instead of a console print already scrolled off a window nothing
captures.

Format: JSON Lines (one compact JSON object per line, newline-delimited,
UTF-8) -- append-only, never rewritten, so a crash mid-write can corrupt
at most the partial last line, never anything already flushed. Every
record carries "ts" (wall-clock unix time) and "kind" (the decision
type) plus whatever fields that kind needs -- deliberately free-form
beyond that rather than one rigid schema.

Not wired into a rotation/trim scheme -- same "grows forever, gitignored,
regenerated at runtime" convention every other state file in this
project already uses.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def log(path: str, kind: str, **fields: Any) -> None:
    """Appends one decision record. Never raises -- a logging failure
    must never take the trading loop down with it, same fail-soft
    contract this project's alert senders already use."""
    record = {"ts": int(time.time()), "kind": kind, **fields}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[V7S-DECISIONLOG] write failed: {exc!r} -- record was: {record}")


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
