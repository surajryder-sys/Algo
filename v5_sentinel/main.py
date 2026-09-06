"""V5-Sentinel Trend Manager -- main loop.

Data path: MT5 bar history (mt5.copy_rates_from_pos via v5_sentinel/
rates.py) is PRIMARY for M5/M15/M3 alike -- no chart or indicator is
required for this bot to run. As of 2026-09-04, the live MQL5 ATR Trail
Dual bridge is additionally consulted (bridge.py) purely as a tie-breaker
whenever it disagrees with that recompute (XAUUSD is volatile enough
that this happens for real -- see bridge.py's docstring); the bridge
itself is never a hard dependency, everything falls back to pure
copy_rates the moment it's missing or stale. M5/ICT and M15/ICT
(OB-formation bias) are NOT implemented yet; both parents are STR-only
for now (see bias.py).

Run with: python -m v5_sentinel.main

Rules implemented here (full design recap):
  - Parent bias: M5 AND M15 both act as parents (2026-09-03 change --
    see bias.compute_parent_bias's own docstring for the full decision
    table). Short version: bullish M3 trades are allowed if EITHER
    parent currently reads bullish; bearish allowed if EITHER reads
    bearish -- no tie-break when they disagree, both directions just
    stay open. A parent currently mid-trap doesn't get a vote; if only
    one parent is trapped the other decides alone, if both are trapped
    both directions stay open.
  - M3 execution: flip_state on M3's own trail lines. A fresh event
    (FLIP or TRAP_RESOLVED) on the LAST CLOSED bar is the only thing that
    ever triggers an entry/exit decision -- a merely-persisting confirmed
    state (no new event this bar) never does anything by itself.
  - A fresh M3 event only leads to a trade if its direction is allowed by
    the current parent bias ("valid setup"). An invalid TRAP_RESOLVED
    leaves any open position alone -- it just waits for its own SL. An
    invalid FLIP instead arms the WATCH ZONE (watch_zone.py, added
    2026-09-07) -- fixes a real gap where the parent gate was rejecting
    exactly the trades that matter most (an M3 flip at the START of a
    real trending move, before the slower parent has caught up -- normal
    M1->M3->M5->M15 lag, not an edge case). A parent flipping into
    agreement shortly after either fires the trade immediately (price
    still within 2pts of M3's own qualifying price) or arms a 45%
    pullback target (recomputed every cycle off that parent's own
    current near trail line) to wait for instead. See watch_zone.py for
    the full design, including cancellation and the "one trade per flip"
    rule as it applies here.
  - No position open + valid fresh event -> open a new position (full
    lot size) in that direction.
  - Position open, valid fresh event, OPPOSITE direction -> square off
    the current position, open a new one in the new direction.
  - Position open, valid fresh event, SAME direction (structurally only
    possible via TRAP_RESOLVED, since FLIP always changes the confirmed
    side) -> only refresh (square off leftover + reopen full size) if the
    current position has already been partially cut by Trade Manager;
    a still-full-size matching position has nothing to refresh.
  - Every cycle, regardless of the above: SL Manager and Trade Manager
    both run against whatever position ends up open (or the fresh one
    just opened this same cycle).
  - Only ever one position at a time.

Safety: V5S_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import bias, bridge, broker, flip_state, rates, sl_manager, trade_manager, watch_zone
from v5_sentinel.config import Config, load_config

_M3_MINUTES = 3
_DIR_LABEL = {1: "BUY", -1: "SELL"}


class RuntimeState:
    """Persists the bar_time of the last M3 event actually EVALUATED, so a
    fresh event only ever gets acted on once. FIXED 2026-09-03, confirmed
    live: flip_state.event_just_happened() stays True for the entire
    ~3-minute window that bar remains the most recent CLOSED one, not just
    the single poll right after it closed -- without this dedup, the same
    decision (square-off + reopen, in a live valid case) would have fired
    on every ~1s poll for the whole window instead of once. This specific
    incident was a SELL flip against a bullish M5 Bias (valid=False), so
    the order-sending code path was never reached and no position was
    ever opened -- confirmed by checking live positions afterward -- but
    a VALID matching event would have repeatedly re-fired real orders."""

    def __init__(self, path: str):
        self._path = Path(path)
        self.last_m3_event_time: Optional[int] = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self.last_m3_event_time = data.get("last_m3_event_time")
        except (json.JSONDecodeError, OSError, TypeError):
            self.last_m3_event_time = None

    def mark_seen(self, bar_time: int) -> None:
        self.last_m3_event_time = bar_time
        self._path.write_text(json.dumps({"last_m3_event_time": bar_time}))


def _far_line_for(direction: int, m3_series: rates.TrailSeries) -> float:
    far, _near = flip_state.far_near_line(direction, m3_series.trail1[-1], m3_series.trail2[-1])
    return far


def _parent_tag(parent: "bias.ParentBiasResult", direction: int) -> str:
    """Which parent to credit in the entry comment (2026-09-04 naming
    convention, confirmed with the user). M5_ONLY/M15_ONLY are literal --
    that parent alone decided. AGREE (both clear, same direction) defaults
    to M5, since both back it equally. DISAGREE picks whichever parent's
    own confirmed direction actually matches this trade's direction --
    exactly one always does, since disagreeing means one is bull and the
    other bear. BOTH_TRAPPED writes "M15M5" (not the word "both") --
    neither parent is really deciding in that case, both vetoes are just
    off."""
    if parent.source == "M5_ONLY":
        return "M5"
    if parent.source == "M15_ONLY":
        return "M15"
    if parent.source == "BOTH_TRAPPED":
        return "M15M5"
    if parent.source == "DISAGREE":
        return "M5" if parent.m5.confirmed.value == direction else "M15"
    return "M5"  # AGREE


def _tag(parent: "bias.ParentBiasResult", direction: int, label: str) -> str:
    """"{parent}/3{F|T}" -- e.g. "M5/3F", "M15M5/3T". The core identity
    piece shared by every comment this bot writes."""
    event_code = "F" if label == "FLIP" else "T"
    return f"{_parent_tag(parent, direction)}/3{event_code}"


def _extract_tag(comment: str) -> str:
    """Pulls the tag back out of one of our own past comments
    (V5S-TM-{tag}-...) -- used to carry an ENTRY's own tag forward onto
    its later partial-booking comments, so a partial-close deal can be
    traced back to what opened the position by comment alone (2026-09-04,
    confirmed with the user). Joins everything from parts[2] onward
    (not just parts[2] alone) -- 2026-09-07: watch-zone tags like "M5-PB"
    contain a "-" themselves, so a naive parts[2] would silently truncate
    them to "M5" on a later partial-booking comment. Falls back to "UNK"
    if the given comment isn't in our own format (predates this scheme,
    missing, etc.) -- never raises."""
    parts = comment.split("-") if comment else []
    if len(parts) >= 3 and parts[0] == "V5S" and parts[1] == "TM":
        return "-".join(parts[2:])
    return "UNK"


def _comment_for_tag(tag: str) -> str:
    """"V5S-TM-{tag}" -- the shared entry-comment shape, e.g.
    "V5S-TM-M5/3F", "V5S-TM-M15M5/3T", or a watch-zone tag like
    "V5S-TM-M5/5F" (straight-fire) / "V5S-TM-M5-PB" (pullback-triggered,
    see watch_zone.py). No trailing timestamp (dropped 2026-09-07 at the
    user's request -- purely cosmetic, nothing in the logic ever read it
    back; _extract_tag() only ever looks at parts[0]/[1]/[2:], and the
    bar-time dedup that actually matters lives in RuntimeState's own
    JSON, not the comment string)."""
    return f"V5S-TM-{tag}"


def _entry_comment(parent: "bias.ParentBiasResult", direction: int, label: str) -> str:
    return _comment_for_tag(_tag(parent, direction, label))


def _action_comment(tag: str, action_code: str) -> str:
    """"V5S-TM-{tag}-{action_code}" -- the shared shape for every non-entry
    comment (P1/P2/SQ/RF). action_code is kept to 2 letters deliberately:
    the worst-case tag ("M15M5/3F") plus a longer word like "SQOFF" would
    exceed MT5's real 31-char comment limit on this account (confirmed:
    "V5S-TM-M15M5/3F-SQOFF" territory) -- P1/P2/SQ/RF all stay comfortably
    under it instead. No trailing timestamp, see _entry_comment()."""
    return f"V5S-TM-{tag}-{action_code}"


def _open_position(cfg: Config, direction: int, m3_series: rates.TrailSeries, comment: str) -> None:
    far = _far_line_for(direction, m3_series)
    sl = far - cfg.sl_buffer if direction == 1 else far + cfg.sl_buffer

    print(f"[V5S-ENTRY] {_DIR_LABEL[direction]} ({comment}) far_line={far:.3f} sl={sl:.3f}")
    if not cfg.enable_trading:
        print("[V5S-ENTRY] enable_trading is false -- decision only, no order sent")
        return

    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V5S-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
    else:
        print(f"[V5S-ENTRY] filled, ticket={result.ticket}")


def _close_position(cfg: Config, position, action_label: str, tag: str, action_code: str) -> bool:
    """action_label is the human-readable log word (e.g. "SQOFF"); tag is
    the "{parent}/3{F|T}" of whatever NEW event triggered this close (not
    the position's own entry tag -- SQOFF/REFRESH are about why it's
    closing now, unlike P1/P2 which trace back to the entry instead, see
    _run_trade_manager); action_code is the short comment code (SQ/RF)."""
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


def _apply_signal(cfg: Config, position, new_dir: int, tag: str, m3_series: rates.TrailSeries,
                  tm_mgr: trade_manager.TradeManager):
    """Shared position-lifecycle branching -- used by BOTH a normal valid
    M3 event and a watch-zone-driven signal (2026-09-07), so the two
    trigger sources behave identically once a direction+tag is decided:
    no position -> open; opposite direction -> square off + reopen;
    same direction + already partially cut -> refresh (square off +
    reopen full size); same direction + still full size -> no-op.
    Returns the resulting position (re-queried after any broker action)."""
    comment = _comment_for_tag(tag)
    if position is None:
        _open_position(cfg, new_dir, m3_series, comment)
        positions = broker.get_positions(cfg.symbol, cfg.magic_number)
        return positions[0] if positions else None

    pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    if new_dir != pos_direction:
        if _close_position(cfg, position, "SQOFF", tag, "SQ"):
            _open_position(cfg, new_dir, m3_series, comment)
            positions = broker.get_positions(cfg.symbol, cfg.magic_number)
            return positions[0] if positions else None
        return position

    if tm_mgr.is_partially_cut(position.ticket):
        if _close_position(cfg, position, "REFRESH", tag, "RF"):
            _open_position(cfg, new_dir, m3_series, comment)
            positions = broker.get_positions(cfg.symbol, cfg.magic_number)
            return positions[0] if positions else None
        return position

    return position  # same direction, still full size -- nothing to refresh


def _run_sl_manager(cfg: Config, mgr: sl_manager.SLManager, position, m3_series: rates.TrailSeries) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask  # the side that matters for "favor" is the closing side
    far = _far_line_for(direction, m3_series)
    current_sl = position.sl if position.sl else None

    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl, far)
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
    # Traces back to the ENTRY's own tag (not any current event) -- this
    # is a pure profit-threshold booking, nothing new "happened" on M3 to
    # tag it with. get_entry_comment() is the position's comment as it
    # read at first sighting, before MT5 overwrote it on the broker side.
    entry_tag = _extract_tag(mgr.get_entry_comment(position.ticket) or "")

    print(f"[V5S-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V5S-TM] enable_trading is false -- decision only, no close sent")
        return

    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(entry_tag, action_code))
    if not result.ok:
        print(f"[V5S-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def _check_watch_zone(cfg: Config, wz_store: watch_zone.WatchZoneStore, parent: "bias.ParentBiasResult",
                      fs_m3: "flip_state.FlipStateResult", m5_series: Optional[rates.TrailSeries],
                      m15_series: Optional[rates.TrailSeries]) -> Optional[tuple[int, str]]:
    """Runs the watch-zone state machine for one cycle -- see
    watch_zone.py for the full design. Returns (direction, tag) if a
    watch-zone-driven trade should fire THIS cycle, else None. Arming a
    new zone and cancelling an invalidated one both happen here as a
    side effect regardless of whether a trade fires."""
    zone = wz_store.zone

    # Cancellation -- M3 entering a trap, or flipping to a DIFFERENT
    # confirmed direction than the zone's own, both invalidate it.
    if zone is not None and (fs_m3.watching is not None or fs_m3.confirmed.value != zone.direction):
        reason = "M3 trapped" if fs_m3.watching is not None else "M3 flipped opposite"
        print(f"[V5S-WATCHZONE] cancelled ({reason}) -- was {_DIR_LABEL[zone.direction]} "
              f"@ {zone.qualifying_price:.3f}")
        wz_store.cancel()
        zone = None

    if zone is None:
        return None

    # Look for the MOST RECENT qualifying parent flip (recency-first,
    # 2026-09-07: a later parent event supersedes an earlier one still
    # pending) -- M5 or M15, matching the zone's own direction.
    candidates = []
    for name, tf_code, fs in (("M5", "5", parent.m5), ("M15", "15", parent.m15)):
        if fs is not None and fs.event_just_happened() and fs.confirmed.value == zone.direction:
            candidates.append((fs.last_event.bar_time, name, tf_code, fs.last_close))

    if candidates:
        candidates.sort()
        bar_time, name, tf_code, close = candidates[-1]
        already = zone.pending is not None and zone.pending.source_bar_time == bar_time
        if not already:
            gap = abs(close - zone.qualifying_price)
            if gap <= watch_zone.PULLBACK_GATE_POINTS:
                print(f"[V5S-WATCHZONE] {name} confirms within {gap:.3f}pts of qualifying price "
                      f"{zone.qualifying_price:.3f} -- straight fire")
                wz_store.clear()
                return zone.direction, f"{name}/{tf_code}F"
            print(f"[V5S-WATCHZONE] {name} confirms {gap:.3f}pts away -- arming 45% pullback target")
            wz_store.set_pending(name, tf_code, close, bar_time)

    zone = wz_store.zone  # re-read -- set_pending() above may have just changed it
    if zone is not None and zone.pending is not None:
        p = zone.pending
        parent_series = m5_series if p.parent_tf_code == "5" else m15_series
        if (parent_series is not None and parent_series.trail1[-1] is not None
                and parent_series.trail2[-1] is not None):
            _far, near = flip_state.far_near_line(zone.direction, parent_series.trail1[-1], parent_series.trail2[-1])
            if zone.direction == 1:
                target = p.anchor_close - watch_zone.PULLBACK_RETRACE_FRACTION * (p.anchor_close - near)
            else:
                target = p.anchor_close + watch_zone.PULLBACK_RETRACE_FRACTION * (near - p.anchor_close)

            bid, ask = broker.get_tick_price(cfg.symbol)
            price = bid if zone.direction == 1 else ask
            reached = price <= target if zone.direction == 1 else price >= target
            if reached:
                print(f"[V5S-WATCHZONE] {p.parent_name} pullback target {target:.3f} reached (price={price:.3f})")
                wz_store.clear()
                return zone.direction, f"{p.parent_name}-PB"
            print(f"[V5S-WATCHZONE] pending {p.parent_name} pullback -- target={target:.3f} current={price:.3f}")

    return None


def run_once(cfg: Config, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            runtime: RuntimeState, wz_store: watch_zone.WatchZoneStore) -> None:
    parent = bias.compute_parent_bias(cfg.symbol)
    m3_series = rates.read_trail_series(cfg.symbol, _M3_MINUTES)
    if parent is None or m3_series is None:
        print("[V5S] waiting for enough bar history (parent bias / M3 series unavailable)")
        return
    fs_m3 = flip_state.compute(m3_series)
    if fs_m3 is None:
        print("[V5S] waiting for enough M3 bar history for flip_state")
        return
    fs_m3 = bridge.reconcile(fs_m3, m3_series)

    # Needed for the watch-zone's parent-confirmation + pullback-target
    # calc (2026-09-07) -- parent.m5/parent.m15 already carry the
    # FlipStateResult, but the pullback target needs each parent's own
    # raw trail1/trail2 values too, which only the series itself has.
    m5_series = rates.read_trail_series(cfg.symbol, 5)
    m15_series = rates.read_trail_series(cfg.symbol, 15)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None  # one position at a time, enforced by construction below

    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    # event_just_happened() alone stays True for the whole ~3-minute window
    # this bar remains the most recent CLOSED one -- the bar_time dedup
    # below is what makes this fire exactly ONCE per genuine event, not
    # once per poll while it's still the latest bar. See RuntimeState.
    if fs_m3.event_just_happened() and fs_m3.last_event.bar_time != runtime.last_m3_event_time:
        runtime.mark_seen(fs_m3.last_event.bar_time)
        event = fs_m3.last_event
        new_dir = event.confirmed.value
        valid = parent.allows(new_dir)
        label = event.event_type.value

        print(f"[V5S] M3 {label} -> {_DIR_LABEL[new_dir]} at {event.bar_time} "
              f"(parent={parent.source}, M5={parent.m5.label()}, M15={parent.m15.label()}, "
              f"bull_allowed={parent.bull_allowed}, bear_allowed={parent.bear_allowed}, valid={valid})")

        if valid:
            new_event_tag = _tag(parent, new_dir, label)
            position = _apply_signal(cfg, position, new_dir, new_event_tag, m3_series, tm_mgr)
        elif label == "FLIP":
            # Invalid FLIP (not TRAP_RESOLVED, see watch_zone.py's own
            # docstring for why that's excluded) -- 2026-09-07: park it in
            # the watch zone instead of dropping it outright, so a parent
            # catching up shortly after still gets to trade the move.
            wz_store.arm(new_dir, fs_m3.last_close, event.bar_time)
            print(f"[V5S-WATCHZONE] armed {_DIR_LABEL[new_dir]} @ {fs_m3.last_close:.3f} (parent disagrees)")
        # else: invalid TRAP_RESOLVED -- leave any open position alone, it waits on its own SL (unchanged)

    wz_signal = _check_watch_zone(cfg, wz_store, parent, fs_m3, m5_series, m15_series)
    if wz_signal is not None:
        wz_dir, wz_tag = wz_signal
        position = _apply_signal(cfg, position, wz_dir, wz_tag, m3_series, tm_mgr)

    if position is not None:
        _run_sl_manager(cfg, sl_mgr, position, m3_series)
        _run_trade_manager(cfg, tm_mgr, position)


def main() -> None:
    cfg = load_config()
    print(f"[V5S] starting -- symbol={cfg.symbol} magic={cfg.magic_number} "
          f"enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")

    broker.connect(cfg)
    sl_mgr = sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer)
    tm_mgr = trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                        cfg.partial2_trigger_points, cfg.partial2_fraction)
    runtime = RuntimeState(cfg.runtime_state_file)
    wz_store = watch_zone.WatchZoneStore(cfg.watch_zone_state_file)

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, runtime, wz_store)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S] cycle error: {exc!r}")
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
