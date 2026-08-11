"""Main loop: bridge -> zone effective direction -> M5/M15 candidates (both
directions) -> order execution.

Run with: python -m algo_v2_usoil.main

Preserved snapshot -- see config.py's docstring: this is the standalone,
single-symbol USOIL bot as it existed just before being merged into
algo_v2_usoil_btc_eth. Kept here, fully independent and ready to run on
its own, in case USOIL ever needs to run in isolation again.

  - M15 is the zone anchor (ATR + M15's own OB flips define the
    effective direction -- see zone.py) and uses the lenient eligibility
    check: its own direction is always eligible, and an opposite-direction
    M15 OB is eligible too if it postdates the zone's own event_time
    boundary (that's what actually flips the bias). M5 is the subordinate
    STRICT tier: it only trades when its direction ALSO matches M15/the
    zone's effective direction right now -- no "postdates the boundary"
    exception, so it never fires against the current bias. This is the
    opposite pairing from algo_v2 (XAUUSD), where M5 is the anchor and
    M1/M3 are the strict tiers above it -- same mechanism, different
    timeframe playing the anchor role. M30 is meant to be added as a
    further strict tier once this two-tier version is validated live.
  - The ATR Trail input to the zone race is ALSO M15-based, not M5 -- it
    has to match the OB-flip inputs' timeframe for the "most recent of the
    three" race to mean anything (see zone.py's docstring). This means the
    indicator must be attached to the M15 USOIL chart (not M5) so it
    publishes ATRSTATE_USOIL_15.json.
  - No higher-timeframe (H1/H2/H4) bias layer -- same as algo_v2, which
    also has none (bias.py was retired there; the zone itself is the only
    direction signal). M5 does NOT participate in the zone/effective-
    direction calculation -- only in entries and SL/trailing edges --
    exactly like M1/M3 on algo_v2.
  - The forces-a-close rule (fresh_opposite_ob_exists) checks M15 (the
    anchor), not M5 -- a fresh opposite M15 OB is what actually activates
    the opposite setup / threatens to flip the bias, so that's the one
    that should force an exit. An opposite M5 OB alone does not.
  - Separate package (not a config knob on algo_v2) so the two bots can run
    fully independently -- different magic number, state/block files, and
    order-comment prefix ("V2O" vs "V2") -- and so extending this one's
    timeframe set never risks the live XAUUSD bot's logic. Runs on its own
    MT5 terminal install (see config.py), same pattern the old eth_smc/
    btc_smc bots used. That terminal still shares ONE Windows-wide
    "Common\\Files" bridge folder with every other MT5 terminal on this
    machine (that's what makes it "Common"), so every file this bot reads
    or writes is namespaced to avoid collisions regardless -- see
    blocking.py's docstring for the one that mattered (BLOCK_STATUS_V2.json
    / RESET_V2_<tf>.flag are NOT symbol-scoped by default, so this bot uses
    "_USOIL"-suffixed names instead of algo_v2's).

Safety: SMC_V2_USOIL_ENABLE_TRADING must be explicitly set to true in .env
for any order to actually be sent/modified/cancelled. Left unset (default
false), every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from atr_bridge.reader import read_atr
from ob_bridge.reader import read_zone, OBSnapshot
from algo_v2_usoil import broker
from algo_v2_usoil.blocking import BlockedZoneStore, check_reset_requests
from algo_v2_usoil.candidates import (
    build_m5_candidate, build_m15_candidate, choose_winning_candidate, should_replace_pending,
    order_comment, parse_order_comment,
)
from algo_v2_usoil.config import Config, load_config
from algo_v2_usoil.entries import EntryMode
from algo_v2_usoil.intervention import check_manual_pending_cancellations, check_manual_position_closes
from algo_v2_usoil.management import compute_trailing_sl, fresh_opposite_ob_exists
from algo_v2_usoil.state_store import TradedZoneStore
from algo_v2_usoil.zone import ZoneState, compute_zone, is_eligible


@dataclass
class RuntimeState:
    """Tickets seen on the previous poll, so a disappeared ticket can be
    classified as filled / bot-cancelled / manually cancelled."""
    seen_pending_tickets: set = field(default_factory=set)
    seen_position_tickets: set = field(default_factory=set)
    expected_cancellations: set = field(default_factory=set)
    # source_tf -> (candidate_zone_key, first_seen_monotonic_time) for a
    # manual block whose release is being confirmed -- see release_stale_blocks.
    pending_block_release: dict = field(default_factory=dict)
    # label ("M5"/"M15"/"ATR") -> bool, last-known staleness -- so
    # _fresh_or_none() only prints on a state CHANGE, not every poll while
    # something stays stale (confirmed live elsewhere: printing every poll
    # produces tens of thousands of duplicate lines within minutes).
    stale_flags: dict = field(default_factory=dict)


# No indicator publishes faster than roughly once a second (ScanEverySeconds
# in the MQL5 indicator); 30s is a generous multiple of that, matching
# OBSnapshot/ATRSnapshot's own is_stale() default. Data older than this
# means the indicator isn't actively running anymore (chart closed, MT5
# disconnected, terminal hiccup). Treated identically to "no data at all"
# (None) below -- every consumer (compute_zone, candidates.py,
# management.py) already handles None as "fail closed", so gating at the
# read point here is the only change needed; nothing downstream has to
# know staleness exists. Ported from algo_v2_usoil_btc_eth after that bot
# briefly built real trade candidates off 40-65-hour-old BTCUSD/ETHUSD
# bridge files with zero warning -- this snapshot didn't have the gap live
# (USOIL's indicator was always running), but shipping it with a known
# hole for "future use" made no sense once the fix existed.
MAX_DATA_AGE_SECONDS = 30.0


def _fresh_or_none(label: str, snap, runtime: RuntimeState):
    """Returns snap unchanged if fresh (or already None), else None --
    logging only on a fresh<->stale transition, not every poll."""
    if snap is None:
        return None

    is_stale = snap.is_stale(MAX_DATA_AGE_SECONDS)
    was_stale = runtime.stale_flags.get(label, False)

    if is_stale and not was_stale:
        print(f"[STALE] {label} data is {snap.age_seconds():.0f}s old "
              f"(> {MAX_DATA_AGE_SECONDS:.0f}s) -- treating as no data until it refreshes")
    elif was_stale and not is_stale:
        print(f"[STALE] {label} data is fresh again ({snap.age_seconds():.0f}s old)")

    runtime.stale_flags[label] = is_stale
    return None if is_stale else snap


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


BLOCK_RELEASE_CONFIRM_SECONDS = 5.0


def _newest_ob_start_time(snap: Optional[OBSnapshot]) -> Optional[int]:
    """Most recent start_time across BOTH directions in this snapshot's
    history (not just history[0] of one direction) -- per the rule "a
    new OB, bullish or bearish, resets the block"."""
    if snap is None:
        return None
    times = [z.start_time for z in snap.bull] + [z.start_time for z in snap.bear]
    return max(times) if times else None


def release_stale_blocks(blocked: BlockedZoneStore, runtime: RuntimeState,
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
            print(f"[BLOCK] auto-released {source_tf} block ({blocked_key}): "
                  f"new OB detected after block (confirmed {BLOCK_RELEASE_CONFIRM_SECONDS:.0f}s)")
            blocked.release(source_tf)
            runtime.pending_block_release.pop(source_tf, None)


def cancel_zone_ineligible_pending(cfg: Config, zone, blocked: BlockedZoneStore,
                                   runtime: RuntimeState) -> None:
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
            runtime.expected_cancellations.add(order.ticket)
            broker.cancel_pending_order(order.ticket)
            blocked.block(source_tf, zone_key, reason="zone_ineligible")


def run_once(cfg: Config, store: TradedZoneStore, blocked: BlockedZoneStore, runtime: RuntimeState) -> None:
    m5 = _fresh_or_none("M5", read_zone(cfg.symbol, 5), runtime)
    m15 = _fresh_or_none("M15", read_zone(cfg.symbol, 15), runtime)
    atr = _fresh_or_none("ATR", read_atr(cfg.symbol, cfg.atr_timeframe_minutes), runtime)

    zone = compute_zone(atr, m15)
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
        release_stale_blocks(blocked, runtime, m5, m15)

    # 1. Close any open position the instant M15 -- the zone anchor --
    #    forms a fresh OPPOSITE-direction OB -- "fresh" meaning its origin
    #    candle postdates the ATR zone's own last Strong<->Weak flip
    #    (atr.event_time). That's what actually activates the opposite
    #    setup / threatens the bias itself; an opposite M5 OB alone does
    #    not (M5 doesn't participate in the zone calc at all -- see
    #    zone.py). Pending orders are NOT touched here --
    #    cancel_zone_ineligible_pending (step 1b) already handles those via
    #    the proper zone-eligibility rule.
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        if fresh_opposite_ob_exists(m15, atr, direction):
            print(f"[EXIT] closing {'BUY' if direction == 1 else 'SELL'} position #{pos.ticket}: "
                  f"fresh opposite M15 OB after zone event")
            if cfg.enable_trading:
                broker.close_position(cfg.symbol, pos, cfg.deviation_points)

    # 1b. Independent of bias: a resting pending order whose OB the zone has
    #     turned against (Strong<->Weak flip, no opposing OB needed) gets
    #     cancelled too. Positions are untouched here.
    cancel_zone_ineligible_pending(cfg, zone, blocked, runtime)

    # 2. Trail every open position in its own direction. M15's OB edge
    #    only -- not M5 -- regardless of which tier opened the trade (see
    #    _direction_edges' docstring for why).
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        edges = _direction_edges(direction, m15)
        new_sl = compute_trailing_sl(direction, current_price, pos.sl or None, edges)
        if new_sl is not None:
            print(f"[TRAIL] #{pos.ticket} {'BUY' if direction == 1 else 'SELL'} SL -> {new_sl}")
            if cfg.enable_trading:
                broker.modify_position_sl(cfg.symbol, pos.ticket, new_sl, pos.tp)

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
    if broker.get_positions(cfg.symbol, cfg.magic_number):
        return

    candidates = []

    def eligible(c, strict: bool = False) -> bool:
        return (c is not None
                and not store.is_traded(c.zone_key)
                and not blocked.is_blocked(c.source_tf, c.zone_key)
                and is_eligible(zone, c.direction, c.event_time, strict=strict))

    for direction in (1, -1):
        c = build_m15_candidate(direction, m15, m5, current_price)
        if eligible(c):
            candidates.append(c)

        c = build_m5_candidate(direction, m5, m15, current_price)
        if eligible(c, strict=True):
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
            # Do NOT mark traded here: a pending order can sit unfilled and
            # later get cancelled/replaced by a newer setup without ever
            # executing.


def main() -> None:
    cfg = load_config()
    print(f"SMC V2 USOIL bot starting | symbol={cfg.symbol} lots={cfg.lots} magic={cfg.magic_number} "
          f"trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)
    blocked = BlockedZoneStore(cfg.blocked_state_file)
    runtime = RuntimeState()

    try:
        while True:
            try:
                run_once(cfg, store, blocked, runtime)
            except Exception as exc:
                print(f"[ERROR] {exc}")
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
