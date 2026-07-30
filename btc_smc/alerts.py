"""Telegram alert when price trades into a still-virgin OB zone on one of the
higher timeframes (H4/H2/H1/M30/M15) -- informational only, never places or
touches any order. Independent of the M5/M15/M30 zones the trading logic
itself reads in main.py. Shares the same Telegram bot/chat as the XAUUSD
(algo/) and ETHUSD (eth_smc/) bots -- every message includes the symbol name.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from btc_smc.bridge_reader import TIMEFRAMES, Zone, read_zone
from btc_smc.config import Config

ALERT_TIMEFRAMES = ["H4", "H2", "H1", "M30", "M15"]


class AlertedZoneStore:
    """Persists which zone keys have already fired an entry alert, so a
    restart doesn't re-alert on a zone whose entry was already reported."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._alerted: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._alerted = set(data.get("alerted", []))
        except (json.JSONDecodeError, OSError):
            self._alerted = set()

    def _save(self) -> None:
        self._path.write_text(json.dumps({"alerted": sorted(self._alerted)}))

    def is_alerted(self, zone_key: str) -> bool:
        return zone_key in self._alerted

    def mark_alerted(self, zone_key: str) -> None:
        self._alerted.add(zone_key)
        self._save()


def _event_time(zone: Zone) -> int:
    return zone.detected_time if zone.detected_time > 0 else zone.start_time


def send_telegram_message(cfg: Config, text: str) -> bool:
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": cfg.telegram_chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as exc:
        print(f"[ALERT] telegram send failed: {exc}")
        return False


def check_virgin_zone_alerts(cfg: Config, current_price: float, store: AlertedZoneStore) -> None:
    for tf_label in ALERT_TIMEFRAMES:
        snap = read_zone(cfg.symbol, TIMEFRAMES[tf_label])
        if snap is None:
            continue

        for history, kind in ((snap.bull, "DEMAND"), (snap.bear, "SUPPLY")):
            for zone in history:
                if not zone.virgin:
                    continue
                if not (zone.low <= current_price <= zone.high):
                    continue

                zone_key = f"{tf_label}|{kind}|{_event_time(zone)}"
                if store.is_alerted(zone_key):
                    continue

                plain = (f"{cfg.symbol} entered virgin {kind} zone ({tf_label}): "
                         f"{zone.low:.2f} - {zone.high:.2f} @ price {current_price:.2f}")
                print(f"[ALERT] {plain}")
                emoji = "\U0001F7E2" if kind == "DEMAND" else "\U0001F534"
                text = f"{emoji} {plain}"
                if send_telegram_message(cfg, text):
                    store.mark_alerted(zone_key)
