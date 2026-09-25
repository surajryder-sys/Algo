"""V7-Sentinel Reversal Manager -- main loop, running RM-ICT (OB-zone
touch + CISD confirmation) per symbol. Ported from
v5_sentinel/reversal_main.py (2026-09-18), reshaped for V7S's
multi-instrument design: instead of one global RMConfig for a single
hardcoded symbol, this module builds one full runtime bundle (config +
every stateful tracker/store) PER SYMBOL in config.ACTIVE_SYMBOLS, and
the main loop cycles through all of them each poll. XAUUSD is the only
active symbol today; adding another means adding a deliberately-tuned
entry to reversal_config._SYMBOL_DEFAULTS -- no change to this file.

RM-STR REMOVED (2026-09-25, user: "lets remove RM-STR completely, we
dont want reversal manager based based on ATR"): this module used to run
TWO fully independent components sharing this process -- RM-STR (HTF
ATR-dual/Supertrend line touch + CISD confirmation, magic 26092402) and
RM-ICT (OB zone touch + CISD confirmation, magic 26092403). RM-STR's own
entry approach is gone entirely, not just disabled -- its own
touch-scanning (reversal_entry.py's find_signals()/_check_level(), now
removed from that module too -- only its shared scan_touches() utility
remains, still used by exit_manager_ltf.py), its own position-management
loop, its own Sideways Trapper usage, and the cross-component
square-off feature (added 2026-09-22 specifically so STR and ICT could
close each other's opposite position) are all gone -- with only ICT
left, there is no "other component" to cross-close against. Confirmed 0
open RM-STR positions before removal, so nothing was orphaned. Magic
26092402 is retired -- this process will never place another order under
it.

M3-REVERSAL-TRADE EARLY EXIT (added 2026-09-23, see _check_m3_reversal_exit's
own docstring for the full rule): a position entered on a fresh M3 CISD
("M3CD" -- a genuine reversal-against-the-prevailing-bias trade) closes
early if M3 later genuinely reverses (a fresh, opposite-direction M3 CISD)
while BOTH M5 and M15's own confirmed structure agree with that new
direction ("parent and primary timeframe agreement", user's own words,
asked for twice -- deliberately AND, not the M1 entry gate's own OR).
Otherwise the position waits for its own SL, exactly as before this rule.
Lives here (not Exit Manager) since it needs the position's own ENTRY
trigger, which only this process's own journal records.

MAGIC NUMBERS -- RM-ICT is 26092403 (RM-STR's own 26092402 retired, see
above), deliberately DIFFERENT from V5-Sentinel's own 26090701/26090702
(see reversal_config.py's own docstring for why: V5S is still running
live on the same MT5 account).

Run with: python -m v7_sentinel.reversal_main

Summary of RM-ICT's own full rule set (see reversal_ict.py's own
docstring for the complete design):
  - OB zone (NLB/NSB Block + the merged TV+MT5 M15/M5/M3 store) touch +
    post-touch CISD confirmation (or the M1/M3 direct-fire exception).
  - One trade per zone (ICTEligibilityStore), plus a role-aware
    geometric overlap backstop against the same real zone re-firing
    under a churned identity.
  - Position lifecycle (_process_signal): no position -> open fresh.
    Opposite direction -> square off + reopen. Same direction -> ignore,
    mark traded, alert only.
  - SL Manager, Trade Manager (70%/15% partial booking), and post-
    breakeven trailing all reuse the same classes UNCHANGED. Trailing
    always follows M3's far line (bridge-only) once past breakeven,
    regardless of which timeframe/trigger opened the trade. If M3's
    bridge is stale when a trailing update would otherwise fire, that
    cycle's SL update is skipped rather than guessed at.
  - Comments: "V7S-RM-ICT-{tag}" on entry, "-P1"/"-P2"/"-SQ" appended
    for partials/square-off.
  - Alerts: a qualifying-but-not-acted-on signal (the "ignore" case
    above) pushes to Telegram via the shared V7S alerts bot
    (V7S_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID in .env -- the only Telegram
    bot V7-Sentinel uses anywhere, 2026-09-24) -- best-effort, never
    crashes the loop.

Safety: each symbol's own enable_trading (reversal_config.py,
V7S_RM_{SYMBOL}_ENABLE_TRADING) must be explicitly true in .env for any
order to actually be sent/modified/cancelled for THAT symbol --
independent of every other symbol's/component's own flag.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from v7_sentinel import broker, cisd_bridge, config, decision_log, flip_state, heartbeat, ict_guard, \
    position_size_manager, reversal_ict, session_manager, sl_manager, telegram_alerts, trade_journal, trade_manager
from v7_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v7_sentinel.bridge_flip import m3_far_line
from v7_sentinel.reversal_config import RMSymbolConfig, load_symbol_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}
_COMPONENTS = ("ICT",)  # historically ("STR", "ICT") -- RM-STR removed 2026-09-25, see module docstring

# Bridge-lag alert threshold -- see trend_main.py's own ENTRY_BRIDGE_LAG_ALERT_SECONDS docstring
# for the full rationale (2026-09-23, user: "instead send me an alert on telegram / instead of
# adding staleness", then "yes extend" to RM-STR/RM-ICT). Entries are gated on
# cisd_bridge.fresh_cisd(), so the OnCalculate-lag risk still applies to RM-ICT alone.
ENTRY_BRIDGE_LAG_ALERT_SECONDS = 30.0


def _cisd_tf_from_trigger(trigger: str) -> Optional[int]:
    """"M3CD" -> 3 -- the CISD timeframe embedded in every CISD-based trigger tag this module
    uses. None for a non-CISD trigger (e.g. "M1FLIP"/"M1FLIP-DIRECT", 2026-09-24 -- structure
    flips have no CISD bridge of their own to report a lag against, so the bridge-lag alert
    below simply never applies to them, by design) or anything else that doesn't match the
    expected "M{n}CD..." shape."""
    if not trigger.startswith("M") or "CD" not in trigger:
        return None
    try:
        return int(trigger.split("CD")[0][1:])
    except ValueError:
        return None


def _ict_tag(sig: "reversal_ict.ICTSignal") -> str:
    return f"{sig.timeframe_name}/{sig.trigger}"


def _extract_tag(comment: str) -> str:
    """Mirrors this project's other _extract_tag() implementations but
    for the "V7S-RM-ICT-{tag}-..." shape -- used to carry an entry's own
    tag forward onto its later partial-booking comments. Joins everything
    after the fixed prefix (not just one part) so a dash-containing tag
    survives intact."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 4 and parts[0] == "V7S" and parts[1] == "RM" and parts[2] in _COMPONENTS:
        return "-".join(parts[3:])
    return "UNK"


def _entry_comment(component: str, tag: str) -> str:
    return f"V7S-RM-{component}-{tag}"


def _action_comment(component: str, tag: str, action_code: str) -> str:
    return f"V7S-RM-{component}-{tag}-{action_code}"


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
    position.

    ICT GUARD DELIBERATELY NOT APPLIED RIGHT NOW (confirmed with the
    user 2026-09-18, "we are not using any guard here as of now [for
    reversal manager]... later we might add it") -- RM-ICT's entries are
    already sourced FROM these exact zones. `sticky` is still threaded
    through and instantiated (see _build_runtime()) so reinstating the
    check later is a small, isolated change."""
    comment = _entry_comment(component, tag)
    # Position Size Manager -- halved lots during the 23:00-04:00 IST window (position_size_manager.py).
    lots = cfg.night_lots if position_size_manager.is_night() else cfg.lots
    print(f"[V7S-{component}-ENTRY] {_DIR_LABEL[direction]} ({tag}) {ref_desc} sl={sl:.3f} lots={lots}")
    if not cfg.enable_trading:
        print(f"[V7S-{component}-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, sl=sl, lots=lots)
        return True
    result = broker.send_market_order(cfg.symbol, direction, lots, sl, magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V7S-{component}-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, retcode=result.retcode,
                         broker_comment=result.comment)
        return False
    print(f"[V7S-{component}-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", component=component, direction=_DIR_LABEL[direction],
                     tag=tag, ref=ref_desc, sl=sl, ticket=result.ticket, lots=lots)
    if journal is not None and result.ticket is not None:
        journal.entry(result.ticket, _DIR_LABEL[direction], lots, sl, comment, logic or {})
    # ICT signals carry timeframe_name (the zone's own base tf, e.g. "H1") + trigger (whichever
    # confirmation fired it, e.g. "M3CD" or "M1FLIP") -- see reversal_ict.ICTSignal.
    tf_name = (logic or {}).get("timeframe_name")
    trigger = (logic or {}).get("trigger")
    tf_desc = f"{tf_name}/{trigger}" if (tf_name and trigger) else (tf_name or "tf n/a")
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V7S] ENTRY: RM-{component} {_DIR_LABEL[direction]} {cfg.symbol} #{result.ticket} ({tag}) -- "
        f"{tf_desc} -- {(logic or {}).get('rule', ref_desc)} -- sl={sl:.3f}")

    # Bridge-lag alert (see module's own ENTRY_BRIDGE_LAG_ALERT_SECONDS docstring) -- only ever
    # applies to a genuine CISD-based trigger (M3CD/M5CD); _cisd_tf_from_trigger() already returns
    # None for M1FLIP/M1FLIP-DIRECT (no CISD bridge of their own to be lagging), so those are
    # naturally skipped here with no extra check needed.
    confirm_bar_time = (logic or {}).get("confirm_bar_time")
    cisd_tf = _cisd_tf_from_trigger(trigger) if trigger else None
    if confirm_bar_time is not None and cisd_tf:
        bar_close_time = confirm_bar_time + cisd_tf * 60
        lag = time.time() - bar_close_time
        if lag > ENTRY_BRIDGE_LAG_ALERT_SECONDS:
            telegram_alerts.send_if_configured(
                cfg.alerts_bot_token, cfg.alerts_chat_id,
                f"[V7S] BRIDGE LAG: RM-{component} {_DIR_LABEL[direction]} {cfg.symbol} #{result.ticket} -- "
                f"the M{cisd_tf} CISD's own bar closed {lag:.0f}s before this entry actually fired -- "
                f"the M{cisd_tf} CISD bridge indicator's OnCalculate likely fell behind real bar "
                f"closes, so the entry price may be far from that bar's own open/close. Check the "
                f"M{cisd_tf} chart/indicator.")
    return True


def _close_position(cfg: RMSymbolConfig, component: str, position, action_label: str, tag: str, action_code: str,
                    journal: Optional[trade_journal.TradeJournal] = None, detail: Optional[dict] = None) -> bool:
    print(f"[V7S-{component}-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print(f"[V7S-{component}-EXIT] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(component, tag, action_code))
    if not result.ok:
        print(f"[V7S-{component}-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
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
        print(f"[V7S-{component}-SL] M3 bridge stale/missing -- skipping SL update this cycle")
        return
    current_sl = position.sl if position.sl else None

    # Pre-breakeven flip check (2026-09-22) -- see sl_manager.py's own docstring.
    # M3 always, matching m3_far_line's own hardcoded trailing timeframe above.
    m3_fs = tracker.update(cfg.symbol, 3) if tracker is not None else None
    with_flip = flip_state.with_direction_flip_after(m3_fs, direction, 3, position.time)

    # Breakeven gates on Trade Manager's own partial-booking progress OR
    # a standalone points-in-favor check -- see sl_manager.py's own docstring.
    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far,
                           tm_mgr.is_partially_cut(position.ticket), with_direction_flip_after_entry=with_flip)
    if proposed is None:
        return
    print(f"[V7S-{component}-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print(f"[V7S-{component}-SL] enable_trading is false -- decision only, no modify sent")
        return
    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
        if journal is not None:
            journal.sl_move(position.ticket, current_sl, proposed, current_price)
    else:
        print(f"[V7S-{component}-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _check_m3_reversal_exit(cfg: RMSymbolConfig, component: str, position, tracker: Optional[BridgeBarFlipTracker],
                            journal: Optional[trade_journal.TradeJournal]) -> bool:
    """Returns True if this position was just closed by this rule (the
    caller should skip any further management of it this cycle -- the
    ticket no longer exists)."""
    """M3-TRIGGERED REVERSAL TRADE EARLY EXIT (added 2026-09-23, user's own
    real incident: a LONG opened by RM-ICT on a fresh M3 BULLISH CISD while
    M5/M15 were ALREADY bearish -- a genuine reversal-against-the-prevailing-
    bias trade. Bias Exit Manager's own "fresh opposite M5/M15 CISD AFTER
    entry" rule can never close a trade like this: M5/M15 were already
    bearish BEFORE entry, not freshly reversing after it, so it would
    otherwise just sit open until its own SL, however long that takes.
    User's own words: "if an m3 cisd opens a reversal trade, and if same m3
    reverses with opposite cisd, agreeing with parent and primary, then we
    can close the trade based on m3 bearish cisd with parent and primary
    timeframe agreement, else this will wait for sl."

    Lives in RM's OWN process (not Exit Manager) because it needs to know
    the position's own ENTRY trigger, which only RM's own journal records
    (TradeJournal.entry_trigger()) -- Exit Manager has no such visibility
    into how a position was originally opened, only its live state.

    RULE, precisely:
      1. This position's own entry_trigger must be EXACTLY "M3CD" (not
         M5CD, M1FLIP, or M1FLIP-DIRECT) -- only M3-triggered entries qualify.
      2. A FRESH M3 CISD (cisd_bridge.fresh_cisd(), not a standing state --
         a genuine new confirmation event, "same m3 reverses") in the
         OPPOSITE direction to the position.
      3. That CISD's own confirming candle closed AFTER the position
         opened (same "already-existing doesn't count" guard every other
         fresh-CISD trigger in this project uses).
      4. "PARENT AND PRIMARY TIMEFRAME AGREEMENT" -- BOTH M5's AND M15's
         own confirmed ATR-dual STRUCTURE (not CISD -- same structure-only
         convention reversal_ict.py's own M1 gate already uses) must agree
         with the reversal CISD's own direction. AND, not OR -- this is
         deliberately stricter than the M1 entry gate's own "M5 or M15"
         (asked for TWICE in the user's own words: "agreeing with parent
         and primary" then "with parent and primary timeframe agreement"),
         and matches the real incident where both had already agreed.
    If ANY of these fail, no early exit -- "else this will wait for sl",
    the position rides to its own SL/other exit mechanism exactly as
    before this rule existed."""
    if journal is None or tracker is None:
        return False
    if journal.entry_trigger(position.ticket) != "M3CD":
        return False
    position_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    cisd = cisd_bridge.fresh_cisd(cfg.symbol, 3)
    if cisd is None:
        return False
    cisd_direction = cisd_bridge.direction_of(cisd)
    if cisd_direction != -position_direction:
        return False
    confirm_time = cisd.bar_time + 3 * 60
    if position.time >= confirm_time:
        return False   # opened at/after this CISD's own confirm time -- "already existing", not a genuine reversal after
    m5_fs = tracker.update(cfg.symbol, 5)
    m15_fs = tracker.update(cfg.symbol, 15)
    m5_structure = m5_fs.confirmed.value if m5_fs is not None else None
    m15_structure = m15_fs.confirmed.value if m15_fs is not None else None
    if m5_structure != cisd_direction or m15_structure != cisd_direction:
        return False   # no full parent+primary agreement -- waits for SL instead, per the user's own words
    return _close_position(cfg, component, position, "M3REV", "M3CD", "M3R", journal,
                           {"rule": "M3-triggered reversal trade reversed, with parent+primary (M5+M15) structure agreement",
                            "original_entry_trigger": "M3CD", "reversal_cisd": cisd.last_cisd,
                            "m5_structure": m5_structure, "m15_structure": m15_structure,
                            "confirm_time": confirm_time, "position_open_time": position.time})


def _run_trade_manager(cfg: RMSymbolConfig, component: str, mgr: trade_manager.TradeManager, position,
                       tracker: Optional[BridgeBarFlipTracker] = None,
                       journal: Optional[trade_journal.TradeJournal] = None) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    has_tp = broker.has_manual_tp(position)

    # Position Size Manager's NIGHT MODE booking (2026-09-24) -- see trend_main.py's own
    # identical block for the full rationale. REPLACES Type1/Type2 partial booking entirely for
    # the 23:00-04:00 IST window: a single FULL close the instant the position reaches
    # NIGHT_PROFIT_TARGET_POINTS, no partials. SL Manager/Exit Manager are untouched -- this
    # function's only job either way is booking.
    if position_size_manager.is_night():
        if has_tp or not position_size_manager.night_target_hit(direction, position.price_open, current_price):
            return
        entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")
        print(f"[V7S-{component}-TM] #{position.ticket} NIGHT MODE full close -- "
              f"{position_size_manager.NIGHT_PROFIT_TARGET_POINTS:.0f}+ points reached")
        _close_position(cfg, component, position, "NIGHTFULL", entry_tag, "NF", journal,
                        {"rule": "night-mode full close at fixed points target",
                         "target_points": position_size_manager.NIGHT_PROFIT_TARGET_POINTS})
        return

    symbol_info = mt5.symbol_info(cfg.symbol)
    volume_step = symbol_info.volume_step if symbol_info is not None else 0.01

    # TYPE 2 booking gate (2026-09-23) -- see trade_manager.py's own docstring. "Parent and primary"
    # = M5 AND M15 (structure only, same convention reversal_ict.py's M1 gate and this module's own
    # M3-reversal-exit already use), BOTH agreeing with this position's own direction.
    m5_fs = tracker.update(cfg.symbol, 5) if tracker is not None else None
    m15_fs = tracker.update(cfg.symbol, 15) if tracker is not None else None
    m5_structure = m5_fs.confirmed.value if m5_fs is not None else None
    m15_structure = m15_fs.confirmed.value if m15_fs is not None else None
    structure_agrees = m5_structure == direction and m15_structure == direction

    outcome = mgr.evaluate(position.ticket, direction, position.price_open, current_price,
                           position.volume, has_tp, volume_step, entry_comment=position.comment,
                           structure_agrees=structure_agrees)
    if outcome is None:
        return
    volume, label = outcome
    action_code = "P1" if label == "partial1" else "P2"
    entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")

    print(f"[V7S-{component}-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print(f"[V7S-{component}-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(component, entry_tag, action_code))
    if not result.ok:
        print(f"[V7S-{component}-TM] partial close failed: retcode={result.retcode} comment={result.comment}")
    elif journal is not None:
        journal.partial(position.ticket, label, volume, current_price)


def _process_signal(cfg: RMSymbolConfig, sticky: ict_guard.ICTGuardStickyStore, component: str, magic_number: int,
                    direction: int, sl: float, tag: str, ref_desc: str, mark_traded, on_redundant,
                    journal: Optional[trade_journal.TradeJournal] = None, logic: Optional[dict] = None,
                    signal_tf: Optional[int] = None) -> None:
    """Acts on ONE signal against whatever position is ACTUALLY open right
    now on RM-ICT's own magic number (re-queried, so an earlier signal's
    own action this same cycle is visible here). mark_traded is a zero-arg
    callback (eligibility.mark_traded(zone_id), bound by the caller) so
    this function stays agnostic to the exact eligibility scheme. Only
    calls it once the entry actually went through (or, for the "already
    open, same direction" case, always -- no order is even attempted
    there) -- a failed order_send must never consume eligibility anyway,
    silently dropping a genuinely valid setup that never got a position
    (see _open_position's own docstring).

    on_redundant(direction, tag, ref_desc, ticket) is called instead of
    alerting directly -- the caller (run_once) collects these across the
    whole cycle and sends ONE aggregated Telegram message instead of one
    per matching zone (a single trigger event can legitimately match
    dozens of zones at once); decision_log still gets one line per signal
    for full audit granularity, only the Telegram side is collapsed.

    (Historical note: this function used to also accept
    other_component/other_magic_number/other_journal for a
    cross-component square-off against RM-STR's own opposite position --
    removed 2026-09-25 along with RM-STR itself, see module docstring.)"""
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

    if same:
        # SAME direction already open, whether still full-size or already partially cut -- NO-OP: no
        # closing leftover and entering full qty again.
        position = same[0]
        mark_traded()
        print(f"[V7S-{component}] {tag} qualifies ({_DIR_LABEL[direction]}) but a {_DIR_LABEL[direction]} "
              f"position is already open on #{position.ticket} -- marked traded, no new entry")
        decision_log.log(cfg.decision_log_file, "redundant_signal", component=component,
                         direction=_DIR_LABEL[direction], tag=tag, ref=ref_desc, existing_ticket=position.ticket)
        on_redundant(direction, tag, ref_desc, position.ticket)
        return

    if _open_position(cfg, sticky, component, magic_number, direction, sl, tag, ref_desc, journal, logic):
        mark_traded()


@dataclass
class _SymbolRuntime:
    """Every stateful object one symbol's own RM-ICT needs -- one of
    these per symbol in config.ACTIVE_SYMBOLS. Building this out per
    symbol (rather than one flat set of module-level globals, V5S's own
    single-symbol shape) is what makes this file genuinely
    multi-instrument: adding a symbol means constructing another
    _SymbolRuntime, never touching run_once() or any class above."""
    cfg: RMSymbolConfig
    sl_mgr_ict: sl_manager.SLManager
    tm_mgr_ict: trade_manager.TradeManager
    ict_eligibility: reversal_ict.ICTEligibilityStore
    sticky: ict_guard.ICTGuardStickyStore
    tracker: Optional[BridgeBarFlipTracker] = None    # ATR flip state for the bridge-sourced M15/M5/M1 reads
    last_tick_msc: int = 0                             # time_msc of the newest tick already looked at (touch extremes)
    journal_ict: Optional[trade_journal.TradeJournal] = None
    session_watch: session_manager.SessionWindowWatch = dataclasses.field(
        default_factory=session_manager.SessionWindowWatch)  # daily no-new-trade windows


def _build_runtime(symbol: str) -> _SymbolRuntime:
    cfg = load_symbol_config(symbol)
    return _SymbolRuntime(
        cfg=cfg,
        sl_mgr_ict=sl_manager.SLManager(cfg.ict_sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer),
        tm_mgr_ict=trade_manager.TradeManager(cfg.ict_state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                              cfg.partial2_trigger_points, cfg.partial2_fraction,
                                              cfg.type2_partial1_trigger_points, cfg.type2_partial2_trigger_points),
        ict_eligibility=reversal_ict.ICTEligibilityStore(cfg.ict_eligibility_state_file),
        sticky=ict_guard.ICTGuardStickyStore(cfg.ict_guard_sticky_state_file),
        tracker=BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file),
        journal_ict=trade_journal.TradeJournal(cfg.ict_trade_journal_file, "RM-ICT", cfg.symbol),
    )


def run_once(rt: _SymbolRuntime) -> None:
    cfg = rt.cfg
    bid, ask = broker.get_tick_price(cfg.symbol)

    # M15/M5 structure -- feed ONLY RM-ICT's own M1-FLIP gate below (see
    # reversal_ict._check_zone()'s own docstring).
    m15_fs = rt.tracker.update(cfg.symbol, 15) if rt.tracker is not None else None
    m15_structure = m15_fs.confirmed.value if m15_fs is not None else None
    m5_fs = rt.tracker.update(cfg.symbol, 5) if rt.tracker is not None else None
    m5_structure = m5_fs.confirmed.value if m5_fs is not None else None
    # M1 structure FLIP state -- feeds ONLY RM-ICT's own M5-zone exception pool (2026-09-24,
    # replaced an M1 CISD confirmation there, see reversal_ict._check_zone()'s own docstring).
    m1_fs = rt.tracker.update(cfg.symbol, 1) if rt.tracker is not None else None

    # RM-ICT -- OB-zone (NLB/NSB Block + merged TV+MT5 store) touch + post-touch confirmation.
    # See reversal_ict.py's own docstring for the full entry rule. m1_fs/m5_structure/m15_structure
    # feed ONLY the M1-FLIP gate/direct-fire -- every other confirmation timeframe (M3/M5) is
    # unaffected by them.
    ict_signals = reversal_ict.find_ict_signals(cfg.symbol, cfg.nlb_nsb_block_state_file,
                                                cfg.ict_ob_block_state_file, rt.ict_eligibility,
                                                cfg.sl_buffer, bid, ask, cfg.ict_sl_override_points,
                                                cfg.ict_touch_max_age_minutes,
                                                m1_fs=m1_fs, m5_structure=m5_structure, m15_structure=m15_structure)

    # Session Manager -- daily no-new-trade windows (session_manager.py). Eligibility syncing
    # above already happened regardless; this only ever discards signals that would otherwise
    # become NEW entries -- existing positions (SL/TM/Exit Manager, all below) are unaffected.
    session_blocked, session_window, session_event = rt.session_watch.update()
    if session_event == "entered":
        msg = f"[V7S-RM] {cfg.symbol} entering session window '{session_window}' -- new entries PAUSED until it ends (open trades still managed)."
        print(msg)
        telegram_alerts.send_if_configured(cfg.alerts_bot_token, cfg.alerts_chat_id, msg)
    elif session_event == "left":
        msg = f"[V7S-RM] {cfg.symbol} session window ended -- new entries resumed."
        print(msg)
        telegram_alerts.send_if_configured(cfg.alerts_bot_token, cfg.alerts_chat_id, msg)
    if session_blocked:
        ict_signals = []

    # Logged BEFORE _process_signal acts on them, listing EVERY qualifying signal this cycle (not
    # just the one that becomes a real position) -- a multi-zone-match cycle would otherwise leave
    # no record of WHICH exact zone became the real trade vs. which got marked redundant.
    if ict_signals:
        decision_log.log(cfg.decision_log_file, "ict_signals_found", count=len(ict_signals), signals=[
            {"zone_id": s.zone_id, "timeframe_name": s.timeframe_name, "direction": _DIR_LABEL[s.direction],
             "trigger": s.trigger, "zone_top": s.zone_top, "zone_btm": s.zone_btm, "sl": s.sl,
             "sl_source": s.sl_source} for s in ict_signals])

    ict_positions = broker.get_positions(cfg.symbol, cfg.ict_magic_number)
    rt.journal_ict.reconcile({p.ticket for p in ict_positions})
    rt.sl_mgr_ict.prune({p.ticket for p in ict_positions})
    rt.tm_mgr_ict.prune({p.ticket for p in ict_positions})

    # redundant_signal alerts collected here instead of sent inline -- a single trigger event can
    # legitimately match dozens of untested zones at once, so alerting once EACH would spam.
    redundant: list[tuple[int, str, str, int]] = []

    def _on_redundant(direction: int, tag: str, ref_desc: str, ticket: int) -> None:
        redundant.append((direction, tag, ref_desc, ticket))

    for sig in ict_signals:
        _process_signal(cfg, rt.sticky, "ICT", cfg.ict_magic_number, sig.direction, sig.sl, _ict_tag(sig),
                        f"zone=[{sig.zone_btm:.3f}-{sig.zone_top:.3f}]",
                        lambda zid=sig.zone_id, top=sig.zone_top, btm=sig.zone_btm, role=sig.role:
                            rt.ict_eligibility.mark_traded(zid, top, btm, role),
                        _on_redundant,
                        rt.journal_ict, {**dataclasses.asdict(sig), "rule": "OB zone touch + CISD",
                                         "direction": _DIR_LABEL[sig.direction], "bid": bid, "ask": ask},
                        trade_journal.timeframe_of_logic({"zone_id": sig.zone_id, "timeframe_name": sig.timeframe_name}))

    if redundant:
        direction, first_tag, _first_ref, ticket = redundant[0]
        extra = f" (+{len(redundant) - 1} more matching this cycle)" if len(redundant) > 1 else ""
        msg = (f"[V7S-ICT] {cfg.symbol} {_DIR_LABEL[direction]} qualifies e.g. {first_tag}{extra} but a "
              f"position is already open on #{ticket} -- marked traded, no new entry")
        print(msg)
        telegram_alerts.send_if_configured(cfg.alerts_bot_token, cfg.alerts_chat_id, f"[V7S] SKIPPED: {msg}")

    # EVERY open position is managed (SL trailing + partials), not just the first -- normally at
    # most one now (any opposite signal squares off the prior one), but this still covers the
    # transient case where a square-off's own close failed and both ended up open.
    for ict_position in broker.get_positions(cfg.symbol, cfg.ict_magic_number):
        if _check_m3_reversal_exit(cfg, "ICT", ict_position, rt.tracker, rt.journal_ict):
            continue   # closed this cycle -- ticket no longer exists, nothing left to manage
        _run_sl_manager(cfg, "ICT", rt.sl_mgr_ict, rt.tm_mgr_ict, ict_position, rt.tracker, rt.journal_ict)
        _run_trade_manager(cfg, "ICT", rt.tm_mgr_ict, ict_position, rt.tracker, rt.journal_ict)


def main() -> None:
    runtimes = [_build_runtime(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for rt in runtimes:
        cfg = rt.cfg
        print(f"[V7S-RM] {cfg.symbol} starting -- ict_magic={cfg.ict_magic_number} "
              f"enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")
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
                    print(f"[V7S-RM] {rt.cfg.symbol} cycle error: {exc!r}")
                # See heartbeat.py's own docstring -- proves the loop
                # itself is alive, regardless of whether this cycle
                # raised.
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(rt.cfg.poll_seconds for rt in runtimes))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
