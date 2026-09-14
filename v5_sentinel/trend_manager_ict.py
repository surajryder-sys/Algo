"""Trend Manager's ICT component (TM-ICT) -- an OB-zone-based entry
engine, fully INDEPENDENT from TM-STR (main.py's own M5/M3 structure
logic; confirmed with the user 2026-09-14, same precedent as RM-STR/
RM-ICT). Own magic number/position slot/SL+Trade Manager state files --
can hold a position opposite to TM-STR's own at the same time, they
never square each other off.

Run with: python -m v5_sentinel.trend_manager_ict

Scoped to M3 only for now ("we will design TM-ICT for just M3 and
possibly M5 later") -- every M3-specific piece below takes tf_minutes/
tf_label as an explicit value rather than hardcoding it structurally, so
a second M5 instance later is a second config/state-file set, not a
rewrite.

OB ZONE SOURCE: ict_ob_block.ICTBlockStore, merging TWO sources every
cycle -- the tv_scraper's own raw M3 zone store (TV bridge) AND
ob_bridge_lite.read_lite() (MT5's own OB_Zone_Bridge_Lite indicator, see
that module's own docstring for what it took to confirm this exists and
is live). "whichever is recent and valid" (the user's own words) falls
out of scanning EVERY currently-valid zone from both sources each cycle
and sorting matches by formed_time descending -- see find_signals().

THREE ENTRY RULES per zone (confirmed 2026-09-14, ported from algo_v2's
own entries.py where explicitly said to be "as it is", changed only
where the user explicitly said to change it):

  Rule 1 ("MO") -- current price within MARKET_MAX_POINTS (4pts) of the
  zone's own entry edge (top for bullish/demand, bottom for bearish/
  supply) at the moment the zone was first seeded here -> fires as a
  real MARKET order, no waiting.

  Rule 2 ("PB") -- 4-12pts away at seed time -> a 40% pullback target
  is computed once (frozen, same PULLBACK_MIN_EDGE_OFFSET=4.0 floor as
  algo_v2) and watched every cycle; the moment LIVE price actually
  reaches it, fires as a MARKET order. "no need to place pending order,
  compute internally and push orders when criteria is met" -- unlike
  algo_v2, TM-ICT never places a real broker-side pending order for
  this.

  Rule 1/2 own M3-BIAS GATE (_m3_bias_agrees(), added 2026-09-15, user's
  own words: "if a bearish ob qualifies sell setup, recent bias from the
  same m3 timeframe should be either weak or bearish supertrend...
  similarly for bullish trade as well" -- corrected the same day once
  the user caught that a naive "either signal currently agrees" check is
  wrong: "recency matters... lets ATR Dual Trail formed strong at 17:00,
  but super trend is bearish since 13:00, dont qualify sell" [bearish OB
  at 17:30] -- Supertrend's own bearish reading is stale/irrelevant once
  ATR-dual's own MORE RECENT event already turned things bullish; a
  plain OR of "is either currently bearish" would have wrongly let the
  stale Supertrend reading qualify that sell). So: neither MO nor PB
  fires at all unless structure.compute_structure_signal() for M3 --
  the SAME recency-arbitrated "whichever of Supertrend or ATR-dual
  produced the MOST RECENT clear event wins" model TM-STR's own M5/M3
  bias already runs on, not a separate OR check -- currently agrees
  with the entry's own direction. Unavailable (either bridge stale)
  blocks the entry (a stated requirement, not an exceptional-harm check
  -- can't confirm it, don't fire unconfirmed). Deliberately NOT applied
  to Rule 3 (3F/ST3F) -- both already require a FRESH matching flip on
  the more decisive side, which this recency arbitration would trivially
  agree with anyway.

  Rule 3 ("3F" / "ST3F") -- a fresh M3 structure-flip event whose own
  bar_time/event_time is AFTER the zone's own formation, matching the
  zone's own direction -- independent of Rule 1/2, can fire on a zone
  regardless of its own entry_mode (including "NONE", i.e. a zone too
  far away for Rule 1/2 to ever have fired on it at all). Two flavors,
  either sufficient alone, same "any ONE condition satisfying" precedent
  as RM's own dual-trigger design:
    - "3F" -- M3's ATR-dual FLIP (bridge_bar_flip.BridgeBarFlipTracker,
      same bar-close-gated mechanism every other M3 signal in this
      project uses). SL = M3's own far trail line, FROZEN at the exact
      flip bar (tracker.event_far_near()), +/- trail_sl_buffer.
    - "ST3F" -- M3's own Supertrend fresh flip (st_bridge.fresh_flip()).
      SL = that fresh-flip bar's own Supertrend value +/- trail_sl_buffer
      (no freezing needed -- the MQL5 side only updates it once per
      closed bar already).

ELIGIBILITY BLOCKING (_zone_blocked(), confirmed 2026-09-14): a zone
becomes PERMANENTLY ineligible for entry in its own original direction
the moment M3's structure has moved AGAINST it since it formed --
"a bearish flip event on dual atr will block buys for existing ob's
which formed before flip event" and (for a pending Rule 2 setup
specifically) "if price turns weak by flipping dual atr even that
disqualifies the pullback entry, then we wait for a 3rd rule to happen".
Both are the SAME underlying check: M3's CURRENT confirmed direction
(BridgeBarFlipTracker's own state.confirmed) disagrees with the zone's
own direction, AND that disagreement's own onset (state.
confirmed_since_time) is AFTER the zone's own formed_time. A zone
blocked this way naturally UN-blocks the moment a fresh Rule-3 flip back
to its own direction fires (confirmed_since_time resets to that new,
matching event) -- no separate re-enable logic needed, this is why Rule
3 is described as "the only way it gets executed" once disqualified.
Checked for every rule (1/2/3), not just 1/2 -- a blocked zone never
fires at all until that same fresh flip un-blocks it, at which point
Rule 3 itself is what's firing anyway.

SL PROGRESSION: see ict_sl_manager.py's own docstring for the full
three-stage design (OB-based frozen -> structure-confirmed far-line ->
partial-booked breakeven-floored far-line).

ICT GUARD: APPLIED to TM-ICT's own entries (corrected 2026-09-14, user's
own words: "kindly use NLB and NSB for this as well / the 5 points rule
from qualifying entry level") -- shared ict_guard.py, same module
main.py/reversal_main.py's own STR entries use (5pt default buffer,
cfg.ict_guard_buffer_points), including its STICKY blocking (see that
module's own docstring -- once a zone has blocked an entry once, it
stays blocked for this component until the zone itself is invalidated,
not merely until price drifts a few points away). NOT circular the way
RM-ICT's own exemption is: RM-ICT trades directly off the NLB/NSB
Block's OWN zones, so gating it against the very level it's entering
from would make no sense -- TM-ICT's own zones are a completely
DIFFERENT set (M3 TV+MT5 zones, not the Block's HTF D1-M5 ones), so this
is a genuine independent cross-check, same as TM-STR/RM-STR's own. "from
qualifying entry level" -- checked against whatever price THIS entry is
actually about to fire at: live ask/bid for Rule 1 (MO) and Rule 3
(3F/ST3F), the computed pullback target for Rule 2 (PB) -- see
_open_position()'s own entry_price parameter, not a separate live price
read at send time.

COMMENT SCHEME (given verbatim by the user, 2026-09-14, originally
WITHOUT the "V5S-" prefix seen elsewhere in this project, CORRECTED the
same day once source-mixing was found live -- the original scheme
hardcoded "MT5-M3" for every trade regardless of which source the zone
actually came from, so a genuinely TV-sourced zone's own trade still
read "MT5-M3" in its comment, now source-aware -- and CORRECTED AGAIN
2026-09-15 to add the "V5S-" prefix after all: "all comments to follow
V5S prefix / some are not coming"):
  V5S-TM-ICT/MT5-M3/ST3F, V5S-TM-ICT/MT5-M3/3F, V5S-TM-ICT/MT5-M3/MO, V5S-TM-ICT/MT5-M3/PB
  V5S-TM-ICT/TV-M3/ST3F,   V5S-TM-ICT/TV-M3/3F,   V5S-TM-ICT/TV-M3/MO,   V5S-TM-ICT/TV-M3/PB
"-P1"/"-P2"/"-SQ" appended the same way as every other component's own
partial-booking/square-off comments.

Safety: V5S_TM_ICT_ENABLE_TRADING must be explicitly true in .env for any
order to actually be sent/modified/cancelled -- independent of every
other component's own enable_trading flag.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import (
    bridge_flip, broker, decision_log, heartbeat, ict_guard, ict_ob_block, ict_sl_manager, st_bridge, structure,
    trade_manager,
)
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType, FlipStateResult
from v5_sentinel.reversal_ict import ICTEligibilityStore
from v5_sentinel.trend_manager_ict_config import TMICTConfig, load_config

_M3_MINUTES = 3
_TF_LABEL = "M3"
_DIR_LABEL = {1: "BUY", -1: "SELL"}


@dataclass(frozen=True)
class TMICTSignal:
    direction: int
    zone_id: str
    trigger: str        # "MO" | "PB" | "3F" | "ST3F"
    sl: float
    zone_top: float
    zone_btm: float
    formed_time: int
    entry_price: float   # the "qualifying entry level" the ICT Guard checks -- see module docstring
    source: str            # "tv" | "mt5" -- the zone's own source, see ict_ob_block.ICTZone.source


_SOURCE_LABEL = {"tv": "TV", "mt5": "MT5"}


def _tag(source: str, trigger: str) -> str:
    """2026-09-14, corrected per the user's own direction -- the comment
    now carries which source the underlying zone actually came from
    (found live: a trade's comment read "MT5-M3" for a zone that was
    actually TV-sourced, since the original scheme was a fixed label,
    not source-aware -- see this module's own COMMENT SCHEME docs).
    "V5S-" prefix added 2026-09-15 ("all comments to follow V5S prefix")
    -- the original 2026-09-14 scheme deliberately omitted it; that's
    now reversed so every component's own comment is consistently
    prefixed."""
    label = _SOURCE_LABEL.get(source, source.upper())
    return f"V5S-TM-ICT/{label}-{_TF_LABEL}/{trigger}"


def _action_comment(tag: str, action_code: str) -> str:
    return f"{tag}-{action_code}"


def _zone_blocked(zone_direction: int, fs: Optional[FlipStateResult], zone_formed_time: int) -> bool:
    """See module docstring's own ELIGIBILITY BLOCKING section. No
    bridge data at all (fs is None) is treated as NOT blocked -- same
    "no fallback, no guess" contract as everywhere else, but blocking is
    the exceptional case here (must be positively demonstrated), not the
    default."""
    if fs is None:
        return False
    return fs.confirmed.value != zone_direction and fs.confirmed_since_time > zone_formed_time


def _m3_bias_agrees(direction: int, m3_structure: Optional["structure.StructureSignal"]) -> bool:
    """Rule 1/2 (MO/PB) ONLY -- 2026-09-15, user's own words: "if a
    bearish ob qualifies sell setup, recent bias from the same m3
    timeframe should be either weak or bearish supertrend... similarly
    for bullish trade as well" -- CORRECTED the same day once the user
    caught that a naive "either signal currently agrees" OR-check is
    wrong: "recency matters... lets ATR Dual Trail formed strong at
    17:00, but super trend is bearish since 13:00, dont qualify sell"
    (bearish OB at 17:30) -- Supertrend being bearish is stale/irrelevant
    once ATR-dual's OWN more recent event (17:00, after Supertrend's
    13:00) already turned things bullish; a plain OR would have wrongly
    let Supertrend's older reading qualify the sell.

    So this reuses structure.compute_structure_signal() UNCHANGED --
    the exact same recency-arbitrated "whichever of Supertrend or
    ATR-dual produced the MOST RECENT clear event wins, full stop"
    model TM-STR's own M5/M3 bias already runs on (see structure.py's
    own docstring) -- rather than a separate OR of the two signals'
    current states. "Weak"/"strong" is this project's own established
    vocabulary (flip_state.label(): STRONG=confirmed bullish,
    WEAK=confirmed bearish), which is exactly what compute_structure_
    signal's own ATR_STRUCTURE branch already resolves to.

    Deliberately NOT applied to Rule 3 (3F/ST3F): both already require a
    FRESH matching flip on the more decisive side, which the recency
    arbitration here would trivially agree with anyway. None (bridge
    data unavailable) blocks the entry -- a stated REQUIREMENT for
    MO/PB to fire at all, not an exceptional-harm check, so "can't
    confirm it" doesn't default to "allow it"."""
    return m3_structure is not None and m3_structure.direction == direction


def find_signals(cfg: TMICTConfig, store: ict_ob_block.ICTBlockStore, eligibility: ICTEligibilityStore,
                 tracker: BridgeBarFlipTracker, bid: float, ask: float) -> list[TMICTSignal]:
    """Every currently-valid, unblocked, untraded zone that qualifies
    under Rule 1, 2, or 3 THIS cycle. Sorted by formed_time descending --
    "whichever is recent and valid, start executions based on that"; the
    caller's own position lifecycle acts on index 0 first when more than
    one qualifies (the rest get marked traded + logged as redundant, same
    pattern as RM's own multi-signal handling)."""
    signals: list[TMICTSignal] = []

    fs_m3 = tracker.update(cfg.symbol, _M3_MINUTES)
    st_fresh = st_bridge.fresh_flip(cfg.symbol, _M3_MINUTES)
    m3_structure = structure.compute_structure_signal(tracker, cfg.symbol, _M3_MINUTES)
    fresh_atr_flip = (fs_m3 is not None and fs_m3.event_just_happened() and fs_m3.last_event is not None
                      and fs_m3.last_event.event_type == EventType.FLIP)

    for zone in store.zones():
        if eligibility.is_traded(zone.zone_id):
            continue
        dup = eligibility.overlaps_traded(zone.top, zone.btm)
        if dup is not None:
            print(f"[V5S-TM-ICT] zone {zone.zone_id} [{zone.btm:.3f}-{zone.top:.3f}] skipped -- "
                  f"substantially overlaps already-traded zone {dup}")
            continue
        if _zone_blocked(zone.direction_int, fs_m3, zone.formed_time):
            continue

        market_price = ask if zone.direction_int == 1 else bid

        if zone.entry_mode == "MARKET" and _m3_bias_agrees(zone.direction_int, m3_structure):
            sl = ict_ob_block.initial_sl(zone, cfg.ict_sl_buffer)
            signals.append(TMICTSignal(zone.direction_int, zone.zone_id, "MO", sl, zone.top, zone.btm,
                                       zone.formed_time, market_price, zone.source))
        elif (zone.entry_mode == "PENDING" and zone.entry_target is not None
              and _m3_bias_agrees(zone.direction_int, m3_structure)):
            reached = (market_price <= zone.entry_target) if zone.direction_int == 1 else \
                (market_price >= zone.entry_target)
            if reached:
                sl = ict_ob_block.initial_sl(zone, cfg.ict_sl_buffer)
                # "the 5 points rule from qualifying entry level" -- the
                # qualifying level for a pullback entry is the computed
                # target itself, not whatever live price happens to be
                # at the exact instant it's reached.
                signals.append(TMICTSignal(zone.direction_int, zone.zone_id, "PB", sl, zone.top, zone.btm,
                                           zone.formed_time, zone.entry_target, zone.source))

        if (fresh_atr_flip and fs_m3.last_event.confirmed.value == zone.direction_int
                and fs_m3.last_event.bar_time > zone.formed_time):
            frozen = tracker.event_far_near(_M3_MINUTES)
            if frozen is not None:
                far, _near = frozen
                sl = far - cfg.trail_sl_buffer if zone.direction_int == 1 else far + cfg.trail_sl_buffer
                signals.append(TMICTSignal(zone.direction_int, zone.zone_id, "3F", sl, zone.top, zone.btm,
                                           zone.formed_time, market_price, zone.source))

        if (st_fresh is not None and st_fresh.trend == zone.direction_int
                and st_fresh.event_time > zone.formed_time):
            sl = st_fresh.supertrend - cfg.trail_sl_buffer if zone.direction_int == 1 else \
                st_fresh.supertrend + cfg.trail_sl_buffer
            signals.append(TMICTSignal(zone.direction_int, zone.zone_id, "ST3F", sl, zone.top, zone.btm,
                                       zone.formed_time, market_price, zone.source))

    signals.sort(key=lambda s: -s.formed_time)
    return signals


def _open_position(cfg: TMICTConfig, sticky: ict_guard.ICTGuardStickyStore, direction: int, sl: float, tag: str,
                   ref_desc: str, entry_price: float) -> bool:
    """Returns True if the entry actually went through (or enable_trading
    is False, decision only) -- False on a genuine order rejection OR an
    ICT Guard block, same "don't consume eligibility on a failed/blocked
    entry" contract as every other component. See ict_guard.py's own
    docstring for why this genuinely applies to TM-ICT too, unlike
    RM-ICT's exemption. entry_price is the signal's own "qualifying
    entry level" (see TMICTSignal.entry_price), not a fresh live read."""
    block_reason = ict_guard.check(cfg.nlb_nsb_block_state_file, sticky, direction, entry_price,
                                   cfg.ict_guard_buffer_points)
    if block_reason is not None:
        label = "ICT Long Blocked" if direction == 1 else "ICT Short Blocked"
        msg = f"{label} -- {block_reason} -- {_DIR_LABEL[direction]} ({tag}) skipped"
        print(f"[V5S-TM-ICT-ENTRY] {msg}")
        decision_log.log(cfg.decision_log_file, "ict_guard_blocked", direction=_DIR_LABEL[direction],
                         tag=tag, reason=block_reason, entry_price=entry_price)
        return False

    print(f"[V5S-TM-ICT-ENTRY] {_DIR_LABEL[direction]} ({tag}) {ref_desc} sl={sl:.3f}")
    if not cfg.enable_trading:
        print("[V5S-TM-ICT-ENTRY] enable_trading is false -- decision only, no order sent")
        decision_log.log(cfg.decision_log_file, "entry_decision_only", direction=_DIR_LABEL[direction],
                         tag=tag, ref=ref_desc, sl=sl)
        return True
    result = broker.send_market_order(cfg.symbol, direction, cfg.lots, sl, cfg.magic_number,
                                      cfg.deviation_points, tag)
    if not result.ok:
        print(f"[V5S-TM-ICT-ENTRY] order_send failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "entry_failed", direction=_DIR_LABEL[direction], tag=tag,
                         ref=ref_desc, retcode=result.retcode, broker_comment=result.comment)
        return False
    print(f"[V5S-TM-ICT-ENTRY] filled, ticket={result.ticket}")
    decision_log.log(cfg.decision_log_file, "entry_filled", direction=_DIR_LABEL[direction], tag=tag,
                     ref=ref_desc, sl=sl, ticket=result.ticket)
    return True


def _close_position(cfg: TMICTConfig, position, action_label: str, tag: str, action_code: str) -> bool:
    print(f"[V5S-TM-ICT-EXIT] closing #{position.ticket} ({action_label}), volume={position.volume}")
    if not cfg.enable_trading:
        print("[V5S-TM-ICT-EXIT] enable_trading is false -- decision only, no order sent")
        return True
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points,
                                   comment=_action_comment(tag, action_code))
    if not result.ok:
        print(f"[V5S-TM-ICT-EXIT] close failed: retcode={result.retcode} comment={result.comment}")
        decision_log.log(cfg.decision_log_file, "close_failed", ticket=position.ticket, action=action_label,
                         retcode=result.retcode)
        return False
    decision_log.log(cfg.decision_log_file, "close_filled", ticket=position.ticket, action=action_label, tag=tag)
    return True


def _process_signal(cfg: TMICTConfig, sticky: ict_guard.ICTGuardStickyStore, sig: TMICTSignal,
                    eligibility: ICTEligibilityStore) -> None:
    """Acts on ONE signal against whatever position is ACTUALLY open
    right now (re-queried, so an earlier signal's own action this same
    cycle is visible here) -- same shape as reversal_main.py's own
    _process_signal(), scoped to TM-ICT's own magic number."""
    tag = _tag(sig.source, sig.trigger)
    ref_desc = f"zone=[{sig.zone_btm:.3f}-{sig.zone_top:.3f}]"
    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None

    if position is None:
        if _open_position(cfg, sticky, sig.direction, sig.sl, tag, ref_desc, sig.entry_price):
            eligibility.mark_traded(sig.zone_id, sig.zone_top, sig.zone_btm)
        return

    pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1

    if sig.direction != pos_direction:
        if (_close_position(cfg, position, "SQOFF", tag, "SQ")
                and _open_position(cfg, sticky, sig.direction, sig.sl, tag, ref_desc, sig.entry_price)):
            eligibility.mark_traded(sig.zone_id, sig.zone_top, sig.zone_btm)
    else:
        # SAME direction, whether still full-size or already partially
        # cut -- NO-OP, same "no closing leftover and entering fresh
        # trade" rule as every other component.
        eligibility.mark_traded(sig.zone_id)
        msg = (f"[V5S-TM-ICT] {tag} qualifies ({_DIR_LABEL[sig.direction]}) but a "
              f"{_DIR_LABEL[pos_direction]} position is already open on #{position.ticket} "
              f"-- marked traded, no new entry")
        print(msg)
        decision_log.log(cfg.decision_log_file, "redundant_signal", direction=_DIR_LABEL[sig.direction],
                         tag=tag, ref=ref_desc, existing_ticket=position.ticket)


def _run_sl_manager(cfg: TMICTConfig, mgr: ict_sl_manager.ICTSLManager, tm_mgr: trade_manager.TradeManager,
                    tracker: BridgeBarFlipTracker, position) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    fs_m3 = tracker.update(cfg.symbol, _M3_MINUTES)
    structure_confirmed = fs_m3 is not None and fs_m3.confirmed.value == direction
    far_result = bridge_flip.far_near(cfg.symbol, _M3_MINUTES, direction)
    far_line = far_result[0] if far_result is not None else None
    current_sl = position.sl if position.sl else None

    # initial_sl is only ever actually USED by ict_sl_manager on this
    # ticket's very FIRST sighting, and even then only if the broker
    # itself somehow shows no SL at all yet (see that module's own
    # docstring) -- current_sl at first sighting already IS the correct
    # OB-based value in the normal case (send_market_order set it), so
    # this entry-price fallback is a last resort only, never the OB edge
    # itself (this call site has no zone reference to recompute that
    # from) -- deliberately not None so a position is never left with
    # literally no protection on that first read.
    proposed = mgr.compute(position.ticket, direction, position.price_open, current_price, current_sl,
                          initial_sl=position.price_open, far_line=far_line,
                          structure_confirmed=structure_confirmed, partial_booked=tm_mgr.is_partially_cut(position.ticket))
    if proposed is None:
        return

    print(f"[V5S-TM-ICT-SL] #{position.ticket} -> {proposed:.3f}")
    if not cfg.enable_trading:
        print("[V5S-TM-ICT-SL] enable_trading is false -- decision only, no modify sent")
        return
    result = broker.modify_position_sl(cfg.symbol, position.ticket, proposed, tp=position.tp)
    if result.ok:
        mgr.confirm_applied(position.ticket, proposed)
    else:
        print(f"[V5S-TM-ICT-SL] modify failed: retcode={result.retcode} comment={result.comment}")


def _run_trade_manager(cfg: TMICTConfig, mgr: trade_manager.TradeManager, position) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask
    has_tp = broker.has_manual_tp(position)

    symbol_info = mt5.symbol_info(cfg.symbol)
    volume_step = symbol_info.volume_step if symbol_info is not None else 0.01

    outcome = mgr.evaluate(position.ticket, direction, position.price_open, current_price, position.volume,
                          has_tp, volume_step, entry_comment=position.comment)
    if outcome is None:
        return
    volume, label = outcome
    action_code = "P1" if label == "partial1" else "P2"
    tag = mgr.get_entry_comment(position.ticket) or f"V5S-TM-ICT/UNK-{_TF_LABEL}/UNK"

    print(f"[V5S-TM-ICT-TM] #{position.ticket} booking {label}: {volume} lots")
    if not cfg.enable_trading:
        print("[V5S-TM-ICT-TM] enable_trading is false -- decision only, no close sent")
        return
    result = broker.close_position(cfg.symbol, position, cfg.deviation_points, volume=volume,
                                   comment=_action_comment(tag, action_code))
    if not result.ok:
        print(f"[V5S-TM-ICT-TM] partial close failed: retcode={result.retcode} comment={result.comment}")


def run_once(cfg: TMICTConfig, sl_mgr: ict_sl_manager.ICTSLManager, tm_mgr: trade_manager.TradeManager,
            store: ict_ob_block.ICTBlockStore, eligibility: ICTEligibilityStore,
            tracker: BridgeBarFlipTracker, sticky: ict_guard.ICTGuardStickyStore) -> None:
    bid, ask = broker.get_tick_price(cfg.symbol)
    store.sync(cfg.tv_zone_state_file, cfg.symbol, _M3_MINUTES, bid, ask)
    store.update_live(bid, ask)

    signals = find_signals(cfg, store, eligibility, tracker, bid, ask)
    if signals:
        decision_log.log(cfg.decision_log_file, "signals_found", count=len(signals), signals=[
            {"zone_id": s.zone_id, "direction": _DIR_LABEL[s.direction], "trigger": s.trigger,
             "zone_top": s.zone_top, "zone_btm": s.zone_btm, "sl": s.sl, "formed_time": s.formed_time}
            for s in signals])

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    for sig in signals:
        _process_signal(cfg, sticky, sig, eligibility)

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None
    if position is not None:
        _run_sl_manager(cfg, sl_mgr, tm_mgr, tracker, position)
        _run_trade_manager(cfg, tm_mgr, position)


def main() -> None:
    cfg = load_config()
    print(f"[V5S-TM-ICT] starting -- symbol={cfg.symbol} magic={cfg.magic_number} "
          f"enable_trading={cfg.enable_trading} poll={cfg.poll_seconds}s")

    broker.connect(cfg)
    sl_mgr = ict_sl_manager.ICTSLManager(cfg.sl_state_file, cfg.trail_sl_buffer, cfg.breakeven_trigger_points)
    tm_mgr = trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                        cfg.partial2_trigger_points, cfg.partial2_fraction)
    store = ict_ob_block.ICTBlockStore(cfg.block_state_file)
    eligibility = ICTEligibilityStore(cfg.eligibility_state_file)
    tracker = BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file)
    sticky = ict_guard.ICTGuardStickyStore(cfg.ict_guard_sticky_state_file)

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, store, eligibility, tracker, sticky)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-TM-ICT] cycle error: {exc!r}")
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
