"""Minimal Telegram Bot API client -- sendMessage only. Ported from
v3/alert_manager/telegram_client.py (2026-09-22) since this project's own
per-lineage isolation convention (see CLAUDE.md) means v6_sentinel never
imports from v3 directly -- this is V6-Sentinel's own copy, trimmed to just
what zone_touch_alert.py needs (no getUpdates/long-poll listener here, V6S
has no inbound-command bot yet).
"""
from __future__ import annotations

import requests

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort send -- raises on failure so the caller's own
    try/except decides whether to log and move on, matching every other
    bot's own poll-loop resilience pattern in this repo rather than
    silently swallowing send failures here."""
    url = _SEND_URL.format(token=bot_token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()


def send_if_configured(bot_token: str | None, chat_id: str | None, text: str) -> None:
    """send_message(), but a no-op if bot_token/chat_id aren't both set,
    and any send failure is caught and printed rather than raised -- the
    common 'best-effort optional alert' pattern every Exit Manager
    component that sends a Telegram alert uses (2026-09-22)."""
    if not bot_token or not chat_id:
        return
    try:
        send_message(bot_token, chat_id, text)
    except Exception as exc:  # noqa: BLE001 -- never take a caller's own loop down over this
        print(f"[V6S-ALERT] send failed: {exc!r}")
