"""Main loop: bridge -> bias -> gating -> candidates -> order execution.

Run with: python -m eth_smc.main

Fully independent from the XAUUSD bot (algo/main.py): separate MT5 terminal
connection (see eth_smc/config.py), separate state/block files, separate
symbol-scoped bridge control files (eth_smc/blocking.py) -- safe to run
alongside the XAUUSD bot without either one affecting the other.

Safety: ETH_SMC_ENABLE_TRADING must be explicitly set to true in .env for
any order to actually be sent/modified/cancelled. Left unset (default
false), every decision is printed but nothing touches the account -- use
this to watch the bot's decisions against a demo/live chart before turning
it on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from eth_smc.bridge_reader import read_zone, OBSnapshot
from eth_smc import broker
from eth_smc.alerts import AlertedZoneStore, check_virgin_zone_alerts
from eth_smc.bias import compute_bias, TFBias, allowed_entry_sources
from eth_smc.blocking import BlockedZoneStore, check_reset_requests
from eth_smc.candidates import (
    build_m5_candidate, build_m15_candidate, build_m30_candidate,
    choose_winning_candidate, should_replace_pending, current_zone_key,
    order_comment, parse_order_comment,
)
from eth_smc.config import Config, load_config
from eth_smc.entries import EntryMode
from eth_smc.intervention import check_manual_pending_cancellations, check_manual_position_closes
from eth_smc.management import compute_trailing_sl, bias_flip_exit_direction
from eth_smc.state_store import TradedZoneStore


@dataclass
class RuntimeState:
    """Tickets seen on the previous poll, so a disappeared ticket can be
    classified as filled / bot-cancelled / manually cancelled."""
    seen_pending_tickets: set = field(default_factory=set)
    seen_position_tickets: set = field(default_factory=set)
    # Tickets the bot itself just cancelled -- checked (and consumed) by
    # check_manual_pending_cancellations so a bot-initiated cancel is never
    # mistaken for a manual one. ORDER_REASON can't be used for this (it
    # reflects who created the order, not who cancelled it).
    expected_cancellations: set = field(default_factory=set)


def _tf_bias(snap: Optional[OBSnapshot]) -> TFBias:
    if snap is None:
        return TFBias(0, 0)
    return TFBias(snap.bias, snap.latest_time)


def _direction_edges(direction: int, m5: Optional[OBSnapshot], m15: Optional[OBSnapshot],
                     m30: Optional[OBSnapshot]) -> dict:
    def edge(snap: Optional[OBSnapshot]):
        if snap is None:
            return None
        history = snap.bull if direction == 1 else snap.bear
        if not history:
            return None
        return history[0].low if direction == 1 else history[0].high

    return {"M5": edge(m5), "M15": edge(m15), "M30": edge(m30)}


def sync_filled_zones(cfg: Config, store: TradedZoneStore) -> None:
    """Marks a zone traded only once a live position carrying its comment
    actually exists -- i.e. its pending order filled. Runs every loop so a
    fill is caught on the next poll after it happens."""
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        parsed = parse_order_comment(pos.comment)
        if parsed is None:
            continue
        zone_key, _event_time = parsed
        if not store.is_traded(zone_key):
            print(f"[SYNC] zone {zone_key} confirmed filled via position #{pos.ticket}")
            store.mark_traded(zone_key)


def sync_manual_intervention(cfg: Config, blocked: BlockedZoneStore, runtime: RuntimeState) -> None:
    """Compares this poll's live pending/position tickets against last
    poll's, and blocks the underlying zone for any that disappeared due to
    a manual (client/mobile/web) cancel or close -- never for a fill, a
    bot-initiated action, or an SL/TP/stop-out."""
    current_pending = {o.ticket for o in broker.get_pending_orders(cfg.symbol, cfg.magic_number)}
    current_positions = {p.ticket for p in broker.get_positions(cfg.symbol, cfg.magic_number)}

    disappeared_pending = runtime.seen_pending_tickets - current_pending
    disappeared_positions = runtime.seen_position_tickets - current_positions

    if disappeared_pending:
        for source_tf, zone_key in check_manual_pending_cancellations(disappeared_pending, runtime.expected_cancellations):
            print(f"[BLOCK] manual pending cancellation -> blocking {source_tf} zone {zone_key}")
            blocked.block(source_tf, zone_key, reason="manual_cancel")

    if disappeared_positions:
        for source_tf, zone_key in check_manual_position_closes(disappeared_positions):
            print(f"[BLOCK] manual position close -> blocking {source_tf} zone {zone_key}")
            blocked.block(source_tf, zone_key, reason="manual_close")

    runtime.seen_pending_tickets = current_pending
    runtime.seen_position_tickets = current_positions


def release_stale_blocks(blocked: BlockedZoneStore, m5: Optional[OBSnapshot],
                         m15: Optional[OBSnapshot], m30: Optional[OBSnapshot]) -> None:
    for source_tf, snap in (("M5", m5), ("M15", m15), ("M30", m30)):
        for direction in (1, -1):
            latest_key = current_zone_key(source_tf, snap, direction)
            blocked.release_if_stale(source_tf, direction, latest_key)


def run_once(cfg: Config, store: TradedZoneStore, blocked: BlockedZoneStore, runtime: RuntimeState,
             alerts: AlertedZoneStore) -> None:
    m5 = read_zone(cfg.symbol, 5)
    m15 = read_zone(cfg.symbol, 15)
    m30 = read_zone(cfg.symbol, 30)

    bias = compute_bias(_tf_bias(m5), _tf_bias(m15), _tf_bias(m30))
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = (bid + ask) / 2.0

    # Always check for a chart reset-button press, regardless of trading mode.
    check_reset_requests(blocked)

    if cfg.enable_trading:
        # 0a. Confirm any pending orders that filled since the last poll.
        sync_filled_zones(cfg, store)
        # 0b. Detect manual cancels/closes since the last poll and block
        #     their zones; auto-release blocks a new same-direction OB
        #     has superseded.
        sync_manual_intervention(cfg, blocked, runtime)
        release_stale_blocks(blocked, m5, m15, m30)
        check_virgin_zone_alerts(cfg, current_price, alerts)

    # 1. Strong forces the opposite direction closed/blocked, unconditionally --
    #    even a ShortTerm-protected coexisting position on that side.
    exit_dir = bias_flip_exit_direction(bias)
    if exit_dir is not None:
        for pos in broker.get_positions_by_direction(cfg.symbol, cfg.magic_number, exit_dir):
            print(f"[EXIT] closing {'BUY' if exit_dir == 1 else 'SELL'} position #{pos.ticket}: Strong bias flip")
            if cfg.enable_trading:
                broker.close_position(cfg.symbol, pos, cfg.deviation_points)
        for order in broker.get_pending_orders_by_direction(cfg.symbol, cfg.magic_number, exit_dir):
            print(f"[EXIT] cancelling pending #{order.ticket}: Strong bias flip")
            if cfg.enable_trading:
                runtime.expected_cancellations.add(order.ticket)
                broker.cancel_pending_order(order.ticket)

    # 2. Trail every open position in its own direction, regardless of which
    #    source timeframe opened it.
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        edges = _direction_edges(direction, m5, m15, m30)
        new_sl = compute_trailing_sl(direction, current_price, pos.sl or None, edges)
        if new_sl is not None:
            print(f"[TRAIL] #{pos.ticket} {'BUY' if direction == 1 else 'SELL'} SL -> {new_sl}")
            if cfg.enable_trading:
                broker.modify_position_sl(cfg.symbol, pos.ticket, new_sl, pos.tp)

    # 3. New entries, only in the current bias direction.
    if bias.direction == 0:
        return

    # Already holding a position in this direction -- don't stack another.
    if broker.get_positions_by_direction(cfg.symbol, cfg.magic_number, bias.direction):
        return

    sources = allowed_entry_sources(bias)
    candidates = []

    def eligible(c) -> bool:
        return c is not None and not store.is_traded(c.zone_key) and not blocked.is_blocked(c.source_tf, c.zone_key)

    if "M5" in sources:
        c = build_m5_candidate(bias.direction, m5, m15, m30)
        if eligible(c):
            candidates.append(c)

    if "M15" in sources:
        c = build_m15_candidate(bias.direction, m15, m5, m30)
        if eligible(c):
            candidates.append(c)

    if "M30" in sources:
        c = build_m30_candidate(bias.direction, m30, m5, m15)
        if eligible(c):
            candidates.append(c)

    winner = choose_winning_candidate(candidates, current_price)
    if winner is None:
        return

    pending_orders = broker.get_pending_orders_by_direction(cfg.symbol, cfg.magic_number, bias.direction)
    pending_ticket = None
    pending_zone_key = None
    pending_entry_price = None
    if pending_orders:
        pending_ticket = pending_orders[0].ticket
        pending_entry_price = pending_orders[0].price_open
        parsed = parse_order_comment(pending_orders[0].comment)
        if parsed is not None:
            pending_zone_key, _pending_event_time = parsed

    if not should_replace_pending(winner, pending_zone_key, pending_entry_price, current_price):
        return

    if pending_ticket is not None:
        print(f"[REPLACE] cancelling pending #{pending_ticket} for closer {winner.source_tf} setup")
        if not cfg.enable_trading:
            return  # dry run: never place the replacement without a real cancel first
        runtime.expected_cancellations.add(pending_ticket)
        result = broker.cancel_pending_order(pending_ticket)
        if not result.ok:
            print(f"[REPLACE] cancel failed: {result.retcode} {result.comment}")
            return

    # A stale pending order left over in the OPPOSITE direction (e.g. from
    # before bias flipped) isn't a committed trade -- only open positions get
    # the Strong/ShortTerm coexistence protection. Clear it before entering
    # the new direction so it can't unexpectedly fill later on its own.
    for order in broker.get_pending_orders_by_direction(cfg.symbol, cfg.magic_number, -bias.direction):
        print(f"[REPLACE] cancelling stale opposite-direction pending #{order.ticket} for new "
              f"{winner.source_tf} {'BUY' if winner.direction == 1 else 'SELL'} setup")
        if cfg.enable_trading:
            runtime.expected_cancellations.add(order.ticket)
            result = broker.cancel_pending_order(order.ticket)
            if not result.ok:
                print(f"[REPLACE] opposite-direction cancel failed: {result.retcode} {result.comment}")

    comment = order_comment(winner)

    if winner.mode == EntryMode.MARKET:
        print(f"[ENTRY] {winner.source_tf} MARKET {'BUY' if winner.direction == 1 else 'SELL'} sl={winner.sl}")
        if cfg.enable_trading:
            result = broker.send_market_order(cfg.symbol, winner.direction, cfg.lots, winner.sl,
                                              cfg.magic_number, cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY] market order failed: {result.retcode} {result.comment}")
                return
            # A market order fills synchronously (DONE really means filled),
            # so it's safe to mark the zone traded immediately.
            store.mark_traded(winner.zone_key)
    else:
        print(f"[ENTRY] {winner.source_tf} PENDING {'BUY' if winner.direction == 1 else 'SELL'} "
              f"@ {winner.entry_price} sl={winner.sl}")
        if cfg.enable_trading:
            result = broker.send_pending_order(cfg.symbol, winner.direction, winner.entry_price, cfg.lots,
                                               winner.sl, cfg.magic_number, cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY] pending order failed: {result.retcode} {result.comment}")
                return
            # Do NOT mark traded here: a pending order can sit unfilled and
            # later get cancelled/replaced by a newer setup without ever
            # executing. The zone should only count as traded once it
            # actually fills -- see sync_filled_zones().


def main() -> None:
    cfg = load_config()
    print(f"ETH SMC bot starting | symbol={cfg.symbol} lots={cfg.lots} magic={cfg.magic_number} "
          f"terminal={cfg.mt5_terminal_path} trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)
    blocked = BlockedZoneStore(cfg.blocked_state_file, cfg.symbol)
    alerts = AlertedZoneStore(cfg.alert_state_file)
    runtime = RuntimeState()

    try:
        while True:
            try:
                run_once(cfg, store, blocked, runtime, alerts)
            except Exception as exc:
                print(f"[ERROR] {exc}")
                # The MT5 IPC channel can get stuck without the process
                # crashing (seen live on the XAUUSD bot: a fresh connection
                # works fine while this process's own channel keeps failing
                # every poll). Reconnecting here means a degraded connection
                # self-heals on the next cycle instead of failing silently
                # forever. NOTE: calling shutdown() first hung that process
                # live (the channel was already broken, and closing+
                # reopening it blocked indefinitely) -- re-initialize only,
                # no shutdown.
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
