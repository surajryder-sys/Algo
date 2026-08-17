"""Alert Manager main loop -- see v3/alert_manager/__init__.py and the
project_v3_crypto_architecture memory note for what this is and isn't.
Watches MT5's own live tick price against tv_scraper's zone data
(BTCUSD/XAUUSD/ETHUSD) and fires one Telegram message the first time
price enters a still-virgin zone -- see project_v3_crypto_architecture
for why MT5's live feed is used instead of tv_scraper's own 5s-polled
"retested" flag (lower latency, explicit user choice).

Genuinely fresh zones still alert at normal ~1s latency -- the
visibility-stability wait (see _passes_stability) only ever holds back
a zone that CLAIMS to be older than the wait window but that Alert
Manager has only just started seeing as eligible, which is what a zone
reappearing in tv_scraper's visible top-4 after being crowded out looks
like. User's explicit requirement: real-time "exactly when it gets
retested" alerts must not be delayed for the common case.

Run with: python -m v3.alert_manager.watcher
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

from v3.alert_manager import mt5_price
from v3.alert_manager.alerted_store import AlertedZoneStore
from v3.alert_manager.config import Config, load_config
from v3.alert_manager.confirmation_tracker import ConfirmationTracker
from v3.alert_manager.telegram_client import send_message

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15",
              "5": "M5", "3": "M3", "1": "M1"}


def _read_zones(path: str) -> list[dict]:
    """Reads tv_scraper's raw zone store JSON directly -- own small parse,
    not ZoneStore, since this only ever needs to iterate + check virgin
    zones, no write path. Returns a flat list of dicts, one per zone,
    each carrying its own (timeframe, direction) parsed out of the
    "symbol|timeframe|direction" key. Tolerates a torn/mid-write read
    (ZoneStore._save() isn't atomic -- confirmed live earlier this
    session as a transient JSONDecodeError) by just skipping this file
    for this one cycle rather than crashing the whole watcher."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    out = []
    for key, entries in raw.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        _symbol, timeframe, direction = parts
        for start_time_str, zone in entries.items():
            out.append({
                "timeframe": timeframe,
                "direction": direction,
                "start_time": int(start_time_str),
                "top": zone["top"],
                "btm": zone["btm"],
                "virgin": zone.get("virgin", True),
                # False means start_time is a wall-clock guess, not a
                # real Pine-confirmed formation time -- see
                # ZoneStore.TVZone.formed_time_confirmed's own docstring.
                # Default True matches that field's own pre-fix-data
                # default, not a design choice made here.
                "formed_time_confirmed": zone.get("formed_time_confirmed", True),
            })
    return out


def _zone_key(symbol: str, zone: dict) -> str:
    return f"{symbol}|{zone['timeframe']}|{zone['direction']}|{zone['start_time']}"


def _format_alert(symbol: str, zone: dict, price: float) -> str:
    formed = datetime.datetime.fromtimestamp(zone["start_time"], tz=_IST)
    direction_label = "BULL" if zone["direction"] == "bull" else "BEAR"
    # Emoji only ever goes into this Telegram-bound string, never printed
    # to console -- Windows' default console encoding (cp1252) can't
    # encode emoji, and a crash here inside the broad per-cycle
    # try/except would silently skip that whole cycle's real work, not
    # just the alert (this exact gotcha bit the old, now-deleted
    # algo/alerts.py -- see project_virgin_zone_telegram_alerts memory).
    emoji = "\U0001F7E2" if zone["direction"] == "bull" else "\U0001F534"
    tf_label = _TF_LABELS.get(zone["timeframe"], zone["timeframe"])
    return (
        f"{emoji} {symbol} {tf_label} {direction_label} zone retested\n"
        f"Range: {zone['btm']:.2f} - {zone['top']:.2f}\n"
        f"Formed: {formed.strftime('%Y-%m-%d %H:%M IST')}\n"
        f"Price: {price:.2f}"
    )


def _passes_stability(zone: dict, confirmation: ConfirmationTracker, symbol: str, key: str,
                       min_visible_seconds: float) -> bool:
    """Decides whether to trust this zone RIGHT NOW without waiting out
    the full visibility-stability window -- added after the user pointed
    out a blanket wait would delay "exactly when it gets retested" for
    the common case of a genuinely fresh zone.

    Two ways to pass:
    1. Genuinely new -- zone['start_time'] is a real Pine-confirmed
       formation time (guaranteed by the caller already having checked
       formed_time_confirmed), and it's younger than min_visible_seconds
       itself. Nothing to distrust here: the zone really did just form,
       so there's no "reappeared after being crowded out of view"
       possibility to guard against. Alerts fire with normal ~1s
       latency, same as before this whole fix existed.
    2. Old zone, continuously tracked -- zone['start_time'] claims to be
       OLDER than the window, but Alert Manager has independently
       observed it continuously eligible (virgin + confirmed) for the
       full window via ConfirmationTracker.is_stable(). This is the only
       path a zone that just reappeared in tv_scraper's visible top-4
       can take, and it's exactly the case that produced the false
       positives (XAUUSD M30, ETHUSD H2, the XAUUSD H1/M15/M5 burst) --
       a zone claiming to be hours or days old that Alert Manager only
       just started seeing as eligible is suspicious and has to prove
       itself stable before triggering.
    """
    zone_age_seconds = time.time() - zone["start_time"]
    if zone_age_seconds <= min_visible_seconds:
        return True
    return confirmation.is_stable(symbol, key, min_visible_seconds)


def run_once(cfg: Config, alerted: AlertedZoneStore, confirmation: ConfirmationTracker) -> None:
    for sym_cfg in cfg.symbols:
        try:
            price = mt5_price.get_mid_price(sym_cfg.symbol)
        except Exception as exc:
            print(f"[alert_manager] {sym_cfg.symbol} price ERROR: {exc}")
            continue

        zones = _read_zones(sym_cfg.zone_state_file)

        # Eligible = virgin AND formed_time_confirmed (see
        # ZoneStore.TVZone's own docstring -- skips zones tv_scraper can
        # only wall-clock-guess a formation time for, which could
        # genuinely be over a month old and already retested/mitigated
        # in reality despite looking "just formed"). Excluded timeframes
        # are filtered out of the confirmation set too, not just the
        # final alert check -- no reason to track staleness for
        # timeframes that can never fire an alert anyway.
        eligible_now = {
            _zone_key(sym_cfg.symbol, z) for z in zones
            if z["virgin"] and z["formed_time_confirmed"] and z["timeframe"] not in cfg.excluded_timeframes
        }
        confirmation.update(sym_cfg.symbol, sym_cfg.zone_state_file, eligible_now)

        for zone in zones:
            if zone["timeframe"] in cfg.excluded_timeframes:
                continue
            if not zone["virgin"] or not zone["formed_time_confirmed"]:
                continue
            if not (zone["btm"] <= price <= zone["top"]):
                continue
            key = _zone_key(sym_cfg.symbol, zone)
            if not confirmation.is_confirmed(sym_cfg.symbol, key):
                # Seen for the first time (or only in the current, not
                # yet the previous, distinct tv_scraper write) -- not
                # stale/wrong necessarily, just not YET confirmed across
                # 2 real refreshes. Will be re-checked next cycle; no
                # alert lost, only delayed by however long tv_scraper
                # takes to write its next poll for this symbol.
                continue
            if not _passes_stability(zone, confirmation, sym_cfg.symbol, key, cfg.min_visible_seconds):
                # Data-quality-confirmed, but this zone claims to be
                # older than the visibility window AND Alert Manager
                # hasn't observed it continuously eligible for that long
                # yet -- see _passes_stability's own docstring. A
                # genuinely fresh zone never hits this branch (path 1
                # there always passes it instantly), so this only ever
                # holds back the "reappeared in view" case that caused
                # the prior false positives.
                continue
            if alerted.already_alerted(sym_cfg.symbol, zone["timeframe"], zone["direction"], zone["start_time"]):
                continue

            text = _format_alert(sym_cfg.symbol, zone, price)
            try:
                send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
                alerted.mark_alerted(sym_cfg.symbol, zone["timeframe"], zone["direction"], zone["start_time"])
                # Full zone snapshot (every field, not just range/price)
                # logged here as JSON -- confirmed live this was needed
                # multiple times: several user-reported "alert fired but
                # nothing shows on the actual chart" cases couldn't be
                # fully diagnosed after the fact because the zone had
                # already aged out of tv_scraper's own store by the time
                # it was investigated, and the old line only logged range
                # + trigger price, not formed_time_confirmed or how long
                # the zone had been visible before firing.
                zone_age_seconds = time.time() - zone["start_time"]
                print(f"[alert_manager] sent alert: {sym_cfg.symbol} {zone['timeframe']} {zone['direction']} "
                      f"@ {zone['start_time']} range={zone['btm']:.2f}-{zone['top']:.2f} "
                      f"trigger_price={price:.2f} "
                      f"zone_age_seconds={zone_age_seconds:.0f} "
                      f"visible_seconds={confirmation.visible_seconds(sym_cfg.symbol, key):.0f} "
                      f"zone_snapshot={json.dumps(zone)}")
            except Exception as exc:
                # Deliberately NOT marked alerted on a failed send -- a
                # transient Telegram/network error should retry next
                # cycle rather than silently losing the alert forever.
                print(f"[alert_manager] Telegram send ERROR: {exc}")


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID must be set in .env")

    mt5_price.connect(cfg)
    alerted = AlertedZoneStore(cfg.alerted_state_file)
    confirmation = ConfirmationTracker()

    print(f"[alert_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    try:
        while True:
            try:
                run_once(cfg, alerted, confirmation)
            except Exception as exc:
                print(f"[alert_manager] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_price.shutdown()


if __name__ == "__main__":
    main()
