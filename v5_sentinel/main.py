"""V5-Sentinel Trend Manager -- main loop.

Data path (rewritten 2026-09-07): M3/M5/M15 are all BRIDGE-ONLY now --
user's explicit direction: "remove dependancy of copy rates for
M5,M3,M1 -- follow exactly bridge, nothing else... not just for RM,
even for TM, it should use bridge data." Still strictly bar-close-gated
("strictly on bar close even on bridge data", not live/tick-driven) --
see bridge_bar_flip.py for the mechanism: copy_rates is used ONLY to
detect when a bar closed and what its close price was (no ATR/trail
computation of our own at all), the bridge's CURRENT line values decide
what that close confirms. If a timeframe's bridge data is missing/stale
right when its bar closes, that bar is simply skipped (state stays as
it was) -- no fallback, no guess -- and a sustained-staleness alert
fires via the same @smcsecret_bot channel Reversal Manager uses. This
replaces the old copy_rates-primary/bridge-tie-breaker design (2026-09-
04) entirely, after that same drift issue (independent recompute vs.
live chart divergence) kept needing patch after patch -- see bridge.py's
OWN docstring for that history, now superseded. M5/ICT and M15/ICT
(OB-formation bias) are NOT implemented yet; both parents are STR-only
for now (see bias.py).

Run with: python -m v5_sentinel.main

Rules implemented here (full design recap):
  - Parent bias, REVISED 2026-09-07: M5 is the PRIMARY parent, M15 only
    gets a vote when M5 ITSELF is trapped (see bias.compute_parent_bias's
    own docstring for the full table) -- this retires the 2026-09-03
    "M5 AND M15 both act as parents, disagreement opens both directions"
    design. Short version: M5 clear -> follow M5 alone, M15 not
    consulted at all. M5 trapped, M15 clear -> follow M15 alone. Both
    trapped -> NEITHER direction allowed, wait for M5 to resolve.
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
  - Ownership (added 2026-09-07, see _owner_timeframe()): once a
    watch-zone trade is taken over by M5 or M15 (straight-fire or
    pullback), that timeframe OWNS the resulting position for its whole
    life -- SL trails THAT timeframe's own far line (not M3's), and only
    THAT SAME timeframe's own fresh opposite-direction flip can close/
    reverse it. M3 activity (and, symmetrically, the other of M5/M15) has
    ZERO effect on an owned position while it's open -- see
    _maybe_apply()'s gating. A normal M3-triggered position is unaffected,
    still M3-owned as before.
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
import os
import time
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import (
    bias, bridge, broker, critical_alerts_subscribers, flip_state, heartbeat, nlb_nsb_block, rates, sl_manager,
    trade_manager, watch_zone,
)
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.bridge_flip import StaleAlertTracker, far_near
from v5_sentinel.config import Config, load_config
from v5_sentinel.critical_alerts_telegram import send_message as _telegram_send

_M3_MINUTES = 3
_DIR_LABEL = {1: "BUY", -1: "SELL"}


def _send_alert(text: str) -> None:
    """Best-effort push to @smcsecret_bot -- same channel Reversal
    Manager uses, never raises (a Telegram outage must never take the
    trading loop down with it)."""
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[V5S-ALERT] (no bot configured) {text}")
        return
    try:
        _telegram_send(token, chat_id, text)
    except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
        print(f"[V5S-ALERT] send failed: {exc!r} -- message was: {text}")


# Lazily created + reused across cycles -- SubscriberStore's own file read
# only needs to happen once per process, not once per blocked entry.
_critical_subscribers: Optional["critical_alerts_subscribers.SubscriberStore"] = None


def _send_critical_alert(text: str) -> None:
    """Broadcasts to the critical-alerts bot (SecretTrader_Critical_Bot),
    same channel + same approved-subscriber-list pattern
    critical_alerts_watcher.py and watchdog.py already use -- re-added
    2026-09-09 for the ICT Guard (replaces the earlier ATR trail-based
    safeguard, which used this same alerting and was removed the same
    day). Deliberately a SEPARATE bot/channel from _send_alert()'s
    @smcsecret_bot (bridge-staleness only) -- this is a trading-decision
    alert, not an infrastructure one. Never raises, same fail-soft
    contract as _send_alert()."""
    global _critical_subscribers
    token = os.getenv("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN")
    owner_chat_id = os.getenv("CRITICAL_ALERTS_TELEGRAM_CHAT_ID")
    if not token or not owner_chat_id:
        print(f"[V5S-CRITICAL-ALERT] (no bot configured) {text}")
        return
    if _critical_subscribers is None:
        subscribers_file = os.getenv("V5S_CRITICAL_ALERTS_SUBSCRIBERS_FILE",
                                      "v5_sentinel_critical_alerts_subscribers.json")
        _critical_subscribers = critical_alerts_subscribers.SubscriberStore(subscribers_file, owner_chat_id)
    for chat_id in _critical_subscribers.approved_chat_ids():
        try:
            _telegram_send(token, chat_id, text)
        except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
            print(f"[V5S-CRITICAL-ALERT] send failed for {chat_id}: {exc!r} -- message was: {text}")


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
        # 2026-09-07: same dedup problem, now also needed for a M5/M15-
        # OWNED position's own opposite-flip exit trigger (see
        # _owner_timeframe/_maybe_apply) -- keyed by timeframe since M5
        # and M15 each need their own independent "already acted on this
        # bar" tracking.
        self.last_owner_event_time: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self.last_m3_event_time = data.get("last_m3_event_time")
            self.last_owner_event_time = {int(k): v for k, v in data.get("last_owner_event_time", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self.last_m3_event_time = None
            self.last_owner_event_time = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "last_m3_event_time": self.last_m3_event_time,
            "last_owner_event_time": self.last_owner_event_time,
        }))

    def mark_seen(self, bar_time: int) -> None:
        self.last_m3_event_time = bar_time
        self._save()

    def owner_event_already_seen(self, tf_minutes: int, bar_time: int) -> bool:
        return self.last_owner_event_time.get(tf_minutes) == bar_time

    def mark_owner_event_seen(self, tf_minutes: int, bar_time: int) -> None:
        self.last_owner_event_time[tf_minutes] = bar_time
        self._save()


def _far_line_for(symbol: str, tf_minutes: int, direction: int) -> Optional[float]:
    result = far_near(symbol, tf_minutes, direction)
    return None if result is None else result[0]


def _owner_timeframe(tag: str) -> int:
    """Which timeframe currently governs this position's SL trailing
    basis and exit/reversal trigger, READ BACK from its own comment tag
    -- 2026-09-07: "a M5 [watch-zone] trade shouldn't check for M3
    structure once it takes over... it follows SL of M5, and nothing
    [closes/reverses it] unless M5 itself makes the flip." A watch-zone
    trade credited to M5 or M15 (tag ending "/5F"/"/15F", or exactly
    "M5-PB"/"M15-PB") is owned by that same timeframe -- M3 activity has
    zero effect on it -- UNLESS a later M3 TRAP_RESOLVED reconfirms the
    SAME direction, added 2026-09-08: that hands ownership to M3 (see
    run_once's own handover branch), reopening the position under a
    fresh M3-owned tag so this function reads the new ownership back
    correctly on a later restart too, not just for the rest of this
    process's run. A normal M3-triggered trade (tag ending "/3F" or
    "/3T") is unaffected, still M3-owned. Unrecognized tags default to
    3, matching this system's original, only-ever-M3 behavior."""
    if tag.endswith("/5F") or tag == "M5-PB":
        return 5
    if tag.endswith("/15F") or tag == "M15-PB":
        return 15
    return 3


def _parent_tag(parent: "bias.ParentBiasResult", direction: int) -> str:
    """Which parent to credit in the entry comment. 2026-09-07: bias.py's
    source is now literally "M5" or "M15" (M5 decided, or M5 was trapped
    and M15 decided instead) -- BOTH_TRAPPED never reaches here in
    practice, since allows() is False for both directions in that case,
    but "M15M5" is kept as a safe fallback label just in case."""
    if parent.source in ("M5", "M15"):
        return parent.source
    return "M15M5"  # BOTH_TRAPPED -- shouldn't normally be reached, see above


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


def _ict_guard_check(cfg: Config, direction: int, entry_price: float) -> Optional[str]:
    """ICT Guard -- replaces the earlier ATR trail-based safeguard
    (removed 2026-09-09), now checking the NLB/NSB Block instead of ATR
    trail lines. User's own rule (2026-09-09): "ob edge of bullish ob
    should be minimum 5 points away for short, ob edge of bearish ob
    should be minimum 5 points away for long" -- "when i say edge,
    bullish ob edge is top, bearish ob edge is bottom." So: a LONG checks
    every NLB (bearish OB) zone's own BOTTOM edge; a SHORT checks every
    NSB (bullish OB) zone's own TOP edge. Scoped to whatever's currently
    in the block -- nlb_nsb_block.py only ever seeds/tracks the same 6
    timeframes ob_levels.py does (D1, H4, H1, M30, M15, M5), so M3/M1 are
    already out of scope by construction, matching the user's own "we
    have nothing to do with M3 and M1 zones... we gonna ignore m3 and m1
    now" -- nothing extra to filter here.

    Reads the block fresh every call (cheap JSON file) rather than
    caching it in memory -- the block is written by a SEPARATE process
    (nlb_nsb_watcher.py), so a cached copy here would silently drift from
    whatever that process has actually seen live.

    Returns a human-readable block reason (which timeframe, which exact
    zone, how close) if blocked, else None."""
    target_role = "no_long_buffer" if direction == 1 else "no_short_buffer"
    store = nlb_nsb_block.BlockStore(cfg.nlb_nsb_block_state_file)
    for zone in store.zones():
        if zone.role != target_role:
            continue
        edge = zone.btm if target_role == "no_long_buffer" else zone.top
        gap = abs(entry_price - edge)
        if gap < cfg.ict_guard_buffer_points:
            return (f"{zone.timeframe_name} {zone.role} [{zone.btm:.3f}-{zone.top:.3f}] "
                   f"edge@{edge:.3f} is only {gap:.3f}pts away")
    return None


def _open_position(cfg: Config, direction: int, sl_tf: int, comment: str) -> None:
    far = _far_line_for(cfg.symbol, sl_tf, direction)
    if far is None:
        print(f"[V5S-ENTRY] M{sl_tf} bridge stale/missing -- cannot compute SL, skipping {_DIR_LABEL[direction]} ({comment})")
        return
    sl = far - cfg.sl_buffer if direction == 1 else far + cfg.sl_buffer

    bid, ask = broker.get_tick_price(cfg.symbol)
    entry_price = ask if direction == 1 else bid
    block_reason = _ict_guard_check(cfg, direction, entry_price)
    if block_reason is not None:
        label = "ICT Long Blocked" if direction == 1 else "ICT Short Blocked"
        msg = f"{label} -- {block_reason} -- {_DIR_LABEL[direction]} ({comment}) skipped"
        print(f"[V5S-ENTRY] {msg}")
        _send_critical_alert(f"\U0001F6D1 {msg}")
        return

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


def _apply_signal(cfg: Config, position, new_dir: int, tag: str, sl_tf: int, tm_mgr: trade_manager.TradeManager):
    """Shared position-lifecycle branching -- used by every trigger source
    (normal M3 event, watch-zone signal, or a M5/M15-owned position's own
    reversal, all 2026-09-07), so they all behave identically once a
    direction+tag+sl_tf is decided: no position -> open; opposite
    direction -> square off + reopen; same direction + already partially
    cut -> refresh (square off + reopen full size); same direction +
    still full size -> no-op. Returns the resulting position (re-queried
    after any broker action)."""
    comment = _comment_for_tag(tag)
    if position is None:
        _open_position(cfg, new_dir, sl_tf, comment)
        positions = broker.get_positions(cfg.symbol, cfg.magic_number)
        return positions[0] if positions else None

    pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    if new_dir != pos_direction:
        if _close_position(cfg, position, "SQOFF", tag, "SQ"):
            _open_position(cfg, new_dir, sl_tf, comment)
            positions = broker.get_positions(cfg.symbol, cfg.magic_number)
            return positions[0] if positions else None
        return position

    if tm_mgr.is_partially_cut(position.ticket):
        if _close_position(cfg, position, "REFRESH", tag, "RF"):
            _open_position(cfg, new_dir, sl_tf, comment)
            positions = broker.get_positions(cfg.symbol, cfg.magic_number)
            return positions[0] if positions else None
        return position

    return position  # same direction, still full size -- nothing to refresh


def _maybe_apply(cfg: Config, position, owner_tf: int, new_dir: int, tag: str, sl_tf: int,
                 tm_mgr: trade_manager.TradeManager, source_label: str) -> tuple:
    """Gate before _apply_signal -- 2026-09-07: once a position is open,
    ONLY its own owning timeframe's signals may touch it. A different
    timeframe's activity (e.g. M3 noise while an M5-owned trade is open)
    is simply ignored -- no SQOFF, no refresh, nothing. Returns
    (position, owner_tf), updated if a broker action actually happened."""
    if position is not None and owner_tf != sl_tf:
        print(f"[V5S] {source_label} ignored -- current position is M{owner_tf}-owned")
        return position, owner_tf
    new_position = _apply_signal(cfg, position, new_dir, tag, sl_tf, tm_mgr)
    return new_position, sl_tf


def _run_sl_manager(cfg: Config, mgr: sl_manager.SLManager, position, owner_tf: int) -> None:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = broker.get_tick_price(cfg.symbol)
    current_price = bid if direction == 1 else ask  # the side that matters for "favor" is the closing side
    far = _far_line_for(cfg.symbol, owner_tf, direction)
    if far is None:
        print(f"[V5S-SL] M{owner_tf} bridge stale/missing -- skipping SL update this cycle")
        return
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
                      fs_m3) -> Optional[tuple[int, str, int]]:
    """Runs the watch-zone state machine for one cycle -- see
    watch_zone.py for the full design. Returns (direction, tag, sl_tf) if
    a watch-zone-driven trade should fire THIS cycle, else None -- sl_tf
    is the CONFIRMING parent's own timeframe (5 or 15), since 2026-09-07
    that parent OWNS the resulting trade's SL basis and exit trigger, not
    M3. Arming a new zone and cancelling an invalidated one both happen
    here as a side effect regardless of whether a trade fires."""
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
    # pending) -- M5 always eligible; M15 only counts when M5 is
    # CURRENTLY trapped, matching bias.py's own M5-primary/M15-fallback
    # table (M15 has no vote at all while M5 is decisive, agree or not).
    candidates = []
    parent_candidates = [("M5", "5", parent.m5)]
    if parent.m5.watching is not None:
        parent_candidates.append(("M15", "15", parent.m15))
    for name, tf_code, fs in parent_candidates:
        if fs is not None and fs.event_just_happened() and fs.confirmed.value == zone.direction:
            candidates.append((fs.last_event.bar_time, name, tf_code, fs.last_close))

    if candidates:
        candidates.sort()
        bar_time, name, tf_code, close = candidates[-1]
        already = zone.pending is not None and zone.pending.source_bar_time == bar_time
        if not already:
            gap = abs(close - zone.qualifying_price)
            if gap <= watch_zone.PULLBACK_GATE_POINTS:
                # Staleness pre-check, same reasoning/incident as the M3
                # event path above (2026-09-08) -- wz_store.clear() used to
                # run unconditionally before _open_position ever got a
                # chance to fail on a stale far line, permanently losing
                # this fire (the zone would already be gone, nothing left
                # to retry) instead of just skipping one cycle.
                if _far_line_for(cfg.symbol, int(tf_code), zone.direction) is None:
                    print(f"[V5S-WATCHZONE] {name} confirms within {gap:.3f}pts -- "
                          f"but M{tf_code} bridge stale, deferring straight-fire, will retry once fresh")
                else:
                    print(f"[V5S-WATCHZONE] {name} confirms within {gap:.3f}pts of qualifying price "
                          f"{zone.qualifying_price:.3f} -- straight fire")
                    wz_store.clear()
                    return zone.direction, f"{name}/{tf_code}F", int(tf_code)
            else:
                print(f"[V5S-WATCHZONE] {name} confirms {gap:.3f}pts away -- arming "
                      f"{watch_zone.PULLBACK_RETRACE_FRACTION:.0%} pullback target")
                wz_store.set_pending(name, tf_code, close, bar_time)

    zone = wz_store.zone  # re-read -- set_pending() above may have just changed it
    if zone is not None and zone.pending is not None:
        p = zone.pending
        parent_tf = 5 if p.parent_tf_code == "5" else 15
        # FROZEN at the confirming parent's OWN flip bar, not live --
        # 2026-09-07, see watch_zone.py's own docstring for why (a real
        # trade was missed when this re-read the live bridge every cycle
        # instead: the near line ratcheted past the anchor close over a
        # sustained rally, and the target kept drifting rather than
        # staying put). The bridge has no history endpoint at all, so
        # this specific lookup has to go through copy_rates.
        parent_series = rates.read_trail_series(cfg.symbol, parent_tf)
        result = (rates.trail_values_at(parent_series, p.source_bar_time)
                 if parent_series is not None else None)
        if result is not None:
            _far, near = flip_state.far_near_line(zone.direction, result[0], result[1])
            if zone.direction == 1:
                target = p.anchor_close - watch_zone.PULLBACK_RETRACE_FRACTION * (p.anchor_close - near)
            else:
                target = p.anchor_close + watch_zone.PULLBACK_RETRACE_FRACTION * (near - p.anchor_close)

            bid, ask = broker.get_tick_price(cfg.symbol)
            price = bid if zone.direction == 1 else ask
            reached = price <= target if zone.direction == 1 else price >= target
            if reached:
                # Same staleness pre-check as the straight-fire branch above
                # (2026-09-08) -- clearing the zone before confirming
                # _open_position can get a fresh far line would permanently
                # lose this fire instead of retrying next cycle.
                if _far_line_for(cfg.symbol, parent_tf, zone.direction) is None:
                    print(f"[V5S-WATCHZONE] {p.parent_name} pullback target {target:.3f} reached "
                          f"(price={price:.3f}) -- but M{parent_tf} bridge stale, deferring, will retry once fresh")
                else:
                    print(f"[V5S-WATCHZONE] {p.parent_name} pullback target {target:.3f} reached (price={price:.3f})")
                    wz_store.clear()
                    return zone.direction, f"{p.parent_name}-PB", parent_tf
            else:
                print(f"[V5S-WATCHZONE] pending {p.parent_name} pullback -- target={target:.3f} current={price:.3f}")

    return None


def _check_stale(cfg: Config, stale_tracker: StaleAlertTracker) -> None:
    for tf in (3, 5, 15):
        msg = stale_tracker.check(tf, bridge.read_lines(cfg.symbol, tf) is not None)
        if msg is not None:
            print(msg)
            _send_alert(msg)


def run_once(cfg: Config, sl_mgr: sl_manager.SLManager, tm_mgr: trade_manager.TradeManager,
            runtime: RuntimeState, wz_store: watch_zone.WatchZoneStore, tracker: BridgeBarFlipTracker,
            stale_tracker: StaleAlertTracker) -> None:
    _check_stale(cfg, stale_tracker)

    parent = bias.compute_parent_bias(tracker, cfg.symbol)
    fs_m3 = tracker.update(cfg.symbol, _M3_MINUTES)
    if parent is None or fs_m3 is None:
        print("[V5S] waiting for enough bar history (parent bias / M3 state unavailable)")
        return

    positions = broker.get_positions(cfg.symbol, cfg.magic_number)
    position = positions[0] if positions else None  # one position at a time, enforced by construction below

    sl_mgr.prune({p.ticket for p in positions})
    tm_mgr.prune({p.ticket for p in positions})

    # Which timeframe owns the CURRENT position's SL basis + exit trigger
    # -- 2026-09-07: a watch-zone trade taken over by M5/M15 is immune to
    # M3 (and to the other of M5/M15) once open, see _owner_timeframe().
    owner_tf = 3
    if position is not None:
        entry_tag = _extract_tag(tm_mgr.get_entry_comment(position.ticket) or "")
        owner_tf = _owner_timeframe(entry_tag)

    # event_just_happened() alone stays True for the whole ~3-minute window
    # this bar remains the most recent CLOSED one -- the bar_time dedup
    # below is what makes this fire exactly ONCE per genuine event, not
    # once per poll while it's still the latest bar. See RuntimeState.
    if fs_m3.event_just_happened() and fs_m3.last_event.bar_time != runtime.last_m3_event_time:
        event = fs_m3.last_event
        new_dir = event.confirmed.value
        valid = parent.allows(new_dir)
        label = event.event_type.value

        # Staleness pre-check, added 2026-09-08 (found live: a real trade's
        # SL was computed off a bridge read that was 3 bars/9 minutes stale
        # -- fixed at the bridge.py layer, see its own bar_time-staleness
        # comment). That fix alone would have LOST the trade instead: the
        # event used to get marked "seen" unconditionally, before knowing
        # whether _open_position could even get a fresh far line -- a
        # stale-at-the-wrong-instant bridge meant the event was consumed
        # and never retried, even once the bridge recovered moments later.
        # Only mark_seen() once we know this specific attempt can actually
        # proceed with fresh data -- event_just_happened() stays True for
        # this bar's whole ~3-minute window (see comment above), so leaving
        # it unmarked here means the very next fresh-bridge cycle retries
        # the SAME event instead of silently dropping it.
        if valid and _far_line_for(cfg.symbol, 3, new_dir) is None:
            print(f"[V5S] M3 {label} -> {_DIR_LABEL[new_dir]} at {event.bar_time} -- "
                  f"M3 bridge stale -- deferring this event, will retry once fresh")
        else:
            runtime.mark_seen(fs_m3.last_event.bar_time)

            print(f"[V5S] M3 {label} -> {_DIR_LABEL[new_dir]} at {event.bar_time} "
                  f"(parent={parent.source}, M5={parent.m5.label()}, M15={parent.m15.label()}, "
                  f"bull_allowed={parent.bull_allowed}, bear_allowed={parent.bear_allowed}, valid={valid})")

            if valid:
                new_event_tag = _tag(parent, new_dir, label)
                if (label == "TRAP_RESOLVED" and position is not None and owner_tf in (5, 15)
                        and new_dir == (1 if position.type == mt5.POSITION_TYPE_BUY else -1)):
                    # M3 TRAP_RESOLVED reconfirming an M5/M15-owned
                    # position's OWN direction -- 2026-09-08, user
                    # direction: the M5/M15 ownership rule exists so a
                    # trade isn't missed when M3 flips first and the
                    # parent only confirms later; once that trade has run
                    # long enough to take partial exits, and M3 itself
                    # THEN independently resolves back in agreement, the
                    # parent and M3 are back in full alignment -- refresh
                    # to full size (closes any partial-cut leftover, same
                    # mechanism _apply_signal's own REFRESH branch already
                    # uses) AND hand ownership over to M3 from here on,
                    # not just for this cycle: reopening under M3's own
                    # tag makes the handover durable across a restart too
                    # (_owner_timeframe reads ownership back from the
                    # position's own comment, not from any separately
                    # persisted "current owner" -- an in-memory-only
                    # reassignment here would silently revert on the next
                    # restart otherwise). Always refreshes (not gated on
                    # is_partially_cut like a normal same-direction event)
                    # specifically so the handover is always durable, even
                    # if triggered before any partial exit happened yet.
                    print(f"[V5S] M3 TRAP_RESOLVED reconfirms M{owner_tf}-owned position's own "
                          f"{_DIR_LABEL[new_dir]} direction -- refreshing to full size, handing ownership to M3")
                    if _close_position(cfg, position, "HANDOVER", new_event_tag, "HO"):
                        _open_position(cfg, new_dir, 3, _comment_for_tag(new_event_tag))
                        positions = broker.get_positions(cfg.symbol, cfg.magic_number)
                        position = positions[0] if positions else None
                        owner_tf = 3
                else:
                    position, owner_tf = _maybe_apply(cfg, position, owner_tf, new_dir, new_event_tag, 3, tm_mgr, "M3 event")
            else:
                # Invalid M3 event (FLIP or TRAP_RESOLVED) -- park it in
                # the watch zone instead of dropping it outright, so a
                # parent catching up shortly after still gets to trade the
                # move. TRAP_RESOLVED used to be excluded from this
                # (dropped outright when invalid, per the ORIGINAL "trap
                # and resolve enters as it is" design) -- found live
                # 2026-09-08 to miss a real BUY: M3 resolved bullish while
                # M5 still disagreed, M5 itself confirmed bullish just ONE
                # MINUTE later, but nothing was parked to catch that
                # confirmation. Both event types now get the same
                # treatment. Doesn't need far_line at all (uses
                # last_close), so no staleness pre-check applies here.
                wz_store.arm(new_dir, fs_m3.last_close, event.bar_time)
                print(f"[V5S-WATCHZONE] armed {_DIR_LABEL[new_dir]} @ {fs_m3.last_close:.3f} "
                      f"(parent disagrees, {label})")

    wz_signal = _check_watch_zone(cfg, wz_store, parent, fs_m3)
    if wz_signal is not None:
        wz_dir, wz_tag, wz_sl_tf = wz_signal
        position, owner_tf = _maybe_apply(cfg, position, owner_tf, wz_dir, wz_tag, wz_sl_tf, tm_mgr, "watch-zone signal")

    # A M5/M15-OWNED position's own reversal trigger -- 2026-09-07: "it
    # shouldn't impact the M5 based trade [if M3 changes]... nothing to
    # do unless M5 doesn't make the flip." Only that SAME owning
    # timeframe's own fresh opposite-direction flip can close/reverse it.
    if position is not None and owner_tf in (5, 15):
        fs_owner = parent.m5 if owner_tf == 5 else parent.m15
        if (fs_owner.event_just_happened()
                and not runtime.owner_event_already_seen(owner_tf, fs_owner.last_event.bar_time)):
            owner_dir = fs_owner.confirmed.value
            pos_direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
            if owner_dir != pos_direction:
                # Staleness pre-check, same reasoning/incident as the M3
                # event and watch-zone paths above (2026-09-08) -- only
                # mark_owner_event_seen once we know _open_position can get
                # a fresh far line; a stale-at-the-wrong-instant read here
                # would otherwise permanently skip this reversal instead of
                # retrying next cycle.
                if _far_line_for(cfg.symbol, owner_tf, owner_dir) is None:
                    print(f"[V5S] M{owner_tf} owner-flip -> {_DIR_LABEL[owner_dir]} -- "
                          f"M{owner_tf} bridge stale, deferring reversal, will retry once fresh")
                else:
                    runtime.mark_owner_event_seen(owner_tf, fs_owner.last_event.bar_time)
                    owner_name = "M5" if owner_tf == 5 else "M15"
                    owner_tag = f"{owner_name}/{owner_tf}F"
                    print(f"[V5S] M{owner_tf} owner-flip -> {_DIR_LABEL[owner_dir]} -- reversing M{owner_tf}-owned position")
                    position, owner_tf = _maybe_apply(cfg, position, owner_tf, owner_dir, owner_tag, owner_tf,
                                                      tm_mgr, f"M{owner_tf} owner-flip")
            else:
                # Same direction -- nothing to act on, safe to mark seen
                # unconditionally (no staleness risk since no far_line read
                # happens on this branch).
                runtime.mark_owner_event_seen(owner_tf, fs_owner.last_event.bar_time)

    if position is not None:
        _run_sl_manager(cfg, sl_mgr, position, owner_tf)
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
    tracker = BridgeBarFlipTracker(cfg.bridge_bar_flip_state_file)
    stale_tracker = StaleAlertTracker()

    try:
        while True:
            try:
                run_once(cfg, sl_mgr, tm_mgr, runtime, wz_store, tracker, stale_tracker)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S] cycle error: {exc!r}")
            # Written every iteration regardless of whether run_once
            # raised -- see heartbeat.py's own docstring. A caught cycle
            # error still proves the loop is alive; only a genuine hang
            # inside run_once (or process death) stops this from updating.
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
