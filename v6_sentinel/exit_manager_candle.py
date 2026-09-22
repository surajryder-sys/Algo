"""Exit Manager -- COMPONENT 3: EA-CandleExit. Built 2026-09-22 (user's own
rule):

  "we have mt5 bridge data, we need m3, m5 data, in this bridge we have
  hammer star values as well, whenever a hammer is formed in m3 or m5, the
  same candle should touch any of the htf resistance levels, qualifies to
  close a sell trade, touch event and hammer should be in same m3 or m5
  candle, qualifies to exit sell trades, similarly for buy trades we need
  star formation on m3 or m5 to close the long trades"

PATTERN SOURCE: bridge.read_hammer_star() -- HAMMERSTAR_<sym>_<tf>.json,
published by mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5's own
HammerShootingStar block. Checked on M3 and M5 independently every cycle
(_CANDLE_TIMEFRAMES). Its own hammer/star booleans are True for that LAST
CLOSED bar's own shape (long lower/upper wick + a new local low/high -- see
that indicator's hammerShape/starShape math), for that bar's whole duration
-- same "momentary but bar-scoped" contract cisd_bridge's fresh_cisd() uses.

LEVEL/DIRECTION PAIRING (confirmed with the user 2026-09-22 -- "natural
pairing", matching how the two shapes actually form): a HAMMER forms at the
BOTTOM of a move (its own defining long lower wick), so it must touch an HTF
SUPPORT level -> confirms a BULLISH signal -> closes SELL positions. A STAR
forms at the TOP of a move (long upper wick), so it must touch an HTF
RESISTANCE level -> confirms a BEARISH signal -> closes BUY/long positions.
Extended the same way to OB zones (see ZONE SOURCE below): a bullish/demand
zone (role "no_short_buffer") is SUPPORT-like -> pairs with hammer; a
bearish/supply zone ("no_long_buffer") is RESISTANCE-like -> pairs with
star. "Opposite" is relative to the confirmed signal direction, same
convention components 1/2 already use -- across every manager
(TM-STR/RM-STR/RM-ICT alike), no exemptions.

HTF LINES -- "TOUCH EVENT AND HAMMER SHOULD BE IN SAME M3 OR M5 CANDLE":
the SAME bar that formed the pattern must ALSO be the one whose own high
(star) or low (hammer) reached the line's value -- not a separately-armed
touch from some earlier candle. rates.read_bar_high_low() fetches that
specific bar's own high/low directly from MT5 (the HAMMERSTAR bridge itself
only publishes that bar's close, not its full OHLC).

SCOPE (HTF lines): D1, H4, H2, H1, M30, M15, M10 -- CANDLE_HTF_TIMEFRAMES
below. Started as component 2's own D1-M15 set (reused directly), then the
user explicitly asked to "add m10 as well" (2026-09-22) once shown the exact
scope -- component 3 now carries its OWN separate scope constant rather than
importing component 2's, so the two can diverge cleanly (M10 here, not
there) without ever silently coupling one component's scope change to the
other's. Still excludes M5 (RM-STR's own fuller 8-timeframe scope goes one
step further than this) -- M3/M5 are this component's PATTERN timeframes,
not part of its HTF-line touch scope. M10 needs no new bridge dependency:
htf_levels.compute_htf_state() already computes it NATIVELY via rates.py
(M10 isn't in bridge.BRIDGE_ONLY_TIMEFRAMES), same as every other non-bridge
timeframe here.

ZONE SOURCE (2026-09-22, "add virgin zones as well from ict / only untested
zones"): nlb_nsb_block.BlockStore -- the SAME OB zone Block RM-ICT itself
reads (read-only here, never written to, same relationship reversal_ict.py
already has to it). "ONLY UNTESTED ZONES": zone.retested == False (virgin --
BlockZone has no separate "virgin" field; retested IS the tested/untested
flag, see nlb_nsb_block.py's own docstring -- retested=False is virgin).

"THE TESTING CANDLE CAN BE A HAMMER OR STAR, OR A PREVIOUS CANDLE COULD BE A
RETEST CANDLE": unlike the HTF-line rule, a zone's own touch does NOT have
to land on the exact same bar as the pattern -- either the pattern candle
ITSELF touches the zone's [btm, top] range, OR the ONE candle immediately
before it does (rates.read_bar_high_low() at bar_time - tf_minutes*60).
Deliberately does NOT read the Block's own retested_at/retested_source
fields for this touch test (those track the WATCHER's own live-tick
retest detection, which would already have flipped retested=True the
instant a genuine touch happened -- using that here would make "only
untested zones" and "the testing candle touched it" self-contradictory).
Instead this component does its OWN independent, candle-OHLC-vs-zone-range
overlap test, using zone.retested purely as an ELIGIBILITY filter (this OB
is still structurally virgin in the Block's own tracking), never as the
touch signal itself. "Touch" = candle's [low, high] overlaps the zone's
[btm, top] -- the same "price entered the range" test the watcher itself
uses for its own retest detection, just applied to one candle's OHLC
instead of live ticks.

Both the HTF-line and zone checks are fully stateless (re-derived fresh
every cycle from live bridge/rates/Block reads) -- no persisted touch-
arming store needed anywhere in this component, unlike component 2's
RM-STR-style LevelEligibilityStore. Needs its own BridgeBarFlipTracker
instance (M15 is the only bridge-only timeframe in the HTF-line scope) --
own state file, never shared with RM-STR's or component 2's own trackers.

Also carries the same two guards components 1/2 already use:
  - broker.has_manual_tp() pauses auto-close for a position that currently
    carries a manual TP (the user's own "just like sl manager, and trade
    manager" convention) -- fully reactive, not latched.
  - "already-existing" guard: only a position opened BEFORE the pattern
    candle's own CLOSE time (bar_time + tf_minutes*60) qualifies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import MetaTrader5 as mt5

from v6_sentinel import broker, decision_log, htf_levels, rates, trade_journal
from v6_sentinel.bridge import HammerStar, read_hammer_star
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v6_sentinel.nlb_nsb_block import BlockStore, BlockZone

if TYPE_CHECKING:
    from v6_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_CANDLE_TIMEFRAMES = (3, 5)  # M3, M5 -- both checked independently every cycle, PATTERN source only

# D1, H4, H2, H1, M30, M15, M10 -- this component's OWN HTF-line touch
# scope, see module docstring's SCOPE section (started from component 2's
# D1-M15, then M10 added on the user's explicit follow-up).
CANDLE_HTF_TIMEFRAMES = (1440, 240, 120, 60, 30, 15, 10)

# (level_role_required, confirmed_direction) -- see module docstring's
# LEVEL/DIRECTION PAIRING section. Zone roles use a different vocabulary
# ("no_short_buffer"/"no_long_buffer", see ob_levels.py) -- same pairing.
_HAMMER_ROLE, _HAMMER_DIRECTION = "SUPPORT", 1
_STAR_ROLE, _STAR_DIRECTION = "RESISTANCE", -1
_ZONE_ROLE_FOR_PATTERN = {"hammer": "no_short_buffer", "star": "no_long_buffer"}


@dataclass
class CandleExitRuntime:
    """Own BridgeBarFlipTracker (M15 is bridge-only) -- no touch-arming
    store needed at all, see module docstring."""
    tracker: BridgeBarFlipTracker


def build_runtime(cfg: "ExitManagerSymbolConfig") -> CandleExitRuntime:
    return CandleExitRuntime(tracker=BridgeBarFlipTracker(cfg.candle_bridge_bar_flip_state_file))


def _close_position(cfg: "ExitManagerSymbolConfig", position, source: "WatchedSource", pattern_tf: int,
                    pattern_name: str, sub_tag: str, trigger_label: str, confirm_time: int,
                    detail_extra: dict) -> None:
    """sub_tag ("STR" or "ICT", user's own explicit request 2026-09-22 --
    "comments will come right? who exited the trade... whether the bias
    flip, or candle exit based on STR, or candle exit based on ICT"):
    distinguishes the two independent sub-sources this component checks --
    an HTF ATR/Supertrend LINE touch ("STR", same vocabulary as TM-STR/
    RM-STR's own line-based approach) vs an RM-ICT OB ZONE touch ("ICT") --
    both in the MT5 order comment itself (so it's visible on the broker
    side, not just in this project's own logs) and in the trade journal's
    own exit reason. Comment stays well under MT5's 31-character limit
    (see this project's own prior comment-length incident)."""
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    print(f"[V6S-XM-CANDLE] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
          f"M{pattern_tf} {pattern_name} touched {trigger_label}")
    detail = {"rule": "candle pattern + touch (EA-CandleExit)", "pattern": pattern_name, "sub_source": sub_tag,
              "pattern_timeframe": pattern_tf, "trigger": trigger_label,
              "confirm_time": confirm_time, "position_open_time": position.time, **detail_extra}
    if not cfg.enable_trading:
        print("[V6S-XM-CANDLE] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "candle_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=f"V6S-XM-CANDLE-{sub_tag}-SQ")
    if not result.ok:
        print(f"[V6S-XM-CANDLE] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "candle_close_failed", ticket=position.ticket, target=source.name,
                         retcode=result.retcode)
        return
    decision_log.log(cfg.decision_log_file, "candle_close_filled", ticket=position.ticket, target=source.name,
                     direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, f"CANDLEEXIT-{sub_tag}", detail)


def _close_opposite_positions(cfg: "ExitManagerSymbolConfig", direction: int, confirm_time: int,
                              pattern_tf: int, pattern_name: str, sub_tag: str, trigger_label: str,
                              detail_extra: dict) -> None:
    opposite_type = mt5.POSITION_TYPE_SELL if direction == 1 else mt5.POSITION_TYPE_BUY
    for source in cfg.sources:
        for position in broker.get_positions(cfg.symbol, source.magic_number):
            if position.type != opposite_type:
                continue
            if position.time >= confirm_time:
                continue   # opened at/after the pattern candle's own close -- "already existing", not after
            if broker.has_manual_tp(position):
                print(f"[V6S-XM-CANDLE] {source.name} #{position.ticket} has a manual TP set -- "
                      f"skipping auto-close (user is watching it manually)")
                continue
            _close_position(cfg, position, source, pattern_tf, pattern_name, sub_tag, trigger_label,
                            confirm_time, detail_extra)


def _check_htf_lines(cfg: "ExitManagerSymbolConfig", htf_states: dict, pattern_tf: int, hs: HammerStar,
                     role: str, direction: int, pattern_name: str) -> None:
    extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, hs.bar_time)
    if extremes is None:
        return   # that bar isn't in MT5's own history (yet, or expired) -- nothing to check against
    high, low = extremes
    touch_price = low if role == "SUPPORT" else high

    touching = next(
        (
            (level_tf, level.source)
            for level_tf, state in htf_states.items() if state is not None
            for level in state.levels
            if level.role == role
            and ((touch_price <= level.value) if role == "SUPPORT" else (touch_price >= level.value))
        ),
        None,
    )
    if touching is None:
        return
    level_tf, level_source = touching
    confirm_time = hs.bar_time + pattern_tf * 60  # real close time of the pattern candle
    _close_opposite_positions(cfg, direction, confirm_time, pattern_tf, pattern_name, sub_tag="STR",
                              trigger_label=f"M{level_tf}/{level_source}",
                              detail_extra={"level_timeframe": level_tf, "level_source": level_source})


def _candle_touches_zone(high: float, low: float, zone: BlockZone) -> bool:
    """True if [low, high] overlaps the zone's own [btm, top] range -- the
    same 'price entered the range' test nlb_nsb_watcher's own live retest
    detection uses, just evaluated against one specific candle's OHLC
    instead of live ticks."""
    return low <= zone.top and high >= zone.btm


def _check_zones(cfg: "ExitManagerSymbolConfig", block: BlockStore, pattern_tf: int, hs: HammerStar,
                 direction: int, pattern_name: str) -> None:
    wanted_role = _ZONE_ROLE_FOR_PATTERN[pattern_name]
    same_extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, hs.bar_time)
    prev_extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, hs.bar_time - pattern_tf * 60)
    if same_extremes is None and prev_extremes is None:
        return

    touching_zone = None
    for zone in block.zones():
        if zone.role != wanted_role or zone.retested:
            continue   # only untested (still virgin) zones of the matching role
        if same_extremes is not None and _candle_touches_zone(*same_extremes, zone):
            touching_zone = zone
            break
        if prev_extremes is not None and _candle_touches_zone(*prev_extremes, zone):
            touching_zone = zone
            break
    if touching_zone is None:
        return

    confirm_time = hs.bar_time + pattern_tf * 60  # real close time of the pattern candle
    _close_opposite_positions(cfg, direction, confirm_time, pattern_tf, pattern_name, sub_tag="ICT",
                              trigger_label=f"{touching_zone.timeframe_name} OB zone {touching_zone.zone_id}",
                              detail_extra={"zone_id": touching_zone.zone_id,
                                           "zone_timeframe": touching_zone.timeframe,
                                           "zone_top": touching_zone.top, "zone_btm": touching_zone.btm})


def run_once(cfg: "ExitManagerSymbolConfig", rt: CandleExitRuntime) -> None:
    htf_states = {tf: htf_levels.compute_htf_state(cfg.symbol, tf, rt.tracker) for tf in CANDLE_HTF_TIMEFRAMES}
    block = BlockStore(cfg.ict_block_state_file)   # read fresh every cycle -- never written to here

    for tf in _CANDLE_TIMEFRAMES:
        hs = read_hammer_star(cfg.symbol, tf)
        if hs is None:
            continue
        if hs.hammer:
            _check_htf_lines(cfg, htf_states, tf, hs, _HAMMER_ROLE, _HAMMER_DIRECTION, "hammer")
            _check_zones(cfg, block, tf, hs, _HAMMER_DIRECTION, "hammer")
        if hs.star:
            _check_htf_lines(cfg, htf_states, tf, hs, _STAR_ROLE, _STAR_DIRECTION, "star")
            _check_zones(cfg, block, tf, hs, _STAR_DIRECTION, "star")
