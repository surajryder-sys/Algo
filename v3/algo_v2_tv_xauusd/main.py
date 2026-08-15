"""Main loop: tv_bridge/tradingview_bot -> zone effective direction ->
candidates (both directions) -> order execution.

Run with: python -m algo_v2_tv_xauusd.main

This is algo_v2's exact strategy (see algo_v2/main.py for the full design
rationale -- unchanged here), fed by TradingView webhook data instead of
the MT5 OB/ATR-Trail indicator bridge. Own magic number, state files, and
order-comment prefix so this runs alongside both the V1 (algo/) and V2
(algo_v2/) XAUUSD bots on the same terminal/account without colliding.

Depends on tradingview_bot's own zone/ATR store files being kept current --
that means tv_bridge.receiver and tradingview_bot.main must both be running
(see their own main()s), and TradingView must actually be alerting on M1,
M3, M5, and M15 (OB detector) plus M5 (ATR trail) to https://tv.secrettrader.net
with TVX's shared secret. Missing timeframes just read as "no zone data yet"
here (read_zone/read_atr return None) -- fails closed, no entries, rather
than trading off partial/stale data.

Safety: TVX_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from v3.algo_v2_tv_xauusd import broker, reader
from v3.algo_v2_tv_xauusd.blocking import BlockedZoneStore
from v3.algo_v2_tv_xauusd.candidates import (
    build_m1_candidate, build_m3_candidate, build_m5_candidate,
    choose_winning_candidate, should_replace_pending,
    order_comment, parse_order_comment,
)
from v3.algo_v2_tv_xauusd.config import Config, load_config
from v3.algo_v2_tv_xauusd.entries import EntryMode, select_sl
from v3.algo_v2_tv_xauusd.intervention import check_manual_pending_cancellations, check_manual_position_closes
from v3.algo_v2_tv_xauusd.management import fresh_opposite_ob_exists
from v3.algo_v2_tv_xauusd.reader import OBSnapshot, read_zone, read_atr
from v3.algo_v2_tv_xauusd.sl_manager import SLManager
from v3.algo_v2_tv_xauusd.state_store import TradedZoneStore
from v3.algo_v2_tv_xauusd.zone import ZoneState, compute_zone, is_eligible

# Event-log/bias-history coverage only (see event_watcher.py) -- the
# ACTUAL strategy in run_once() below is unchanged, still just M1/M3/M5
# entries + M15 SL edges + M5 ATR bias, exactly as algo_v2's does. Values
# are read_zone/read_atr's tf_minutes-as-string form; H1/H2/H4 are "60"/
# "120"/"240" (Pine's timeframe.period convention -- see
# tv_scraper/parser.py's _normalize_timeframe for why the scraper path
# has to be converted to match rather than left as "1h"/"2h"/"4h").
TRACKED_TIMEFRAMES = ("1", "3", "5", "15", "30", "60", "120", "240")


@dataclass
class RuntimeState:
    """Tickets seen on the previous poll, so a disappeared ticket can be
    classified as filled / bot-cancelled / manually cancelled."""
    seen_pending_tickets: set = field(default_factory=set)
    seen_position_tickets: set = field(default_factory=set)
    expected_cancellations: set = field(default_factory=set)
    pending_block_release: dict = field(default_factory=dict)


def _direction_edges(direction: int, m15: Optional[OBSnapshot], m5: Optional[OBSnapshot],
                     m3: Optional[OBSnapshot]) -> dict:
    def edge(snap: Optional[OBSnapshot]):
        if snap is None:
            return None
        history = snap.bull if direction == 1 else snap.bear
        if not history:
            return None
        return history[0].low if direction == 1 else history[0].high

    return {"M15": edge(m15), "M5": edge(m5), "M3": edge(m3)}


def sync_filled_zones(cfg: Config, store: TradedZoneStore) -> None:
    """Marks a zone traded only once a live position carrying its comment
    actually exists -- i.e. its pending order filled."""
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
    a manual (client/mobile/web) cancel or close."""
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


BLOCK_RELEASE_CONFIRM_SECONDS = 5.0


def _newest_ob_start_time(snap: Optional[OBSnapshot]) -> Optional[int]:
    """Most recent start_time across BOTH directions in this snapshot's
    history -- per the rule "a new OB, bullish or bearish, resets the block"."""
    if snap is None:
        return None
    times = [z.start_time for z in snap.bull] + [z.start_time for z in snap.bear]
    return max(times) if times else None


def release_stale_blocks(blocked: BlockedZoneStore, runtime: RuntimeState,
                         m1: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                         m5: Optional[OBSnapshot]) -> None:
    now = time.monotonic()
    for source_tf, snap in (("M1", m1), ("M3", m3), ("M5", m5)):
        blocked_key = blocked.blocked_zone_key(source_tf)
        if blocked_key is None:
            runtime.pending_block_release.pop(source_tf, None)
            continue

        blocked_event_time = int(blocked_key.split("|")[2])
        newest = _newest_ob_start_time(snap)
        has_new_ob = (newest is not None and newest > blocked_event_time)

        if not has_new_ob:
            runtime.pending_block_release.pop(source_tf, None)
            continue

        pending = runtime.pending_block_release.get(source_tf)
        if pending is None or pending[0] != newest:
            runtime.pending_block_release[source_tf] = (newest, now)
            continue

        _, first_seen = pending
        if now - first_seen >= BLOCK_RELEASE_CONFIRM_SECONDS:
            print(f"[BLOCK] auto-released {source_tf} block ({blocked_key}): "
                  f"new OB detected after block (confirmed {BLOCK_RELEASE_CONFIRM_SECONDS:.0f}s)")
            blocked.release(source_tf)
            runtime.pending_block_release.pop(source_tf, None)


def cancel_zone_ineligible_pending(cfg: Config, zone, blocked: BlockedZoneStore,
                                   runtime: RuntimeState) -> None:
    """Cancels a resting pending order the instant the zone's own character
    turns against it. Deliberately pending-orders only -- see
    algo_v2/main.py's docstring for the full rationale (unchanged here)."""
    for order in broker.get_pending_orders(cfg.symbol, cfg.magic_number):
        parsed = parse_order_comment(order.comment)
        if parsed is None:
            continue
        zone_key, event_time = parsed
        direction = int(zone_key.split("|")[1])
        source_tf = zone_key.split("|")[0]

        if is_eligible(zone, direction, event_time):
            continue

        print(f"[EXIT] cancelling pending #{order.ticket}: zone turned against {zone_key} "
              f"-> blocking {source_tf} zone {zone_key}")
        if cfg.enable_trading:
            result = broker.cancel_pending_order(order.ticket)
            if not result.ok:
                print(f"[EXIT] cancel failed: {result.retcode} {result.comment}")
                continue
            runtime.expected_cancellations.add(order.ticket)
            blocked.block(source_tf, zone_key, reason="zone_ineligible")


def run_once(cfg: Config, store: TradedZoneStore, blocked: BlockedZoneStore, runtime: RuntimeState,
            sl_manager: SLManager) -> None:
    m15 = read_zone(cfg.symbol, 15)
    m5 = read_zone(cfg.symbol, 5)
    m3 = read_zone(cfg.symbol, 3)
    m1 = read_zone(cfg.symbol, 1)
    atr = read_atr(cfg.symbol, cfg.atr_timeframe_minutes)

    zone = compute_zone(atr, m5)
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = (bid + ask) / 2.0

    if cfg.enable_trading:
        sync_filled_zones(cfg, store)
        sync_manual_intervention(cfg, blocked, runtime)
        release_stale_blocks(blocked, runtime, m1, m3, m5)

    # 1. Close any open position the instant M5 forms a fresh OPPOSITE-
    #    direction OB (postdating the ATR zone's own last flip). See
    #    algo_v2/main.py's docstring for the full rationale.
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        if fresh_opposite_ob_exists(m5, atr, direction):
            print(f"[EXIT] closing {'BUY' if direction == 1 else 'SELL'} position #{pos.ticket}: "
                  f"fresh opposite M5 OB after zone event")
            if cfg.enable_trading:
                broker.close_position(cfg.symbol, pos, cfg.deviation_points)

    # 1b. A resting pending order whose OB the zone has turned against gets
    #     cancelled too, independent of bias.
    cancel_zone_ineligible_pending(cfg, zone, blocked, runtime)

    # 2. Trail every open position -- OB-edge + point-based combined, same
    #    as algo_v2/main.py.
    open_positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    for pos in open_positions:
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        edges = _direction_edges(direction, m15, m5, m3)
        ob_candidate = select_sl(direction, current_price, edges)
        new_sl = sl_manager.compute(pos.ticket, direction, pos.price_open, current_price,
                                    pos.sl or None, ob_candidate)
        if new_sl is not None:
            print(f"[TRAIL] #{pos.ticket} {'BUY' if direction == 1 else 'SELL'} SL -> {new_sl}")
            if cfg.enable_trading:
                result = broker.modify_position_sl(cfg.symbol, pos.ticket, new_sl, pos.tp)
                if not result.ok:
                    print(f"[TRAIL] modify failed: {result.retcode} {result.comment}")
                else:
                    sl_manager.confirm_applied(pos.ticket, new_sl)
    sl_manager.prune({p.ticket for p in open_positions})

    # 3. New entries -- both directions tried every cycle, direction-gated
    #    per-candidate via is_eligible(). See algo_v2/main.py's docstring
    #    for the full rationale (unchanged here).
    if zone.state == ZoneState.NONE:
        return  # no zone data yet -- fail closed, no entries either direction

    if broker.get_positions(cfg.symbol, cfg.magic_number):
        return

    candidates = []

    def eligible(c, strict: bool = False) -> bool:
        return (c is not None
                and not store.is_traded(c.zone_key)
                and not blocked.is_blocked(c.source_tf, c.zone_key)
                and is_eligible(zone, c.direction, c.event_time, strict=strict))

    for direction in (1, -1):
        c = build_m1_candidate(direction, m1, m15, m5, m3)
        if eligible(c, strict=True):
            candidates.append(c)

        c = build_m3_candidate(direction, m3, m15, m5, current_price)
        if eligible(c, strict=True):
            candidates.append(c)

        c = build_m5_candidate(direction, m5, m15, m3, current_price)
        if eligible(c):
            candidates.append(c)

    winner = choose_winning_candidate(candidates, current_price)
    if winner is None:
        return

    pending_orders = broker.get_pending_orders(cfg.symbol, cfg.magic_number)
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

    comment = order_comment(winner)

    if winner.mode == EntryMode.MARKET:
        print(f"[ENTRY] {winner.source_tf} MARKET {'BUY' if winner.direction == 1 else 'SELL'} sl={winner.sl}")
        if cfg.enable_trading:
            result = broker.send_market_order(cfg.symbol, winner.direction, cfg.lots, winner.sl,
                                              cfg.magic_number, cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY] market order failed: {result.retcode} {result.comment}")
                return
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
            # Do NOT mark traded here -- only once it actually fills, via
            # sync_filled_zones().


def main() -> None:
    cfg = load_config()
    print(f"TVX (TradingView XAUUSD) bot starting | symbol={cfg.symbol} lots={cfg.lots} "
          f"magic={cfg.magic_number} trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    reader.configure(cfg.tv_zone_state_file, cfg.tv_atr_state_file,
                     cfg.tv_scraper_zone_state_file, cfg.tv_scraper_atr_state_file)
    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)
    blocked = BlockedZoneStore(cfg.blocked_state_file)
    sl_manager = SLManager(cfg.sl_state_file)
    runtime = RuntimeState()

    try:
        while True:
            try:
                run_once(cfg, store, blocked, runtime, sl_manager)
            except Exception as exc:
                print(f"[ERROR] {exc}")
                # Same MT5-IPC self-heal as algo_v2/main.py: reconnect only,
                # no shutdown() (that hung live once with an already-broken
                # channel).
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
