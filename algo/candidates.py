"""Turns bias state + zone data into concrete trade candidates per source
timeframe (M1/M3/M5), and arbitrates which single candidate should own the
live pending order slot.

This module is pure logic: no MT5 connection, no live order state beyond
what's passed in. The live execution loop is responsible for supplying
current price and the currently-live pending order's identity (if any),
recovered from its comment via parse_order_comment().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ob_bridge.reader import OBSnapshot, Zone
from algo.entries import (
    EntryMode, EntryPlan, m1_entry_price, m3_entry, m5_entry, select_sl,
)

COMMENT_PREFIX = "SMC"


@dataclass(frozen=True)
class TradeCandidate:
    source_tf: str            # "M1", "M3", "M5"
    direction: int             # 1 bullish, -1 bearish
    mode: EntryMode
    entry_price: Optional[float]   # None for MARKET (fill at send time)
    sl: float
    event_time: int            # the OB's detection time, or origin time if never live-detected
    zone_key: str               # compact identity: f"{source_tf}|{direction}|{event_time}"


def _direction_key(direction: int) -> str:
    return "bull" if direction == 1 else "bear"


def _event_time(zone: Zone) -> int:
    return zone.detected_time if zone.detected_time > 0 else zone.start_time


def _zone_key(source_tf: str, direction: int, event_time: int) -> str:
    return f"{source_tf}|{direction}|{event_time}"


def order_comment(candidate: TradeCandidate) -> str:
    """Short, MT5-comment-safe identity written onto every order this bot
    sends, so a restart can recover which zone a live order belongs to."""
    return f"{COMMENT_PREFIX}|{candidate.zone_key}"


def parse_order_comment(comment: str) -> Optional[tuple]:
    """Returns (zone_key, event_time) or None if this isn't our comment format."""
    if not comment or not comment.startswith(COMMENT_PREFIX + "|"):
        return None
    rest = comment[len(COMMENT_PREFIX) + 1:]
    parts = rest.split("|")
    if len(parts) != 3:
        return None
    try:
        event_time = int(parts[2])
    except ValueError:
        return None
    return rest, event_time


def current_zone_key(source_tf: str, snap: Optional[OBSnapshot], direction: int) -> Optional[str]:
    """Zone key for whichever zone is CURRENTLY the latest for this source
    timeframe + direction, regardless of virgin status -- used to detect
    when a manual block has been superseded by a genuinely new OB."""
    if snap is None:
        return None
    history = snap.bull if direction == 1 else snap.bear
    if not history:
        return None
    return _zone_key(source_tf, direction, _event_time(history[0]))


def _htf_edges(direction: int, m15: Optional[OBSnapshot], m5: Optional[OBSnapshot],
                m3: Optional[OBSnapshot]) -> dict:
    """Current same-direction OB edge (low for bullish SL, high for bearish SL)
    per bias timeframe, for SL selection. Uses each timeframe's single latest
    zone in that direction (not the history list) -- SL follows the *current*
    structure, not an older one."""
    def edge(snap: Optional[OBSnapshot]) -> Optional[float]:
        if snap is None:
            return None
        history = snap.bull if direction == 1 else snap.bear
        if not history:
            return None
        return history[0].low if direction == 1 else history[0].high

    return {"M15": edge(m15), "M5": edge(m5), "M3": edge(m3)}


def build_m1_candidate(direction: int, m1: Optional[OBSnapshot],
                       m15: Optional[OBSnapshot], m5: Optional[OBSnapshot],
                       m3: Optional[OBSnapshot]) -> Optional[TradeCandidate]:
    if m1 is None:
        return None

    zone = m1.latest_untested(_direction_key(direction))
    if zone is None:
        return None

    ob_edge = zone.high if direction == 1 else zone.low
    entry = m1_entry_price(direction, ob_edge)

    sl = select_sl(direction, entry, _htf_edges(direction, m15, m5, m3))
    if sl is None:
        return None

    event_time = _event_time(zone)
    return TradeCandidate("M1", direction, EntryMode.PENDING, entry, sl,
                          event_time, _zone_key("M1", direction, event_time))


def _build_m3_or_m5_candidate(source_tf: str, entry_fn, direction: int,
                              snap: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                              m5: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                              current_price: float) -> Optional[TradeCandidate]:
    if snap is None:
        return None

    zone = snap.latest_untested(_direction_key(direction))
    # A zone with no live detection (still "baseline") has no detected_price
    # to measure distance from -- can't classify market/pending/none yet.
    if zone is None or zone.detected_time <= 0:
        return None

    ob_edge = zone.high if direction == 1 else zone.low
    plan: EntryPlan = entry_fn(direction, ob_edge, zone.detected_price)
    if plan.mode == EntryMode.NONE:
        return None

    reference_price = plan.entry_price if plan.entry_price is not None else current_price
    sl = select_sl(direction, reference_price, _htf_edges(direction, m15, m5, m3))
    if sl is None:
        return None

    event_time = _event_time(zone)
    return TradeCandidate(source_tf, direction, plan.mode, plan.entry_price, sl,
                          event_time, _zone_key(source_tf, direction, event_time))


def build_m3_candidate(direction: int, m3: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                       m5: Optional[OBSnapshot], current_price: float) -> Optional[TradeCandidate]:
    return _build_m3_or_m5_candidate("M3", m3_entry, direction, m3, m15, m5, m3, current_price)


def build_m5_candidate(direction: int, m5: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                       m3: Optional[OBSnapshot], current_price: float) -> Optional[TradeCandidate]:
    return _build_m3_or_m5_candidate("M5", m5_entry, direction, m5, m15, m5, m3, current_price)


def choose_winning_candidate(candidates: list) -> Optional[TradeCandidate]:
    """Among currently-valid candidates (already filtered to allowed sources
    and to un-traded zones by the caller), the newest by event_time wins."""
    valid = [c for c in candidates if c is not None]
    if not valid:
        return None
    return max(valid, key=lambda c: c.event_time)


def should_replace_pending(winning: Optional[TradeCandidate],
                           pending_zone_key: Optional[str],
                           pending_event_time: Optional[int]) -> bool:
    """True only if a genuinely newer setup should cancel-and-replace the
    live pending order. The caller must compute `winning` BEFORE cancelling
    anything, and only actually cancel once this returns True."""
    if winning is None:
        return False
    if pending_zone_key is None:
        return True  # nothing pending yet -- just place it
    if winning.zone_key == pending_zone_key:
        return False  # same setup already owns the pending order
    if pending_event_time is None:
        return False  # unknown ownership -- never blind-cancel
    return winning.event_time > pending_event_time
