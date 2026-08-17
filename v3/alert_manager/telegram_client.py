"""Minimal Telegram Bot API client -- just sendMessage, nothing else.

Uses `requests` directly. The old, now-deleted algo/alerts.py used
stdlib urllib because `requests` wasn't installed in this environment at
the time (2026-07-28) -- re-verified 2026-08-17 that `requests` IS
installed now, so no reason to match that older constraint (see
project_virgin_zone_telegram_alerts memory).
"""
from __future__ import annotations

import requests

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort send -- raises on failure so the caller's own
    try/except (the watcher's per-cycle guard) decides whether to log
    and move on, matching every other bot's own poll-loop resilience
    pattern in this repo rather than silently swallowing send failures
    here."""
    url = _API_BASE.format(token=bot_token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()
