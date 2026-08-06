"""Main loop for the FX cross-pairs bot: H1 order-block pullback entries
across every symbol in cfg.symbols, plus per-position trailing SL and a
bias/opposite-OB exit once filled (see management.py). No market entries --
every entry is a pending order.

Run with: python -m algo_v2_fx.main

Safety: FX_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent. Left unset (default false), every decision is
printed but nothing touches the account.
"""
from __future__ import annotations

import time
from typing import Optional

import MetaTrader5 as mt5

from ob_bridge.reader import read_zone, Zone
from algo_v2_fx import broker
from algo_v2_fx.config import Config, load_config
from algo_v2_fx.entries import pullback_entry
from algo_v2_fx.management import compute_trailing_sl, fresh_opposite_ob_exists
from algo_v2_fx.state_store import TradedZoneStore

_BASE36_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_COMMENT_EPOCH = 1735689600  # 2025-01-01T00:00:00Z, same epoch as algo_v2/candidates.py
_DIR_CODE = {1: "B", -1: "S"}
_CODE_DIR = {v: k for k, v in _DIR_CODE.items()}
COMMENT_PREFIX = "FX"


def _to_base36(n: int) -> str:
    if n <= 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_BASE36_DIGITS[r])
    return "".join(reversed(out))


def _zone_key(symbol: str, direction: int, start_time: int) -> str:
    return f"{symbol}|{direction}|{start_time}"


def _order_comment(direction: int, start_time: int) -> str:
    """"FX|" + 1 direction char + up to 6 base36 time digits -- well under
    MT5's observed 16-char comment truncation (see algo_v2/candidates.py for
    where that limit was confirmed live). No timeframe code needed here
    (H1-only), no symbol either -- every lookup already queries MT5 filtered
    to one symbol, so the symbol is implicit from which query found it."""
    time_code = _to_base36(start_time - _COMMENT_EPOCH)
    return f"{COMMENT_PREFIX}|{_DIR_CODE[direction]}{time_code}"


def _parse_order_comment(symbol: str, comment: str) -> Optional[tuple[str, int]]:
    """Returns (zone_key, direction) for a comment this bot wrote, or None."""
    if not comment or not comment.startswith(COMMENT_PREFIX + "|"):
        return None
    rest = comment[len(COMMENT_PREFIX) + 1:]
    if len(rest) < 2:
        return None
    direction = _CODE_DIR.get(rest[0])
    time_code = rest[1:]
    if direction is None or not time_code:
        return None
    try:
        start_time = int(time_code, 36) + _COMMENT_EPOCH
    except ValueError:
        return None
    return _zone_key(symbol, direction, start_time), direction


def sync_filled_zones(cfg: Config, symbol: str, store: TradedZoneStore) -> None:
    """Marks a zone traded only once a live position carrying its comment
    actually exists -- i.e. its pending order filled. Same pattern as
    algo_v2.main.sync_filled_zones."""
    for pos in broker.get_positions(symbol, cfg.magic_number):
        parsed = _parse_order_comment(symbol, pos.comment)
        if parsed is None:
            continue
        zone_key, _direction = parsed
        if not store.is_traded(zone_key):
            print(f"[SYNC] {symbol} zone {zone_key} confirmed filled via position #{pos.ticket}")
            store.mark_traded(zone_key)


def manage_open_positions(cfg: Config, symbol: str, h1) -> None:
    """Bias/opposite-OB exit first, then trailing SL for whatever's still
    open after that -- same order algo_v2/main.py uses (close on a fresh
    opposite OB takes priority over trailing a position that's about to be
    closed anyway). See management.py for both."""
    positions = broker.get_positions(symbol, cfg.magic_number)
    if not positions:
        return

    bid, ask = broker.get_tick_price(symbol)
    current_price = (bid + ask) / 2.0

    for pos in positions:
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1

        if fresh_opposite_ob_exists(h1, direction, int(pos.time)):
            print(f"[EXIT] {symbol} closing #{pos.ticket} "
                  f"{'BUY' if direction == 1 else 'SELL'}: fresh opposite H1 OB since entry")
            if cfg.enable_trading:
                broker.close_position(symbol, pos, cfg.deviation_points)
            continue  # closed (or would close in dry-run) -- skip trailing it

        history = h1.bull if direction == 1 else h1.bear
        zone = history[0] if history else None
        new_sl = compute_trailing_sl(direction, current_price, pos.sl or None, zone)
        if new_sl is not None:
            print(f"[TRAIL] {symbol} #{pos.ticket} SL -> {new_sl}")
            if cfg.enable_trading:
                broker.modify_position_sl(symbol, pos.ticket, new_sl, pos.tp)


def run_symbol(cfg: Config, symbol: str, store: TradedZoneStore) -> None:
    h1 = read_zone(symbol, 60)
    if h1 is None or h1.is_stale():
        return

    if cfg.enable_trading:
        sync_filled_zones(cfg, symbol, store)

    manage_open_positions(cfg, symbol, h1)

    # One trade at a time per symbol -- an open position (still open after
    # manage_open_positions above -- a bias-exit close this same poll frees
    # it up again next poll, not this one) or a resting pending order both
    # block a new entry, regardless of direction.
    if broker.get_positions(symbol, cfg.magic_number):
        return
    if broker.get_pending_orders(symbol, cfg.magic_number):
        return

    for direction, history in ((1, h1.bull), (-1, h1.bear)):
        if not history:
            continue
        zone: Zone = history[0]
        if not zone.virgin or zone.detected_time <= 0:
            continue

        zone_key = _zone_key(symbol, direction, zone.start_time)
        if store.is_traded(zone_key):
            continue

        plan = pullback_entry(direction, zone)
        if plan is None:
            continue

        comment = _order_comment(direction, zone.start_time)
        side = "BUY" if direction == 1 else "SELL"
        print(f"[ENTRY] {symbol} H1 {side} pending @ {plan.entry_price} sl={plan.sl}")
        if cfg.enable_trading:
            result = broker.send_pending_order(symbol, direction, plan.entry_price, cfg.lots,
                                               plan.sl, cfg.magic_number, cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY] {symbol} pending order failed: {result.retcode} {result.comment}")
        # Only one entry attempt per symbol per poll -- if both directions
        # somehow have a fresh virgin zone at once, the next poll picks up
        # whichever's left (still gated by the pending/position check above
        # until this one fills, gets manually cleared, or expires).
        return


def run_once(cfg: Config, store: TradedZoneStore) -> None:
    for symbol in cfg.symbols:
        try:
            run_symbol(cfg, symbol, store)
        except Exception as exc:
            print(f"[ERROR] {symbol}: {exc}")


def main() -> None:
    cfg = load_config()
    print(f"FX bot starting | symbols={','.join(cfg.symbols)} lots={cfg.lots} "
          f"magic={cfg.magic_number} trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)

    try:
        while True:
            try:
                run_once(cfg, store)
            except Exception as exc:
                print(f"[ERROR] {exc}")
                # Same self-healing reconnect as algo_v2/main.py -- the MT5
                # IPC channel can get stuck without the process crashing;
                # re-initialize only, no shutdown() first (confirmed live to
                # hang when the channel's already broken).
                try:
                    broker.connect(cfg)
                    print("[RECOVERY] reconnected to MT5")
                except Exception as reconnect_exc:
                    print(f"[RECOVERY] reconnect failed: {reconnect_exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
