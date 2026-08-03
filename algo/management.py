"""Position management: trailing SL and the bias-flip forced exit rule.

Trailing applies uniformly regardless of which timeframe originated the
trade (M1, M3, or M5): always follow whichever of M15/M5/M3's current
same-direction OB edge is closest to the CURRENT price, moving SL only in
the favorable direction (raise for longs, lower for shorts) -- never loosen.

Only one position is ever meant to be open at a time, matching the current
bias direction. Any bias direction (full or ShortTerm) unconditionally
forces the opposite-direction position/pending order closed -- otherwise a
stale position could sit there blocking new entries in the now-correct
direction until its own SL or a manual close, which defeats the point of
having a single live bias.
"""
from __future__ import annotations

from typing import Optional

from algo.bias import BiasResult
from algo.entries import select_sl


def compute_trailing_sl(direction: int, current_price: float, current_sl: Optional[float],
                        candidate_edges: dict) -> Optional[float]:
    """Returns a new SL only if it moves in the favorable direction; None if
    no update should be made (no closer/appropriate OB, or it would loosen)."""
    proposed = select_sl(direction, current_price, candidate_edges)
    if proposed is None:
        return None

    if current_sl is None:
        return proposed

    if direction == 1 and proposed > current_sl:
        return proposed
    if direction == -1 and proposed < current_sl:
        return proposed
    return None


def bias_flip_exit_direction(bias: BiasResult) -> Optional[int]:
    """Direction (1 or -1) whose open/pending exposure must be closed/
    cancelled right now, or None if bias has no direction. Any bias
    direction -- full or ShortTerm -- forces the opposite side out."""
    if bias.direction == 1:
        return -1
    if bias.direction == -1:
        return 1
    return None


ENTRY_GUARD_SECONDS = 5.0


def entry_recently_sent(direction: int, recent_entry: dict, now: float,
                        guard_seconds: float = ENTRY_GUARD_SECONDS) -> bool:
    """True if EITHER a MARKET or PENDING entry was sent in this direction
    recently enough that re-attempting now risks thrashing. Covers two
    distinct confirmed-live failure modes (on eth_smc/btc_smc) with one
    guard:

    1. MARKET: the broker's own position list can lag a real fill by more
       than one poll cycle at poll_seconds=1, letting the very next poll's
       "already holding a position" check see nothing live yet and fire a
       second entry for what's economically the same trade -- sometimes
       under a genuinely different zone_key too (if the source OB's
       detected_time drifted between polls), so comment-based duplicate-
       fill cleanup can't catch it either.

    2. PENDING: a pending order is deliberately never marked "traded" until
       it actually fills (so a genuinely unfilled order can be replaced by
       a better setup later -- see should_replace_pending). But that means
       a pending order that gets CANCELLED without filling (e.g. a bias
       flip closes it) leaves its zone still fully eligible -- if bias
       flips back moments later, the exact same still-virgin zone gets
       re-placed immediately. Confirmed live: with bias oscillating, this
       produced a place/cancel/replace loop of hundreds of orders in under
       a minute, all for the identical zone.

    This is a short local guard against both windows -- tracked in the
    bot's own memory, not queried from the broker, so it can't be fooled by
    the same lag/oscillation it's guarding against. For MARKET, the broker-
    side "already holding a position" check remains the long-term source of
    truth and takes over well before this guard would ever block a
    legitimate new entry that comes after a real close."""
    sent_at = recent_entry.get(direction)
    return sent_at is not None and (now - sent_at) < guard_seconds
