"""Best-effort Telegram alert helper shared by V6-Sentinel's processes
(reversal_main, trend_main). Uses the same shared bot every component in
this repo already uses (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env).
Never raises -- a Telegram outage must never take a trading loop down
with it -- and always prints, so an alert is visible in the console even
when no bot is configured.
"""
from __future__ import annotations

import os

from v6_sentinel.critical_alerts_telegram import send_message as _telegram_send


def send_alert(text: str) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[V6S-ALERT] (no bot configured) {text}")
        return
    try:
        _telegram_send(token, chat_id, text)
    except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
        print(f"[V6S-ALERT] send failed: {exc!r} -- message was: {text}")
