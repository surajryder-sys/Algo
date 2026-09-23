"""V6-Sentinel Trend Manager (TM-STR) -- main loop. Built 2026-09-20 from a
rule-by-rule discussion with the user (NOT a port of V5-Sentinel's main.py,
whose M5-parent/M3-execution design sat on structure.py, which V6S no
longer has). One runtime bundle PER SYMBOL in config.ACTIVE_SYMBOLS, same
multi-instrument shape as reversal_main.py.

Run with: python -m v6_sentinel.trend_main

THE RULES:

  M15 GATE -- REDESIGNED 2026-09-22 (user: "m15 parenting change, now its
  not recency, its structure and cisd" -- see trend_bias.py's own docstring
  for the full 4-case table). M15 structure (ATR-dual confirmed direction)
  and the M15 standing CISD are read TOGETHER, never one overriding the
  other by recency: if they AGREE, only an M5 CISD in that same direction
  may fire; if they DISAGREE, both directions are allowed and M5 decides
  freely. No gate at all (no entries either way) if either signal is
  unavailable.

  ENTRY -- M5 execution (trend_entry.py): a FRESH M5 CISD whose direction is
  in the M15 gate's currently allowed set. Nothing else triggers an entry.
  One trade per M5 CISD event.

  ENTRY -- M3 execution (added 2026-09-22, trend_entry.py): a STRICTER
  second entry path -- a fresh M3 CISD only fires when the M15 gate is in
  STRICT agreement with it (not merely "allowed") AND M5's own confirmed
  ATR state also agrees. One trade per M3 CISD event, tracked separately
  from M5's own eligibility.

  INITIAL SL -- farthest usable M5 line (ATR dual / Supertrend), else the
  same on M15, else the CISD's swing, else no trade; plus buffer.

  AFTER ENTRY (V5S's scheme, trailing on M5 -- confirmed): breakeven at
  +breakeven_trigger_points or after the first partial, 70% closed at +10
  and a further 15% at +15, the last 15% trails M5's far ATR line minus
  buffer. The SL only tightens and a manual SL edit pauses trailing
  (sl_manager.py / trade_manager.py, reused unchanged).

  LIFECYCLE -- a same-direction signal while a trade is open is ignored
  (its event is marked handled so it can't fire later). NO M15-bias-flip
  close any more (removed 2026-09-22, user: "we have added m5 cisd exit so
  we can remove m15 bias exit logic" -- there is no longer a single M15
  "bias direction" to compare an open trade against, since the gate can
  allow both directions at once). TM-STR itself now closes a trade only via
  the M5 flip exit or the square-off below; the SL; or the separate Exit
  Manager process (exit_manager.py), which is not part of this file.

  M5 FLIP EXIT (user, 2026-09-20: "the M5 flip itself closes the SELL
  straight away") -- when the M5 ATR-dual state genuinely FLIPS against an
  open trade (to strong under a SELL, to weak under a BUY) on a bar that
  closed after the trade opened, the trade is closed at once
  (trend_entry.find_flip_exit). An EVENT, not a state comparison: a BUY
  entered while M5 is already weak is not closed for that. A trap-resolved
  snap-back is not a flip. Everything is on CLOSED candles -- live price never
  matters ("candle close always").

  SQUARE-OFF (user, 2026-09-20) -- the other way a trade closes: a fresh M5
  CISD in the OPPOSITE direction that qualifies on its own, i.e. the M5
  ATR-dual CONFIRMED state (as of the last closed candle -- even while
  price sits between the lines) already agrees with it
  (trend_entry.find_squareoff).
  E.g. SELL entered on M5 bearish CISD while M5 price is above both ATR
  lines (strong); a bullish M5 CISD now arrives with M5 still strong -> it
  qualifies a buy by itself -> the SELL is squared off (no buy follows
  unless the M15 gate also allows one). Mirror for a BUY. An opposite CISD
  that the M5 state does NOT agree with closes nothing -- the trade waits
  for the structure to shift. The M5 state is NOT an entry gate.

  BIAS FEED WATCH -- the M15 gate needs BOTH the ATR bridge and the CISD
  bridge; either one going stale means no gate. BiasFeedWatch below watches
  both together: when either file has been stale for STALE_FEED_SECONDS
  while prices are still ticking, it sends ONE alert and PAUSES NEW ENTRIES
  until both are fresh again (open trades are still managed). It only
  counts as a fault while ticks are actually arriving -- a closed market
  makes the files stale too, and that must not alarm or block anything.
  The M5 ATR feed (which the flip exit and the square-off read) gets its
  own separate watch: when it is stale both are PAUSED (a frozen
  "strong/weak" must not close a trade) and one alert is sent; entries are
  unaffected.

  ICT GUARD -- not applied (assumed to match RM's "no guard as of now";
  not separately confirmed for TM).

Comments: "V6S-TM-STR-{trigger}" on entry (e.g. "V6S-TM-STR-M5CD"),
"-P1"/"-P2"/"-MF"/"-SQ" appended for partials / M5-flip close / square-off.

TRADE JOURNAL -- every real trade is written to trade_journal.py's per-trade
journal: the full entry logic when it opens, every partial and SL move, and
why it ended (this bot's own exit reason, or the broker's: SL hit / manual).

Safety: each symbol's own enable_trading (trend_config.py,
V6S_TM_{SYMBOL}_ENABLE_TRADING) must be explicitly true for any order to
be sent/modified/closed for THAT symbol.
"""
from __future__ import annotations

import time
import dataclasses
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from v6_sentinel import alerts, bridge, bridge_flip, broker, cisd_bridge, config, decision_log, flip_state, heartbeat, rates, sideways_trapper, sl_manager, telegram_alerts, trade_journal, trade_manager, trend_bias, trend_entry
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v6_sentinel.flip_state import far_near_line
from v6_sentinel.trend_config import TMSymbolConfig, load_symbol_config

_DIR_LABEL = {1: "BUY", -1: "SELL"}

STALE_FEED_SECONDS = 60.0            # sustained staleness, not a blip (same threshold V5S used)
TICKS_FLOWING_WINDOW_SECONDS = 120.0  # prices count as "flowing" if the tick time changed this recently


class BiasFeedWatch:
    """Watches the M15 ATR bridge feed. Pure logic (time and tick time are
    passed in) so it can be tested without MT5.

    update() returns (entries_blocked, message): entries_blocked is True once
    the feed has been stale for STALE_FEED_SECONDS WHILE ticks are flowing;
    message is a one-off alert string on entering that state, and a one-off
    recovery string on leaving it (else None).

    "Ticks flowing" needs an OBSERVED CHANGE of the broker's tick time within
    TICKS_FLOWING_WINDOW_SECONDS -- the very first tick seen only sets the
    baseline. So a process started on a weekend never counts as flowing, and
    a stale file then is treated as normal, not a fault. Deliberately based
    on the tick time CHANGING rather than its age, so it doesn't depend on the
    broker's server clock matching this machine's."""

    def __init__(self):
        self._last_tick_time: Optional[int] = None
        self._last_tick_change: Optional[float] = None
        self._stale_since: Optional[float] = None
        self._alerted = False

    def update(self, now: float, tick_time: Optional[int], feed_ok: bool) -> tuple[bool, Optional[str]]:
        if tick_time is not None and tick_time != self._last_tick_time:
            if self._last_tick_time is not None:
                self._last_tick_change = now
            self._last_tick_time = tick_time
        flowing = (self._last_tick_change is not None
                   and (now - self._last_tick_change) < TICKS_FLOWING_WINDOW_SECONDS)

        if feed_ok or not flowing:
            recovered = self._alerted and feed_ok
            self._stale_since = None
            self._alerted = False
            return False, ("recovered" if recovered else None)

        if self._stale_since is None:
            self._stale_since = now
        if (now - self._stale_since) < STALE_FEED_SECONDS:
            return False, None
        if not self._alerted:
            self._alerted = True
            return True, "stale"
        return True, None


def _entry_comment(tag: str) -> str:
    return f"V6S-TM-STR-{tag}"


def _action_comment(tag: str, action_code: str) -> str:
    return f"V6S-TM-STR-{tag}-{action_code}"


def _extract_tag(comment: str) -> str:
    """The entry tag out of a "V6S-TM-STR-{tag}[-action]" comment, used to
    carry it forward onto later partial-booking / close comments. Joins
    everything after the fixed prefix so a dash-containing tag survives."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 4 and parts[0] == "V6S" and parts[1] == "TM" and parts[2] == "STR":
        return "-".join(parts[3:])
    return "UNK"


def _open_position(cfg: TMSymbolConfig, direction: int, sl: float, tag: str, ref_desc: str,
                   journal: Optional[trade_journal.TradeJournal] = None, logic: Optional[dict] = None) -> bool:
    """True if the entry went through (filled, or enable_trading is False
    so it's decision-only and conceptually accepted); False only on a
    genuine order rejection while live. The caller must NOT mark the
    event handled on a False return -- a failed order (e.g. retcode 10044
    "session closed") must not consume a valid setup that never got a
    position."""
    comment = _entry_comment(tag)
    print(f"[V6S-TM-ENTRY] {cfg.symbol} {_DIR_LABEL[direction]} ({tag}) {ref_desc} sl={sl:.3f}")
    if not cfg.enable_trading:
        print("[V6S-TM-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", direction=_DIR_LABEL[direction],
                         tag=tag, ref=ref_desc, sl=sl)
        return True
    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V6S-TM-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", direction=_DIR_LABEL[direction], tag=tag,
                         ref=ref_desc, retcode=result.retcode, broker_comment=result.comment)
        return False
    print(f"[V6S-TM-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", direction=_DIR_LABEL[direction], tag=tag,
                     ref=ref_desc, sl=sl, ticket=result.ticket)
    if journal is not None and result.ticket is not None:
        journal.entry(result.ticket, _DIR_LABEL[direction], cfg.lots, sl, comment, logic or {})
    tf = (logic or {}).get("timeframe_minutes")
    telegram_alerts.send_if_configured(
        cfg.alerts_bot_token, cfg.alerts_chat_id,
        f"[V6S] ENTRY: TM-STR {_DIR_LABEL[direction]} {cfg.symbol} #{result.ticket} ({tag}) -- "
        f"{f'M{tf}' if tf else 'tf n/a'} -- {(logic or {}).get('rule', ref_desc)} -- sl={sl:.3f}")
    return True


def _close_position(cfg: TMSymbolConfig, position, action_label: str, tag: str, action_code: str,
                    journal: Optional[trade_journal.TradeJournal] = None, detail: Optional[dict] = None) -> bool:
    print(f"[V6S-TM-EXIT] {cfg.symbol} closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print("[V6S-TM-EXIT] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "close_decision_only", ticket=position.ticket, action=action_label)
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(tag, action_code))
    if not result.ok:
        print(f"[V6S-TM-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "close_failed", ticket=position.ticket, action=action_label,
                         retcode=result.retcode)
        return False
    decision_log.log(cfg.decision_log_file, "close_filled", ticket=position.ticket, action=action_label, tag=tag)
    if journal is not None:
        journal.exit_requested(position.ticket, action_label, detail)
    return True


def _trailing_far_line(symbol: str, tf_minutes: int, direction: int) -> Optional[float]:
    """The far ATR trail line of one timeframe for a trade's own
    direction (the lower line for a BUY, the higher for a SELL). M1/M3/M5/M15
    are read STRICTLY from the MT5 bridge (user, 2026-09-21); any other
    timeframe is computed natively from MT5 history. None if unavailable, in
    which case that cycle's SL update is skipped rather than guessed at."""
    if tf_minutes in bridge.BRIDGE_ONLY_TIMEFRAMES:
        # M1/M3/M5/M15: straight from the MT5 bridge, never computed here (user, 2026-09-21).
        result = bridge_flip.far_near(symbol, tf_minutes, direction)
        return None if result is None else result[0]
    series = rates.read_trail_series(symbol, tf_minutes)
    if series is None:
        return None
    t1, t2 = series.trail1[-1], series.trail2[-1]
    if t1 is None or t2 is None:
        return None
    far, _near = far_near_line(direction, t1, t2)
    return far


def _run_sl_manager(cfg: TMSymbolConfig, mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager, position,
                    tracker: Optional[BridgeBarFlipTracker] = None,
                    journal: Optional[trade_journal.TradeJournal] = None) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    far = _trailing_far_line(cfg.symbol, cfg.trailing_timeframe, direction)
    if far is None:
        print(f"[V6S-TM-SL] M{cfg.trailing_timeframe} far line unavailable -- skipping SL update this cycle")
        return
    current_sl = position.sl if position.sl else None

    # Pre-breakeven flip check (2026-09-22) -- see sl_manager.py's own docstring.
    trailing_fs = tracker.update(cfg.symbol, cfg.trailing_timeframe) if tracker is not None else None
    with_flip = flip_state.with_direction_flip_after(trailing_fs, direction, cfg.trailing_timeframe, position.time)

    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far,
                           tm_mgr.is_partially_cut(position.ticket), with_direction_flip_after_entry=with_flip)
    if proposed is None:
        return
    print(f"[V6S-TM-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print("[V6S-TM-SL] enable_trading is false -- decision only, no modify sent")
        return
    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
        if journal is not None:
            journal.sl_move(position.ticket, current_sl, proposed, current_price)
    else:
        print(f"[V6S-TM-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _run_trade_manager(cfg: TMSymbolConfig, mgr: trade_manager.TradeManager, position,
                       tracker: Optional[BridgeBarFlipTracker] = None,
                       journal: Optional[trade_journal.TradeJournal] = None) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    has_tp = broker.has_manual_tp(position)

    symbol_info = mt5.symbol_info(cfg.symbol)
    volume_step = symbol_info.volume_step if symbol_info is not None else 0.01

    # TYPE 2 booking gate (2026-09-23) -- see trade_manager.py's own docstring. "Parent and primary"
    # = M5 AND M15 (structure only, same convention reversal_ict.py's M1 gate and reversal_main.py's
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

    print(f"[V6S-TM-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V6S-TM-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(entry_tag, action_code))
    if not result.ok:
        print(f"[V6S-TM-TM] partial close failed: retcode={result.retcode} comment={result.comment}")
    elif journal is not None:
        journal.partial(position.ticket, label, volume, current_price)


@dataclass
class _SymbolRuntime:
    """Every stateful object one symbol's TM-STR needs -- one per symbol
    in config.ACTIVE_SYMBOLS."""
    cfg: TMSymbolConfig
    tracker: BridgeBarFlipTracker
    eligibility: trend_entry.TrendEligibilityStore
    sl_mgr: sl_manager.SLManager
    tm_mgr: trade_manager.TradeManager
    feed_watch: BiasFeedWatch                  # the M15 bias feed: stale -> entries paused
    state_feed_watch: BiasFeedWatch = field(default_factory=BiasFeedWatch)   # the M5 state feed: stale -> square-off paused
    journal: Optional[trade_journal.TradeJournal] = None                       # per-trade entry/exit logic
    trapper: Optional[sideways_trapper.SidewaysTrapper] = None                   # Sideways Trapper, M5+M3


def _build_runtime(symbol: str) -> _SymbolRuntime:
    cfg = load_symbol_config(symbol)
    return _SymbolRuntime(
        cfg=cfg,
        tracker=BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file),
        eligibility=trend_entry.TrendEligibilityStore(cfg.eligibility_state_file),
        sl_mgr=sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer),
        tm_mgr=trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                          cfg.partial2_trigger_points, cfg.partial2_fraction,
                                          cfg.type2_partial1_trigger_points, cfg.type2_partial2_trigger_points),
        feed_watch=BiasFeedWatch(),
        journal=trade_journal.TradeJournal(cfg.trade_journal_file, "TM-STR", cfg.symbol),
        trapper=sideways_trapper.SidewaysTrapper(cfg.sideways_trapper_state_file),
    )


def _current_position(cfg: TMSymbolConfig):
    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    return positions[0] if positions else None


def run_once(rt: _SymbolRuntime) -> None:
    cfg = rt.cfg
    bid, ask = broker.get_tick_price(cfg.symbol)
    gate = trend_bias.compute_gate(rt.tracker, cfg.symbol, cfg.bias_timeframe)

    tick = mt5.symbol_info_tick(cfg.symbol)
    # The gate needs BOTH the ATR bridge and the standing CISD -- either one stale means no gate.
    feed_ok = (bridge.read_lines(cfg.symbol, cfg.bias_timeframe) is not None
              and cisd_bridge.read_cisd(cfg.symbol, cfg.bias_timeframe) is not None)
    entries_blocked, feed_event = rt.feed_watch.update(time.time(), tick.time if tick is not None else None, feed_ok)
    if feed_event == "stale":
        msg = (f"[V6S-TM] {cfg.symbol} M{cfg.bias_timeframe} ATR/CISD bridge has been stale for over "
               f"{STALE_FEED_SECONDS:.0f}s while prices are still ticking -- the M15 gate may be frozen. New "
               f"entries are PAUSED until it recovers; open trades are still managed. Check the "
               f"M{cfg.bias_timeframe} chart/indicator.")
        print(msg)
        decision_log.log(cfg.decision_log_file, "bias_feed_stale", bias_timeframe=cfg.bias_timeframe)
        alerts.send_alert(msg)
    elif feed_event == "recovered":
        msg = f"[V6S-TM] {cfg.symbol} M{cfg.bias_timeframe} ATR/CISD bridge is fresh again -- new entries resumed."
        print(msg)
        decision_log.log(cfg.decision_log_file, "bias_feed_recovered", bias_timeframe=cfg.bias_timeframe)
        alerts.send_alert(msg)

    # The M5 ATR strong/weak state (1 strong, -1 weak, None unknown) -- read every cycle so the
    # persisted tracker stays warm -- and its own feed watch (see the docstring).
    state_fs = rt.tracker.update(cfg.symbol, cfg.squareoff_timeframe)
    m5_state = state_fs.confirmed.value if state_fs is not None else None
    m5_exits_paused, state_event = rt.state_feed_watch.update(
        time.time(), tick.time if tick is not None else None,
        bridge.read_lines(cfg.symbol, cfg.squareoff_timeframe) is not None)
    if state_event == "stale":
        msg = (f"[V6S-TM] {cfg.symbol} M{cfg.squareoff_timeframe} ATR bridge has been stale for over "
               f"{STALE_FEED_SECONDS:.0f}s while prices are still ticking -- the strong/weak state may be "
               f"frozen. The M5 flip exit and square-off are PAUSED until it recovers (entries and other management continue). "
               f"Check the M{cfg.squareoff_timeframe} chart/indicator.")
        print(msg)
        decision_log.log(cfg.decision_log_file, "state_feed_stale", timeframe=cfg.squareoff_timeframe)
        alerts.send_alert(msg)
    elif state_event == "recovered":
        msg = f"[V6S-TM] {cfg.symbol} M{cfg.squareoff_timeframe} ATR bridge is fresh again -- M5 exits resumed."
        print(msg)
        decision_log.log(cfg.decision_log_file, "state_feed_recovered", timeframe=cfg.squareoff_timeframe)
        alerts.send_alert(msg)

    open_tickets = {p.ticket for p in broker.get_positions(cfg.symbol, cfg.magic_number)}
    if rt.journal is not None:
        tm_exits = rt.journal.reconcile(open_tickets)
        for exit_rec in tm_exits:
            if exit_rec.get("exit_reason") == "SL_HIT" and rt.trapper is not None:
                exit_direction = 1 if exit_rec.get("direction") == "BUY" else -1
                m15_structure_now = gate.structure if gate is not None else None
                rt.trapper.record_sl_hit(exit_direction, exit_rec["entry_price"], m15_structure_now)
                print(f"[V6S-TM] Sideways Trapper recorded {exit_rec['direction']} SL-hit @ "
                      f"{exit_rec['entry_price']:.3f} -- next M5/M3 {exit_rec['direction']} needs to be "
                      f"{cfg.sideways_trap_min_distance_points:.1f}+ points away")
    rt.sl_mgr.prune(open_tickets)
    rt.tm_mgr.prune(open_tickets)
    position = _current_position(cfg)

    # 1. The M5 state genuinely flipped against the trade after it opened -> close it at once.
    if position is not None and not m5_exits_paused:
        pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
        flip = trend_entry.find_flip_exit(pos_direction, position.time, cfg.squareoff_timeframe, state_fs)
        if flip is not None:
            tag = _extract_tag(rt.tm_mgr.get_entry_comment(position.ticket) or position.comment or "")
            print(f"[V6S-TM] {cfg.symbol} M{cfg.squareoff_timeframe} state flipped {flip.confirmed.name} "
                  f"(bar {flip.bar_time}) against the open {_DIR_LABEL[pos_direction]} #{position.ticket}")
            decision_log.log(cfg.decision_log_file, "m5_flip_exit", ticket=position.ticket,
                             position=_DIR_LABEL[pos_direction], timeframe=cfg.squareoff_timeframe,
                             flip_to=flip.confirmed.name, flip_bar_time=flip.bar_time)
            _close_position(cfg, position, "M5FLIP", tag, "MF", rt.journal,
                            {"rule": "M5 ATR state flipped against the trade", "flip_to": flip.confirmed.name,
                             "flip_bar_time": flip.bar_time})
            position = _current_position(cfg)

    # 2. Square-off: a fresh opposite M5 CISD that the M5 strong/weak state agrees with (see docstring).
    if position is not None and not m5_exits_paused:
        pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
        cisd = trend_entry.find_squareoff(cfg.symbol, pos_direction, cfg.squareoff_timeframe, m5_state)
        if cisd is not None:
            tag = _extract_tag(rt.tm_mgr.get_entry_comment(position.ticket) or position.comment or "")
            print(f"[V6S-TM] {cfg.symbol} M{cfg.squareoff_timeframe} {cisd.last_cisd} CISD@{cisd.last_cisd_time} "
                  f"with M{cfg.squareoff_timeframe} {'strong' if m5_state == 1 else 'weak'} squares off the open "
                  f"{_DIR_LABEL[pos_direction]} #{position.ticket}")
            decision_log.log(cfg.decision_log_file, "squareoff", ticket=position.ticket,
                             position=_DIR_LABEL[pos_direction], timeframe=cfg.squareoff_timeframe,
                             cisd=cisd.last_cisd, cisd_time=cisd.last_cisd_time, state=m5_state)
            _close_position(cfg, position, "SQUAREOFF", tag, "SQ", rt.journal,
                            {"rule": "opposite M5 CISD that the confirmed M5 state agrees with",
                             "cisd": cisd.last_cisd, "cisd_time": cisd.last_cisd_time,
                             "m5_state": "strong" if m5_state == 1 else "weak"})
            position = _current_position(cfg)

    # 3. Entry: fresh M5/M3 CISD whose direction the M15 gate allows (M3 has its own stricter
    #    rule, see trend_entry.py); paused while the bias feed is stale.
    if gate is not None and not entries_blocked:
        sig = trend_entry.find_signal(cfg.symbol, gate, cfg.execution_timeframes, rt.eligibility,
                                      cfg.sl_buffer, bid, ask, m5_state,
                                      rt.trapper, cfg.sideways_trap_min_distance_points)
        if sig is not None:
            ref = (f"cisd@{sig.event_time} m15_structure={'strong' if gate.structure == 1 else 'weak'} "
                  f"m15_cisd={'bullish' if gate.cisd == 1 else 'bearish'} sl={sig.sl_source}")
            decision_log.log(cfg.decision_log_file, "signal_found", direction=_DIR_LABEL[sig.direction],
                             tf_minutes=sig.timeframe_minutes, trigger=sig.trigger, event_time=sig.event_time,
                             sl=sig.sl, sl_source=sig.sl_source, m15_structure=sig.m15_structure,
                             m15_cisd=sig.m15_cisd)
            if position is None:
                logic = {**dataclasses.asdict(sig), "rule": "M15 structure+CISD gate + fresh M5 CISD",
                         "direction": _DIR_LABEL[sig.direction], "bid": bid, "ask": ask,
                         "m5_state": {1: "strong", -1: "weak"}.get(m5_state)}
                if _open_position(cfg, sig.direction, sig.sl, sig.trigger, ref, rt.journal, logic):
                    rt.eligibility.mark_traded(sig.timeframe_minutes, sig.event_time)
            else:
                pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                if sig.direction == pos_direction:
                    rt.eligibility.mark_traded(sig.timeframe_minutes, sig.event_time)
                    print(f"[V6S-TM] {sig.trigger} qualifies ({_DIR_LABEL[sig.direction]}) but a "
                          f"{_DIR_LABEL[pos_direction]} is already open on #{position.ticket} -- marked handled")
                    decision_log.log(cfg.decision_log_file, "redundant_signal", direction=_DIR_LABEL[sig.direction],
                                     trigger=sig.trigger, existing_ticket=position.ticket)
                else:
                    # An opposite trade is somehow still open (its bias-flip close failed):
                    # don't stack, and don't consume the event -- retry next cycle.
                    print(f"[V6S-TM] {sig.trigger} qualifies ({_DIR_LABEL[sig.direction]}) but an opposite "
                          f"position #{position.ticket} is still open -- not entering this cycle")

    # 4. Manage whatever is open (independent of whether a gate exists right now).
    position = _current_position(cfg)
    if position is not None:
        _run_sl_manager(cfg, rt.sl_mgr, rt.tm_mgr, position, rt.tracker, rt.journal)
        _run_trade_manager(cfg, rt.tm_mgr, position, rt.tracker, rt.journal)


def main() -> None:
    runtimes = [_build_runtime(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for rt in runtimes:
        cfg = rt.cfg
        print(f"[V6S-TM] {cfg.symbol} starting -- magic={cfg.magic_number} bias=M{cfg.bias_timeframe} "
              f"exec={[f'M{t}' for t in cfg.execution_timeframes]} enable_trading={cfg.enable_trading} "
              f"poll={cfg.poll_seconds}s")
        broker.connect(cfg.symbol, cfg.mt5_terminal_path, cfg.mt5_login, cfg.mt5_password, cfg.mt5_server)

    try:
        while True:
            for rt in runtimes:
                try:
                    run_once(rt)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-TM] {rt.cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(rt.cfg.heartbeat_file)
            time.sleep(min(rt.cfg.poll_seconds for rt in runtimes))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
