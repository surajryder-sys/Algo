"""V6-Sentinel Reversal Manager -- main loop, running TWO fully
INDEPENDENT components (STR + ICT) per symbol. Ported from
v5_sentinel/reversal_main.py (2026-09-18), reshaped for V6S's
multi-instrument design: instead of one global RMConfig for a single
hardcoded symbol, this module builds one full runtime bundle (config +
every stateful tracker/store) PER SYMBOL in config.ACTIVE_SYMBOLS, and
the main loop cycles through all of them each poll. XAUUSD is the only
active symbol today; adding another means adding a deliberately-tuned
entry to reversal_config._SYMBOL_DEFAULTS -- no change to this file.

INDEPENDENCE (V5S's own original confirmed design, "no keep them both
seperate... no interference"): STR and ICT do NOT share a magic number or
a position slot -- each has its own (RMSymbolConfig.magic_number/
ict_magic_number), own SL Manager/Trade Manager state files, and runs its
own entirely separate ENTRY/eligibility lifecycle. They can both be open
on the same symbol at the same time, in the same or opposite directions.

CROSS-COMPONENT SQUARE-OFF -- PARTIAL EXCEPTION (added 2026-09-22, a real
live incident: RM-ICT held a SELL open through an RM-STR BUY qualifying,
with no mechanism to close it until the user did so manually). "Square
off on opposite side" still primarily describes EACH component's own
internal lifecycle (an opposite signal from the SAME component squares
off ITS OWN prior position) -- but a qualifying signal on EITHER
component now ALSO squares off the OTHER component's own opposite-
direction position (_process_signal's own other_component/
other_magic_number/other_journal parameters), tagged "SQOFF-CROSS"/"SQX"
to stay distinguishable from a component's own internal square-off
("SQOFF"/"SQ"). Entry, eligibility, SL, and TP remain fully independent
otherwise -- this is the ONE place STR and ICT now interact.

MAGIC NUMBERS -- 26091801 (STR) / 26091802 (ICT), deliberately DIFFERENT
from V5-Sentinel's own 26090701/26090702 (see reversal_config.py's own
docstring for why: V5S is still running live on the same MT5 account).

Run with: python -m v6_sentinel.reversal_main

Summary of the STR component's own full rule set (FULL REDESIGN,
2026-09-19 -- see reversal_entry.py's own docstring for the complete
design; no longer the M15-Primary-Structure-gated system this used to
be):
  - 8 HTF timeframes (D1, H4, H2, H1, M30, M15, M10, M5), each with
    THREE independently-tracked lines: the ATR dual-trail's own two
    lines (STRONG/WEAK/TRAP) PLUS a Supertrend line. M15 and M5 are read
    STRICTLY from the MT5 bridge (user, 2026-09-21 -- no internal
    computation for M1/M3/M5/M15; the flip state comes from a persisted
    BridgeBarFlipTracker on the bridge's own line values); the other
    timeframes are still computed natively from copy_rates.
  - A level is ARMED the moment LIVE price (bid for support, ask for
    resistance) touches it, and stays armed across cycles until traded or
    that SPECIFIC source's own character changes (ATR and Supertrend
    tracked independently per timeframe).
  - Confirmation + entry (reversal_entry.find_signals): once armed and
    untraded, wait for a FRESH CISD confirmation in the matching
    direction from EITHER M3 or M5, whichever fires first -- one
    uniform rule across all 8 timeframes.
  - One trade per flip: each specific line, once traded, is skipped
    until ITS OWN source (ATR or Supertrend) genuinely changes character
    (a fresh flip/trap-resolve), or its own value moves on (a trailing
    line's old touch self-invalidates the moment its value changes).
  - Position lifecycle (_process_signal, shared by BOTH components), run
    once per qualifying signal per cycle -- STR's own signals scanned
    first, then ICT's, always against whatever position is ACTUALLY open
    at that moment (so an earlier signal's own action is visible to the
    next one this same cycle):
      no position        -> open fresh
      opposite direction -> square off + reopen opposite
      same direction     -> ignore, mark traded, alert only (whether
                             still full-size or already partially cut)
  - SL Manager, Trade Manager (70%/15% partial booking), and post-
    breakeven trailing all reuse the same classes UNCHANGED, just under
    each component's own magic number/state files. Trailing always
    follows M3's far line (bridge-only) once past breakeven, regardless
    of which timeframe/trigger opened the trade. If M3's bridge is stale
    when a trailing update would otherwise fire, that cycle's SL update
    is skipped rather than guessed at.
  - Comments: "V6S-RM-STR-{HTF}/{trigger}" on entry (e.g.
    "V6S-RM-STR-H1/3F"), "-P1"/"-P2"/"-SQ" appended for
    partials/square-off. ICT's own positions carry "V6S-RM-ICT-{tag}"
    instead -- since the two components run on separate magic numbers,
    the prefix is purely for readability, not needed to tell them apart
    programmatically.
  - Alerts: a qualifying-but-not-acted-on signal (the "ignore" case
    above) pushes to Telegram (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in
    .env, same shared bot every component in this repo already uses) --
    best-effort, never crashes the loop.

Safety: each symbol's own enable_trading (reversal_config.py,
V6S_RM_{SYMBOL}_ENABLE_TRADING) must be explicitly true in .env for any
order to actually be sent/modified/cancelled for THAT symbol --
independent of every other symbol's/component's own flag.
"""
from __future__ import annotations

import os
import dataclasses
import time
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from v6_sentinel import broker, config, decision_log, flip_state, heartbeat, htf_levels, ict_guard, reversal_entry, reversal_ict, sideways_trapper, sl_manager, trade_journal, trade_manager
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v6_sentinel.bridge_flip import m3_far_line
from v6_sentinel.alerts import send_alert as _send_alert
from v6_sentinel.reversal_config import RMSymbolConfig, load_symbol_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_COMPONENTS = ("STR", "ICT")  # the two Reversal Manager components sharing this magic number/position slot
_SOURCE_SHORT = {"ATR": "ATR", "SUPERTREND": "ST"}


def _tag(sig: "reversal_entry.ReversalSignal") -> str:
    return f"{htf_levels.TIMEFRAME_NAMES[sig.timeframe_minutes]}/{_SOURCE_SHORT[sig.source]}/{sig.trigger}"


def _ict_tag(sig: "reversal_ict.ICTSignal") -> str:
    return f"{sig.timeframe_name}/{sig.trigger}"


def _extract_tag(comment: str) -> str:
    """Mirrors this project's other _extract_tag() implementations but
    for the "V6S-RM-{STR|ICT}-{tag}-..." shape -- used to carry an
    entry's own tag forward onto its later partial-booking comments.
    Joins everything after the fixed prefix (not just one part) so a
    dash-containing tag survives intact."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 4 and parts[0] == "V6S" and parts[1] == "RM" and parts[2] in _COMPONENTS:
        return "-".join(parts[3:])
    return "UNK"


def _entry_comment(component: str, tag: str) -> str:
    return f"V6S-RM-{component}-{tag}"


def _action_comment(component: str, tag: str, action_code: str) -> str:
    return f"V6S-RM-{component}-{tag}-{action_code}"


def _open_position(cfg: RMSymbolConfig, sticky: ict_guard.ICTGuardStickyStore, component: str, magic_number: int,
                   direction: int, sl: float, tag: str, ref_desc: str,
                   journal: Optional[trade_journal.TradeJournal] = None, logic: Optional[dict] = None) -> bool:
    """Returns True if the entry actually went through (filled, or
    enable_trading is False so it's decision-only and conceptually
    "accepted") -- False only on a genuine order rejection while live.
    The caller must NOT mark eligibility consumed on a False return -- a
    failed order (e.g. retcode 10044 "session closed" right at market
    reopen) must not consume "one trade per flip" eligibility anyway,
    silently dropping a genuinely valid setup that never actually got a
    position. Serves both STR and ICT -- component picks the log prefix
    and comment prefix, magic_number is THAT component's own (they're
    independent, see module docstring), ref_desc is just a human-readable
    description of whatever triggered this (an HTF level's value for
    STR, a zone's own range for ICT).

    ICT GUARD DELIBERATELY NOT APPLIED RIGHT NOW (confirmed with the
    user 2026-09-18, "we are not using any guard here as of now [for
    reversal manager]... later we might add it") -- V5S applied this
    proximity check to STR's own entries only (never ICT's, since ICT's
    entries are already sourced FROM these exact zones). `sticky` is
    still threaded through and instantiated (see _build_runtime()) so
    reinstating the check later is a small, isolated change."""
    comment = _entry_comment(component, tag)
    print(f"[V6S-{component}-ENTRY] {_DIR_LABEL[direction]} ({tag}) {ref_desc} sl={sl:.3f}")
    if not cfg.enable_trading:
        print(f"[V6S-{component}-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, sl=sl)
        return True
    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V6S-{component}-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, retcode=result.retcode,
                         broker_comment=result.comment)
        return False
    print(f"[V6S-{component}-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", component=component, direction=_DIR_LABEL[direction],
                     tag=tag, ref=ref_desc, sl=sl, ticket=result.ticket)
    if journal is not None and result.ticket is not None:
        journal.entry(result.ticket, _DIR_LABEL[direction], cfg.lots, sl, comment, logic or {})
    return True


def _close_position(cfg: RMSymbolConfig, component: str, position, action_label: str, tag: str, action_code: str,
                    journal: Optional[trade_journal.TradeJournal] = None, detail: Optional[dict] = None) -> bool:
    print(f"[V6S-{component}-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print(f"[V6S-{component}-EXIT] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(component, tag, action_code))
    if not result.ok:
        print(f"[V6S-{component}-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "close_failed", component=component, ticket=position.ticket,
                         action=action_label, retcode=result.retcode)
        return False
    decision_log.log(cfg.decision_log_file, "close_filled", component=component, ticket=position.ticket,
                     action=action_label, tag=tag)
    if journal is not None:
        journal.exit_requested(position.ticket, action_label, detail)
    return True


def _run_sl_manager(cfg: RMSymbolConfig, component: str, mgr: sl_manager.SLManager,
                    tm_mgr: trade_manager.TradeManager, position,
                    tracker: Optional[BridgeBarFlipTracker] = None,
                    journal: Optional[trade_journal.TradeJournal] = None) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    far = m3_far_line(cfg.symbol, direction)
    if far is None:
        print(f"[V6S-{component}-SL] M3 bridge stale/missing -- skipping SL update this cycle")
        return
    current_sl = position.sl if position.sl else None

    # Pre-breakeven flip check (2026-09-22) -- see sl_manager.py's own docstring.
    # M3 always, matching m3_far_line's own hardcoded trailing timeframe above.
    m3_fs = tracker.update(cfg.symbol, 3) if tracker is not None else None
    with_flip = flip_state.with_direction_flip_after(m3_fs, direction, 3, position.time)

    # Breakeven gates on Trade Manager's own partial-booking progress OR
    # a standalone points-in-favor check -- see sl_manager.py's own
    # docstring. Applies to both STR and ICT (whichever tm_mgr the caller
    # passes in for this SAME ticket).
    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far,
                           tm_mgr.is_partially_cut(position.ticket), with_direction_flip_after_entry=with_flip)
    if proposed is None:
        return
    print(f"[V6S-{component}-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print(f"[V6S-{component}-SL] enable_trading is false -- decision only, no modify sent")
        return
    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
        if journal is not None:
            journal.sl_move(position.ticket, current_sl, proposed, current_price)
    else:
        print(f"[V6S-{component}-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _run_trade_manager(cfg: RMSymbolConfig, component: str, mgr: trade_manager.TradeManager, position,
                       journal: Optional[trade_journal.TradeJournal] = None) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    has_tp = broker.has_manual_tp(position)

    symbol_info = mt5.symbol_info(cfg.symbol)
    volume_step = symbol_info.volume_step if symbol_info is not None else 0.01

    outcome = mgr.evaluate(position.ticket, direction, position.price_open, current_price,
                           position.volume, has_tp, volume_step, entry_comment=position.comment)
    if outcome is None:
        return
    volume, label = outcome
    action_code = "P1" if label == "partial1" else "P2"
    # component is passed in by the caller -- each component only ever
    # manages its OWN magic-number-scoped position (see run_once()), so
    # which one owns this position is already known from context, no
    # need to infer it back from the comment any more.
    entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")

    print(f"[V6S-{component}-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print(f"[V6S-{component}-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(component, entry_tag, action_code))
    if not result.ok:
        print(f"[V6S-{component}-TM] partial close failed: retcode={result.retcode} comment={result.comment}")
    elif journal is not None:
        journal.partial(position.ticket, label, volume, current_price)


def _process_signal(cfg: RMSymbolConfig, sticky: ict_guard.ICTGuardStickyStore, component: str, magic_number: int,
                    direction: int, sl: float, tag: str, ref_desc: str, mark_traded, on_redundant,
                    journal: Optional[trade_journal.TradeJournal] = None, logic: Optional[dict] = None,
                    signal_tf: Optional[int] = None, other_component: Optional[str] = None,
                    other_magic_number: Optional[int] = None,
                    other_journal: Optional[trade_journal.TradeJournal] = None) -> None:
    """Acts on ONE signal against whatever position is ACTUALLY open right
    now on THIS component's own magic number (re-queried, so an earlier
    signal's own action this same cycle is visible here). Shared CODE
    between both Reversal Manager components (STR and ICT), but each call
    is scoped entirely to its own magic_number for entry/eligibility --
    they never share those (see module docstring's own INDEPENDENCE
    section). mark_traded is a zero-arg callback (STR's own
    store.mark_traded(tf, direction) or ICT's own
    eligibility.mark_traded(zone_id), bound by the caller) so this
    function stays agnostic to which component's own eligibility scheme
    it's consuming. Only calls it once the entry actually went through
    (or, for the "already open, same direction" case, always -- no order
    is even attempted there) -- a failed order_send must never consume
    eligibility anyway, silently dropping a genuinely valid setup that
    never got a position (see _open_position's own docstring).

    on_redundant(direction, tag, ref_desc, ticket) is called instead of
    alerting directly -- the caller (run_once) collects these across the
    whole cycle and sends ONE aggregated Telegram message per component
    instead of one per matching zone/level (a single trigger event can
    legitimately match dozens of zones/levels at once); decision_log
    still gets one line per signal for full audit granularity, only the
    Telegram side is collapsed.

    other_component/other_magic_number/other_journal: CROSS-COMPONENT
    SQUARE-OFF (added 2026-09-22, a real live incident: RM-ICT held a SELL
    open through an RM-STR BUY qualifying -- STR and ICT's own magic
    numbers meant STR's square-off never even looked at ICT's position,
    so it sat open until the user closed it manually). This PARTIALLY
    REVERSES the module docstring's own INDEPENDENCE design (confirmed
    2026-09-19, "no keep them both seperate... no interference") --
    ONLY for this one thing: a qualifying signal on either component now
    ALSO squares off the OTHER component's own opposite-direction
    position, tagged "SQOFF-CROSS"/"SQX" to stay distinguishable from a
    component's own internal square-off ("SQOFF"/"SQ") in logs/journal/
    comment. Entry, eligibility, SL, and TP for each component otherwise
    remain fully independent -- unlike the own-component square-off
    above, a failed cross-close does NOT block this signal's own entry
    (the two trades are still nominally independent; only prints/logs the
    failure)."""
    positions = broker.get_positions(cfg.symbol, magic_number)
    want_type = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
    same = [p for p in positions if p.type == want_type]
    opposite = [p for p in positions if p.type != want_type]

    # TIMEFRAME HIERARCHY REVERTED (user, 2026-09-22, after seeing RM-STR hold an M15 BUY and an M10
    # SELL open at once for ~39 minutes on real data -- confirmed live 2026-09-21: "m3/m1 doesnt have
    # access to close the htf trade"): back to the original rule -- ANY qualifying opposite-direction
    # signal squares off this component's own opposite position, regardless of relative timeframe.
    for position in opposite:
        position_tf = journal.timeframe_minutes(position.ticket) if journal is not None else None
        if not _close_position(cfg, component, position, "SQOFF", tag, "SQ", journal,
                               {"rule": "opposite-direction reversal signal squared it off",
                                "new_signal": tag, "new_direction": _DIR_LABEL[direction],
                                "signal_tf": signal_tf, "position_tf": position_tf}):
            return      # could not close it -> do not open and do not consume this setup

    if other_magic_number is not None:
        for position in broker.get_positions(cfg.symbol, other_magic_number):
            if position.type == want_type:
                continue   # same direction as this signal -- not opposite, leave it alone
            _close_position(cfg, other_component, position, "SQOFF-CROSS", tag, "SQX", other_journal,
                            {"rule": f"opposite-direction {component} reversal signal squared it off (cross-component)",
                             "new_signal": tag, "new_direction": _DIR_LABEL[direction], "signal_tf": signal_tf})

    if same:
        # SAME direction already open, whether still full-size or already partially cut -- NO-OP: no
        # closing leftover and entering full qty again.
        position = same[0]
        mark_traded()
        print(f"[V6S-{component}] {tag} qualifies ({_DIR_LABEL[direction]}) but a {_DIR_LABEL[direction]} "
              f"position is already open on #{position.ticket} -- marked traded, no new entry")
        decision_log.log(cfg.decision_log_file, "redundant_signal", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, existing_ticket=position.ticket)
        on_redundant(direction, tag, ref_desc, position.ticket)
        return

    if _open_position(cfg, sticky, component, magic_number, direction, sl, tag, ref_desc, journal, logic):
        mark_traded()


@dataclass
class _SymbolRuntime:
    """Every stateful object one symbol's own STR+ICT Reversal Manager
    needs -- one of these per symbol in config.ACTIVE_SYMBOLS. Building
    this out per symbol (rather than one flat set of module-level
    globals, V5S's own single-symbol shape) is what makes this file
    genuinely multi-instrument: adding a symbol means constructing
    another _SymbolRuntime, never touching run_once() or any class
    above. No flip/stale-alert trackers any more (2026-09-19)
    -- neither STR nor ICT depend on the live ATR-dual bridge for entry
    logic any more, both are now native-copy_rates + CISD driven."""
    cfg: RMSymbolConfig
    sl_mgr_str: sl_manager.SLManager
    tm_mgr_str: trade_manager.TradeManager
    sl_mgr_ict: sl_manager.SLManager
    tm_mgr_ict: trade_manager.TradeManager
    store: htf_levels.LevelEligibilityStore
    ict_eligibility: reversal_ict.ICTEligibilityStore
    sticky: ict_guard.ICTGuardStickyStore
    tracker: Optional[BridgeBarFlipTracker] = None    # ATR flip state for the bridge-sourced M15/M5 levels
    last_tick_msc: int = 0                             # time_msc of the newest tick already looked at (touch extremes)
    journal_str: Optional[trade_journal.TradeJournal] = None    # per-trade entry/exit logic, one per component
    journal_ict: Optional[trade_journal.TradeJournal] = None
    trapper_str: Optional[sideways_trapper.SidewaysTrapper] = None   # RM-STR only, see sideways_trapper.py


def _build_runtime(symbol: str) -> _SymbolRuntime:
    cfg = load_symbol_config(symbol)
    return _SymbolRuntime(
        cfg=cfg,
        sl_mgr_str=sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer),
        tm_mgr_str=trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                              cfg.partial2_trigger_points, cfg.partial2_fraction),
        sl_mgr_ict=sl_manager.SLManager(cfg.ict_sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer),
        tm_mgr_ict=trade_manager.TradeManager(cfg.ict_state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                              cfg.partial2_trigger_points, cfg.partial2_fraction),
        store=htf_levels.LevelEligibilityStore(cfg.levels_state_file),
        ict_eligibility=reversal_ict.ICTEligibilityStore(cfg.ict_eligibility_state_file),
        sticky=ict_guard.ICTGuardStickyStore(cfg.ict_guard_sticky_state_file),
        tracker=BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file),
        journal_str=trade_journal.TradeJournal(cfg.str_trade_journal_file, "RM-STR", cfg.symbol),
        journal_ict=trade_journal.TradeJournal(cfg.ict_trade_journal_file, "RM-ICT", cfg.symbol),
        trapper_str=sideways_trapper.SidewaysTrapper(cfg.sideways_trapper_state_file),
    )


def run_once(rt: _SymbolRuntime) -> None:
    cfg = rt.cfg
    htf_states = htf_levels.compute_all_htf_states(cfg.symbol, rt.tracker)
    bid, ask = broker.get_tick_price(cfg.symbol)
    # Touch arming stays LIVE-tick (bid/ask against HTF levels) -- only
    # the CISD confirmation itself is bar-close-gated.
    bid_low, ask_high = bid, ask
    if rt.last_tick_msc:
        lo, hi, newest = broker.price_extremes_since(cfg.symbol, rt.last_tick_msc)
        if lo is not None:
            bid_low, ask_high = min(bid, lo), max(ask, hi)
        rt.last_tick_msc = max(rt.last_tick_msc, newest)
    else:                                              # first cycle: start looking from now, never from history
        tick = mt5.symbol_info_tick(cfg.symbol)
        rt.last_tick_msc = int(tick.time_msc) if tick is not None else 0
    reversal_entry.scan_touches(htf_states, rt.store, bid, ask, bid_low, ask_high)

    # M15 structure (ATR-dual confirmed direction) -- read purely for the Sideways Trapper's own reset
    # condition (sideways_trapper.py); RM-STR's own entry/touch logic never uses M15 for anything else.
    m15_fs = rt.tracker.update(cfg.symbol, 15) if rt.tracker is not None else None
    m15_structure = m15_fs.confirmed.value if m15_fs is not None else None
    # M5 structure -- ADDED 2026-09-22, feeds ONLY RM-ICT's own M1-CISD gate below (see
    # reversal_ict._check_zone()'s own docstring); RM-STR's own logic never uses this.
    m5_fs = rt.tracker.update(cfg.symbol, 5) if rt.tracker is not None else None
    m5_structure = m5_fs.confirmed.value if m5_fs is not None else None

    str_signals = reversal_entry.find_signals(cfg.symbol, htf_states, rt.store, cfg.sl_buffer, bid, ask,
                                              rt.trapper_str, cfg.sideways_trap_min_distance_points, m15_structure)
    # RM-ICT (second component) -- OB-zone (NLB/NSB Block) touch +
    # post-touch CISD confirmation, fully independent of the M15/tracker
    # machinery STR uses. See reversal_ict.py's own docstring for the
    # full entry rule (a complete redesign, 2026-09-18 -- no longer the
    # M15-Primary-Structure-gated system). m5_structure/m15_structure feed
    # ONLY the M1-CISD gate (2026-09-22) -- every other confirmation
    # timeframe (M3/M5) is unaffected by them.
    ict_signals = reversal_ict.find_ict_signals(cfg.symbol, cfg.nlb_nsb_block_state_file, rt.ict_eligibility,
                                                cfg.sl_buffer, bid, ask, cfg.ict_sl_override_points,
                                                cfg.ict_touch_max_age_minutes,
                                                m5_structure=m5_structure, m15_structure=m15_structure)

    # Logged BEFORE _process_signal acts on them, listing EVERY qualifying
    # signal this cycle (not just the one that becomes a real position) --
    # a multi-zone-match cycle (several signals qualifying the same M3
    # flip at once) would otherwise leave no record of WHICH exact
    # zone/level became the real trade vs. which got marked redundant.
    # _process_signal runs str_signals/ict_signals in THIS SAME order
    # below, so index 0 here is always the one that actually got the
    # position (assuming no position was already open).
    if str_signals:
        decision_log.log(cfg.decision_log_file, "str_signals_found", count=len(str_signals), signals=[
            {"tf_minutes": s.timeframe_minutes, "source": s.source, "line_no": s.line_no,
             "direction": _DIR_LABEL[s.direction], "trigger": s.trigger, "level_value": s.level_value,
             "sl": s.sl, "sl_source": s.sl_source}
            for s in str_signals])
    if ict_signals:
        decision_log.log(cfg.decision_log_file, "ict_signals_found", count=len(ict_signals), signals=[
            {"zone_id": s.zone_id, "timeframe_name": s.timeframe_name, "direction": _DIR_LABEL[s.direction],
             "trigger": s.trigger, "zone_top": s.zone_top, "zone_btm": s.zone_btm, "sl": s.sl,
             "sl_source": s.sl_source} for s in ict_signals])

    # Each component's own magic-number-scoped positions, pruned/acted on
    # entirely independently -- see module docstring, this is not a
    # shared position slot.
    str_positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    str_exits = rt.journal_str.reconcile({p.ticket for p in str_positions})
    for exit_rec in str_exits:
        if exit_rec.get("exit_reason") == "SL_HIT" and rt.trapper_str is not None:
            exit_direction = 1 if exit_rec.get("direction") == "BUY" else -1
            rt.trapper_str.record_sl_hit(exit_direction, exit_rec["entry_price"], m15_structure)
            print(f"[V6S-STR] Sideways Trapper recorded {exit_rec['direction']} SL-hit @ "
                  f"{exit_rec['entry_price']:.3f} -- next {exit_rec['direction']} needs to be "
                  f"{cfg.sideways_trap_min_distance_points:.1f}+ points away")
    rt.sl_mgr_str.prune({p.ticket for p in str_positions})
    rt.tm_mgr_str.prune({p.ticket for p in str_positions})
    ict_positions = broker.get_positions(cfg.symbol, cfg.ict_magic_number)
    rt.journal_ict.reconcile({p.ticket for p in ict_positions})
    rt.sl_mgr_ict.prune({p.ticket for p in ict_positions})
    rt.tm_mgr_ict.prune({p.ticket for p in ict_positions})

    # redundant_signal alerts collected here instead of sent inline --
    # a single trigger event can legitimately match dozens of untested
    # ICT zones at once, so alerting once EACH would spam.
    redundant: dict[str, list[tuple[int, str, str, int]]] = {}

    def _on_redundant(component: str, direction: int, tag: str, ref_desc: str, ticket: int) -> None:
        redundant.setdefault(component, []).append((direction, tag, ref_desc, ticket))

    for sig in str_signals:
        _process_signal(cfg, rt.sticky, "STR", cfg.magic_number, sig.direction, sig.sl, _tag(sig),
                        f"level={sig.level_value:.3f}",
                        lambda tf=sig.timeframe_minutes, src=sig.source, d=sig.direction: rt.store.mark_traded(tf, src, d),
                        lambda direction, tag, ref_desc, ticket: _on_redundant("STR", direction, tag, ref_desc, ticket),
                        rt.journal_str, {**dataclasses.asdict(sig), "rule": "HTF line touch + CISD",
                                         "direction": _DIR_LABEL[sig.direction], "bid": bid, "ask": ask},
                        sig.timeframe_minutes, other_component="ICT", other_magic_number=cfg.ict_magic_number,
                        other_journal=rt.journal_ict)
    for sig in ict_signals:
        _process_signal(cfg, rt.sticky, "ICT", cfg.ict_magic_number, sig.direction, sig.sl, _ict_tag(sig),
                        f"zone=[{sig.zone_btm:.3f}-{sig.zone_top:.3f}]",
                        lambda zid=sig.zone_id, top=sig.zone_top, btm=sig.zone_btm:
                            rt.ict_eligibility.mark_traded(zid, top, btm),
                        lambda direction, tag, ref_desc, ticket: _on_redundant("ICT", direction, tag, ref_desc, ticket),
                        rt.journal_ict, {**dataclasses.asdict(sig), "rule": "OB zone touch + CISD",
                                         "direction": _DIR_LABEL[sig.direction], "bid": bid, "ask": ask},
                        trade_journal.timeframe_of_logic({"zone_id": sig.zone_id, "timeframe_name": sig.timeframe_name}),
                        other_component="STR", other_magic_number=cfg.magic_number, other_journal=rt.journal_str)

    for component, entries in redundant.items():
        direction, first_tag, _first_ref, ticket = entries[0]
        extra = f" (+{len(entries) - 1} more matching this cycle)" if len(entries) > 1 else ""
        msg = (f"[V6S-{component}] {cfg.symbol} {_DIR_LABEL[direction]} qualifies e.g. {first_tag}{extra} but a "
              f"position is already open on #{ticket} -- marked traded, no new entry")
        print(msg)
        _send_alert(msg)

    # EVERY open position of each component is managed (SL trailing + partials), not just the first --
    # normally at most one per component now that any opposite signal squares off the other one again,
    # but this still covers the transient case where a square-off's own close failed and both are open.
    for str_position in broker.get_positions(cfg.symbol, cfg.magic_number):
        _run_sl_manager(cfg, "STR", rt.sl_mgr_str, rt.tm_mgr_str, str_position, rt.tracker, rt.journal_str)
        _run_trade_manager(cfg, "STR", rt.tm_mgr_str, str_position, rt.journal_str)

    for ict_position in broker.get_positions(cfg.symbol, cfg.ict_magic_number):
        _run_sl_manager(cfg, "ICT", rt.sl_mgr_ict, rt.tm_mgr_ict, ict_position, rt.tracker, rt.journal_ict)
        _run_trade_manager(cfg, "ICT", rt.tm_mgr_ict, ict_position, rt.journal_ict)


def main() -> None:
    runtimes = [_build_runtime(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for rt in runtimes:
        cfg = rt.cfg
        print(f"[V6S-RM] {cfg.symbol} starting -- str_magic={cfg.magic_number} "
              f"ict_magic={cfg.ict_magic_number} enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")
        # One shared MT5 terminal connection (mt5.initialize() is
        # idempotent) -- symbol_select() is what's actually per-symbol,
        # so this must still be called once per active symbol.
        broker.connect(cfg.symbol, cfg.mt5_terminal_path, cfg.mt5_login, cfg.mt5_password, cfg.mt5_server)

    try:
        while True:
            for rt in runtimes:
                try:
                    run_once(rt)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-RM] {rt.cfg.symbol} cycle error: {exc!r}")
                # See heartbeat.py's own docstring -- proves the loop
                # itself is alive, regardless of whether this cycle
                # raised.
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(rt.cfg.poll_seconds for rt in runtimes))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
