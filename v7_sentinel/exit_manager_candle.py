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
convention components 1/2 already use. (Originally applied across every
manager, no exemptions -- SCOPED TO SCALPER ONLY 2026-09-24, see the
SCALPER-ONLY REWORK section further down.)

TOUCH LOGIC (HTF lines same-candle-only, OB zones virgin-as-of + same-or-
previous-candle) -- fully shared with scalper_main.py (the Scalper manager,
an ENTRY use of the exact same pattern+touch signal) via candle_touch.py,
extracted 2026-09-22 specifically so both callers use the EXACT SAME logic,
especially the virgin-zone eligibility-timing fix (see
candle_touch.virgin_as_of()'s own docstring for the full "why" and the real
live miss that prompted it) -- never two independently-drifting copies. See
that module's own docstring for the complete rule (scope, pairing, both
touch mechanisms).

Both the HTF-line and zone checks are fully stateless (re-derived fresh
every cycle from live bridge/rates/Block reads) -- no persisted touch-
arming store needed anywhere in this component, unlike component 2's
RM-STR-style LevelEligibilityStore. Needs its own BridgeBarFlipTracker
instance (M15 is the only bridge-only timeframe in the HTF-line scope) --
own state file, never shared with RM-STR's, component 2's, or Scalper's own
trackers.

Also carries the same two guards components 1/2 already use:
  - broker.has_manual_tp() pauses auto-close for a position that currently
    carries a manual TP (the user's own "just like sl manager, and trade
    manager" convention) -- fully reactive, not latched. A Telegram alert
    fires on every skip too (2026-09-22), via
    telegram_alerts.send_if_configured().
  - "already-existing" guard: only a position opened BEFORE the pattern
    candle's own CLOSE time (bar_time + tf_minutes*60) qualifies.

SCALPER-ONLY REWORK (2026-09-24, user: "EA Candle Exit shouldn't close
positions, turn it off, scalper positions can close on opposite side
candle signal, H signal can be close by S signal on m3 or m5, or m1 dual
atr flip or M3 cisd, candle exit not applicable for any other trade
managers apart from scalper"): this ENTIRE component now only ever
closes SCALPER's own positions -- TM-STR/RM-STR/RM-ICT are no longer
touched by it at all (component 1/2/4 still cover them; this one is
Scalper-exclusive going forward). Three independent triggers, all OR'd,
all scoped to Scalper only:
  1. OPPOSITE PATTERN + TOUCH (the ORIGINAL mechanism above, unchanged --
     confirmed with the user this STILL needs the touch, same as before):
     a Hammer position closed by a later Star (or vice versa) on M3/M5,
     with that opposite pattern candle also touching an HTF line or OB
     zone of the matching role.
  2. M1 STRUCTURE FLIP, fresh (this exact bar) -- flip_state.
     fresh_flip_direction(), same "privileged, momentary" one-shot
     contract as fresh_cisd() (confirmed with the user 2026-09-24 for
     exit_manager_ltf.py's own identical new trigger, applied here too
     for consistency). NO touch required -- deliberately simpler/faster
     than trigger 1, matching component 4's own existing precedent of
     giving Scalper progressively faster, less-gated exits layered on
     top of the shared ones.
  3. M3 CISD, fresh, matching direction -- also no touch required. Note:
     this overlaps with component 4's own existing Scalper-only M3/M5
     CISD exit (exit_manager_bias.run_once_scalper) -- harmless, not a
     bug: whichever of the two independent checks runs first in a given
     cycle closes the position, the other then simply finds nothing left
     to close.
  Trigger 1 keeps its own BridgeBarFlipTracker (rt.tracker, D1-M5, for
  the HTF-line sub-check) -- trigger 2 reuses that SAME instance for M1
  (a new timeframe key in its internal dict, no separate state needed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import MetaTrader5 as mt5

from v7_sentinel import broker, candle_touch, cisd_bridge, decision_log, flip_state, rates, telegram_alerts, \
    trade_journal
from v7_sentinel.bridge import HammerStar, read_hammer_star
from v7_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v7_sentinel.nlb_nsb_block import BlockStore

if TYPE_CHECKING:
    from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}

# Re-exported for backward compat / anyone importing these from this module's
# own namespace -- the real definitions now live in candle_touch.py, shared
# with scalper_main.py. See that module's own docstring for the full rule.
CANDLE_HTF_TIMEFRAMES = candle_touch.CANDLE_HTF_TIMEFRAMES
_CANDLE_TIMEFRAMES = candle_touch.CANDLE_TIMEFRAMES
_HAMMER_ROLE, _HAMMER_DIRECTION = candle_touch.HAMMER_ROLE, candle_touch.HAMMER_DIRECTION
_STAR_ROLE, _STAR_DIRECTION = candle_touch.STAR_ROLE, candle_touch.STAR_DIRECTION


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
    print(f"[V7S-XM-CANDLE] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
          f"M{pattern_tf} {pattern_name} touched {trigger_label}")
    detail = {"rule": "candle pattern + touch (EA-CandleExit)", "pattern": pattern_name, "sub_source": sub_tag,
              "pattern_timeframe": pattern_tf, "trigger": trigger_label,
              "confirm_time": confirm_time, "position_open_time": position.time, **detail_extra}
    if not cfg.enable_trading:
        print("[V7S-XM-CANDLE] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "candle_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=f"V7S-XM-CANDLE-{sub_tag}-SQ")
    if not result.ok:
        print(f"[V7S-XM-CANDLE] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "candle_close_failed", ticket=position.ticket, target=source.name,
                         retcode=result.retcode)
        telegram_alerts.send_if_configured(
            cfg.alerts_bot_token, cfg.alerts_chat_id,
            f"[V7S] EXIT FAILED: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
            f"CANDLEEXIT-{sub_tag} (M{pattern_tf} {pattern_name} touched {trigger_label}) -- "
            f"retcode={result.retcode} {result.comment}")
        return
    decision_log.log(cfg.decision_log_file, "candle_close_filled", ticket=position.ticket, target=source.name,
                     direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, f"CANDLEEXIT-{sub_tag}", detail)
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V7S] EXIT: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) closed -- "
        f"CANDLEEXIT-{sub_tag} (M{pattern_tf} {pattern_name} touched {trigger_label})")


def _scalper_source(cfg: "ExitManagerSymbolConfig") -> "WatchedSource | None":
    return next((s for s in cfg.sources if s.name == "SCALPER"), None)


def _close_opposite_positions(cfg: "ExitManagerSymbolConfig", direction: int, confirm_time: int,
                              pattern_tf: int, pattern_name: str, sub_tag: str, trigger_label: str,
                              detail_extra: dict) -> None:
    """SCALPER-ONLY (2026-09-24, see module docstring) -- was every
    cfg.sources before; now only ever closes Scalper's own positions."""
    source = _scalper_source(cfg)
    if source is None:
        return
    opposite_type = mt5.POSITION_TYPE_SELL if direction == 1 else mt5.POSITION_TYPE_BUY
    for position in broker.get_positions(cfg.symbol, source.magic_number):
        if position.type != opposite_type:
            continue
        if position.time >= confirm_time:
            continue   # opened at/after the pattern candle's own close -- "already existing", not after
        own_tp = source.own_tp_lookup(position.ticket) if source.own_tp_lookup else None
        if broker.is_paused_by_manual_tp(position, own_tp):
            pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
            print(f"[V7S-XM-CANDLE] {source.name} #{position.ticket} has a manual TP set -- "
                  f"skipping auto-close (user is watching it manually)")
            telegram_alerts.send_if_configured(
                cfg.alerts_bot_token, cfg.alerts_chat_id,
                f"[V7S] EM SKIPPED (manual TP set): {source.name} #{position.ticket} "
                f"({_DIR_LABEL[pos_direction]}) -- would have closed via CANDLEEXIT-{sub_tag} "
                f"(M{pattern_tf} {pattern_name} touched {trigger_label})")
            continue
        _close_position(cfg, position, source, pattern_tf, pattern_name, sub_tag, trigger_label,
                        confirm_time, detail_extra)


def _close_scalper_simple(cfg: "ExitManagerSymbolConfig", direction: int, confirm_time: int,
                          trigger_tag: str, trigger_label: str) -> None:
    """Triggers 2/3 (M1 structure FLIP / M3 CISD, see module docstring's
    own SCALPER-ONLY REWORK section) -- no touch, no pattern, just a
    direction match against Scalper's own opposite-direction positions."""
    source = _scalper_source(cfg)
    if source is None:
        return
    opposite_type = mt5.POSITION_TYPE_SELL if direction == 1 else mt5.POSITION_TYPE_BUY
    for position in broker.get_positions(cfg.symbol, source.magic_number):
        if position.type != opposite_type:
            continue
        if position.time >= confirm_time:
            continue   # opened at/after this trigger's own confirm time -- "already existing", not after
        own_tp = source.own_tp_lookup(position.ticket) if source.own_tp_lookup else None
        if broker.is_paused_by_manual_tp(position, own_tp):
            pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
            print(f"[V7S-XM-CANDLE] {source.name} #{position.ticket} has a manual TP set -- "
                  f"skipping auto-close (user is watching it manually)")
            telegram_alerts.send_if_configured(
                cfg.alerts_bot_token, cfg.alerts_chat_id,
                f"[V7S] EM SKIPPED (manual TP set): {source.name} #{position.ticket} "
                f"({_DIR_LABEL[pos_direction]}) -- would have closed via CANDLEEXIT-{trigger_tag} "
                f"({trigger_label})")
            continue
        pos_direction = -direction   # position.type == opposite_type by construction above
        print(f"[V7S-XM-CANDLE] closing {source.name} #{position.ticket} "
              f"({_DIR_LABEL[pos_direction]}) -- {trigger_label}")
        detail = {"rule": f"{trigger_label} (EA-CandleExit, Scalper-only)", "trigger": trigger_tag,
                  "confirm_time": confirm_time, "position_open_time": position.time}
        if not cfg.enable_trading:
            print("[V7S-XM-CANDLE] enable_trading is false -- decision only, no order sent")
            decision_log.log(cfg.decision_log_file, "candle_close_decision_only", ticket=position.ticket,
                             target=source.name, direction=_DIR_LABEL[pos_direction], **detail)
            continue
        result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                       comment=f"V7S-XM-CANDLE-{trigger_tag}-SQ")
        if not result.ok:
            print(f"[V7S-XM-CANDLE] close failed: retcode={result.retcode} comment={result.comment}")
            decision_log.log(cfg.decision_log_file, "candle_close_failed", ticket=position.ticket,
                             target=source.name, retcode=result.retcode)
            telegram_alerts.send_if_configured(
                cfg.alerts_bot_token, cfg.alerts_chat_id,
                f"[V7S] EXIT FAILED: {source.name} #{position.ticket} ({_DIR_LABEL[pos_direction]}) -- "
                f"CANDLEEXIT-{trigger_tag} ({trigger_label}) -- retcode={result.retcode} {result.comment}")
            continue
        decision_log.log(cfg.decision_log_file, "candle_close_filled", ticket=position.ticket, target=source.name,
                         direction=_DIR_LABEL[pos_direction], **detail)
        trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
            position.ticket, f"CANDLEEXIT-{trigger_tag}", detail)
        telegram_alerts.send_if_configured(
            cfg.alerts_bot_token, cfg.alerts_chat_id,
            f"[V7S] EXIT: {source.name} #{position.ticket} ({_DIR_LABEL[pos_direction]}) closed -- "
            f"CANDLEEXIT-{trigger_tag} ({trigger_label})")


def _check_m1_flip(cfg: "ExitManagerSymbolConfig", rt: "CandleExitRuntime") -> None:
    m1_fs = rt.tracker.update(cfg.symbol, 1)   # same tracker as the HTF states -- M1 is just a new key
    direction = flip_state.fresh_flip_direction(m1_fs)
    if direction is None:
        return
    confirm_time = m1_fs.last_event.bar_time + 1 * 60
    label = f"M1 structure FLIP ({'bullish' if direction == 1 else 'bearish'})"
    _close_scalper_simple(cfg, direction, confirm_time, "M1FLIP", label)


def _check_m3_cisd(cfg: "ExitManagerSymbolConfig") -> None:
    cisd = cisd_bridge.fresh_cisd(cfg.symbol, 3)
    if cisd is None:
        return
    direction = cisd_bridge.direction_of(cisd)
    confirm_time = cisd.bar_time + 3 * 60
    label = f"M3 {cisd.last_cisd} CISD"
    _close_scalper_simple(cfg, direction, confirm_time, "M3CD", label)


def _check_htf_lines(cfg: "ExitManagerSymbolConfig", htf_states: dict, pattern_tf: int, hs: HammerStar,
                     role: str, direction: int, pattern_name: str) -> None:
    extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, hs.bar_time)
    if extremes is None:
        return   # that bar isn't in MT5's own history (yet, or expired) -- nothing to check against
    high, low = extremes
    touch_price = low if role == "SUPPORT" else high

    touching = candle_touch.find_touching_line(htf_states, role, touch_price, hs.close)
    if touching is None:
        return
    level_tf, level_source = touching
    confirm_time = hs.bar_time + pattern_tf * 60  # real close time of the pattern candle
    _close_opposite_positions(cfg, direction, confirm_time, pattern_tf, pattern_name, sub_tag="STR",
                              trigger_label=f"M{level_tf}/{level_source}",
                              detail_extra={"level_timeframe": level_tf, "level_source": level_source})


def _check_zones(cfg: "ExitManagerSymbolConfig", block: BlockStore, pattern_tf: int, hs: HammerStar,
                 direction: int, pattern_name: str) -> None:
    wanted_role = candle_touch.ZONE_ROLE_FOR_PATTERN[pattern_name]
    same_bar_time = hs.bar_time
    prev_bar_time = hs.bar_time - pattern_tf * 60
    same_extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, same_bar_time)
    prev_extremes = rates.read_bar_high_low(cfg.symbol, pattern_tf, prev_bar_time)
    if same_extremes is None and prev_extremes is None:
        return

    touching_zone = candle_touch.find_touching_zone(block.zones(), wanted_role, same_extremes, prev_extremes,
                                                     same_bar_time, prev_bar_time)
    if touching_zone is None:
        return

    confirm_time = hs.bar_time + pattern_tf * 60  # real close time of the pattern candle
    _close_opposite_positions(cfg, direction, confirm_time, pattern_tf, pattern_name, sub_tag="ICT",
                              trigger_label=f"{touching_zone.timeframe_name} OB zone {touching_zone.zone_id}",
                              detail_extra={"zone_id": touching_zone.zone_id,
                                           "zone_timeframe": touching_zone.timeframe,
                                           "zone_top": touching_zone.top, "zone_btm": touching_zone.btm})


def run_once(cfg: "ExitManagerSymbolConfig", rt: CandleExitRuntime) -> None:
    htf_states = candle_touch.compute_htf_states(cfg.symbol, rt.tracker)
    block = BlockStore(cfg.ict_block_state_file)   # read fresh every cycle -- never written to here

    for tf in candle_touch.CANDLE_TIMEFRAMES:
        hs = read_hammer_star(cfg.symbol, tf)
        if hs is None:
            continue
        if candle_touch.is_hammer(hs):
            _check_htf_lines(cfg, htf_states, tf, hs, _HAMMER_ROLE, _HAMMER_DIRECTION, "hammer")
            _check_zones(cfg, block, tf, hs, _HAMMER_DIRECTION, "hammer")
        if candle_touch.is_star(hs):
            _check_htf_lines(cfg, htf_states, tf, hs, _STAR_ROLE, _STAR_DIRECTION, "star")
            _check_zones(cfg, block, tf, hs, _STAR_DIRECTION, "star")

    # Triggers 2/3 (Scalper-only, no touch needed) -- see module docstring's own SCALPER-ONLY REWORK section.
    _check_m1_flip(cfg, rt)
    _check_m3_cisd(cfg)
