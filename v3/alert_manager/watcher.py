"""Alert Manager main loop -- see v3/alert_manager/__init__.py and the
project_v3_crypto_architecture memory note for what this is and isn't.
Watches MT5's own live tick price against tv_scraper's zone data
(BTCUSD/XAUUSD/ETHUSD) and fires one Telegram message the first time
price enters a still-virgin zone -- see project_v3_crypto_architecture
for why MT5's live feed is used instead of tv_scraper's own 5s-polled
"retested" flag (lower latency, explicit user choice).

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
            })
    return out


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


def run_once(cfg: Config, alerted: AlertedZoneStore) -> None:
    for sym_cfg in cfg.symbols:
        try:
            price = mt5_price.get_mid_price(sym_cfg.symbol)
        except Exception as exc:
            print(f"[alert_manager] {sym_cfg.symbol} price ERROR: {exc}")
            continue

        for zone in _read_zones(sym_cfg.zone_state_file):
            if zone["timeframe"] in cfg.excluded_timeframes:
                continue
            if not zone["virgin"]:
                continue
            if not (zone["btm"] <= price <= zone["top"]):
                continue
            if alerted.already_alerted(sym_cfg.symbol, zone["timeframe"], zone["direction"], zone["start_time"]):
                continue

            text = _format_alert(sym_cfg.symbol, zone, price)
            try:
                send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
                alerted.mark_alerted(sym_cfg.symbol, zone["timeframe"], zone["direction"], zone["start_time"])
                print(f"[alert_manager] sent alert: {sym_cfg.symbol} {zone['timeframe']} {zone['direction']} "
                      f"@ {zone['start_time']}")
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

    print(f"[alert_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    try:
        while True:
            try:
                run_once(cfg, alerted)
            except Exception as exc:
                print(f"[alert_manager] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_price.shutdown()


if __name__ == "__main__":
    main()
