"""Main loop: bridge -> zone effective direction -> M5/M15 candidates (both
directions) -> order execution -- run once per symbol (USOIL, BTCUSD,
ETHUSD) each poll cycle, all three sharing ONE MT5 connection.

Run with: python -m algo_v2_usoil_btc_eth.main

Merged from three would-be independent bots (algo_v2_usoil, plus new
algo_v2_btc/algo_v2_eth that never existed as separate packages) into one
process, deliberately -- avoids running three separate MT5 IPC connections
to what would all be the same terminal anyway (see config.py's docstring).
Per-symbol identity (magic number, lots, entry/SL constants, state/block
files, order-comment... no, comment prefix is shared, see candidates.py)
stays fully separated; only the connection and poll loop are shared. Each
symbol gets its own TradedZoneStore, BlockedZoneStore, and RuntimeState
(tracked in a SymbolState, one per symbol -- see below) so nothing about
one symbol's polling can leak into another's.

Trading logic itself is UNCHANGED from the standalone algo_v2_usoil bot
this was merged from:
  - M15 is the zone anchor per symbol (ATR + that symbol's own M15 OB
    flips define the effective direction -- see zone.py) and uses the
    lenient eligibility check. M5 is the subordinate STRICT tier: only
    trades when its direction ALSO matches M15/the zone's effective
    direction right now, ignoring opposite-direction M5 OBs entirely.
  - The ATR Trail input to each symbol's zone race is M15-based, same
    timeframe as the OB-flip inputs -- each symbol's indicator instance
    must be attached to that symbol's own M15 chart.
  - No higher-timeframe (H1/H2/H4) bias layer for any symbol.
  - Trailing SL follows each symbol's M15 OB edge only, not M5, even if a
    trade originated on M5 (see management.py).
  - Force-close triggers on a fresh opposite OB on M15 (the anchor), not
    M5, per symbol.
  - M30 is meant to be added as a further strict tier per symbol later,
    once this two-tier version is validated live across all three.

Safety: SMC_V2_MULTI_ENABLE_TRADING must be explicitly set to true in .env
for any order to actually be sent/modified/cancelled, for ANY symbol.
Left unset (default false), every decision is printed but nothing touches
any account. There's no per-symbol trading toggle -- it's all three or
none, matching this being one bot, not three.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from atr_bridge.reader import read_atr
from ob_bridge.reader import read_zone, OBSnapshot
from algo_v2_usoil_btc_eth import broker
from algo_v2_usoil_btc_eth.blocking import BlockedZoneStore, check_reset_requests
from algo_v2_usoil_btc_eth.candidates import (
    build_m5_candidate, build_m15_candidate, choose_winning_candidate, should_replace_pending,
    order_comment, parse_order_comment,
)
from algo_v2_usoil_btc_eth.config import Config, SymbolConfig, load_config
from algo_v2_usoil_btc_eth.entries import EntryMode
from algo_v2_usoil_btc_eth.intervention import check_manual_pending_cancellations, check_manual_position_closes
from algo_v2_usoil_btc_eth.management import compute_trailing_sl, fresh_opposite_ob_exists
from algo_v2_usoil_btc_eth.state_store import TradedZoneStore
from algo_v2_usoil_btc_eth.zone import ZoneState, compute_zone, is_eligible


@dataclass
class RuntimeState:
    """Tickets seen on the previous poll, so a disappeared ticket can be
    classified as filled / bot-cancelled / manually cancelled. One per
    symbol -- see SymbolState."""
    seen_pending_tickets: set = field(default_factory=set)
    seen_position_tickets: set = field(default_factory=set)
    expected_cancellations: set = field(default_factory=set)
    # source_tf -> (candidate_zone_key, first_seen_monotonic_time) for a
    # manual block whose release is being confirmed -- see release_stale_blocks.
    pending_block_release: dict = field(default_factory=dict)


@dataclass
class SymbolState:
    """Everything that needs to persist across polls for one symbol."""
    cfg: SymbolConfig
    store: TradedZoneStore
    blocked: BlockedZoneStore
    runtime: RuntimeState = field(default_factory=RuntimeState)


def _direction_edges(direction: int, m15: Optional[OBSnapshot]) -> dict:
    """Trailing SL follows M15's OB edge only -- deliberately not M5,
    unlike the initial SL set at entry (candidates.py's _tier_edges, which
    still picks whichever of M5/M15 is nearest). M15 is the zone anchor;
    once a position is open, its own structure is what should govern where
    the stop trails to, regardless of which tier actually opened the trade."""
    def edge(snap: Optional[OBSnapshot]):
        if snap is None:
            return None
        history = snap.bull if direction == 1 else snap.bear
        if not history:
            return None
        return history[0].low if direction == 1 else history[0].high

    return {"M15": edge(m15)}


def sync_filled_zones(sym_cfg: SymbolConfig, store: TradedZoneStore) -> None:
    """Marks a zone traded only once a live position carrying its comment
    actually exists -- i.e. its pending order filled. Runs every loop so a
    fill is caught on the next poll after it happens."""
    for pos in broker.get_positions(sym_cfg.symbol, sym_cfg.magic_number):
        parsed = parse_order_comment(pos.comment)
        if parsed is None:
            continue
        zone_key, _event_time = parsed
        if not store.is_traded(zone_key):
            print(f"[SYNC {sym_cfg.symbol}] zone {zone_key} confirmed filled via position #{pos.ticket}")
            store.mark_traded(zone_key)


def sync_manual_intervention(sym_cfg: SymbolConfig, blocked: BlockedZoneStore, runtime: RuntimeState) -> None:
    """Compares this poll's live pending/position tickets against last
    poll's, and blocks the underlying zone for any that disappeared due to
    a manual (client/mobile/web) cancel or close -- never for a fill, a
    bot-initiated action, or an SL/TP/stop-out."""
    current_pending = {o.ticket for o in broker.get_pending_orders(sym_cfg.symbol, sym_cfg.magic_number)}
    current_positions = {p.ticket for p in broker.get_positions(sym_cfg.symbol, sym_cfg.magic_number)}

    disappeared_pending = runtime.seen_pending_tickets - current_pending
    disappeared_positions = runtime.seen_position_tickets - current_positions

    if disappeared_pending:
        for source_tf, zone_key in check_manual_pending_cancellations(disappeared_pending, runtime.expected_cancellations):
            print(f"[BLOCK {sym_cfg.symbol}] manual pending cancellation -> blocking {source_tf} zone {zone_key}")
            blocked.block(source_tf, zone_key, reason="manual_cancel")

    if disappeared_positions:
        for source_tf, zone_key in check_manual_position_closes(disappeared_positions):
            print(f"[BLOCK {sym_cfg.symbol}] manual position close -> blocking {source_tf} zone {zone_key}")
            blocked.block(source_tf, zone_key, reason="manual_close")

    runtime.seen_pending_tickets = current_pending
    runtime.seen_position_tickets = current_positions


BLOCK_RELEASE_CONFIRM_SECONDS = 5.0


def _newest_ob_start_time(snap: Optional[OBSnapshot]) -> Optional[int]:
    """Most recent start_time across BOTH directions in this snapshot's
    history (not just history[0] of one direction) -- per the rule "a
    new OB, bullish or bearish, resets the block"."""
    if snap is None:
        return None
    times = [z.start_time for z in snap.bull] + [z.start_time for z in snap.bear]
    return max(times) if times else None


def release_stale_blocks(symbol: str, blocked: BlockedZoneStore, runtime: RuntimeState,
                         m5: Optional[OBSnapshot], m15: Optional[OBSnapshot]) -> None:
    now = time.monotonic()
    for source_tf, snap in (("M5", m5), ("M15", m15)):
        blocked_key = blocked.blocked_zone_key(source_tf)
        if blocked_key is None:
            runtime.pending_block_release.pop(source_tf, None)
            continue

        block_time = blocked.blocked_since(source_tf)
        newest = _newest_ob_start_time(snap)
        has_new_ob = (block_time is not None and newest is not None and newest > block_time)

        if not has_new_ob:
            runtime.pending_block_release.pop(source_tf, None)
            continue

        pending = runtime.pending_block_release.get(source_tf)
        if pending is None or pending[0] != newest:
            # First sighting of this specific post-block OB -- start (or
            # restart, if an even newer one has since appeared) the
            # confirmation clock rather than releasing immediately.
            runtime.pending_block_release[source_tf] = (newest, now)
            continue

        _, first_seen = pending
        if now - first_seen >= BLOCK_RELEASE_CONFIRM_SECONDS:
            print(f"[BLOCK {symbol}] auto-released {source_tf} block ({blocked_key}): "
                  f"new OB detected after block (confirmed {BLOCK_RELEASE_CONFIRM_SECONDS:.0f}s)")
            blocked.release(source_tf)
            runtime.pending_block_release.pop(source_tf, None)


def cancel_zone_ineligible_pending(sym_cfg: SymbolConfig, enable_trading: bool, zone,
                                   blocked: BlockedZoneStore, runtime: RuntimeState) -> None:
    """Cancels a resting pending order the instant the zone's own character
    turns against it -- even with no opposite-direction OB yet and no bias
    flip. No confirmation delay -- the MQL5 indicator only ever publishes
    the last CLOSED bar's values, so event_time only changes on a genuine
    bar close.

    The cancelled zone is also BLOCKED (same as a manual cancellation), not
    just cancelled -- see algo_v2/main.py's identical function for the full
    reasoning.

    Deliberately pending-orders only: a FILLED position still only closes
    on a bias flip or an opposing OB, so this never touches
    broker.get_positions()."""
    for order in broker.get_pending_orders(sym_cfg.symbol, sym_cfg.magic_number):
        parsed = parse_order_comment(order.comment)
        if parsed is None:
            continue
        zone_key, event_time = parsed
        direction = int(zone_key.split("|")[1])
        source_tf = zone_key.split("|")[0]

        if is_eligible(zone, direction, event_time):
            continue

        print(f"[EXIT {sym_cfg.symbol}] cancelling pending #{order.ticket}: zone turned against {zone_key} "
              f"-> blocking {source_tf} zone {zone_key}")
        if enable_trading:
            runtime.expected_cancellations.add(order.ticket)
            broker.cancel_pending_order(order.ticket)
            blocked.block(source_tf, zone_key, reason="zone_ineligible")


def run_once_for_symbol(cfg: Config, sym_state: SymbolState) -> None:
    """One full decision cycle for one symbol -- identical logic to the
    standalone algo_v2_usoil bot's run_once(), just parameterized by
    symbol via sym_state.cfg and taking the shared Config for enable_trading."""
    sym_cfg = sym_state.cfg
    store, blocked, runtime = sym_state.store, sym_state.blocked, sym_state.runtime

    m5 = read_zone(sym_cfg.symbol, 5)
    m15 = read_zone(sym_cfg.symbol, 15)
    atr = read_atr(sym_cfg.symbol, sym_cfg.atr_timeframe_minutes)

    zone = compute_zone(atr, m15)
    bid, ask = broker.get_tick_price(sym_cfg.symbol)
    current_price = (bid + ask) / 2.0

    # Always check for a chart reset-button press, regardless of trading mode.
    check_reset_requests(blocked)

    if cfg.enable_trading:
        # 0a. Confirm any pending orders that filled since the last poll.
        sync_filled_zones(sym_cfg, store)
        # 0b. Detect manual cancels/closes since the last poll and block
        #     their zones; auto-release blocks a new same-direction OB
        #     has superseded.
        sync_manual_intervention(sym_cfg, blocked, runtime)
        release_stale_blocks(sym_cfg.symbol, blocked, runtime, m5, m15)

    # 1. Close any open position the instant M15 -- the zone anchor --
    #    forms a fresh OPPOSITE-direction OB -- "fresh" meaning its origin
    #    candle postdates the ATR zone's own last Strong<->Weak flip
    #    (atr.event_time). That's what actually activates the opposite
    #    setup / threatens the bias itself; an opposite M5 OB alone does
    #    not (M5 doesn't participate in the zone calc at all -- see
    #    zone.py). Pending orders are NOT touched here --
    #    cancel_zone_ineligible_pending (step 1b) already handles those via
    #    the proper zone-eligibility rule.
    for pos in broker.get_positions(sym_cfg.symbol, sym_cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        if fresh_opposite_ob_exists(m15, atr, direction):
            print(f"[EXIT {sym_cfg.symbol}] closing {'BUY' if direction == 1 else 'SELL'} position #{pos.ticket}: "
                  f"fresh opposite M15 OB after zone event")
            if cfg.enable_trading:
                broker.close_position(sym_cfg.symbol, pos, sym_cfg.deviation_points)

    # 1b. Independent of bias: a resting pending order whose OB the zone has
    #     turned against (Strong<->Weak flip, no opposing OB needed) gets
    #     cancelled too. Positions are untouched here.
    cancel_zone_ineligible_pending(sym_cfg, cfg.enable_trading, zone, blocked, runtime)

    # 2. Trail every open position in its own direction. M15's OB edge
    #    only -- not M5 -- regardless of which tier opened the trade (see
    #    _direction_edges' docstring for why).
    for pos in broker.get_positions(sym_cfg.symbol, sym_cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        edges = _direction_edges(direction, m15)
        new_sl = compute_trailing_sl(sym_cfg.symbol, direction, current_price, pos.sl or None, edges)
        if new_sl is not None:
            print(f"[TRAIL {sym_cfg.symbol}] #{pos.ticket} {'BUY' if direction == 1 else 'SELL'} SL -> {new_sl}")
            if cfg.enable_trading:
                broker.modify_position_sl(sym_cfg.symbol, pos.ticket, new_sl, pos.tp)

    # 3. New entries -- both directions are tried every cycle. M15 uses the
    #    lenient eligibility check (it's the zone's own input, so the
    #    "opposite but newer" exception is effectively inert for it
    #    anyway). M5 uses strict=True: it only trades when its direction
    #    ALSO matches M15/the effective direction right now, no exception
    #    -- it ignores opposite-direction M5 OBs entirely, trading only
    #    whichever ones are convenient to the current M15 bias. No global
    #    direction gate here at all -- step 1 above handles closing an
    #    already-open position independently.
    if zone.state == ZoneState.NONE:
        return  # no zone data yet -- fail closed, no entries either direction

    # Already holding a position -- don't stack another, regardless of
    # direction: a same-direction entry would pyramid, an opposite one
    # would hedge -- both against the "one position at a time" rule.
    if broker.get_positions(sym_cfg.symbol, sym_cfg.magic_number):
        return

    candidates = []

    def eligible(c, strict: bool = False) -> bool:
        return (c is not None
                and not store.is_traded(c.zone_key)
                and not blocked.is_blocked(c.source_tf, c.zone_key)
                and is_eligible(zone, c.direction, c.event_time, strict=strict))

    for direction in (1, -1):
        c = build_m15_candidate(sym_cfg.symbol, direction, m15, m5, current_price)
        if eligible(c):
            candidates.append(c)

        c = build_m5_candidate(sym_cfg.symbol, direction, m5, m15, current_price)
        if eligible(c, strict=True):
            candidates.append(c)

    winner = choose_winning_candidate(candidates, current_price)
    if winner is None:
        return

    pending_orders = broker.get_pending_orders(sym_cfg.symbol, sym_cfg.magic_number)
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
        print(f"[REPLACE {sym_cfg.symbol}] cancelling pending #{pending_ticket} for closer {winner.source_tf} setup")
        if not cfg.enable_trading:
            return  # dry run: never place the replacement without a real cancel first
        runtime.expected_cancellations.add(pending_ticket)
        result = broker.cancel_pending_order(pending_ticket)
        if not result.ok:
            print(f"[REPLACE {sym_cfg.symbol}] cancel failed: {result.retcode} {result.comment}")
            return

    comment = order_comment(winner)

    if winner.mode == EntryMode.MARKET:
        print(f"[ENTRY {sym_cfg.symbol}] {winner.source_tf} MARKET {'BUY' if winner.direction == 1 else 'SELL'} sl={winner.sl}")
        if cfg.enable_trading:
            result = broker.send_market_order(sym_cfg.symbol, winner.direction, sym_cfg.lots, winner.sl,
                                              sym_cfg.magic_number, sym_cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY {sym_cfg.symbol}] market order failed: {result.retcode} {result.comment}")
                return
            store.mark_traded(winner.zone_key)
    else:
        print(f"[ENTRY {sym_cfg.symbol}] {winner.source_tf} PENDING {'BUY' if winner.direction == 1 else 'SELL'} "
              f"@ {winner.entry_price} sl={winner.sl}")
        if cfg.enable_trading:
            result = broker.send_pending_order(sym_cfg.symbol, winner.direction, winner.entry_price, sym_cfg.lots,
                                               winner.sl, sym_cfg.magic_number, sym_cfg.deviation_points, comment)
            if not result.ok:
                print(f"[ENTRY {sym_cfg.symbol}] pending order failed: {result.retcode} {result.comment}")
                return
            # Do NOT mark traded here: a pending order can sit unfilled and
            # later get cancelled/replaced by a newer setup without ever
            # executing.


def main() -> None:
    cfg = load_config()
    symbol_names = ", ".join(s.symbol for s in cfg.symbols)
    print(f"SMC V2 USOIL+BTC+ETH bot starting | symbols={symbol_names} "
          f"trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")
    for s in cfg.symbols:
        print(f"  {s.symbol}: lots={s.lots} magic={s.magic_number}")

    broker.connect(cfg)

    sym_states = [
        SymbolState(
            cfg=s,
            store=TradedZoneStore(s.state_file),
            blocked=BlockedZoneStore(s.blocked_state_file, s.symbol),
        )
        for s in cfg.symbols
    ]

    try:
        while True:
            for sym_state in sym_states:
                try:
                    run_once_for_symbol(cfg, sym_state)
                except Exception as exc:
                    print(f"[ERROR {sym_state.cfg.symbol}] {exc}")
                    try:
                        broker.connect(cfg)
                        print("[RECOVERY] reconnected to MT5")
                    except Exception as reconnect_exc:
                        print(f"[RECOVERY] reconnect failed: {reconnect_exc}")
                        # A dead connection will fail every symbol identically
                        # this cycle -- no point burning through the rest of
                        # the list before the shared sleep below.
                        break
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
