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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import MetaTrader5 as mt5

from v7_sentinel import broker, candle_touch, decision_log, rates, telegram_alerts, trade_journal
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
