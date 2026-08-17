"""Minimal Telegram Bot API client -- sendMessage plus getUpdates (for
the /bias command listener). Uses `requests` directly -- see
project_virgin_zone_telegram_alerts memory for why this differs from the
old, now-deleted algo/alerts.py's stdlib-urllib approach.
"""
from __future__ import annotations

import requests

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort send -- raises on failure so the caller's own
    try/except (the watcher's per-cycle guard) decides whether to log
    and move on, matching every other bot's own poll-loop resilience
    pattern in this repo rather than silently swallowing send failures
    here."""
    url = _SEND_URL.format(token=bot_token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()


def get_updates(bot_token: str, offset: int, timeout: int = 20) -> list[dict]:
    """Long-polls for new incoming messages since `offset` (Telegram's
    own update_id, not a timestamp -- pass the highest update_id seen so
    far + 1 to avoid re-receiving already-processed messages, including
    across a restart if the caller persists it). `timeout` is Telegram's
    own long-poll wait, not this call's total budget -- the actual HTTP
    read waits up to timeout+a few seconds, so the caller's own request
    timeout below is set generously above it rather than matching it
    exactly."""
    url = _UPDATES_URL.format(token=bot_token)
    resp = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])
