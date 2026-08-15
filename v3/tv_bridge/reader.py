"""Reads events appended by tv_bridge.receiver to its JSON-lines log.

The receiver only appends; this module tracks no position of its own --
callers (e.g. tradingview_bot) keep their own cursor via
tradingview_bot.state_store so a restart resumes instead of reprocessing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TVEvent:
    type: str  # "atr_trail" | "ob_zone_formed" | "ob_zone_mitigated"
    symbol: str
    received_at: float
    data: dict  # full raw record (includes type/symbol/received_at too)


def _parse(raw: dict) -> TVEvent:
    return TVEvent(
        type=raw["type"],
        symbol=raw["symbol"],
        received_at=raw["received_at"],
        data=raw,
    )


def read_new(log_file: str, cursor: int) -> tuple[list[TVEvent], int]:
    """Returns every event appended after byte offset `cursor`, plus the new
    cursor position. Stops before a line still mid-write instead of skipping
    it, so it gets picked up whole on the next poll."""
    path = Path(log_file)
    if not path.exists():
        return [], cursor

    events: list[TVEvent] = []
    with path.open("r", encoding="utf-8") as f:
        f.seek(cursor)
        new_cursor = cursor
        while True:
            line = f.readline()  # (not `for line in f`: mixing iteration
            if not line.endswith("\n"):  # with tell() raises OSError)
                break
            stripped = line.strip()
            new_cursor = f.tell()
            if not stripped:
                continue
            try:
                events.append(_parse(json.loads(stripped)))
            except (json.JSONDecodeError, KeyError):
                continue
    return events, new_cursor
