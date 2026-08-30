"""Minimal Telegram Bot API client -- sendMessage only (no /bias-style
command listener needed here). V4's own tiny copy of
v3/alert_manager/telegram_client.py's identical shape rather than an
import -- V4 does not import from v3's folder (see CLAUDE.md / this
package's own __init__.py).
"""
from __future__ import annotations

import requests

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort send -- raises on failure so the caller's own
    try/except decides whether to log and move on."""
    url = _SEND_URL.format(token=bot_token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()
