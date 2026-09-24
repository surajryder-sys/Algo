"""Exit Manager -- COMPONENT 5: ICT Exit. Built 2026-09-24, user's own
rule, given with worked examples:

  "ob zone touch + cisd confirmation lets say with examples sell trade
  opened, a m3 or m5 virgin bullish ob gets retested and m1 or m3 cisd
  confirm bullish exit lets say a scalper trade, sell trade touches
  bullish ob, m1 already bulliish, didnt turn bearish to reconfirm
  bullish (so it can exit), and m3 is also bullish before touching
  itself, it didnt turn bearish to confirm bullish (so it can exit), in
  such case fire exit on ob touch itself similarly buy side as well this
  is purely for m3 and m5 based exit rest any other htf ob's above m5
  timeframe will need touch with m3 cisd to exit the trade"

Then confirmed "applies to all" (not Scalper-only) -- every manager's
open positions (TM-STR, RM-STR, RM-ICT, SCALPER alike), same "no
exemptions" convention every other Exit Manager component uses.

RULE, precisely: a position closes on a TOUCHED, OPPOSITE-role OB zone --
a SELL closes on a bullish/demand zone retest, a BUY closes on a
bearish/supply zone retest (the opposite of what would have OPENED that
direction -- i.e. reversal evidence against the open position):

  - FAST TIER -- the zone's OWN formation timeframe is M3 or M5: a fresh
    M1 or M3 CISD confirming the zone's own direction, AFTER the touch,
    closes it. DIRECT-FIRE EXCEPTION (applies to every manager, per the
    user's own worked example checking BOTH M1 and M3's standing state):
    if EITHER M1's or M3's STANDING CISD already matches the zone's own
    direction at/after the touch (never needed a fresh flip to
    "reconfirm"), fire immediately on the touch itself -- mirrors
    reversal_ict.py's own M1CD-DIRECT precedent, extended to both M1 and
    M3 here since the user's example explicitly checked both.
  - HTF TIER -- any other zone timeframe (M15 and above): touch + a FRESH
    M3 CISD only, no M1, no direct-fire.

ZONE SOURCES: M15/M5/M3 zones come from the merged TV+MT5 store
(ict_ob_block.ICTBlockStore, Data Manager's own ict_ob_watcher.py) --
role/touch tracking identical to nlb_nsb_block.BlockZone (retested/
retested_at/retested_source), just spanning 3 timeframes and 2 sources.
H4/H2/H1/M30/M10 zones (never covered by the merged store) come from the
EXISTING RM-ICT Block (nlb_nsb_block.BlockStore, cfg.ict_block_state_file)
-- the SAME file component 3 (EA-CandleExit) already reads read-only.
M15/M5 TV-sourced zones exist in BOTH stores (the old Block's own scope
already includes them) -- harmless double-coverage, not a double-close
risk: a position can only be closed once, and a second qualifying check
against an already-gone position simply finds nothing left to close.

TOUCH VALIDITY (same convention as reversal_ict.py's own
_touch_is_current(), ported here rather than shared -- see that module's
own docstring for the full incident this guards against): a touch only
counts if it was caught LIVE (retested_source == "live", never a "seed"
copy of old scraper/bridge history) and happened at most
touch_max_age_minutes ago. ADDITIONALLY (this component's own extra
guard): the touch must have happened AFTER this specific position opened
-- a retest that happened before the position even existed says nothing
about closing it now, same "already-existing signal doesn't count"
principle every other Exit Manager component applies to its own trigger.

NO PERSISTED STATE OF ITS OWN AT FIRST, ONE FLIP TRACKER SINCE (2026-09-24):
originally fully stateless like component 1 (Bias Exit Manager) --
re-derived everything fresh every cycle from the two Blocks + cisd_bridge +
broker.get_positions(). The M1 CISD -> M1 dual-ATR structure FLIP
substitution below needs exactly one small piece of persisted state (a
BridgeBarFlipTracker, to know the prior bar boundary a flip is measured
against) -- own state file (cfg.ict_exit_bridge_bar_flip_state_file), never
shared with any other component's own tracker, same "own state, don't
share" convention this project always uses. Everything else remains fully
stateless. A position that's gone is simply not found again; a process
restart mid-bar self-heals.

M1 DUAL-ATR FLIP REPLACES M1 CISD (2026-09-24, user: "there also use m1
dual atr flip, instead of m1 cisd, rest m3 cisd is good" -- the same
substitution applied to reversal_ict.py's own M5-zone entry pool and
exit_manager_ltf.py's own confirmation the same day): the fast tier's M1
slot is now a genuine M1 ATR-dual structure FLIP
(flip_state.fresh_flip_direction(), same "privileged, momentary" one-shot
contract as fresh_cisd()) for both the fresh-event check and the
direct-fire check (now keyed off M1's CURRENT confirmed structure,
m1_fs.confirmed, rather than a standing CISD classification). M3 stays
exactly CISD-based throughout, unaffected. Trigger tags: "M1FLIP" /
"M1FLIP-DIRECT" (was "M1CD" / "M1CD-DIRECT").

Also carries the same manual-TP guard every other component uses
(broker.is_paused_by_manual_tp()), with a Telegram alert on every skip.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

import MetaTrader5 as mt5

from v7_sentinel import broker, cisd_bridge, decision_log, flip_state, telegram_alerts, trade_journal
from v7_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v7_sentinel.ict_ob_block import ICTBlockStore
from v7_sentinel.nlb_nsb_block import BlockStore

if TYPE_CHECKING:
    from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig, WatchedSource

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_FAST_TIER_TIMEFRAMES = ("5", "3")     # the zone's OWN formation timeframe -- M3/M5, see module docstring


class ICTExitRuntime:
    """Own BridgeBarFlipTracker (M1 structure flip) -- see module
    docstring's own NO PERSISTED STATE OF ITS OWN AT FIRST section."""
    def __init__(self, tracker: BridgeBarFlipTracker):
        self.tracker = tracker


def build_runtime(cfg: "ExitManagerSymbolConfig") -> ICTExitRuntime:
    return ICTExitRuntime(tracker=BridgeBarFlipTracker(cfg.ict_exit_bridge_bar_flip_state_file))


def _check_zone(zone, symbol: str, zone_direction: int,
                m1_fs: Optional["flip_state.FlipStateResult"] = None) -> Optional[tuple]:
    """(trigger_tag, direct_fire) if this zone has a qualifying
    confirmation this cycle, else None. zone_direction: the direction
    implied by the zone's own role (1 bullish, -1 bearish) -- see module
    docstring's FAST TIER / HTF TIER rules and its own M1 DUAL-ATR FLIP
    REPLACES M1 CISD section."""
    is_fast_tier = zone.timeframe in _FAST_TIER_TIMEFRAMES

    if is_fast_tier:
        m1_flip_direction = flip_state.fresh_flip_direction(m1_fs) if m1_fs is not None else None
        if m1_flip_direction == zone_direction:
            return "M1FLIP", False

    cisd = cisd_bridge.fresh_cisd(symbol, 3)
    if cisd is not None and cisd_bridge.direction_of(cisd) == zone_direction:
        return "M3CD", False

    if is_fast_tier:
        # DIRECT-FIRE -- see module docstring. Checked only when neither
        # M1 nor M3 produced a FRESH event above; a standing match here
        # therefore confirmed on an OLDER bar/state, i.e. was already
        # sitting there before this cycle's freshest bar closed.
        if m1_fs is not None and m1_fs.confirmed.value == zone_direction:
            return "M1FLIP-DIRECT", True
        standing = cisd_bridge.read_cisd(symbol, 3)
        if standing is not None and cisd_bridge.direction_of(standing) == zone_direction:
            return "M3CD-DIRECT", True

    return None


def _touch_is_current(zone, max_age_minutes: float, now: float) -> bool:
    if not zone.retested or zone.retested_source != "live" or zone.retested_at is None:
        return False
    return 0 <= now - zone.retested_at <= max_age_minutes * 60


def _find_closing_zone(zones, position_direction: int, symbol: str, max_age_minutes: float,
                       position_open_time: int, now: float, m1_fs: Optional["flip_state.FlipStateResult"] = None):
    """The first touched, current, opposite-role zone with a qualifying
    confirmation for a position of this direction -- (zone, trigger,
    direct_fire), or None if nothing qualifies."""
    wanted_zone_direction = -position_direction   # opposite role -- see module docstring
    for zone in zones:
        zone_direction = 1 if zone.role == "no_short_buffer" else -1
        if zone_direction != wanted_zone_direction:
            continue
        if not _touch_is_current(zone, max_age_minutes, now):
            continue
        if zone.retested_at is None or zone.retested_at <= position_open_time:
            continue   # touched before this position even opened -- not a new signal for it
        result = _check_zone(zone, symbol, zone_direction, m1_fs)
        if result is None:
            continue
        trigger, direct_fire = result
        return zone, trigger, direct_fire
    return None


def _close_position(cfg: "ExitManagerSymbolConfig", position, source: "WatchedSource", zone, trigger: str,
                    direct_fire: bool) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    zone_source = getattr(zone, "source", "tv")   # BlockZone (old H4-M10 Block) has no source field -- always TV
    trigger_label = f"{zone.timeframe_name} {zone_source.upper()} OB zone, {trigger}"
    print(f"[V7S-XM-ICT] closing {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- {trigger_label}")
    detail = {"rule": "OB zone touch + CISD confirmation (ICT Exit)", "zone_id": zone.zone_id,
              "zone_source": zone_source, "zone_timeframe": zone.timeframe, "zone_top": zone.top,
              "zone_btm": zone.btm, "trigger": trigger, "direct_fire": direct_fire,
              "zone_retested_at": zone.retested_at, "position_open_time": position.time}
    if not cfg.enable_trading:
        print("[V7S-XM-ICT] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "ict_close_decision_only", ticket=position.ticket,
                         target=source.name, direction=_DIR_LABEL[direction], **detail)
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment="V7S-XM-ICT-SQ")
    if not result.ok:
        print(f"[V7S-XM-ICT] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "ict_close_failed", ticket=position.ticket, target=source.name,
                         retcode=result.retcode)
        telegram_alerts.send_if_configured(
            cfg.alerts_bot_token, cfg.alerts_chat_id,
            f"[V7S] EXIT FAILED: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) -- "
            f"ICTEXIT ({trigger_label}) -- retcode={result.retcode} {result.comment}")
        return
    decision_log.log(cfg.decision_log_file, "ict_close_filled", ticket=position.ticket, target=source.name,
                     direction=_DIR_LABEL[direction], **detail)
    trade_journal.TradeJournal(source.journal_file, source.name, cfg.symbol).exit_requested(
        position.ticket, "ICTEXIT", detail)
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V7S] EXIT: {source.name} #{position.ticket} ({_DIR_LABEL[direction]}) closed -- "
        f"ICTEXIT ({trigger_label})")


def run_once(cfg: "ExitManagerSymbolConfig", rt: ICTExitRuntime) -> None:
    now = time.time()
    ict_block = ICTBlockStore(cfg.ict_ob_block_state_file)   # merged TV+MT5 M15/M5/M3 store, read-only
    htf_block = BlockStore(cfg.ict_block_state_file)           # existing RM-ICT Block (H4-M10, TV-only), read-only
    zones = ict_block.zones() + htf_block.zones()
    m1_fs = rt.tracker.update(cfg.symbol, 1)   # M1 structure FLIP, replaces M1 CISD -- see module docstring

    for source in cfg.sources:
        for position in broker.get_positions(cfg.symbol, source.magic_number):
            direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
            found = _find_closing_zone(zones, direction, cfg.symbol, cfg.ict_exit_touch_max_age_minutes,
                                       position.time, now, m1_fs)
            if found is None:
                continue
            zone, trigger, direct_fire = found
            own_tp = source.own_tp_lookup(position.ticket) if source.own_tp_lookup else None
            if broker.is_paused_by_manual_tp(position, own_tp):
                zone_source = getattr(zone, "source", "tv")
                print(f"[V7S-XM-ICT] {source.name} #{position.ticket} has a manual TP set -- "
                      f"skipping auto-close (user is watching it manually)")
                telegram_alerts.send_if_configured(
                    cfg.alerts_bot_token, cfg.alerts_chat_id,
                    f"[V7S] EM SKIPPED (manual TP set): {source.name} #{position.ticket} "
                    f"({_DIR_LABEL[direction]}) -- would have closed via ICTEXIT "
                    f"({zone.timeframe_name} {zone_source.upper()} OB zone, {trigger})")
                continue
            _close_position(cfg, position, source, zone, trigger, direct_fire)
