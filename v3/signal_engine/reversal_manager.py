"""Reversal Manager -- a separate component from Trend Manager (own
magic number, own state, can hold a same- or opposite-direction
position simultaneously with Trend Manager's own trade -- confirmed
2026-08-17). Watches for price RETESTING a zone (not a fresh OB
forming, the opposite of Trend Manager's own trigger) across
H4/H2/H1/M30/M15 and M5, and reacts per the user's rules, 2026-08-18:

--- M5: immediate ---
M5's own retest fires a market order right away -- no waiting. SL =
the retested OB's own opposite edge minus/plus buffer
(entries.initial_sl, reused as-is). Stoploss Manager's existing
trailing logic takes over from there (reused, not rebuilt).

--- H4/H2/H1/M30/M15: retest starts a clock, waits for LTF confirmation ---
A retest on any of these five timeframes doesn't enter immediately --
it registers a "waiting" retest (see reversal_tracker.py) and starts
watching M1/M3/M5 (M1/M3 only exist for XAUUSD -- BTCUSD/ETHUSD only
have M5, see reversal_config.py) for a FRESH same-direction OB (formed
after the retest) to confirm entry:
- M1 uses its own wider confirmation thresholds (entries.
  reversal_m1_entry: market<=4, pullback 4<d<8) -- deliberately wider
  than Trend Manager's M1, "we might catch a bottom or top... keep some
  space buffer, making sure not missing the entry" (user's words).
- M3/M5 reuse Trend Manager's own m3_entry/m5_entry exactly
  ("already prescribed entry logics").
- Whichever of M1/M3/M5 confirms first wins (closest-to-price /
  MARKET-beats-PENDING selection, same shape as Trend Manager's own).
- M1/M3/M5 zones NEVER trigger a reversal trade on their own without an
  active HTF wait behind them -- confirmation only, never independent
  entries (except M5's own special immediate-on-its-own-retest rule
  above, which is a different mechanism entirely).
- Missed (price runs past the pullback range)? Wait for the next fresh
  OB on that same LTF -- no different from Trend Manager's own
  "post-parent-formation, freshest-only" philosophy.

--- Invalidation ---
While waiting for confirmation, if M1/M3/M5 forms an OPPOSITE-direction
OB instead, the whole waiting setup is scrapped -- blocked until the
NEXT retest re-arms it (the retest that started this wait is already
permanently watermarked, so it itself can never re-trigger; only a
genuinely newer retest can start a fresh wait).

--- SL when multiple zones are waiting at once ---
"if a single candle retests multiple zones, whichever zone is at
lowerside for buy trade decides sl, similarly whichever zone is far
from the price decides the sl for sell trade" -- computed at the moment
LTF confirmation actually fires, using ALL zones currently in the
waiting list for that direction (furthest btm for buy, furthest top
for sell), not just the one that happened to trigger the wait.

One reversal trade per symbol at a time (mirrors Trend Manager's own
rule) -- not one per direction; Reversal Manager itself won't hold
simultaneous bull+bear reversal positions on the same symbol, even
though it CAN disagree with Trend Manager's own concurrent position.

Signal-only for now -- logs "REVERSAL TRADE ...", never touches MT5.
Not yet wired to Execution Bridge (that integration is a separate,
explicit step -- building this does not make it live).

Run with: python -m v3.signal_engine.reversal_manager
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Tuple

from v3.signal_engine import entries, reversal_config
from v3.signal_engine.reversal_config import Config, SymbolConfig
from v3.signal_engine.reversal_tracker import ActiveReversalTrade, ReversalTracker, WaitingRetest
from v3.tradingview_bot.zone_store import TVZone, ZoneStore

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}


def _read_live_close(path: str, symbol: str, timeframe: str) -> Optional[float]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    entry = raw.get(f"{symbol}|{timeframe}")
    if entry is None:
        return None
    close = entry.get("close")
    return float(close) if close is not None else None


def _newest_retested_zone(store: ZoneStore, tracker: ReversalTracker, symbol: str,
                           timeframe: str, direction: str) -> Optional[TVZone]:
    """Newest zone in this bucket that's confirmed, actually retested
    (virgin=False), and a genuinely NEW retest event (start_time newer
    than the bucket's watermark) -- unlike formation-based lookups
    elsewhere, this can't short-circuit on the first confirmed zone,
    since retest status isn't monotonic with start_time.

    Distrusts retested_at == start_time (retested at the literal same
    second it formed) -- confirmed live 2026-08-18: this is the
    signature of a known tv_scraper artifact (a reused Pine array slot
    inheriting an old zone's Retested flag on first sighting, before
    it's genuinely been touched -- see scraper.py's own comment on this
    exact pattern). tv_scraper already guards one path that can produce
    this, but not every path does, so Reversal Manager distrusts it
    directly rather than assuming the upstream guard always caught it --
    same defensive spirit as formed_time_confirmed elsewhere in this
    system."""
    candidates = [
        z for z in store.zones(symbol, timeframe, direction)
        if z.formed_time_confirmed and not z.virgin
        and z.retested_at != z.start_time
        and tracker.is_new_retest(symbol, timeframe, direction, z.start_time)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.start_time)


def _newest_post_time_zone(store: ZoneStore, symbol: str, timeframe: str,
                            direction: str, after_time: int) -> Optional[TVZone]:
    """Newest confirmed OB formed strictly after after_time -- used for
    LTF confirmation/invalidation, where any fresh OB counts regardless
    of its own retest status (forming is the confirmation signal here,
    not being retested)."""
    for zone in store.zones(symbol, timeframe, direction):
        if not zone.formed_time_confirmed:
            continue
        if zone.start_time > after_time:
            return zone
        return None
    return None


def _fire_m5_immediate(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> bool:
    symbol = sym_cfg.symbol
    for direction in ("bull", "bear"):
        zone = _newest_retested_zone(store, tracker, symbol, "5", direction)
        if zone is None:
            continue
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, "5")
        if current_price is None:
            continue
        sl = entries.initial_sl(direction, zone.top, zone.btm)
        trade = ActiveReversalTrade(direction, "5", zone.start_time, current_price, sl, "MARKET")
        tracker.open_trade(symbol, trade)
        tracker.mark_retest_processed(symbol, "5", direction, zone.start_time)
        label = _DIRECTION_LABELS[direction]
        print(f"[reversal_manager] {symbol}: M5 {label} zone retested -- REVERSAL TRADE MARKET "
              f"@ {current_price:.2f} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
        return True
    return False


def _register_htf_retests(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> None:
    symbol = sym_cfg.symbol
    for timeframe in reversal_config.HTF_TIMEFRAMES:
        for direction in ("bull", "bear"):
            zone = _newest_retested_zone(store, tracker, symbol, timeframe, direction)
            if zone is None:
                continue
            retest_time = float(zone.retested_at) if zone.retested_at is not None else time.time()
            tracker.add_waiting(symbol, direction, WaitingRetest(timeframe, zone.start_time, zone.top, zone.btm, retest_time))
            tracker.mark_retest_processed(symbol, timeframe, direction, zone.start_time)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            label = _DIRECTION_LABELS[direction]
            print(f"[reversal_manager] {symbol}: {tf_label} {label} zone retested -- waiting for LTF confirmation")


def _check_direction(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig, direction: str) -> bool:
    symbol = sym_cfg.symbol
    waiting = tracker.get_waiting(symbol, direction)
    if not waiting:
        return False
    gate_time = min(w.retest_time for w in waiting)
    opposite = "bear" if direction == "bull" else "bull"

    # Invalidation: a fresh opposite-direction LTF OB scraps the whole wait.
    for timeframe in sym_cfg.ltf_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, opposite, int(gate_time))
        if zone is not None:
            tracker.clear_waiting(symbol, direction)
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[reversal_manager] {symbol}: {_DIRECTION_LABELS[opposite]} OB on {tf_label} while waiting "
                  f"-- {_DIRECTION_LABELS[direction]} setup invalidated, blocked until next retest")
            return False

    # Confirmation: best valid entry plan across LTF timeframes.
    best = None  # (mode, entry_price, timeframe, start_time, distance, current_price)
    for timeframe in sym_cfg.ltf_timeframes:
        zone = _newest_post_time_zone(store, symbol, timeframe, direction, int(gate_time))
        if zone is None:
            continue
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
        if current_price is None:
            continue
        entry_fn = entries.REVERSAL_CONFIRM_FUNCS.get(timeframe)
        if entry_fn is None:
            continue
        edge = entries.ob_edge(direction, zone.top, zone.btm)
        plan = entry_fn(direction, edge, current_price)
        if plan.mode == entries.EntryMode.NONE:
            continue
        distance = 0.0 if plan.mode == entries.EntryMode.MARKET else abs(plan.entry_price - current_price)
        candidate = (plan.mode, plan.entry_price, timeframe, zone.start_time, distance, current_price)
        if best is None or distance < best[4]:
            best = candidate

    if best is None:
        return False

    mode, entry_price, timeframe, start_time, _distance, current_price = best
    if direction == "bull":
        sl_zone = min(waiting, key=lambda w: w.btm)
        sl = sl_zone.btm - entries.SL_BUFFER
    else:
        sl_zone = max(waiting, key=lambda w: w.top)
        sl = sl_zone.top + entries.SL_BUFFER

    effective_entry = current_price if mode == entries.EntryMode.MARKET else entry_price
    trade = ActiveReversalTrade(direction, timeframe, start_time, effective_entry, sl, mode.value)
    tracker.open_trade(symbol, trade)
    tracker.clear_waiting(symbol, direction)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    label = _DIRECTION_LABELS[direction]
    print(f"[reversal_manager] {symbol}: LTF confirmation via {tf_label} -- REVERSAL TRADE {mode.value} {label} "
          f"@ {effective_entry:.2f} SL={sl:.2f} (not yet wired to MT5 -- signal only)")
    return True


def _close_if_invalidated(store: ZoneStore, tracker: ReversalTracker, symbol: str) -> bool:
    trade = tracker.active_trade(symbol)
    if trade is None:
        return False
    zone = store.get(symbol, trade.entry_timeframe, trade.direction, trade.entry_start_time)
    if zone is not None:
        return False
    tracker.close_trade(symbol)
    return True


def run_once_symbol(store: ZoneStore, tracker: ReversalTracker, sym_cfg: SymbolConfig) -> None:
    symbol = sym_cfg.symbol
    if _close_if_invalidated(store, tracker, symbol):
        print(f"[reversal_manager] {symbol}: active trade's entry OB was mitigated -- treating as closed")

    if tracker.active_trade(symbol) is not None:
        return  # one reversal trade at a time per symbol

    if _fire_m5_immediate(store, tracker, sym_cfg):
        return

    _register_htf_retests(store, tracker, sym_cfg)

    for direction in ("bull", "bear"):
        if _check_direction(store, tracker, sym_cfg, direction):
            return


def run_once(cfg: Config, tracker: ReversalTracker) -> None:
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        try:
            run_once_symbol(store, tracker, sym_cfg)
        except Exception as exc:
            print(f"[reversal_manager] {sym_cfg.symbol} ERROR: {exc}")


def main() -> None:
    cfg = reversal_config.load_config()
    tracker = ReversalTracker(cfg.state_file)
    print(f"[reversal_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    while True:
        try:
            run_once(cfg, tracker)
        except Exception as exc:
            print(f"[reversal_manager] ERROR: {exc}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
