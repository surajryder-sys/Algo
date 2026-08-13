"""Main loop: bridge -> zone effective direction -> candidates (both
directions) -> order execution.

Run with: python -m algo_v2.main

V2 differences from V1 (algo/main.py):
  - New ENTRIES are gated by algo_v2.zone's effective direction, not a
    single fixed bias.direction -- both directions are tried every cycle,
    and is_eligible() decides per-candidate whether each one is tradeable
    (see zone.py's docstring for the full rule). M15 is still read purely
    for SL-edge selection, same as before.
  - An ALREADY-OPEN position force-closes ("squares off") the moment an
    ELIGIBLE opposite-direction candidate actually WINS -- i.e. the same
    winner-selection used for new entries, on any of M1/M3/M5, not just a
    fresh OB existing somewhere. Superseded an earlier M5-only version
    (fresh_opposite_ob_exists, still in management.py but unused here):
    confirmed live that a fully eligible, winning M3 setup could sit idle
    while an existing position rode all the way to its own SL instead of
    being squared off the moment the opposite trade was actually ready.
    A same-direction winner (or no winner) leaves the position alone.
  - Separate magic number, state files, and order-comment prefix so this
    can run alongside the V1 bot on the same terminal/account without
    colliding. Virgin-zone Telegram alerts are intentionally NOT wired in
    here -- V1 already sends those for the same symbol/timeframes.

Safety: SMC_V2_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from atr_bridge.reader import read_atr
from ob_bridge.reader import read_zone, OBSnapshot
from algo_v2 import broker
from algo_v2.blocking import BlockedZoneStore, check_reset_requests
from algo_v2.candidates import (
    build_m1_candidate, build_m3_candidate, build_m5_candidate,
    choose_winning_candidate, should_replace_pending,
    order_comment, parse_order_comment,
)
from algo_v2.config import Config, load_config
from algo_v2.direction_block import DirectionBlockStore
from algo_v2.entries import EntryMode, select_sl
from algo_v2.intervention import (
    check_manual_pending_cancellations, check_manual_position_closes, check_sl_hit_closes,
)
from algo_v2.sl_manager import SLManager
from algo_v2.state_store import TradedZoneStore
from algo_v2.zone import compute_zone, is_eligible


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
    # source_tf -> (candidate_zone_key, first_seen_monotonic_time) for a
    # manual block whose release is being confirmed -- see release_stale_blocks.
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


def sync_manual_intervention(cfg: Config, blocked: BlockedZoneStore,
                             direction_blocks: DirectionBlockStore, runtime: RuntimeState) -> None:
    """Compares this poll's live pending/position tickets against last
    poll's, and blocks the underlying zone for any that disappeared due to
    a manual (client/mobile/web) cancel or close -- never for a fill, a
    bot-initiated action, or an SL/TP/stop-out. Separately, a genuine SL
    hit on a position blocks that entire DIRECTION (all of M1/M3/M5, not
    just the one zone) -- see direction_block.py."""
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

        for direction, block_time in check_sl_hit_closes(disappeared_positions):
            print(f"[BLOCK] {'BUY' if direction == 1 else 'SELL'} SL hit -> "
                  f"blocking all {'BUY' if direction == 1 else 'SELL'} entries "
                  f"until a new {'bullish' if direction == 1 else 'bearish'} OB appears")
            direction_blocks.block(direction, block_time)

    runtime.seen_pending_tickets = current_pending
    runtime.seen_position_tickets = current_positions


# Comparing "is the current latest zone different from the blocked one"
# turned out unreliable -- confirmed live that current_zone_key (a
# single-direction history[0] lookup) and the candidate-building path
# (_overall_latest_and_previous, mixing bull+bear) can transiently
# disagree on which zone is "latest" when reading the same OB bridge
# data, because the underlying zone list itself isn't perfectly stable
# scan to scan. Comparing against the block's own wall-clock creation
# time instead only needs one fact to hold -- some OB (either direction)
# with a start_time after the block exists -- true regardless of which
# specific zone the bridge's ordering currently favors. Still requires
# that fact to hold for this many real seconds before acting on it, as a
# last line of defense against a single-poll misread.
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
                         m1: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                         m5: Optional[OBSnapshot]) -> None:
    now = time.monotonic()
    for source_tf, snap in (("M1", m1), ("M3", m3), ("M5", m5)):
        blocked_key = blocked.blocked_zone_key(source_tf)
        if blocked_key is None:
            runtime.pending_block_release.pop(source_tf, None)
            continue

        # Compare against the blocked zone's OWN start_time (embedded as the
        # zone_key's own event_time), not wall-clock block_time. OB
        # start_time is broker bar-time, which is free to carry a persistent
        # offset from true UTC (e.g. a data-source broker's server clock
        # running hours ahead/behind) -- confirmed live: comparing it
        # against Python's wall-clock time.time() made the SAME already-
        # blocked OB spuriously look "newer than the block" the moment its
        # offset-inflated timestamp exceeded the (true-UTC) block_time,
        # auto-releasing the block within one confirmation window even
        # though nothing had actually changed. Comparing within the same
        # clock domain (both broker bar-time) sidesteps that entirely,
        # regardless of which broker/timezone is feeding OB data.
        blocked_event_time = int(blocked_key.split("|")[2])
        newest = _newest_ob_start_time(snap)
        has_new_ob = (newest is not None and newest > blocked_event_time)

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


def _newest_ob_time_in_direction(direction: int, *snaps: Optional[OBSnapshot]) -> Optional[int]:
    """Newest start_time across all of the given snapshots (M1/M3/M5), in
    ONE specific direction -- "a bearish OB can appear in any timeframe"
    per spec, so all three are checked, not just one."""
    times = []
    for snap in snaps:
        if snap is None:
            continue
        history = snap.bull if direction == 1 else snap.bear
        if history:
            times.append(history[0].start_time)
    return max(times) if times else None


def release_stale_direction_blocks(direction_blocks: DirectionBlockStore,
                                   m1: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                                   m5: Optional[OBSnapshot]) -> None:
    """No confirmation delay here (unlike release_stale_blocks above) --
    pure broker-time timestamp comparison, per spec. Releases the instant
    any of M1/M3/M5's own latest OB in the blocked direction is newer
    than the SL-hit deal's own time that created the block."""
    for direction in (1, -1):
        block_time = direction_blocks.blocked_since(direction)
        if block_time is None:
            continue

        newest = _newest_ob_time_in_direction(direction, m1, m3, m5)
        if newest is not None and newest > block_time:
            print(f"[BLOCK] auto-released {'BUY' if direction == 1 else 'SELL'} direction block "
                  f"(SL hit at {block_time}): new {'bullish' if direction == 1 else 'bearish'} "
                  f"OB detected")
            direction_blocks.release(direction)


def _ob_still_exists(source_tf: str, direction: int, event_time: int,
                     m1: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                     m5: Optional[OBSnapshot]) -> bool:
    """True if a zone with this exact start_time (== event_time, the OB's
    own origin candle) is still present in source_tf's current direction
    history. False means the specific OB a resting pending order was built
    from has been invalidated/removed from the chart entirely -- confirmed
    live: a LuxAlgo zone can get deleted outright (not just marked tested)
    once price fully violates it, distinct from is_eligible()'s zone-vs-
    direction check, which stays True as long as the OVERALL zone still
    favors that direction, regardless of whether this specific rectangle
    still exists. M15 is never a source_tf for a candidate, so it's not
    checked here."""
    snap = {"M1": m1, "M3": m3, "M5": m5}.get(source_tf)
    if snap is None:
        return True  # unknown source_tf or no data yet -- don't cancel on a guess
    history = snap.bull if direction == 1 else snap.bear
    return any(z.start_time == event_time for z in history)


def cancel_zone_ineligible_pending(cfg: Config, zone, blocked: BlockedZoneStore,
                                   runtime: RuntimeState,
                                   m1: Optional[OBSnapshot], m3: Optional[OBSnapshot],
                                   m5: Optional[OBSnapshot]) -> None:
    """Cancels a resting pending order the instant either:
      (a) the zone's own character turns against it -- even with no
          opposite-direction OB yet and no bias flip, or
      (b) the specific OB it was built from is no longer present at all in
          source_tf's current history (see _ob_still_exists) -- distinct
          from (a): the overall zone/direction can still be perfectly
          favorable while this one particular rectangle has been deleted
          from the chart, which is invisible to is_eligible() since that
          only ever compares direction against the CURRENT zone, never
          checks whether this specific OB still exists.
    No confirmation delay -- this used to need one while the ATR zone's
    event_time could wobble tick-to-tick on the currently-forming bar,
    but the MQL5 indicator now only ever publishes the last CLOSED bar's
    values, so event_time only changes on a genuine bar close.

    The cancelled zone is also BLOCKED (same as a manual cancellation),
    not just cancelled -- so it can't immediately re-enter even if the
    zone's character happens to flip back at the very next bar close;
    it only clears the same way a manual block does: a genuinely
    different/newer OB confirmed latest (release_stale_blocks), or a
    manual reset.

    Deliberately pending-orders only: a FILLED position still only closes
    on a bias flip or an opposing OB (per the original spec -- "that event
    doesn't impact the running trade unless the opposite side ob occurs"),
    so this never touches broker.get_positions()."""
    for order in broker.get_pending_orders(cfg.symbol, cfg.magic_number):
        parsed = parse_order_comment(order.comment)
        if parsed is None:
            continue
        zone_key, event_time = parsed
        direction = int(zone_key.split("|")[1])
        source_tf = zone_key.split("|")[0]

        # Strict for M1/M3, matching their own entry rule -- neither can
        # hold a resting order without M5/the zone's CURRENT agreement,
        # continuously, not just at the moment it was placed. Confirmed
        # live: the old lenient check let an M1 SELL built at a moment the
        # zone briefly favored bearish keep resting after the zone flipped
        # back to bullish moments later, purely because the order's own
        # timestamp was newer than the zone's -- the same "fresh enough to
        # override" exception that strict=True already exists to remove
        # from entries, just not previously applied to cancellation too.
        # M5 keeps the lenient default, same reasoning as its entry check:
        # it's one of the zone's own inputs, so the exception is
        # effectively inert for it anyway.
        strict = source_tf in ("M1", "M3")
        zone_ineligible = not is_eligible(zone, direction, event_time, strict=strict)
        ob_gone = not _ob_still_exists(source_tf, direction, event_time, m1, m3, m5)
        if not zone_ineligible and not ob_gone:
            continue

        reason = "zone turned against it" if zone_ineligible else "its origin OB was invalidated"
        print(f"[EXIT] cancelling pending #{order.ticket}: {reason} ({zone_key}) "
              f"-> blocking {source_tf} zone {zone_key}")
        if cfg.enable_trading:
            result = broker.cancel_pending_order(order.ticket)
            if not result.ok:
                # Don't mark expected_cancellations or block the zone -- the
                # order is still resting. Marking it anyway would (a) make a
                # later genuine manual cancel of this same order get silently
                # misattributed to the bot (expected_cancellations is keyed
                # only by ticket, with no success requirement), and (b) block
                # a zone whose order never actually left. Confirmed live:
                # this call was failing silently every cycle with no result
                # check at all, retrying forever with zero visibility.
                print(f"[EXIT] cancel failed: {result.retcode} {result.comment}")
                continue
            runtime.expected_cancellations.add(order.ticket)
            blocked.block(source_tf, zone_key, reason="zone_ineligible" if zone_ineligible else "ob_invalidated")


def run_once(cfg: Config, store: TradedZoneStore, blocked: BlockedZoneStore,
            direction_blocks: DirectionBlockStore, runtime: RuntimeState,
            sl_manager: SLManager) -> None:
    m15 = read_zone(cfg.symbol, 15)
    m5 = read_zone(cfg.symbol, 5)
    m3 = read_zone(cfg.symbol, 3)
    m1 = read_zone(cfg.symbol, 1)
    atr = read_atr(cfg.symbol, cfg.atr_timeframe_minutes)

    zone = compute_zone(atr, m5)
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
        sync_manual_intervention(cfg, blocked, direction_blocks, runtime)
        release_stale_blocks(blocked, runtime, m1, m3, m5)
        release_stale_direction_blocks(direction_blocks, m1, m3, m5)

    # 1. Build both-direction candidates and find the winner up front --
    #    needed both for the square-off check in step 2 and for placing a
    #    new entry in step 5 (which reuses this same winner rather than
    #    rebuilding it). Safe to run even with zone.state == NONE:
    #    is_eligible() fails closed on every candidate in that case, so
    #    winner naturally comes out None and step 2/5 both no-op.
    candidates = []

    def eligible(c, strict: bool = False) -> bool:
        return (c is not None
                and not store.is_traded(c.zone_key)
                and not blocked.is_blocked(c.source_tf, c.zone_key)
                and not direction_blocks.is_blocked(c.direction)
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

    # 2. Square off: close any open position the instant an ELIGIBLE
    #    opposite-direction candidate currently WINS -- i.e. it's not just
    #    "some OB exists somewhere", it's the actual best available setup
    #    right now, on any of M1/M3/M5, already matching the zone's/M5's
    #    current favored direction (that's what winning via is_eligible
    #    already requires -- no separate bias check needed here). Replaces
    #    the old fresh_opposite_ob_exists mechanism, which only ever looked
    #    at a fresh M5 OB in isolation: confirmed live that let a fully
    #    eligible, winning M3 setup sit there doing nothing while an
    #    existing position rode all the way to its own SL instead of being
    #    squared off the moment the opposite trade was actually ready. A
    #    same-direction winner (or no winner at all) leaves the position
    #    alone, same as always -- this never touches pending orders, only
    #    open positions; cancel_zone_ineligible_pending (step 3) handles
    #    pending orders via the proper zone-eligibility rule.
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        if winner is not None and winner.direction != direction:
            print(f"[EXIT] closing {'BUY' if direction == 1 else 'SELL'} position #{pos.ticket}: "
                  f"opposite {winner.source_tf} setup won "
                  f"({'BUY' if winner.direction == 1 else 'SELL'})")
            if cfg.enable_trading:
                broker.close_position(cfg.symbol, pos, cfg.deviation_points)

    # 3. Independent of the above: a resting pending order whose OB the
    #    zone has turned against (Strong<->Weak flip, no opposing OB
    #    needed) gets cancelled too. Positions are untouched here -- see
    #    the function's docstring for why.
    cancel_zone_ineligible_pending(cfg, zone, blocked, runtime, m1, m3, m5)

    # 4. Trail every open position in its own direction, regardless of which
    #    source timeframe opened it. Two methods combined every cycle --
    #    OB-edge (M15/M5/M3 structure, same as always) and point-based
    #    (breakeven at +7, then a running 10pt gap off the best price seen
    #    since entry) -- whichever proposes the more protective SL wins,
    #    never loosening either way. A manually-changed SL pauses both
    #    methods for that position until a genuinely new OB edge or a new
    #    price extreme appears -- see sl_manager.py for the full mechanics
    #    and the worked examples this was verified against.
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

    # 5. New entries -- reuses the candidates/winner already computed in
    #    step 1 (direction-gating via is_eligible/strict= is described
    #    there). If step 2 just squared off an opposite position this same
    #    cycle, broker.get_positions() below already reflects that (close
    #    is synchronous, same as a market order fill) -- so a winning
    #    opposite candidate can open its new trade in the very same cycle
    #    it closed the old one, not next-cycle. A same-direction winner,
    #    or no winner, or a still-open position that square-off left alone
    #    (dry-run mode, or a losing/no-opposite cycle) all just return
    #    below via the existing "don't stack" check -- no separate
    #    zone.state == NONE guard needed here either: winner is already
    #    None in that case (see step 1).
    if winner is None:
        return

    # Already holding a position -- don't stack another, regardless of
    # direction: a same-direction entry would pyramid, an opposite one
    # would hedge -- both against the "one position at a time" rule.
    if broker.get_positions(cfg.symbol, cfg.magic_number):
        return

    # Not direction-filtered: only one pending order is ever meant to rest
    # at a time now, regardless of which direction it's in, so whatever is
    # currently pending (if anything) is what winner competes against --
    # should_replace_pending() below handles a cross-direction replace the
    # same way it already handles a same-direction one.
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

    # No separate cross-direction pending cleanup needed here: the REPLACE
    # step just above already cancelled whatever was previously pending
    # (any direction) once should_replace_pending() said this winner is
    # strictly closer -- there's only ever one pending order at a time.

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
    print(f"SMC V2 bot starting | symbol={cfg.symbol} lots={cfg.lots} magic={cfg.magic_number} "
          f"trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)
    blocked = BlockedZoneStore(cfg.blocked_state_file)
    direction_blocks = DirectionBlockStore(cfg.direction_block_state_file)
    sl_manager = SLManager(cfg.sl_state_file)
    runtime = RuntimeState()

    try:
        while True:
            try:
                run_once(cfg, store, blocked, direction_blocks, runtime, sl_manager)
            except Exception as exc:
                print(f"[ERROR] {exc}")
                # The MT5 IPC channel can get stuck without the process
                # crashing (seen live: a fresh connection works fine while
                # this process's own channel keeps failing every poll).
                # Reconnecting here means a degraded connection self-heals
                # on the next cycle instead of failing silently forever.
                # NOTE: calling shutdown() first hung the process live (the
                # channel was already broken, and closing+reopening it
                # blocked indefinitely) -- re-initialize only, no shutdown.
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
