"""Minimal Telegram Bot API client -- sendMessage plus getUpdates (for
critical_alerts_listener.py's /pending, /approve command handling).
Renamed 2026-09-07 from profit_alerts_telegram.py -- also imported
directly by main.py and reversal_main.py for their own bridge-staleness
alerts (a different bot, @smcsecret_bot), since this client is fully
generic.
"""
from __future__ import annotations

import requests

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort send -- raises on failure so the caller's own
    try/except decides whether to log and move on."""
    url = _SEND_URL.format(token=bot_token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()


def get_updates(bot_token: str, offset: int, timeout: int = 20) -> list[dict]:
    """Long-polls for new incoming messages since `offset` (Telegram's
    own update_id, not a timestamp)."""
    url = _UPDATES_URL.format(token=bot_token)
    resp = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])
