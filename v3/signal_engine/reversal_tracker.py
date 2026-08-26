"""Persisted state for Reversal Manager -- see reversal_manager.py's own
docstring for the full rule. Three things live here:

1. Per (symbol, timeframe, direction) bucket, a watermark -- same
   "never re-process the same retest twice" mechanism as
   v3/signal_engine/trade_tracker.py's, applied here to RETESTS instead
   of formations: a zone's retest only ever gets turned into a waiting
   event (HTF) or an immediate fire (M5) once, the first time it's
   observed non-virgin with a start_time newer than the bucket's
   watermark. Alongside it, seeded_buckets (per-bucket, not a single
   whole-file flag) tracks which buckets have ever been examined at
   all -- confirmed live 2026-08-18 (twice) that a bucket seen for the
   first time must SEED its watermark to whatever's currently already
   retested rather than fire on it, or it fires on a real but
   arbitrarily stale retest the moment that bucket becomes active for
   the first time (a whole-file "cold start" flag alone isn't enough --
   a bucket that just hadn't been touched yet looks identical to a
   fresh start even in a state file that's been running a while).

2. Per (symbol, direction), a list of currently "waiting" HTF retests
   (H4/H2/H1/M30/M15) -- each one records which zone retested, when,
   and its own top/btm (needed later for SL selection if multiple
   zones are waiting at once when confirmation actually fires -- "if a
   single candle retests multiple zones, whichever zone is at
   lowerside for buy trade decides sl"). Cleared entirely the moment
   either a confirmed LTF entry fires (consumed) or an opposite-
   direction LTF OB invalidates the whole setup.

3. Per symbol, at most one currently active REVERSAL trade -- separate
   from Trend Manager's own active trade, own magic number, can hold a
   position in the same OR opposite direction simultaneously (user's
   explicit confirmation 2026-08-17: "both can open same direction or
   opposite direction trades"). Closed via the entry OB's own
   mitigation, same stand-in-for-stopped-out convention used
   everywhere else in this system. SHARED across both mechanisms below --
   one reversal trade per symbol at a time, regardless of which
   mechanism armed it.

4. A second, fully independent copy of (1) and (2) above -- own
   watermarks/seeded-buckets/waiting-list, "htf_m1_"-prefixed -- for the
   new HTF-retest -> M1-only-confirm mechanism (XAUUSD only, added
   2026-08-25, see reversal_manager.py's own docstring for the full
   rule). Deliberately not sharing state with (1)/(2): each mechanism's
   own invalidation rule (which LTF timeframes count) only ever affects
   the trade IT armed, never the other mechanism's own watch on the same
   underlying retest event.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


def _bucket_key(symbol: str, timeframe: str, direction: str) -> str:
    return f"{symbol}|{timeframe}|{direction}"


@dataclass
class WaitingRetest:
    timeframe: str
    start_time: int
    top: float
    btm: float
    retest_time: float


@dataclass
class ActiveReversalTrade:
    direction: str
    entry_timeframe: str
    entry_start_time: int
    entry_price: float
    sl_price: Optional[float]
    mode: str  # "MARKET" or "PENDING"
    # PENDING -> FILLED once price crosses entry_price (mirrors
    # trade_tracker.py's own fill_pending) -- added 2026-08-18, before
    # this existed a real PENDING order that filled in MT5 would look
    # indistinguishable from "not yet placed" to Execution Bridge,
    # which would then place a SECOND pending order on top of an
    # already-filled position. MARKET starts FILLED immediately (fires
    # the instant it's decided, nothing to wait for).
    status: str = "PENDING"
    # Real wall-clock time this trade actually became a FILLED position
    # -- NOT entry_start_time above, which is the entry OB's own
    # FORMATION time and can predate the actual fill by any amount.
    # Added 2026-08-19 after the user's explicit correction: the
    # opposite-LTF-OB exit rule (see reversal_manager._close_if_
    # opposite_ltf_ob) must only react to an opposite OB that formed
    # AFTER the trade truly opened, not merely after its entry zone
    # formed -- using entry_start_time there could treat an OB that
    # already existed before the trade even filled as a fresh signal.
    # Set at construction for a MARKET fill (immediate), refreshed by
    # ReversalTracker.mark_filled() for a PENDING order's real fill.
    # Defaults 0.0 so a trade persisted before this field existed
    # doesn't break on load -- effectively means "any OB counts" for
    # such a trade, a one-time harmless gap on the very next restart
    # only (mark_filled/open_trade always set a real value from here
    # on).
    opened_at: float = 0.0
    # True when entry_start_time came from an ATR flip
    # (reversal_manager._check_direction_atr_or_ob), not a real OB --
    # same meaning and same live bug as trade_tracker.ActiveTrade's own
    # exec_via_atr (see that field's docstring): _close_if_invalidated's
    # zone lookup on a synthetic ATR timestamp always comes back empty,
    # reading as "mitigated" and closing the trade almost immediately.
    # _close_if_invalidated skips entirely when this is True.
    exec_via_atr: bool = False
    # The "parent" (bias-setting) timeframe for the order comment --
    # added 2026-08-20 for the new parent-exec comment pattern (see
    # order_tracker.make_comment). Reversal Manager has no separate
    # "parent" concept the way Trend Manager does, so this is set to
    # whichever zone's own timeframe actually decided the SL: the M5
    # zone itself for an immediate fire (parent == exec, there's no
    # real distinction), or the SL-determining waiting zone's timeframe
    # for an LTF-confirmed fire (same zone _check_direction/
    # _check_direction_atr_or_ob already pick for the multi-waiting-zone
    # SL calculation). None only for a trade persisted before this field
    # existed -- execution_bridge.py falls back to exec_timeframe then.
    parent_timeframe: Optional[str] = None
    # True when this trade came from the new HTF-retest -> M1-only-confirm
    # mechanism (XAUUSD only, 2026-08-25) rather than the original M1/M3/M5
    # LTF-confirmation mechanism -- see reversal_manager.py's own docstring
    # for the full rule. Gates which invalidation check applies
    # (_close_if_htf_m1_invalidated vs _close_if_opposite_ltf_ob/
    # _close_if_invalidated) and which SL basis was used, since both
    # mechanisms share this same one-trade-per-symbol active slot.
    is_htf_m1: bool = False
    # The winning HTF zone's own retest_time (wall-clock) that this trade's
    # setup was armed from -- kept on the trade itself (not just the
    # waiting-list entry, which gets cleared once the trade fires) so
    # _close_if_htf_m1_invalidated can keep checking "opposite OB on M3/M5/
    # M15 after the ORIGINAL retest event" for as long as the trade/pending
    # order stays open, exactly as specified ("this setup becomes
    # invalid... square off the trade, or cancel pending orders") -- not
    # re-anchored to the trade's own fill time the way the existing
    # opposite-LTF-OB close rule is.
    htf_m1_retest_time: Optional[float] = None
    # How many DISTINCT opposite-direction OBs have formed (since
    # whichever moment sym_cfg.htf_m1.active_invalidation_anchor picks --
    # see that field's own docstring) on the confirmation timeframe
    # itself -- BTCUSD/ETHUSD's own "two opposite OBs on M3 also
    # invalidates" rule, added 2026-08-25, same persistent/cross-cycle
    # counting reasoning as trade_tracker.ActiveTrade.m1_opposite_ob_count
    # (a counted OB can later get mitigated and vanish from the store, so
    # this can't be re-derived from "what's currently live" each cycle).
    # Always 0/None for XAUUSD, whose own rule excludes its confirmation
    # timeframe (M1) from invalidation entirely instead.
    htf_m1_double_ob_count: int = 0
    htf_m1_double_ob_last_start_time: Optional[int] = None
    # Whether this PENDING trade's entry_price sits on the STOP side of
    # current price (bull: entry >= price at proposal time, needs price
    # to RISE to reach it -- same test broker.py's own send_pending_
    # order uses to pick BUY_STOP over BUY_LIMIT) rather than the LIMIT
    # side (entry below current price for a bull, needs price to FALL
    # to reach it). Added 2026-08-26, real bug fix: every PENDING entry
    # before _fire_m5_immediate's own zone-edge direct-fire (2026-08-26)
    # always naturally landed on the limit side by construction (a
    # pullback-from-current-price calculation, or an OB edge price
    # already passed to reach that has already rallied away from it) --
    # _price_crossed's own "bull entries sit below current price"
    # assumption held for every one of them. A zone-edge order doesn't:
    # since the zone was JUST retested (price already touched/passed
    # through it), the zone's own far edge can easily sit ABOVE current
    # price for a bull, needing a real BUY_STOP -- confirmed live, the
    # very first XAUUSD M5 direct-fire under the new rule got marked
    # FILLED by the signal side within one poll of being placed, even
    # though the real MT5 order was still a resting, untriggered stop
    # (current price was already below the zone's own top edge at
    # placement time). False (the original, still-correct assumption)
    # for every other PENDING path in this module -- only
    # _fire_m5_immediate's own direct-fire ever sets this True.
    pending_stop_style: bool = False


class ReversalTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._watermarks: dict[str, int] = {}
        # Which (symbol, timeframe, direction) buckets have ever been
        # examined at least once -- separate from _watermarks itself
        # (whose default-0 value is indistinguishable from "genuinely
        # processed a retest at time 0"). Added 2026-08-18 after TWO
        # separate live incidents: a whole-file "cold start" flag caught
        # the first one (day-old BTCUSD/ETHUSD bull retests firing the
        # moment this process first ran continuously) but NOT the
        # second -- a bucket that simply hadn't been touched yet (the
        # BEAR direction, never having fired before) fired on a real but
        # WEEK-OLD retest on the very next restart, since the file
        # already existed so the whole-file flag no longer applied. This
        # per-bucket set is the actual fix: reversal_manager.py's
        # _newest_retested_zone seeds (not fires on) any bucket seen for
        # the first time, regardless of whether the file as a whole is
        # new.
        self._seeded_buckets: set = set()
        self._waiting: dict[str, dict[str, List[WaitingRetest]]] = {}  # symbol -> direction -> [WaitingRetest]
        self._active: dict[str, ActiveReversalTrade] = {}
        self._manual_event_watermark: dict[str, float] = {}
        # Fully separate watermark/seeded/waiting state for the new
        # HTF-retest -> M1-only-confirm mechanism (XAUUSD only, added
        # 2026-08-25) -- deliberately NOT sharing the originals above.
        # User's own call: an opposite OB on, say, M3 should only ever
        # invalidate the mechanism that actually cares about M3 (the
        # original one); coupling the two would mean each mechanism's own
        # invalidation could wipe out the OTHER'S watch on a retest neither
        # of them intended it to affect. Both still watch the exact same
        # HTF retest events (H4/H2/H1/M30/M15/M5) independently, so a given
        # zone's retest is tracked twice -- once per mechanism -- rather
        # than once shared.
        self._htf_m1_watermarks: dict[str, int] = {}
        self._htf_m1_seeded_buckets: set = set()
        self._htf_m1_waiting: dict[str, dict[str, List[WaitingRetest]]] = {}
        # Waiting-phase "two opposite OBs on the confirmation timeframe"
        # counters (BTCUSD/ETHUSD's own rule, 2026-08-25) -- symbol ->
        # direction -> (count, last-counted start_time). Separate from
        # ActiveReversalTrade.htf_m1_double_ob_count, which covers the
        # SAME rule once a trade is actually open -- this half covers the
        # wait itself, before any trade exists to hold the count on.
        # Reset whenever clear_htf_m1_waiting fires (the wait ending,
        # confirmed or invalidated) -- NOT on set_htf_m1_waiting (mere
        # pruning of a mitigated zone leaves the wait, and this count,
        # alive).
        self._htf_m1_waiting_double_ob_count: dict[str, dict[str, int]] = {}
        self._htf_m1_waiting_double_ob_last_start_time: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._watermarks = dict(raw.get("watermarks", {}))
        self._seeded_buckets = set(raw.get("seeded_buckets", []))
        # Backfill -- any bucket that already has a real watermark entry
        # was obviously seen/processed under the pre-2026-08-18 scheme,
        # before seeded_buckets existed at all. Without this, restarting
        # against an existing state file (any bucket that had already
        # legitimately fired before this fix) would look "unseeded" and
        # get its watermark needlessly re-seeded to the same value --
        # harmless in that specific case, but the intent is "only
        # genuinely untouched buckets get the seed-not-fire treatment."
        self._seeded_buckets |= set(self._watermarks.keys())
        self._manual_event_watermark = dict(raw.get("manual_event_watermark", {}))
        self._waiting = {
            symbol: {
                direction: [WaitingRetest(**w) for w in entries]
                for direction, entries in per_symbol.items()
            }
            for symbol, per_symbol in raw.get("waiting", {}).items()
        }
        self._active = {
            symbol: ActiveReversalTrade(**t)
            for symbol, t in raw.get("active_trades", {}).items()
        }
        self._htf_m1_watermarks = dict(raw.get("htf_m1_watermarks", {}))
        self._htf_m1_seeded_buckets = set(raw.get("htf_m1_seeded_buckets", []))
        self._htf_m1_seeded_buckets |= set(self._htf_m1_watermarks.keys())
        self._htf_m1_waiting = {
            symbol: {
                direction: [WaitingRetest(**w) for w in entries]
                for direction, entries in per_symbol.items()
            }
            for symbol, per_symbol in raw.get("htf_m1_waiting", {}).items()
        }
        self._htf_m1_waiting_double_ob_count = {
            symbol: dict(per_symbol)
            for symbol, per_symbol in raw.get("htf_m1_waiting_double_ob_count", {}).items()
        }
        self._htf_m1_waiting_double_ob_last_start_time = {
            symbol: dict(per_symbol)
            for symbol, per_symbol in raw.get("htf_m1_waiting_double_ob_last_start_time", {}).items()
        }

    def _save(self) -> None:
        out = {
            "watermarks": self._watermarks,
            "seeded_buckets": sorted(self._seeded_buckets),
            "waiting": {
                symbol: {direction: [asdict(w) for w in entries] for direction, entries in per_symbol.items()}
                for symbol, per_symbol in self._waiting.items()
            },
            "active_trades": {symbol: asdict(t) for symbol, t in self._active.items()},
            "manual_event_watermark": self._manual_event_watermark,
            "htf_m1_watermarks": self._htf_m1_watermarks,
            "htf_m1_seeded_buckets": sorted(self._htf_m1_seeded_buckets),
            "htf_m1_waiting": {
                symbol: {direction: [asdict(w) for w in entries] for direction, entries in per_symbol.items()}
                for symbol, per_symbol in self._htf_m1_waiting.items()
            },
            "htf_m1_waiting_double_ob_count": self._htf_m1_waiting_double_ob_count,
            "htf_m1_waiting_double_ob_last_start_time": self._htf_m1_waiting_double_ob_last_start_time,
        }
        self._path.write_text(json.dumps(out))

    def should_react_to_close_event(self, symbol: str, event_time: float,
                                     entry_timeframe: Optional[str] = None,
                                     entry_start_time: Optional[int] = None) -> bool:
        """Mirrors trade_tracker.TradeTracker's own -- True (and records
        event_time as handled) only if this is a real-world close event
        (manual cancel/close, or a genuine SL/TP hit) Reversal Manager
        hasn't already reacted to for this symbol. Added 2026-08-18
        after a real SL hit on a Reversal Manager position left its own
        active_trade record showing FILLED forever, with nothing real
        behind it, and Execution Bridge kept re-opening a brand new
        position for it every cycle since Reversal Manager never
        learned the original had closed.

        Also requires the notification's own (entry_timeframe,
        entry_start_time) identity to match whatever trade is CURRENTLY
        active, not just the symbol -- same fix as
        trade_tracker.TradeTracker's own identical method, 2026-08-25,
        after a real Trend Manager incident where a stale notification
        about an already-superseded trade closed a brand new one instead
        (see that method's own docstring for the full incident).
        entry_timeframe=None means an old-format event -- falls back to
        the old symbol-only behavior."""
        last = self._manual_event_watermark.get(symbol, 0.0)
        if event_time <= last:
            return False
        self._manual_event_watermark[symbol] = event_time
        self._save()
        if entry_timeframe is None:
            return True
        trade = self._active.get(symbol)
        if trade is None:
            return False
        return trade.entry_timeframe == entry_timeframe and trade.entry_start_time == entry_start_time

    # -- watermark (retest de-dup) ---------------------------------------

    def is_new_retest(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._watermarks.get(key, 0)

    def is_bucket_seeded(self, symbol: str, timeframe: str, direction: str) -> bool:
        """False only the very first time this exact bucket is ever
        examined -- see the class docstring's own note on why a
        per-bucket check (not just a whole-file cold-start flag) is
        needed: a bucket that simply hadn't fired before (e.g. one
        direction while only the other had ever been active) looks
        identical to a genuine cold start otherwise, even in a state
        file that's been running for a while."""
        return _bucket_key(symbol, timeframe, direction) in self._seeded_buckets

    def seed_bucket(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Marks this bucket seeded WITHOUT treating start_time as a
        fired/processed retest event -- used by
        reversal_manager.py's cold-start safeguard the first time a
        bucket is ever examined, to skip whatever retest already
        exists rather than firing on it."""
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._seeded_buckets.add(key)
        self._save()

    def mark_retest_processed(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._seeded_buckets.add(key)
        self._save()

    # -- waiting HTF retests ----------------------------------------------

    def add_waiting(self, symbol: str, direction: str, retest: WaitingRetest) -> None:
        self._waiting.setdefault(symbol, {}).setdefault(direction, []).append(retest)
        self._save()

    def get_waiting(self, symbol: str, direction: str) -> List[WaitingRetest]:
        return list(self._waiting.get(symbol, {}).get(direction, []))

    def clear_waiting(self, symbol: str, direction: str) -> None:
        if symbol in self._waiting and direction in self._waiting[symbol]:
            self._waiting[symbol][direction] = []
            self._save()

    def set_waiting(self, symbol: str, direction: str, entries: "List[WaitingRetest]") -> None:
        """Replaces the waiting list for this (symbol, direction) with
        exactly `entries` -- used by reversal_manager._prune_mitigated_
        waiting to drop zones that have since been mitigated on the real
        chart while still sitting in the waiting list. Added 2026-08-20
        after a real live incident: a waiting M30 zone got mitigated
        (no longer visible on the chart at all) sometime after it
        registered, but nothing ever re-checked it -- it just sat there
        until an unrelated M1 OB happened to match direction and
        "confirmed" it, firing a trade off a zone that was already gone."""
        self._waiting.setdefault(symbol, {})[direction] = list(entries)
        self._save()

    # -- HTF-M1 mechanism's own separate state (2026-08-25) ----------------
    # Mirrors the watermark/seeded/waiting methods above exactly, same
    # cold-start-safe/prune-safe reasoning throughout -- kept as fully
    # separate methods (not parameterized) so each mechanism's own call
    # sites stay unambiguous about which state they're touching.

    def is_new_htf_m1_retest(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._htf_m1_watermarks.get(key, 0)

    def is_htf_m1_bucket_seeded(self, symbol: str, timeframe: str, direction: str) -> bool:
        return _bucket_key(symbol, timeframe, direction) in self._htf_m1_seeded_buckets

    def seed_htf_m1_bucket(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._htf_m1_watermarks.get(key, 0):
            self._htf_m1_watermarks[key] = start_time
        self._htf_m1_seeded_buckets.add(key)
        self._save()

    def mark_htf_m1_retest_processed(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._htf_m1_watermarks.get(key, 0):
            self._htf_m1_watermarks[key] = start_time
        self._htf_m1_seeded_buckets.add(key)
        self._save()

    def add_htf_m1_waiting(self, symbol: str, direction: str, retest: WaitingRetest) -> None:
        self._htf_m1_waiting.setdefault(symbol, {}).setdefault(direction, []).append(retest)
        self._save()

    def get_htf_m1_waiting(self, symbol: str, direction: str) -> List[WaitingRetest]:
        return list(self._htf_m1_waiting.get(symbol, {}).get(direction, []))

    def clear_htf_m1_waiting(self, symbol: str, direction: str) -> None:
        if symbol in self._htf_m1_waiting and direction in self._htf_m1_waiting[symbol]:
            self._htf_m1_waiting[symbol][direction] = []
        # The wait is ending (confirmed or invalidated) -- its own
        # double-OB count resets too, so a future fresh wait on this same
        # (symbol, direction) starts counting from zero again, not from
        # wherever a previous, unrelated wait left off.
        if symbol in self._htf_m1_waiting_double_ob_count:
            self._htf_m1_waiting_double_ob_count[symbol].pop(direction, None)
        if symbol in self._htf_m1_waiting_double_ob_last_start_time:
            self._htf_m1_waiting_double_ob_last_start_time[symbol].pop(direction, None)
        self._save()

    def set_htf_m1_waiting(self, symbol: str, direction: str, entries: "List[WaitingRetest]") -> None:
        self._htf_m1_waiting.setdefault(symbol, {})[direction] = list(entries)
        self._save()

    def get_htf_m1_waiting_double_ob_count(self, symbol: str, direction: str) -> int:
        return self._htf_m1_waiting_double_ob_count.get(symbol, {}).get(direction, 0)

    def get_htf_m1_waiting_double_ob_last_start_time(self, symbol: str, direction: str) -> Optional[int]:
        return self._htf_m1_waiting_double_ob_last_start_time.get(symbol, {}).get(direction)

    def record_htf_m1_waiting_double_ob(self, symbol: str, direction: str,
                                         new_sightings: int, newest_start_time: int) -> None:
        """Advances the WAITING-phase double-OB count (see
        _htf_m1_waiting_double_ob_count's own docstring) -- the pre-trade
        half of BTCUSD/ETHUSD's "two opposite OBs on the confirmation
        timeframe also invalidates" rule."""
        current = self._htf_m1_waiting_double_ob_count.setdefault(symbol, {}).get(direction, 0)
        self._htf_m1_waiting_double_ob_count[symbol][direction] = current + new_sightings
        self._htf_m1_waiting_double_ob_last_start_time.setdefault(symbol, {})[direction] = newest_start_time
        self._save()

    # -- active trade -------------------------------------------------------

    def active_trade(self, symbol: str) -> Optional[ActiveReversalTrade]:
        return self._active.get(symbol)

    def open_trade(self, symbol: str, trade: ActiveReversalTrade) -> None:
        self._active[symbol] = trade
        self._save()

    def mark_filled(self, symbol: str) -> None:
        trade = self._active.get(symbol)
        if trade is not None:
            trade.status = "FILLED"
            # Real fill moment for a PENDING order -- see
            # ActiveReversalTrade.opened_at's own docstring for why this
            # must be the actual fill time, not the entry zone's own
            # formation time (entry_start_time).
            trade.opened_at = time.time()
            self._save()

    def record_htf_m1_active_double_ob(self, symbol: str, new_sightings: int, newest_start_time: int) -> None:
        """Advances the active trade's own htf_m1_double_ob_count/
        last_start_time -- see ActiveReversalTrade.htf_m1_double_ob_count's
        own docstring. No-op if there's no active trade for this symbol
        (can race with the trade closing for an unrelated reason on the
        very same cycle) -- same defensive shape as trade_tracker.
        TradeTracker.record_m1_opposite_obs."""
        trade = self._active.get(symbol)
        if trade is None:
            return
        trade.htf_m1_double_ob_count += new_sightings
        trade.htf_m1_double_ob_last_start_time = newest_start_time
        self._save()

    def close_trade(self, symbol: str) -> None:
        self._active.pop(symbol, None)
        self._save()
