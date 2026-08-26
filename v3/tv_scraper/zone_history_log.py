"""Append-only, persistent record of every OB zone tv_scraper has ever
recorded as newly formed -- symbol/timeframe/direction/range/times.
Added 2026-08-19 after the user flagged real OB-driven trades (both a
Reversal Manager entry and a Trend Manager bias flip) they couldn't
find a matching block for on the actual TradingView chart, and there
was no way to check what tv_scraper itself had seen: ZoneStore only
ever holds CURRENTLY LIVE zones (apply_mitigated() deletes on
confirmed mitigation, by design -- see that module's own docstring),
so a zone involved in a trade an hour ago is often already gone from
the live state file with nothing left to inspect.

This is a separate, additive record -- never read back by any bot,
never affects trading logic. Purely so a "where did that OB come from"
question can be answered by looking up the exact symbol/timeframe/
direction/start_time from a real trade's own comment/log line, instead
of asking the user to have been watching the right chart at the right
second.

One JSON line per NEWLY-formed zone (not every poll's re-confirmation
of an already-known one -- see should_log() below). Deliberately plain
JSONL, not a JSON array, so a killed/crashed process never corrupts
previously-written entries and appending needs no read-modify-write.
"""
from __future__ import annotations

import json
from pathlib import Path


def append(path: str, *, symbol: str, timeframe: str, direction: str, start_time: int,
           top: float, btm: float, detected_time: int, formed_time_confirmed: bool) -> None:
    record = {
        "event": "formed",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "start_time": start_time,
        "top": top,
        "btm": btm,
        "detected_time": detected_time,
        "formed_time_confirmed": formed_time_confirmed,
    }
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def append_removed(path: str, *, symbol: str, timeframe: str, direction: str, start_time: int,
                    top: float, btm: float, virgin: bool, removed_time: int, reason: str) -> None:
    """Companion to append() above -- one JSON line per zone _apply_direction
    actually deletes from ZoneStore (apply_mitigated), added 2026-08-26 after
    a real "why did this trade close" investigation (USOIL) hit a wall:
    ZoneStore only ever holds currently-live zones, so there was no way to
    tell whether a zone genuinely mitigated by price, or only ever LOOKED
    mitigated because it dropped out of tv_scraper's own top-N read for the
    _MITIGATION_DEBOUNCE_POLLS-poll debounce window and then reappeared
    under the same start_time -- exactly the false-positive class
    close_if_invalidated's own docstring already documents, but with no way
    to confirm it after the fact. reason distinguishes the two call sites
    that can trigger a real deletion: "debounced" (missing for
    _MITIGATION_DEBOUNCE_POLLS consecutive polls -- the normal path) vs
    "orphan" (a zone found with no matching previously_seen entry at all,
    deleted on sight -- see _apply_direction's own reconciliation-loop
    docstring). Same additive, never-read-by-any-bot contract as append()
    -- purely forensic."""
    record = {
        "event": "removed",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "start_time": start_time,
        "top": top,
        "btm": btm,
        "virgin": virgin,
        "removed_time": removed_time,
        "reason": reason,
    }
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
