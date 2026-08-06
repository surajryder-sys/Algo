"""Position management for the FX cross-pairs bot: trailing SL and the
bias/opposite-OB exit. Both are single-timeframe (H1) versions of the same
two behaviors algo_v2 (XAUUSD) already has proven live -- see
algo_v2/management.py and algo_v2/main.py's fresh_opposite_ob_exists usage.
"""
from __future__ import annotations

from typing import Optional

from ob_bridge.reader import OBSnapshot
from algo_v2_fx.entries import sl_for_zone

# Floating-point tolerance for "did the SL actually improve" -- confirmed
# live on algo_v2/algo_v2_usoil that a bare `>`/`<` comparison can read
# ~1e-14 float noise as "still improving" forever even though the underlying
# OB edge never moved. Real FX tick sizes (0.00001 on 5-digit pairs, 0.001 on
# JPY pairs) are far larger than this epsilon, so any genuine edge move still
# clears it easily. See algo_v2/management.py's _MIN_SL_IMPROVEMENT docstring
# for the full incident this was copied from.
_MIN_SL_IMPROVEMENT = 1e-6


def compute_trailing_sl(direction: int, current_price: float, current_sl: Optional[float],
                        zone) -> Optional[float]:
    """Returns a new SL only if it moves in the favorable direction by more
    than floating-point noise; None if no update should be made.

    zone: the CURRENT latest same-direction H1 zone (virgin or not -- SL
    follows the current structure regardless of virgin status, same as
    algo_v2's _htf_edges). None if no zone exists in this direction."""
    if zone is None:
        return None

    proposed = sl_for_zone(direction, zone)

    # A proposed SL on the wrong side of current price would be a
    # broker-rejected invalid stop -- never propose one, same guard as
    # algo_v2's select_sl "valid side" check.
    if direction == 1 and proposed >= current_price:
        return None
    if direction == -1 and proposed <= current_price:
        return None

    if current_sl is None:
        return proposed

    if direction == 1 and proposed > current_sl + _MIN_SL_IMPROVEMENT:
        return proposed
    if direction == -1 and proposed < current_sl - _MIN_SL_IMPROVEMENT:
        return proposed
    return None


def fresh_opposite_ob_exists(snap: Optional[OBSnapshot], direction: int, since_time: int) -> bool:
    """True if the latest OPPOSITE-direction H1 zone's start_time postdates
    since_time (the position's own open time, pos.time from MT5) -- i.e. a
    genuinely new opposite OB has formed since this trade was opened, not
    just an old one that happened to already exist. Mirrors algo_v2's
    fresh_opposite_ob_exists (there compared against the ATR zone's own last
    flip time; here compared directly against the position's own open time,
    since there's no ATR zone/flip concept on the FX side -- H1 bias IS the
    only structure)."""
    if snap is None:
        return False

    opposite_history = snap.bear if direction == 1 else snap.bull
    if not opposite_history:
        return False

    return opposite_history[0].start_time > since_time
