"""Turns zone data into concrete M5/M15 trade candidates, and arbitrates
which single candidate should own the live pending order slot.

This module is pure logic: no MT5 connection, no live order state beyond
what's passed in. The live execution loop is responsible for supplying
current price and the currently-live pending order's identity (if any),
recovered from its comment via parse_order_comment().

M5 + M15 for now -- see main.py's docstring for the planned M30 extension.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ob_bridge.reader import OBSnapshot, Zone
from algo_v2_usoil.entries import EntryMode, EntryPlan, m5_entry, m15_entry, select_sl

# "V2O" keeps this bot's orders visually distinct from algo_v2's
# "V2"-prefixed (XAUUSD) comments while both may run on the same
# terminal/account.
COMMENT_PREFIX = "V2O"

# MT5 silently truncates order/deal comments to 16 characters on at least one
# broker we tested against (see algo_v2/candidates.py). "V2O|" + 1 tf code +
# 1 direction code + 6 base36 time digits = 12 chars, safely under that
# limit with margin. Base36 seconds since a 2025 epoch covers roughly 69
# years before overflowing 6 digits.
_COMMENT_EPOCH = 1735689600  # 2025-01-01T00:00:00Z
# Single-char codes -- "5" for M5 mirrors algo_v2's own M5 code (minutes as
# a digit). M15/M30 can't reuse that scheme (two digits), so they get a
# mnemonic letter instead: "F" (fifteen) here, "T" (thirty) reserved for
# when that tier is added.
_TF_CODE = {"M5": "5", "M15": "F"}
_CODE_TF = {v: k for k, v in _TF_CODE.items()}
_DIR_CODE = {1: "B", -1: "S"}
_CODE_DIR = {v: k for k, v in _DIR_CODE.items()}
_BASE36_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _to_base36(n: int) -> str:
    if n <= 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_BASE36_DIGITS[r])
    return "".join(reversed(out))


@dataclass(frozen=True)
class TradeCandidate:
    source_tf: str            # "M5" or "M15"
    direction: int             # 1 bullish, -1 bearish
    mode: EntryMode
    entry_price: Optional[float]   # None for MARKET (fill at send time)
    sl: float
    event_time: int            # the OB's detection time, or origin time if never live-detected
    zone_key: str               # compact identity: f"{source_tf}|{direction}|{event_time}"


def _event_time(zone: Zone) -> int:
    # start_time (the OB's origin candle) is the stable identity -- see
    # algo_v2/candidates.py's identical helper for why detected_time isn't
    # used here.
    return zone.start_time


def _zone_key(source_tf: str, direction: int, event_time: int) -> str:
    return f"{source_tf}|{direction}|{event_time}"


def order_comment(candidate: TradeCandidate) -> str:
    """Compact, MT5-comment-safe identity written onto every order this bot
    sends, so the bot can recover which zone a live order belongs to (e.g.
    after a restart, or when checking who owns a live pending order)."""
    tf_code = _TF_CODE[candidate.source_tf]
    dir_code = _DIR_CODE[candidate.direction]
    time_code = _to_base36(candidate.event_time - _COMMENT_EPOCH)
    return f"{COMMENT_PREFIX}|{tf_code}{dir_code}{time_code}"


def parse_order_comment(comment: str) -> Optional[tuple]:
    """Returns (zone_key, event_time) or None if this isn't our comment format.
    zone_key is reconstructed in the same format _zone_key() produces, so it
    compares equal to one built fresh from live zone data."""
    if not comment or not comment.startswith(COMMENT_PREFIX + "|"):
        return None
    rest = comment[len(COMMENT_PREFIX) + 1:]
    if len(rest) < 3:
        return None

    source_tf = _CODE_TF.get(rest[0])
    direction = _CODE_DIR.get(rest[1])
    time_code = rest[2:]
    if source_tf is None or direction is None or not time_code:
        return None

    try:
        event_time = int(time_code, 36) + _COMMENT_EPOCH
    except ValueError:
        return None

    return _zone_key(source_tf, direction, event_time), event_time


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


def _tier_edges(direction: int, m5: Optional[OBSnapshot], m15: Optional[OBSnapshot]) -> dict:
    """Current same-direction OB edge (low for bullish SL, high for bearish
    SL) per tier, for SL selection -- both tiers regardless of which one
    originated the trade, matching algo_v2/candidates.py's _htf_edges
    (XAUUSD's M15/M5/M3 all feed SL selection no matter which tier
    entered). Uses each tier's single latest zone in that direction (not
    the history list) -- SL follows the *current* structure, not an older
    one."""
    def edge(snap: Optional[OBSnapshot]) -> Optional[float]:
        if snap is None:
            return None
        history = snap.bull if direction == 1 else snap.bear
        if not history:
            return None
        return history[0].low if direction == 1 else history[0].high

    return {"M5": edge(m5), "M15": edge(m15)}


def _build_candidate(source_tf: str, entry_fn, direction: int, snap: Optional[OBSnapshot],
                     m5: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                     current_price: float) -> Optional[TradeCandidate]:
    if snap is None:
        return None

    # Never falls back to an older zone in history. If the single most
    # recent OB isn't virgin (or isn't live-detected yet), there is no
    # candidate this cycle -- jumping to an older zone would mean trading a
    # setup whose distance/detection-price math has nothing to do with why
    # price is where it currently is.
    history = snap.bull if direction == 1 else snap.bear
    if not history:
        return None

    zone = history[0]
    if not zone.virgin or zone.detected_time <= 0:
        return None

    ob_edge = zone.high if direction == 1 else zone.low
    plan: EntryPlan = entry_fn(direction, ob_edge, zone.detected_price)
    if plan.mode == EntryMode.NONE:
        return None

    reference_price = plan.entry_price if plan.entry_price is not None else current_price
    sl = select_sl(direction, reference_price, _tier_edges(direction, m5, m15))
    if sl is None:
        return None

    event_time = _event_time(zone)
    return TradeCandidate(source_tf, direction, plan.mode, plan.entry_price, sl,
                          event_time, _zone_key(source_tf, direction, event_time))


def build_m5_candidate(direction: int, m5: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                       current_price: float) -> Optional[TradeCandidate]:
    return _build_candidate("M5", m5_entry, direction, m5, m5, m15, current_price)


def build_m15_candidate(direction: int, m15: Optional[OBSnapshot], m5: Optional[OBSnapshot],
                        current_price: float) -> Optional[TradeCandidate]:
    return _build_candidate("M15", m15_entry, direction, m15, m5, m15, current_price)


def _distance_to_price(candidate: TradeCandidate, current_price: float) -> float:
    """MARKET-mode candidates fire immediately at current price -- zero
    distance, so they always win over any PENDING candidate at a real
    distance away."""
    if candidate.entry_price is None:
        return 0.0
    return abs(candidate.entry_price - current_price)


def choose_winning_candidate(candidates: list, current_price: float) -> Optional[TradeCandidate]:
    """Among currently-valid candidates (already filtered to allowed sources
    and to un-traded zones by the caller), whichever entry price sits
    closest to the current price wins -- not whichever is newest. Newest
    event_time only breaks an exact distance tie."""
    valid = [c for c in candidates if c is not None]
    if not valid:
        return None
    return min(valid, key=lambda c: (_distance_to_price(c, current_price), -c.event_time))


def should_replace_pending(winning: Optional[TradeCandidate],
                           pending_zone_key: Optional[str],
                           pending_entry_price: Optional[float],
                           current_price: float) -> bool:
    """True only if the winning candidate should cancel-and-replace the live
    pending order -- i.e. its entry price is genuinely closer to the current
    price than the pending order's. The caller must compute `winning` BEFORE
    cancelling anything, and only actually cancel once this returns True."""
    if winning is None:
        return False
    if pending_zone_key is None:
        return True  # nothing pending yet -- just place it
    if winning.zone_key == pending_zone_key:
        return False  # same setup already owns the pending order
    if pending_entry_price is None:
        return False  # unknown ownership -- never blind-cancel
    return _distance_to_price(winning, current_price) < abs(pending_entry_price - current_price)
