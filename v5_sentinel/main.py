"""V5-Sentinel Trend Manager -- main loop.

Data path: MT5 bar history only (mt5.copy_rates_from_pos via
v5_sentinel/rates.py) -- no chart, no MQL5 indicator, no bridge JSON file
anywhere in this bot. M5/ICT and M15/ICT (OB-formation bias) are NOT
implemented yet; both parents are STR-only for now (see bias.py).

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
    the current parent bias ("valid setup"). An event that isn't allowed
    leaves any open position alone -- it just waits for its own SL.
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

from v5_sentinel import bias, broker, flip_state, rates, sl_manager, trade_manager
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


def _comment(action: str, code: str = "") -> str:
    """"V5S" = V5-Sentinel -- same pattern v4/trend_manager/main.py's own
    _comment() uses: no L/S direction field (the position's own buy/sell
    type already shows that), just a unix timestamp for uniqueness. e.g.
    "V5S-ENTRY-FLIP-1788373080" -- well under MT5's real 31-char comment
    limit (confirmed on this same account by V4's own comment testing)."""
    parts = ["V5S", action] + ([code] if code else []) + [str(int(time.time()))]
    return "-".join(parts)


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


def _entry_comment(parent: "bias.ParentBiasResult", direction: int, label: str) -> str:
    """"V5S-TM-{parent}/3{F|T}-<unix ts>" -- e.g. "V5S-TM-M5/3F-1788528601"
    or "V5S-TM-M15M5/3T-1788528601". TM = Trend Manager. Confirmed with
    the user this replaces the old "V5S-ENTRY-FLIP/TRAP-<ts>" shape for
    OPEN trades specifically -- exits (SQOFF/REFRESH/PARTIAL1/PARTIAL2)
    keep their existing _comment() format, unchanged."""
    event_code = "F" if label == "FLIP" else "T"
    return f"V5S-TM-{_parent_tag(parent, direction)}/3{event_code}-{int(time.time())}"


def _open_position(cfg: Config, direction: int, m3_series: rates.TrailSeries, label: str,
                   parent: "bias.ParentBiasResult") -> None:
    far = _far_line_for(direction, m3_series)
    sl = far - cfg.sl_buffer if direction == 1 else far + cfg.sl_buffer
    comment = _entry_comment(parent, direction, label)

    print(f"[V5S-ENTRY] {_DIR_LABEL[direction]} ({label}) far_line={far:.3f} sl={sl:.3f}")
    if not cfg.enable_trading:
        print("[V5S-ENTRY] enable_trading is false -- decision only, no order sent")
        return

    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, comment)
    if not result.ok:
        print(f"[V5S-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
    else:
        print(f"[V5S-ENTRY] filled, ticket={result.ticket}")


def _close_position(cfg: Config, position, action: str) -> bool:
    print(f"[V5S-EXIT] closing #{position.ticket} ({action}), volume={position.volume}")
    if not cfg.enable_trading:
        print("[V5S-EXIT] enable_trading is false -- decision only, no order sent")
        return True

    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, comment=_comment(action))
    if not result.ok:
        print(f"[V5S-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        return False
    return True


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
    print(f"[V5S-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V5S-TM] enable_trading is false -- decision only, no close sent")
        return

    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_comment(label.upper()))
    if not result.ok:
        print(f"[V5S-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def run_once(cfg: Config, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            runtime: RuntimeState) -> None:
    parent = bias.compute_parent_bias(cfg.symbol)
    m3_series = rates.read_trail_series(cfg.symbol, _M3_MINUTES)
    if parent is None or m3_series is None:
        print("[V5S] waiting for enough bar history (parent bias / M3 series unavailable)")
        return
    fs_m3 = flip_state.compute(m3_series)
    if fs_m3 is None:
        print("[V5S] waiting for enough M3 bar history for flip_state")
        return

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
            if position is None:
                _open_position(cfg, new_dir, m3_series, label, parent)
                positions = broker.get_positions(cfg.symbol, cfg.magic_number)
                position = positions[0] if positions else None
            else:
                pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
                if new_dir != pos_direction:
                    # valid opposite setup -- square off, then open fresh opposite
                    if _close_position(cfg, position, "SQOFF"):
                        _open_position(cfg, new_dir, m3_series, label, parent)
                        positions = broker.get_positions(cfg.symbol, cfg.magic_number)
                        position = positions[0] if positions else None
                elif tm_mgr.is_partially_cut(position.ticket):
                    # same-direction fresh signal on an already-cut-down
                    # position -- refresh to full size
                    if _close_position(cfg, position, "REFRESH"):
                        _open_position(cfg, new_dir, m3_series, label, parent)
                        positions = broker.get_positions(cfg.symbol, cfg.magic_number)
                        position = positions[0] if positions else None
                # else: same direction, still full size -- nothing to refresh
        # else: no valid opposite/matching setup -- leave any open position alone, it waits on its own SL

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

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, runtime)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S] cycle error: {exc!r}")
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
