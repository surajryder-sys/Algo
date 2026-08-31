"""XAUUSD's new zone-blocking replacement technique, per the user's
explicit design 2026-08-31/09-01, built to replace the OB-zone edge-gap
filter removed the same day (see m1_execution.py's own top docstring).

Watches M3 and M5's own dual-ATR trail lines (4 independent lines total:
M3 line1/line2, M5 line1/line2 -- scoped to just these two timeframes
"as of now", read chart-free via v4.bridge.native_trail) for a specific
touch-then-reject pattern, and turns a confirmed rejection into a
temporary no-entry "trap zone" around that exact price level.

Per-line state machine (independent for each of the 4 lines):
  1. WATCH activates when live price actually TOUCHES the line -- not
     mere proximity -- within TOUCH_BUFFER_POINTS (0.1 for XAUUSD, to
     absorb spread/bid-ask noise so an exact-equality touch isn't
     missed). A line above price is a potential resistance (relevant to
     a long); a line below price is a potential support (relevant to a
     short).
  2. Once watching, wait for M1's own reaction: the moment EITHER of
     M1's two dual-ATR lines flips against the watched side (bearish for
     a resistance watch, bullish for a support watch) -- deliberately
     "any one line", not the full combined STRONG/WEAK structure, since
     waiting for both M1 lines to agree was confirmed too slow for this
     purpose. This is still a genuine bar-close-confirmed flip (read
     straight from the live M1 bridge everything else in this bot
     already trusts), not intrabar noise.
  3. CONFIRMED REJECTION: any currently open position in the
     corresponding direction should be closed (buy position closes on a
     resistance rejection, sell position on a support rejection) -- the
     caller does the actual closing; this module only signals it. The
     line's level (at the moment of touch) becomes a TRAP ZONE:
       - resistance: [level - TRAP_BUFFER_POINTS, level]
       - support:    [level, level + TRAP_BUFFER_POINTS]
     No new entry (buy into a resistance trap, sell into a support trap)
     is allowed while its own flip price falls inside that range.
  4. RESET: a trap clears only when that SAME line gets a genuine NEW
     flip of its own (its own trend value changes again) -- not just any
     market movement, and not another line's flip. Once reset, that
     line's watch/trap state returns to idle and can arm again from
     scratch.
  5. If price instead breaks cleanly through the line without ever
     triggering an M1 reaction, the watch simply stands down (no trap
     created) -- tracked here as "price has moved decisively past the
     line in the favorable direction" (by TRAP_BUFFER_POINTS, reusing
     the same buffer as the trap width for symmetry).

Deliberately mirrors this repo's other per-something state files (JSON,
load/save, keyed dict) rather than anything more elaborate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from v4.bridge.native_trail import read_native_trail_dual
from v4.bridge.reader import ATRDualSnapshot

Side = Literal["resistance", "support"]
Direction = Literal["buy", "sell"]

TOUCH_BUFFER_POINTS = 0.1
TRAP_BUFFER_POINTS = 2.0

# Scoped to M3 + M5 only, both lines each -- explicit user instruction,
# "lets deploy it in m5 and m3 as of now".
_WATCHED_TIMEFRAMES = (3, 5)
_LINE_NUMS = (1, 2)


def _line_key(tf_minutes: int, line_num: int) -> str:
    return f"M{tf_minutes}_line{line_num}"


@dataclass
class RejectionEvent:
    """One confirmed touch-then-M1-reaction rejection this poll."""
    line_key: str
    side: Side
    level: float
    close_direction: Direction  # which open position direction this should close


@dataclass
class TrapWatchResult:
    rejections: list[RejectionEvent]  # usually empty; non-empty the exact poll a rejection confirms
    trap_zones: dict[str, tuple[float, float, Side]]  # line_key -> (low, high, side), currently active only


class TrapWatchState:
    """Persists per-line watch/trap state. Schema per line_key:
    {"watch_active": bool, "watch_side": "resistance"|"support"|None,
     "watch_level": float|None, "trap_active": bool, "trap_low": float|None,
     "trap_high": float|None, "trap_side": ..., "trap_birth_event_time": int|None}"""

    def __init__(self, path: str):
        self._path = Path(path)
        self._lines: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._lines = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._lines = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._lines, indent=2))

    def _entry(self, line_key: str) -> dict:
        entry = self._lines.setdefault(line_key, {})
        entry.setdefault("watch_active", False)
        entry.setdefault("watch_side", None)
        entry.setdefault("watch_level", None)
        entry.setdefault("trap_active", False)
        entry.setdefault("trap_low", None)
        entry.setdefault("trap_high", None)
        entry.setdefault("trap_side", None)
        entry.setdefault("trap_birth_event_time", None)
        return entry


def _opposite_trend_for(side: Side) -> int:
    """The M1 trend value that counts as "reacting against" this side --
    a resistance watch (worried about a long) reacts to M1 turning
    bearish (-1); a support watch (worried about a short) reacts to M1
    turning bullish (+1)."""
    return -1 if side == "resistance" else 1


def evaluate_trap_watch(
    state: TrapWatchState,
    symbol: str,
    current_price: float,
    mt5_atr_m1: ATRDualSnapshot,
) -> TrapWatchResult:
    """Call once per poll. mt5_atr_m1 is the SAME M1 snapshot the caller
    already fetched for its own entry decision -- not re-read here, to
    keep both decisions working off identical data within one poll."""
    rejections: list[RejectionEvent] = []
    trap_zones: dict[str, tuple[float, float, Side]] = {}

    m1_reacted_bearish = mt5_atr_m1.line1.trend == -1 or mt5_atr_m1.line2.trend == -1
    m1_reacted_bullish = mt5_atr_m1.line1.trend == 1 or mt5_atr_m1.line2.trend == 1

    for tf in _WATCHED_TIMEFRAMES:
        snap = read_native_trail_dual(symbol, tf)
        if snap is None:
            continue  # fails open -- same transient-tolerance contract as every other bridge read in this repo

        for line_num, line in ((1, snap.line1), (2, snap.line2)):
            key = _line_key(tf, line_num)
            entry = state._entry(key)
            level = line.trail_stop

            # --- Trap active: check for reset first ---
            if entry["trap_active"]:
                if line.event_time != entry["trap_birth_event_time"]:
                    # This SAME line has genuinely flipped again since the
                    # trap was set -- reset, per explicit instruction
                    # ("trap zone will be reset only if there's a flip in
                    # that particular timeframe").
                    entry["trap_active"] = False
                    entry["trap_low"] = None
                    entry["trap_high"] = None
                    entry["trap_side"] = None
                    entry["trap_birth_event_time"] = None
                    entry["watch_active"] = False
                    entry["watch_side"] = None
                    entry["watch_level"] = None
                else:
                    trap_zones[key] = (entry["trap_low"], entry["trap_high"], entry["trap_side"])
                    continue  # still trapped -- no watch logic needed this poll for this line

            # --- Not (or no longer) trapped: run watch/touch/reaction logic ---
            # >= (not >) so an EXACT equality (level == current_price --
            # the precise instant of a real touch) still classifies as a
            # definite side instead of falling through as ambiguous. Real
            # bid/ask prices essentially never land on an exact float
            # equality anyway, so this only matters at the single instant
            # that actually needs a real answer.
            side: Side = "resistance" if level >= current_price else "support"

            if not entry["watch_active"]:
                touched = (
                    (side == "resistance" and current_price >= level - TOUCH_BUFFER_POINTS)
                    or (side == "support" and current_price <= level + TOUCH_BUFFER_POINTS)
                )
                if touched:
                    entry["watch_active"] = True
                    entry["watch_side"] = side
                    entry["watch_level"] = level
                continue

            # Watch is active -- check for a clean break-through first
            # (situation resolved favorably, stand down with no trap).
            watch_side = entry["watch_side"]
            watch_level = entry["watch_level"]
            broke_through = (
                (watch_side == "resistance" and current_price >= watch_level + TRAP_BUFFER_POINTS)
                or (watch_side == "support" and current_price <= watch_level - TRAP_BUFFER_POINTS)
            )
            if broke_through:
                entry["watch_active"] = False
                entry["watch_side"] = None
                entry["watch_level"] = None
                continue

            # Check for the M1 reaction confirming rejection.
            reacted = (watch_side == "resistance" and m1_reacted_bearish) or \
                      (watch_side == "support" and m1_reacted_bullish)
            if reacted:
                if watch_side == "resistance":
                    trap_low, trap_high = watch_level - TRAP_BUFFER_POINTS, watch_level
                    close_direction: Direction = "buy"
                else:
                    trap_low, trap_high = watch_level, watch_level + TRAP_BUFFER_POINTS
                    close_direction = "sell"

                entry["trap_active"] = True
                entry["trap_low"] = trap_low
                entry["trap_high"] = trap_high
                entry["trap_side"] = watch_side
                entry["trap_birth_event_time"] = line.event_time
                entry["watch_active"] = False
                entry["watch_side"] = None
                entry["watch_level"] = None

                rejections.append(RejectionEvent(
                    line_key=key, side=watch_side, level=watch_level, close_direction=close_direction,
                ))
                trap_zones[key] = (trap_low, trap_high, watch_side)

    state._save()
    return TrapWatchResult(rejections=rejections, trap_zones=trap_zones)


def is_direction_blocked(result: TrapWatchResult, direction: Direction, price: float) -> Optional[str]:
    """None if `direction` at `price` is clear of every active trap zone,
    else a human-readable reason naming which zone blocked it. A buy is
    blocked by any active resistance trap containing `price`; a sell by
    any active support trap."""
    wanted_side: Side = "resistance" if direction == "buy" else "support"
    for line_key, (low, high, side) in result.trap_zones.items():
        if side == wanted_side and low <= price <= high:
            return f"{direction} blocked: {price:.3f} is inside {line_key}'s {side} trap zone [{low:.3f}, {high:.3f}]"
    return None
