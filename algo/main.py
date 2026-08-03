"""Main loop: bridge -> bias -> gating -> candidates -> order execution.

Run with: python -m algo.main

Safety: SMC_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account -- use this to
watch the bot's decisions against a demo/live chart before turning it on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import MetaTrader5 as mt5

from ob_bridge.reader import read_zone, OBSnapshot
from algo import broker
from algo.alerts import AlertedZoneStore, check_virgin_zone_alerts
from algo.bias import compute_bias, TFBias, allowed_entry_sources, BiasState
from algo.blocking import BlockedZoneStore, check_reset_requests
from algo.candidates import (
    build_m1_candidate, build_m3_candidate, build_m5_candidate,
    choose_winning_candidate, should_replace_pending, current_zone_key,
    order_comment, parse_order_comment,
)
from algo.config import Config, load_config
from algo.entries import EntryMode
from algo.instance_lock import SingleInstanceLock
from algo.intervention import check_manual_pending_cancellations, check_manual_position_closes
from algo.management import compute_trailing_sl, bias_flip_exit_direction, entry_recently_sent
from algo.order_log import log_order_attempt
from algo.state_store import TradedZoneStore


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
    # Last bias state seen, so a transition can be logged (with the M15/M5
    # inputs that produced it) instead of the resulting EXIT/ENTRY actions
    # being the only visible trace of *why* the bot did something.
    last_bias_state: Optional[BiasState] = None
    # "M1"/"M3"/"M5"/"M15" -> last successfully-read OBSnapshot, so a single
    # poll's failed bridge read can fall back to it instead of being misread
    # as "this timeframe's OB just disappeared" -- see resolve_snapshot().
    last_snapshot: dict = field(default_factory=dict)
    # direction -> time.time() an entry was last sent in that direction --
    # covers both MARKET fill-visibility lag and PENDING cancel-then-
    # instant-re-place thrashing. See management.entry_recently_sent().
    recent_entry: dict = field(default_factory=dict)
    # "M15"/"M5" -> last CONFIRMED (trusted) TFBias, so a one-poll
    # regression can't immediately override it. See debounce_tf_bias().
    confirmed_tf_bias: dict = field(default_factory=dict)
    # "M15"/"M5" -> ((direction, origin_time), consecutive_poll_count) for
    # a regression candidate currently being evaluated.
    pending_regression: dict = field(default_factory=dict)


def _tf_bias(snap: Optional[OBSnapshot]) -> TFBias:
    if snap is None:
        return TFBias(0, 0)
    return TFBias(snap.bias, snap.latest_time)


REGRESSION_CONFIRM_POLLS = 2


def debounce_tf_bias(label: str, raw: TFBias, runtime: RuntimeState,
                     confirm_polls: int = REGRESSION_CONFIRM_POLLS) -> TFBias:
    """A timeframe's reported "latest" OB origin should only ever move
    forward -- a newer zone can appear, but a real one never legitimately
    goes backward except on genuine deletion/invalidation. Confirmed live:
    the indicator does a full destructive rescan of chart objects on every
    tick (ArrayResize(zones, 0) then re-enumerate), and if that scan
    catches the source OB indicator mid-redraw (deleting and recreating
    its own rectangles as new price data reshapes a pattern), a still-
    valid zone can vanish from a single scan and reappear on the very next
    one -- reported here as a one-poll "latest" regression that never
    actually happened. A genuine deletion/invalidation, by contrast,
    persists across many consecutive polls.

    Any FORWARD move is trusted immediately -- a genuinely newer zone
    existing is never ambiguous. A regression is only trusted once the
    exact same regressed (direction, origin_time) repeats for
    `confirm_polls` consecutive polls, filtering out the one-tick redraw
    race while still reacting within ~confirm_polls seconds to a real,
    sustained change."""
    confirmed = runtime.confirmed_tf_bias.get(label)

    if confirmed is None or raw.origin_time >= confirmed.origin_time:
        runtime.confirmed_tf_bias[label] = raw
        runtime.pending_regression.pop(label, None)
        return raw

    candidate = (raw.direction, raw.origin_time)
    pending = runtime.pending_regression.get(label)
    count = pending[1] + 1 if pending is not None and pending[0] == candidate else 1
    runtime.pending_regression[label] = (candidate, count)

    if count >= confirm_polls:
        runtime.confirmed_tf_bias[label] = raw
        runtime.pending_regression.pop(label, None)
        return raw

    return confirmed


def resolve_snapshot(fresh: Optional[OBSnapshot], cached: Optional[OBSnapshot]) -> Optional[OBSnapshot]:
    """Falls back to the last known-good snapshot when this poll's read
    failed (see read_zone()'s docstring -- a missing/mid-write/briefly-
    locked file is expected and transient). A failed read must never be
    treated as "this timeframe's OB just disappeared": confirmed live on
    eth_smc/btc_smc, that exact misreading collapsed a real M15 direction to
    TFBias(0, 0) for a single poll, flipped compute_bias()'s output, and
    force-closed a position mere seconds after it was opened -- repeatedly,
    on a market that hadn't actually changed. Only falls back if the cached
    snapshot isn't ALSO stale (the bridge could genuinely be down, not just
    one poll's bad luck), in which case None correctly propagates as
    "unknown"."""
    if fresh is not None:
        return fresh
    if cached is not None and not cached.is_stale():
        return cached
    return None


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


def reconcile_duplicate_fills(cfg: Config) -> None:
    """MT5's IPC layer can occasionally submit a single market-order request
    more than once during a connection hiccup -- confirmed live twice now:
    one send_market_order() call (one log_order_attempt entry), two
    broker-side fills, 16 seconds apart the second time. That's a
    terminal/transport-level retry, not something a single order_send()
    call can prevent from the Python side.

    Grouping by comment (the original approach) has a confirmed blind
    spot: one of the two fills' comments came back truncated by one
    character (the same corruption reconcile_duplicate_pending already
    works around), so the two positions had different comment strings and
    were never recognized as duplicates. Direction + SL is what's actually
    identical across retries of the same logical send instead -- and
    since the "already holding a position in this direction" check
    upstream means two INTENTIONAL positions can never be open in the same
    direction simultaneously, any two that ARE both open right now sharing
    direction + SL are definitionally a duplicate fill, not a coincidence.
    Keeps the earliest, closes the rest, every cycle."""
    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    by_setup: dict = {}
    for pos in positions:
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        key = (direction, pos.sl)
        by_setup.setdefault(key, []).append(pos)

    for key, group in by_setup.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda p: p.time)
        keep, extras = group[0], group[1:]
        print(f"[DEDUP] {len(extras)} duplicate fill(s) for direction={key[0]} sl={key[1]} -- "
              f"keeping #{keep.ticket}, closing {[e.ticket for e in extras]}")
        if cfg.enable_trading:
            for extra in extras:
                result = broker.close_position(cfg.symbol, extra, cfg.deviation_points, comment="SMC dedup close")
                log_order_attempt("DEDUP_CLOSE", extra.comment, result, "SMC dedup close")
                if not result.ok:
                    print(f"[DEDUP] close failed for #{extra.ticket}: {result.retcode} {result.comment}")


def reconcile_duplicate_pending(cfg: Config, runtime: RuntimeState) -> None:
    """The same MT5 IPC retry behavior as reconcile_duplicate_fills, but for
    orders that haven't filled yet: confirmed live, one send_pending_order()
    call produced four broker-side order events in six seconds, same
    direction/price/SL every time. Comment can't be used as the grouping
    key here the way reconcile_duplicate_fills uses it for positions:
    one of the retries came back missing its trailing checksum character,
    and that doesn't just fail to parse -- dropping the last character
    shifts which digit the checksum formula sees, so it can coincidentally
    re-validate as a completely different, bogus-but-well-formed zone
    identity instead of being rejected as garbage. Direction + entry price
    + SL is what's actually identical across retries of the same logical
    send, so that's the grouping key instead. Keeps the earliest order in
    each group, cancels the rest -- registering each cancellation as
    expected so sync_manual_intervention never mistakes it for a manual
    one."""
    orders = broker.get_pending_orders(cfg.symbol, cfg.magic_number)
    by_setup: dict = {}
    for o in orders:
        direction = 1 if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP) else -1
        key = (direction, o.price_open, o.sl)
        by_setup.setdefault(key, []).append(o)

    for key, group in by_setup.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda o: o.time_setup)
        keep, extras = group[0], group[1:]
        print(f"[DEDUP] {len(extras)} duplicate pending order(s) for direction={key[0]} "
              f"price={key[1]} sl={key[2]} -- keeping #{keep.ticket}, cancelling {[e.ticket for e in extras]}")
        if cfg.enable_trading:
            for extra in extras:
                runtime.expected_cancellations.add(extra.ticket)
                result = broker.cancel_pending_order(extra.ticket)
                log_order_attempt("DEDUP_CANCEL", f"dir={key[0]} price={key[1]}", result, "")
                if not result.ok:
                    print(f"[DEDUP] cancel failed for #{extra.ticket}: {result.retcode} {result.comment}")


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


def _zone_has_live_order(cfg: Config, zone_key: str) -> bool:
    """Broker-side duplicate guard: checks live positions and pending orders
    directly (not the local TradedZoneStore file) for this exact zone_key.
    Two independently-running processes never see each other's writes to the
    shared JSON state file -- both can pass the "not yet traded" check in the
    same poll cycle before either's mark_traded() lands, and each process's
    in-memory copy never re-reads what the other has written since. The
    broker's own live state is the one thing every process actually shares,
    so it's the only check that closes this race, however many processes
    end up running at once (confirmed live: this is exactly how the BTCUSD
    bot ended up with 2-3 duplicate positions on the same zone)."""
    for p in broker.get_positions(cfg.symbol, cfg.magic_number):
        parsed = parse_order_comment(p.comment)
        if parsed is not None and parsed[0] == zone_key:
            return True
    for o in broker.get_pending_orders(cfg.symbol, cfg.magic_number):
        parsed = parse_order_comment(o.comment)
        if parsed is not None and parsed[0] == zone_key:
            return True
    return False


def release_stale_blocks(blocked: BlockedZoneStore, m1: Optional[OBSnapshot],
                         m3: Optional[OBSnapshot], m5: Optional[OBSnapshot]) -> None:
    for source_tf, snap in (("M1", m1), ("M3", m3), ("M5", m5)):
        for direction in (1, -1):
            latest_key = current_zone_key(source_tf, snap, direction)
            blocked.release_if_stale(source_tf, direction, latest_key)


def run_once(cfg: Config, store: TradedZoneStore, blocked: BlockedZoneStore, runtime: RuntimeState,
             alerts: AlertedZoneStore) -> None:
    m15 = resolve_snapshot(read_zone(cfg.symbol, 15), runtime.last_snapshot.get("M15"))
    m5 = resolve_snapshot(read_zone(cfg.symbol, 5), runtime.last_snapshot.get("M5"))
    m3 = resolve_snapshot(read_zone(cfg.symbol, 3), runtime.last_snapshot.get("M3"))
    m1 = resolve_snapshot(read_zone(cfg.symbol, 1), runtime.last_snapshot.get("M1"))
    runtime.last_snapshot["M15"] = m15
    runtime.last_snapshot["M5"] = m5
    runtime.last_snapshot["M3"] = m3
    runtime.last_snapshot["M1"] = m1

    m15_bias = debounce_tf_bias("M15", _tf_bias(m15), runtime)
    m5_bias = debounce_tf_bias("M5", _tf_bias(m5), runtime)
    bias = compute_bias(m15_bias, m5_bias)
    if bias.state != runtime.last_bias_state:
        old = runtime.last_bias_state.value if runtime.last_bias_state else "NONE"
        print(f"[BIAS] {old} -> {bias.state.value} | "
              f"M15 dir={m15_bias.direction} origin={m15_bias.origin_time} | "
              f"M5 dir={m5_bias.direction} origin={m5_bias.origin_time}")
        runtime.last_bias_state = bias.state
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = (bid + ask) / 2.0

    # Always check for a chart reset-button press, regardless of trading mode.
    check_reset_requests(blocked)

    if cfg.enable_trading:
        # 0a. Confirm any pending orders that filled since the last poll.
        sync_filled_zones(cfg, store)
        # 0b. Collapse any duplicate fills/pending orders (see
        #     reconcile_duplicate_fills / reconcile_duplicate_pending)
        #     before sync_manual_intervention takes its ticket snapshot, so
        #     a dedup cancel/close is never misread as a manual one next poll.
        reconcile_duplicate_fills(cfg)
        reconcile_duplicate_pending(cfg, runtime)
        # 0c. Detect manual cancels/closes since the last poll and block
        #     their zones; auto-release blocks a new same-direction OB
        #     has superseded.
        sync_manual_intervention(cfg, blocked, runtime)
        release_stale_blocks(blocked, m1, m3, m5)
        check_virgin_zone_alerts(cfg, current_price, alerts)

    # 1. Any bias direction (full or ShortTerm) unconditionally forces the
    #    opposite direction closed/cancelled -- only one position is ever
    #    meant to be open at a time.
    exit_dir = bias_flip_exit_direction(bias)
    if exit_dir is not None:
        for pos in broker.get_positions_by_direction(cfg.symbol, cfg.magic_number, exit_dir):
            print(f"[EXIT] closing {'BUY' if exit_dir == 1 else 'SELL'} position #{pos.ticket}: bias flip")
            if cfg.enable_trading:
                broker.close_position(cfg.symbol, pos, cfg.deviation_points)
        for order in broker.get_pending_orders_by_direction(cfg.symbol, cfg.magic_number, exit_dir):
            print(f"[EXIT] cancelling pending #{order.ticket}: bias flip")
            if cfg.enable_trading:
                runtime.expected_cancellations.add(order.ticket)
                broker.cancel_pending_order(order.ticket)

    # 2. Trail every open position in its own direction, regardless of which
    #    source timeframe opened it.
    for pos in broker.get_positions(cfg.symbol, cfg.magic_number):
        direction = 1 if pos.type == mt5.POSITION_TYPE_BUY else -1
        edges = _direction_edges(direction, m15, m5, m3)
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

    # An entry in this direction was sent moments ago. For MARKET, the
    # broker's own position list can lag a real fill by more than one poll
    # cycle, so the check above alone isn't enough. For PENDING, a
    # cancelled-without-filling order leaves its zone still eligible, so
    # without this guard a bias flip-flopping quickly re-places the exact
    # same zone every time it flips back. See management.entry_recently_sent().
    if entry_recently_sent(bias.direction, runtime.recent_entry, time.time()):
        return

    sources = allowed_entry_sources(bias)
    candidates = []

    def eligible(c) -> bool:
        return c is not None and not store.is_traded(c.zone_key) and not blocked.is_blocked(c.source_tf, c.zone_key)

    if "M1" in sources:
        c = build_m1_candidate(bias.direction, m1, m15, m5, m3)
        if eligible(c):
            candidates.append(c)

    if "M3" in sources:
        c = build_m3_candidate(bias.direction, m3, m15, m5, current_price)
        if eligible(c):
            candidates.append(c)

    if "M5" in sources:
        c = build_m5_candidate(bias.direction, m5, m15, m3, current_price)
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

    if not should_replace_pending(winner, pending_ticket, pending_zone_key, pending_entry_price, current_price):
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

    # No separate opposite-direction pending cleanup needed here: step 1
    # already unconditionally cancels every opposite-direction pending order
    # and position on every cycle bias has a direction, before this point.

    comment = order_comment(winner)

    if cfg.enable_trading and _zone_has_live_order(cfg, winner.zone_key):
        print(f"[SKIP] zone {winner.zone_key} already has a live position/pending order on "
              f"the broker -- duplicate-process guard")
        return

    if winner.mode == EntryMode.MARKET:
        print(f"[ENTRY] {winner.source_tf} MARKET {'BUY' if winner.direction == 1 else 'SELL'} sl={winner.sl}")
        if cfg.enable_trading:
            # Mark traded BEFORE sending, not after checking result.ok.
            # Confirmed live (2026-08-02, ETHUSD/BTCUSD): a market order
            # that returned a non-DONE retcode still resulted in a real
            # fill -- because mark_traded only ran on the "ok" path, the
            # zone stayed eligible and the next poll placed a genuine
            # duplicate. A misleading/stale retcode must never cause a
            # retry. The only cost of marking early on a send that truly
            # did fail is one missed trade on this zone -- far cheaper
            # than a duplicate live order.
            store.mark_traded(winner.zone_key)
            # Same "before send" reasoning as mark_traded above -- this
            # guard exists precisely because the broker's own position list
            # can't be trusted to reflect this fill immediately.
            runtime.recent_entry[winner.direction] = time.time()
            result = broker.send_market_order(cfg.symbol, winner.direction, cfg.lots, winner.sl,
                                              cfg.magic_number, cfg.deviation_points, comment)
            log_order_attempt("MARKET", winner.zone_key, result, comment)
            if not result.ok:
                print(f"[ENTRY] market order failed: {result.retcode} {result.comment}")
                return
    else:
        print(f"[ENTRY] {winner.source_tf} PENDING {'BUY' if winner.direction == 1 else 'SELL'} "
              f"@ {winner.entry_price} sl={winner.sl}")
        if cfg.enable_trading:
            # A cancelled-without-filling pending order never gets
            # mark_traded (see should_replace_pending) -- this guard is
            # what actually stops the same zone from being re-placed the
            # instant bias flips back, which is what caused a live
            # place/cancel/replace loop of hundreds of orders in a minute.
            runtime.recent_entry[winner.direction] = time.time()
            result = broker.send_pending_order(cfg.symbol, winner.direction, winner.entry_price, cfg.lots,
                                               winner.sl, cfg.magic_number, cfg.deviation_points, comment)
            log_order_attempt("PENDING", winner.zone_key, result, comment)
            if not result.ok:
                print(f"[ENTRY] pending order failed: {result.retcode} {result.comment}")
                # Same non-DONE-but-actually-happened risk as MARKET, but a
                # pending order can't be safely pre-marked traded (that
                # would break the replace-with-a-closer-setup feature) --
                # re-check the broker's own state before fully giving up.
                if _zone_has_live_order(cfg, winner.zone_key):
                    print(f"[ENTRY] retcode said failed but a live pending order exists for "
                          f"{winner.zone_key} -- not retrying")
                return
            # Do NOT mark traded here: a pending order can sit unfilled and
            # later get cancelled/replaced by a newer setup without ever
            # executing. The zone should only count as traded once it
            # actually fills -- see sync_filled_zones().


def main() -> None:
    cfg = load_config()

    # Refuse to start a second copy. This is what actually fixes the
    # duplicate-order incidents (2026-08-01, both eth_smc and btc_smc): the
    # broker-side guard in run_once() only narrows the race between two
    # concurrent processes down to an order_send() round-trip -- it can't
    # eliminate it. This lock makes a second launch attempt fail here,
    # immediately, instead of racing 35+ minutes later.
    lock = SingleInstanceLock("smc_instance.lock")
    try:
        lock.acquire()
    except RuntimeError as exc:
        print(f"[LOCK] {exc}")
        raise SystemExit(1)

    print(f"SMC bot starting | symbol={cfg.symbol} lots={cfg.lots} magic={cfg.magic_number} "
          f"trading={'ENABLED' if cfg.enable_trading else 'DRY RUN'}")

    broker.connect(cfg)
    store = TradedZoneStore(cfg.state_file)
    blocked = BlockedZoneStore(cfg.blocked_state_file)
    alerts = AlertedZoneStore(cfg.alert_state_file)
    runtime = RuntimeState()

    try:
        while True:
            try:
                run_once(cfg, store, blocked, runtime, alerts)
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
        lock.release()


if __name__ == "__main__":
    main()
