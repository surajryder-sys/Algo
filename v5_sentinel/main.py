"""V5-Sentinel Trend Manager -- main loop. REWRITTEN 2026-09-12: M5
parent bias and M3 execution both now run on the merged Supertrend+ATR
"structure signal" (see structure.py's own docstring for the full
design) -- this retires the old M15-removed-but-still-single-ATR-signal
model (2026-09-10) entirely, along with watch_zone.py (no longer used
here) and the old M5/M15-ownership concept (no longer meaningful once
there's only ever one parent and one execution timeframe).

Run with: python -m v5_sentinel.main

Full design recap (confirmed with the user 2026-09-12):

  PARENT BIAS (M5) -- structure.compute_structure_signal(tracker, symbol,
  5). Always decisive (direction is always 1 or -1, never "neither
  allowed" the way the old M5-trapped-blocks-everything design worked)
  -- whichever of M5's own Supertrend flip or ATR dual-line clear event
  is most recent sets the bias, with its own event_time.

  M3 EXECUTION -- structure.compute_structure_signal(tracker, symbol, 3)
  gives M3 the SAME merged-signal treatment. Two triggers, both gated on
  M3's own signal direction matching the parent bias's direction:

    Trigger 1 (fresh) -- M3's own event_time is AFTER the parent bias's
    own event_time (M3 flipped/reconfirmed to match AFTER M5 already set
    its bias) -> fires immediately, "on the flip candle".

    Trigger 2 (catch-up) -- M3's own event_time is AT OR BEFORE the
    parent bias's event_time (M3 was already favoring before M5 even
    caught up) -> M3's own flip candle CLOSE becomes the "qualifying
    price". Two targets armed simultaneously, race to whichever price
    reaches first (recomputed fresh every cycle from the same frozen
    M3 event_time, so nothing drifts):
      - qualifying price +/- 2 points (the near target)
      - 40% retracement from qualifying price toward M3's OWN near ATR
        trail line (the deeper target, for when price hasn't pulled
        back that close) -- structure.event_reference() computes both
        the qualifying price and the near line via copy_rates at that
        EXACT historical bar, so neither one drifts with live price the
        way the old watch-zone's target used to before it was fixed the
        same way.
    If neither target is ever reached (price never comes back, the
    opportunity goes stale), nothing fires -- just wait for a genuinely
    fresh M3 event instead (Trigger 1's own mechanism naturally covers
    that next).

  ELIGIBILITY -- "only one trade per flip as per M5" (user's own words):
  M5FlipEligibility persists the event_time of whichever M5 bias event a
  trade has already fired for. Once traded, that SAME M5 event can never
  fire again, even if M3 produces further matching events (redundant
  ones are simply ignored, matching the same "already in a full-size
  trade -- marked traded, no new entry" pattern RM's own components use)
  -- eligibility only resets the moment M5 itself produces a genuinely
  new event_time.

  INITIAL SL -- "purely based on parent who gave bias... m3 has nothing
  to do with initial sl" (user's own words): parent.sl_value (the
  Supertrend line value if Supertrend was the more recent driver, or
  M5's own ATR far line if the ATR structure was) +/- cfg.sl_buffer,
  regardless of which M3 trigger actually fired the entry.

  ONGOING SL (post-breakeven trailing) -- UNCHANGED, still M3's own live
  far ATR trail line, same sl_manager.py mechanism as before (this was
  never part of the rewrite -- "everything remains same" except the
  pieces explicitly listed here). Breakeven itself now gates on Trade
  Manager's own partial-booking progress, not a points threshold (see
  sl_manager.py's own docstring, 2026-09-12, applies here and to RM).

  POSITION LIFECYCLE -- unchanged shape: no position + valid M3 signal
  -> open fresh. Position open, valid M3 signal, OPPOSITE direction ->
  square off + reopen ("M5 sets bias... can also square off trade if
  you're in opposite side" -- the squaring-off happens together with
  firing the new-direction entry, not as a standalone action with no
  replacement). Same direction, already partially cut -> refresh. Same
  direction, still full size -> no-op, mark the M5 flip traded anyway
  (nothing left to fire for it).

Safety: V5S_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import (
    bridge, broker, critical_alerts_subscribers, decision_log, heartbeat, nlb_nsb_block, sl_manager, structure,
    trade_manager,
)
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.bridge_flip import StaleAlertTracker, far_near
from v5_sentinel.config import Config, load_config
from v5_sentinel.critical_alerts_telegram import send_message as _telegram_send

_M3_MINUTES = 3
_M5_MINUTES = 5
_DIR_LABEL = {1: "BUY", -1: "SELL"}
_TRIGGER_CODE = {"FRESH": "3F", "QUALIFY": "3Q", "PULLBACK": "3P"}
_PULLBACK_RETRACE_FRACTION = 0.40
_QUALIFY_GATE_POINTS = 2.0


def _send_alert(text: str) -> None:
    """Best-effort push to @smcsecret_bot -- same channel Reversal
    Manager uses, never raises (a Telegram outage must never take the
    trading loop down with it)."""
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[V5S-ALERT] (no bot configured) {text}")
        return
    try:
        _telegram_send(token, chat_id, text)
    except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
        print(f"[V5S-ALERT] send failed: {exc!r} -- message was: {text}")


# Lazily created + reused across cycles -- SubscriberStore's own file read
# only needs to happen once per process, not once per blocked entry.
_critical_subscribers: Optional["critical_alerts_subscribers.SubscriberStore"] = None


def _send_critical_alert(text: str) -> None:
    """Broadcasts to the critical-alerts bot (SecretTrader_Critical_Bot),
    same channel + same approved-subscriber-list pattern
    critical_alerts_watcher.py and watchdog.py already use. Deliberately
    a SEPARATE bot/channel from _send_alert()'s @smcsecret_bot
    (bridge-staleness only) -- this is a trading-decision alert, not an
    infrastructure one. Never raises, same fail-soft contract as
    _send_alert()."""
    global _critical_subscribers
    token = os.getenv("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN")
    owner_chat_id = os.getenv("CRITICAL_ALERTS_TELEGRAM_CHAT_ID")
    if not token or not owner_chat_id:
        print(f"[V5S-CRITICAL-ALERT] (no bot configured) {text}")
        return
    if _critical_subscribers is None:
        subscribers_file = os.getenv("V5S_CRITICAL_ALERTS_SUBSCRIBERS_FILE",
                                      "v5_sentinel_critical_alerts_subscribers.json")
        _critical_subscribers = critical_alerts_subscribers.SubscriberStore(subscribers_file, owner_chat_id)
    for chat_id in _critical_subscribers.approved_chat_ids():
        try:
            _telegram_send(token, chat_id, text)
        except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
            print(f"[V5S-CRITICAL-ALERT] send failed for {chat_id}: {exc!r} -- message was: {text}")


class M5FlipEligibility:
    """Persists the event_time of the M5 bias event a trade has already
    been fired for -- "only one trade per flip as per M5" (user's own
    words, 2026-09-12). Only the CURRENT M5 bias event ever matters (an
    older one is moot the instant M5 produces a new one), so this only
    ever needs to remember the single most recently traded event_time,
    not a growing set -- eligibility for a new M5 event_time is implicit
    (it simply won't match the stored one)."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._traded_event_time: Optional[int] = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._traded_event_time = data.get("traded_event_time")
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._traded_event_time = None

    def _save(self) -> None:
        self._path.write_text(json.dumps({"traded_event_time": self._traded_event_time}))

    def is_traded(self, event_time: int) -> bool:
        return self._traded_event_time == event_time

    def mark_traded(self, event_time: int) -> None:
        if self._traded_event_time != event_time:
            self._traded_event_time = event_time
            self._save()


def _far_line_for(symbol: str, tf_minutes: int, direction: int) -> Optional[float]:
    result = far_near(symbol, tf_minutes, direction)
    return None if result is None else result[0]


def _tag(trigger_type: str) -> str:
    """"M5/{3F|3Q|3P}" -- the parent is always M5 now, so the tag only
    needs to carry which of M3's own triggers actually fired: 3F (fresh
    flip candle, Trigger 1), 3Q (qualifying-price +/-2pt hit, Trigger 2
    near target), 3P (40% pullback hit, Trigger 2 deep target)."""
    return f"M5/{_TRIGGER_CODE[trigger_type]}"


def _extract_tag(comment: str) -> str:
    """Pulls the tag back out of one of our own past comments
    (V5S-TM-{tag}-...) -- used to carry an ENTRY's own tag forward onto
    its later partial-booking comments. Joins everything from parts[2]
    onward (not just parts[2] alone) so a dash-containing tag survives
    intact. Falls back to "UNK" if the given comment isn't in our own
    format (predates this scheme, missing, etc.) -- never raises."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 3 and parts[0] == "V5S" and parts[1] == "TM":
        return "-".join(parts[2:])
    return "UNK"


def _comment_for_tag(tag: str) -> str:
    return f"V5S-TM-{tag}"


def _action_comment(tag: str, action_code: str) -> str:
    return f"V5S-TM-{tag}-{action_code}"


def _ict_guard_check(cfg: Config, direction: int, entry_price: float) -> Optional[str]:
    """ICT Guard -- checks the NLB/NSB Block. User's own rule: "ob edge of
    bullish ob should be minimum 5 points away for short, ob edge of
    bearish ob should be minimum 5 points away for long" -- "when i say
    edge, bullish ob edge is top, bearish ob edge is bottom." A LONG
    checks every NLB (bearish OB) zone's own BOTTOM edge; a SHORT checks
    every NSB (bullish OB) zone's own TOP edge. Scoped to whatever's
    currently in the block -- nlb_nsb_block.py only ever seeds/tracks
    D1/H4/H1/M30/M15/M5, so M3/M1 are already out of scope by
    construction.

    Reads the block fresh every call (cheap JSON file) rather than
    caching it in memory -- the block is written by a SEPARATE process
    (nlb_nsb_watcher.py), so a cached copy here would silently drift.

    Returns a human-readable block reason if blocked, else None."""
    target_role = "no_long_buffer" if direction == 1 else "no_short_buffer"
    store = nlb_nsb_block.BlockStore(cfg.nlb_nsb_block_state_file)
    for zone in store.zones():
        if zone.role != target_role:
            continue
        edge = zone.btm if target_role == "no_long_buffer" else zone.top
        gap = abs(entry_price - edge)
        if gap < cfg.ict_guard_buffer_points:
            return (f"{zone.timeframe_name} {zone.role} [{zone.btm:.3f}-{zone.top:.3f}] "
                   f"edge@{edge:.3f} is only {gap:.3f}pts away")
    return None


def _open_position(cfg: Config, direction: int, sl: float, comment: str, ref_desc: str) -> bool:
    """Returns True if the entry actually went through (filled, or
    enable_trading is False so it's decision-only and conceptually
    "accepted") -- False only on a genuine order rejection or an ICT
    Guard block. Caller must NOT mark the M5 flip's eligibility consumed
    on a False return -- same "don't drop a genuinely valid setup that
    never got a position" lesson this project has already learned the
    hard way more than once (see RM's own _open_position docstrings)."""
    bid, ask = broker.get_tick_price(cfg.symbol)
    entry_price = ask if direction == 1 else bid
    block_reason = _ict_guard_check(cfg, direction, entry_price)
    if block_reason is not None:
        label = "ICT Long Blocked" if direction == 1 else "ICT Short Blocked"
        msg = f"{label} -- {block_reason} -- {_DIR_LABEL[direction]} ({comment}) skipped"
        print(f"[V5S-ENTRY] {msg}")
        _send_critical_alert(f"\U0001F6D1 {msg}")
        decision_log.log(cfg.decision_log_file, "ict_guard_blocked", direction=_DIR_LABEL[direction],
                         comment=comment, reason=block_reason, entry_price=entry_price)
        return False

    print(f"[V5S-ENTRY] {_DIR_LABEL[direction]} ({comment}) {ref_desc} sl={sl:.3f}")
    if not cfg.enable_trading:
        print("[V5S-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", direction=_DIR_LABEL[direction],
                         comment=comment, sl=sl, entry_price=entry_price)
        return True

    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V5S-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", direction=_DIR_LABEL[direction],
                         comment=comment, retcode=result.retcode, broker_comment=result.comment)
        return False

    print(f"[V5S-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", direction=_DIR_LABEL[direction],
                     comment=comment, ticket=result.ticket, sl=sl, entry_price=entry_price)
    return True


def _close_position(cfg: Config, position, action_label: str, tag: str, action_code: str) -> bool:
    print(f"[V5S-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print("[V5S-EXIT] enable_trading is false -- decision only, no order sent")
        return True

    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(tag, action_code))
    if not result.ok:
        print(f"[V5S-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        return False
    return True


def _run_sl_manager(cfg: Config, mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager, position) -> None:
    """Ongoing (post-breakeven) trailing -- UNCHANGED, still M3's own
    live far ATR trail line regardless of which trigger opened the
    position or which signal set the parent bias. Breakeven itself gates
    on partial booking, not a points threshold -- see sl_manager.py."""
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    far = _far_line_for(cfg.symbol, _M3_MINUTES, direction)
    if far is None:
        print("[V5S-SL] M3 bridge stale/missing -- skipping SL update this cycle")
        return
    current_sl = position.sl if position.sl else None

    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far,
                           tm_mgr.is_partially_cut(position.ticket))
    if proposed is None:
        return

    print(f"[V5S-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print("[V5S-SL] enable_trading is false -- decision only, no modify sent")
        return

    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
    else:
        print(f"[V5S-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _run_trade_manager(cfg: Config, mgr: trade_manager.TradeManager, position) -> None:
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
    entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")

    print(f"[V5S-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V5S-TM] enable_trading is false -- decision only, no close sent")
        return

    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(entry_tag, action_code))
    if not result.ok:
        print(f"[V5S-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def _find_m3_signal(cfg: Config, tracker: BridgeBarFlipTracker,
                    parent: "structure.StructureSignal") -> Optional[tuple[str, str]]:
    """Returns (trigger_type, ref_desc) if M3 qualifies THIS cycle to
    fire against the current M5 bias, else None. trigger_type is
    "FRESH"/"QUALIFY"/"PULLBACK" (see module docstring). Direction is
    always parent.direction -- M3 only ever fires IN AGREEMENT with the
    parent, never against it. Does NOT check eligibility -- caller's
    job, since eligibility also gates the "already open, same
    direction" no-op branch, not just fresh entries."""
    m3 = structure.compute_structure_signal(tracker, cfg.symbol, _M3_MINUTES)
    if m3 is None or m3.direction != parent.direction:
        return None

    if m3.event_time > parent.event_time:
        ref_desc = f"m3_source={m3.source} m3_event={m3.event_time}"
        return "FRESH", ref_desc

    # Trigger 2 -- M3 was already favoring before/at M5's own bias event.
    ref = structure.event_reference(cfg.symbol, _M3_MINUTES, m3.event_time, parent.direction)
    if ref is None:
        return None
    qualifying_price, near = ref

    if parent.direction == 1:
        pullback_target = qualifying_price - _PULLBACK_RETRACE_FRACTION * (qualifying_price - near)
    else:
        pullback_target = qualifying_price + _PULLBACK_RETRACE_FRACTION * (near - qualifying_price)

    bid, ask = broker.get_tick_price(cfg.symbol)
    price = bid if parent.direction == 1 else ask

    qualify_hit = abs(price - qualifying_price) <= _QUALIFY_GATE_POINTS
    pullback_hit = (price <= pullback_target) if parent.direction == 1 else (price >= pullback_target)

    if qualify_hit:
        ref_desc = f"qualifying_price={qualifying_price:.3f} price={price:.3f}"
        return "QUALIFY", ref_desc
    if pullback_hit:
        ref_desc = f"pullback_target={pullback_target:.3f} (40% toward near={near:.3f}) price={price:.3f}"
        return "PULLBACK", ref_desc

    print(f"[V5S] M3 pending -- qualifying={qualifying_price:.3f} (+/-{_QUALIFY_GATE_POINTS}pts) or "
          f"pullback={pullback_target:.3f}, current={price:.3f}")
    return None


def _check_stale(cfg: Config, stale_tracker: StaleAlertTracker) -> None:
    for tf in (_M3_MINUTES, _M5_MINUTES):
        msg = stale_tracker.check(tf, bridge.read_lines(cfg.symbol, tf) is not None)
        if msg is not None:
            print(msg)
            _send_alert(msg)


def run_once(cfg: Config, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            eligibility: M5FlipEligibility, tracker: BridgeBarFlipTracker, stale_tracker: StaleAlertTracker) -> None:
    _check_stale(cfg, stale_tracker)

    parent = structure.compute_structure_signal(tracker, cfg.symbol, _M5_MINUTES)
    if parent is None:
        print("[V5S] waiting for enough bar history (M5 structure signal unavailable)")
        return

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None  # one position at a time, enforced by construction below

    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    if not eligibility.is_traded(parent.event_time):
        signal = _find_m3_signal(cfg, tracker, parent)
        if signal is not None:
            trigger_type, ref_desc = signal
            tag = _tag(trigger_type)
            comment = _comment_for_tag(tag)
            sl = parent.sl_value - cfg.sl_buffer if parent.direction == 1 else parent.sl_value + cfg.sl_buffer

            print(f"[V5S] M3 {trigger_type} -> {_DIR_LABEL[parent.direction]} "
                  f"(parent_source={parent.source}, parent_event={parent.event_time}, {ref_desc})")
            decision_log.log(cfg.decision_log_file, "m3_signal", trigger_type=trigger_type,
                             direction=_DIR_LABEL[parent.direction], parent_source=parent.source,
                             parent_event_time=parent.event_time, sl=sl, ref=ref_desc)

            if position is None:
                if _open_position(cfg, parent.direction, sl, comment, ref_desc):
                    eligibility.mark_traded(parent.event_time)
            else:
                pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                if parent.direction != pos_direction:
                    if (_close_position(cfg, position, "SQOFF", tag, "SQ")
                            and _open_position(cfg, parent.direction, sl, comment, ref_desc)):
                        eligibility.mark_traded(parent.event_time)
                elif tm_mgr.is_partially_cut(position.ticket):
                    if (_close_position(cfg, position, "REFRESH", tag, "RF")
                            and _open_position(cfg, parent.direction, sl, comment, ref_desc)):
                        eligibility.mark_traded(parent.event_time)
                else:
                    eligibility.mark_traded(parent.event_time)
                    msg = (f"[V5S] {tag} qualifies ({_DIR_LABEL[parent.direction]}) but a full-size "
                          f"{_DIR_LABEL[pos_direction]} position is already open on #{position.ticket} "
                          f"-- marked traded, no new entry")
                    print(msg)
                    _send_alert(msg)

            positions = broker.get_positions(cfg.symbol, cfg.magic_number)
            position = positions[0] if positions else None

    if position is not None:
        _run_sl_manager(cfg, sl_mgr, tm_mgr, position)
        _run_trade_manager(cfg, tm_mgr, position)


def main() -> None:
    cfg = load_config()
    print(f"[V5S] starting -- symbol={cfg.symbol} magic={cfg.magic_number} "
          f"enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")

    broker.connect(cfg)
    sl_mgr = sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer)
    tm_mgr = trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                        cfg.partial2_trigger_points, cfg.partial2_fraction)
    eligibility = M5FlipEligibility(cfg.runtime_state_file)
    tracker = BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file)
    stale_tracker = StaleAlertTracker()

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, eligibility, tracker, stale_tracker)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S] cycle error: {exc!r}")
            # Written every iteration regardless of whether run_once
            # raised -- see heartbeat.py's own docstring. A caught cycle
            # error still proves the loop is alive; only a genuine hang
            # inside run_once (or process death) stops this from updating.
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
