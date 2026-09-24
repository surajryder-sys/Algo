"""Scalper -- entry engine. Built 2026-09-22 (user's own rule): "takes an
entry with 0.05 lot size when a hammer or star forms from support or
resistance zones / places sl under or above candle, for buy sell
accordingly, with 2.0 buffer... calculates sl and places broker side tp
for 1:1."

SIGNAL: the EXACT SAME hammer/star + HTF-line-or-virgin-OB-zone touch
signal EA-CandleExit uses to CLOSE trades (exit_manager_candle.py), reused
here via candle_touch.py to OPEN one instead -- see that module's own
docstring for the full pattern-source/pairing/scope/touch rule. A hammer
touching a matching support (line or virgin zone) -> BUY. A star touching a
matching resistance -> SELL.

SL: "places sl under or above candle... with 2.0 buffer" -- the PATTERN
CANDLE's own low (BUY) or high (SELL), plus/minus sl_buffer. Not the
touched line/zone's own value -- deliberately the candle's own extreme,
same "buffer beyond a real price point" idiom every other SL basis in this
project uses, just a different reference point (the candle itself, not a
line or swing).

TP: "calculates sl and places broker side tp for 1:1" -- a fixed
risk_reward multiple (1.0) of the SL distance from entry, in the profit
direction. THE ONLY V7S MANAGER that ever places a broker-side TP (every
other manager's own send_market_order() call omits tp entirely) -- see
broker.py's own send_market_order/is_paused_by_manual_tp docstrings for how
Exit Manager is kept able to still close a Scalper trade despite this.

ELIGIBILITY: one trade per (pattern_tf, pattern_name, bar_time) -- a fresh
hammer/star stays "true" on the bridge for its own whole bar (same
momentary-but-bar-scoped contract every other bridge-sourced trigger in
this project uses), so without this, the SAME single pattern occurrence
would otherwise re-fire an entry on every poll cycle for that bar's whole
length. ScalperEligibilityStore below remembers the newest bar_time traded
per (pattern_tf, pattern_name) pair, persisted so a restart can't re-fire
the same event.

find_signal() only detects and reports; it never touches the broker or
marks anything traded -- the caller (scalper_main.py) does that once it
actually acts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v7_sentinel import candle_touch, htf_levels, rates
from v7_sentinel.bridge import read_hammer_star
from v7_sentinel.nlb_nsb_block import BlockStore

_PATTERNS = (
    ("hammer", candle_touch.HAMMER_ROLE, candle_touch.HAMMER_DIRECTION),
    ("star", candle_touch.STAR_ROLE, candle_touch.STAR_DIRECTION),
)


@dataclass(frozen=True)
class ScalperSignal:
    direction: int              # 1 buy, -1 sell
    pattern_tf: int               # the EXECUTION timeframe -- the pattern candle's own (M3 or M5)
    pattern_name: str               # "hammer" | "star"
    bar_time: int                     # the pattern candle's own bar_time -- the eligibility key
    sub_tag: str                        # "STR" (HTF line) | "ICT" (OB zone)
    base_timeframe_name: str              # the BASE timeframe -- the touched line/zone's own (e.g. "H2", "M15")
    trigger_label: str
    sl: float
    tp: float


class ScalperEligibilityStore:
    """Persists, per (pattern_tf, pattern_name), the newest bar_time a
    trade has already been taken for -- see module docstring."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._last: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._last = {str(k): int(v) for k, v in json.loads(self._path.read_text()).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._last = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._last))

    @staticmethod
    def _key(pattern_tf: int, pattern_name: str) -> str:
        return f"{pattern_tf}:{pattern_name}"

    def is_traded(self, pattern_tf: int, pattern_name: str, bar_time: int) -> bool:
        last = self._last.get(self._key(pattern_tf, pattern_name))
        return last is not None and bar_time <= last

    def mark_traded(self, pattern_tf: int, pattern_name: str, bar_time: int) -> None:
        key = self._key(pattern_tf, pattern_name)
        if self._last.get(key) is None or bar_time > self._last[key]:
            self._last[key] = bar_time
            self._save()


def _resolve_sl_tp(direction: int, entry_price: float, candle_low: float, candle_high: float,
                   sl_buffer: float, risk_reward: float) -> tuple[float, float]:
    if direction == 1:
        sl = candle_low - sl_buffer
        risk = entry_price - sl
        tp = entry_price + risk * risk_reward
    else:
        sl = candle_high + sl_buffer
        risk = sl - entry_price
        tp = entry_price - risk * risk_reward
    return sl, tp


def find_signal(symbol: str, tracker, block: BlockStore, eligibility: ScalperEligibilityStore,
                sl_buffer: float, risk_reward: float, bid: float, ask: float) -> Optional[ScalperSignal]:
    """The first fresh, untraded hammer/star (M3 then M5) touching a
    matching-role HTF line or virgin OB zone -- None if none qualify.
    HTF lines are tried before zones for a given pattern (arbitrary but
    deterministic tie-break for the rare case both touch on the exact
    same cycle -- matches _CANDLE_TIMEFRAMES scan order elsewhere)."""
    htf_states = candle_touch.compute_htf_states(symbol, tracker)
    zones = block.zones()

    for tf in candle_touch.CANDLE_TIMEFRAMES:
        hs = read_hammer_star(symbol, tf)
        if hs is None:
            continue
        for pattern_name, role, direction in _PATTERNS:
            pattern_present = candle_touch.is_hammer(hs) if pattern_name == "hammer" else candle_touch.is_star(hs)
            if not pattern_present:
                continue
            if eligibility.is_traded(tf, pattern_name, hs.bar_time):
                continue

            same_extremes = rates.read_bar_high_low(symbol, tf, hs.bar_time)
            if same_extremes is None:
                continue
            high, low = same_extremes
            touch_price = low if role == "SUPPORT" else high

            sub_tag: Optional[str] = None
            base_timeframe_name = ""
            trigger_label = ""
            touching_line = candle_touch.find_touching_line(htf_states, role, touch_price, hs.close)
            if touching_line is not None:
                level_tf, level_source = touching_line
                sub_tag, trigger_label = "STR", f"M{level_tf}/{level_source}"
                base_timeframe_name = htf_levels.TIMEFRAME_NAMES.get(level_tf, f"M{level_tf}")
            else:
                wanted_role = candle_touch.ZONE_ROLE_FOR_PATTERN[pattern_name]
                prev_bar_time = hs.bar_time - tf * 60
                prev_extremes = rates.read_bar_high_low(symbol, tf, prev_bar_time)
                touching_zone = candle_touch.find_touching_zone(zones, wanted_role, same_extremes, prev_extremes,
                                                                 hs.bar_time, prev_bar_time)
                if touching_zone is not None:
                    sub_tag = "ICT"
                    trigger_label = f"{touching_zone.timeframe_name} OB zone {touching_zone.zone_id}"
                    base_timeframe_name = touching_zone.timeframe_name

            if sub_tag is None:
                continue   # neither a line nor a zone touched -- not a signal

            entry_price = ask if direction == 1 else bid
            sl, tp = _resolve_sl_tp(direction, entry_price, low, high, sl_buffer, risk_reward)
            return ScalperSignal(direction=direction, pattern_tf=tf, pattern_name=pattern_name,
                                 bar_time=hs.bar_time, sub_tag=sub_tag, base_timeframe_name=base_timeframe_name,
                                 trigger_label=trigger_label, sl=sl, tp=tp)
    return None
