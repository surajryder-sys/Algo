"""Subscriber approval listener for the profit-alerts bot
(SecretTrader_Critical_Bot) -- long-polls Telegram for new messages,
auto-registers any new sender as a pending subscriber (with an
acknowledgement reply), and lets the OWNER (PROFIT_ALERTS_TELEGRAM_
CHAT_ID) approve them via /pending and /approve <name_or_chat_id>
commands sent directly to the bot -- fully self-service, per the user's
own explicit choice 2026-08-28 ("Command in Telegram... no need to come
back to this chat").

Deliberately a SEPARATE process from profit_alerts_watcher.py (which
only ever sends, never polls for incoming messages) -- Telegram's own
getUpdates offset mechanism means only ONE process can long-poll a
given bot token at a time without the two racing each other for the
same updates.

Commands (owner only -- anyone else's commands are ignored, only their
very first message triggers the pending-registration reply):
  /pending          -- list everyone waiting for approval
  /approve <id>     -- approve by exact chat_id or a substring of their
                        Telegram name (first name); ambiguous or no
                        match replies asking for the exact chat_id
                        instead of guessing

Run with: python -m v5_sentinel.profit_alerts_listener
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from v5_sentinel.profit_alerts_config import load_config
from v5_sentinel.profit_alerts_subscribers import SubscriberStore
from v5_sentinel.profit_alerts_telegram import get_updates, send_message

_OFFSET_FILE = "v5_sentinel_profit_alerts_command_offset.json"


def _load_offset(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    try:
        return int(json.loads(p.read_text()).get("offset", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def _save_offset(path: str, offset: int) -> None:
    Path(path).write_text(json.dumps({"offset": offset}))


def _sender_name(message: dict) -> str:
    frm = message.get("from", {})
    name = frm.get("first_name", "") or frm.get("username", "") or "unknown"
    return name


def _handle_message(cfg, subscribers: SubscriberStore, message: dict) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return
    chat_id = str(chat_id)
    name = _sender_name(message)

    if subscribers.is_owner(chat_id):
        if text == "/pending":
            pending = subscribers.list_pending()
            if not pending:
                send_message(cfg.telegram_bot_token, chat_id, "No pending requests.")
            else:
                lines = "\n".join(f"{cid} -- {n}" for cid, n in pending)
                send_message(cfg.telegram_bot_token, chat_id, f"Pending requests:\n{lines}\n\n"
                                                                f"Approve with /approve <name or chat_id>")
            return
        if text.startswith("/approve"):
            identifier = text[len("/approve"):].strip()
            if not identifier:
                send_message(cfg.telegram_bot_token, chat_id, "Usage: /approve <name or chat_id>")
                return
            result = subscribers.approve_by_identifier(identifier)
            if result is None:
                send_message(cfg.telegram_bot_token, chat_id,
                             f"No single pending match for '{identifier}' -- check /pending "
                             f"and use the exact chat_id if names are ambiguous.")
                return
            approved_chat_id, approved_name = result
            send_message(cfg.telegram_bot_token, chat_id, f"Approved {approved_name} ({approved_chat_id}).")
            send_message(cfg.telegram_bot_token, approved_chat_id,
                         "You're approved -- you'll now receive profit alerts.")
            print(f"[v5_sentinel.profit_alerts_listener] approved {approved_name} ({approved_chat_id})")
            return
        return  # any other message from the owner -- nothing to do

    if subscribers.is_approved(chat_id):
        return  # already approved, no command surface for regular subscribers

    if subscribers.record_pending(chat_id, name):
        send_message(cfg.telegram_bot_token, chat_id,
                     "Thanks -- your request to receive alerts is pending approval.")
        send_message(cfg.telegram_bot_token, subscribers.owner_chat_id,
                     f"New subscriber request: {name} ({chat_id}). Approve with /approve {name}")
        print(f"[v5_sentinel.profit_alerts_listener] new pending request: {name} ({chat_id})")
    # else: already pending, already sent the ack once -- no need to repeat it every message


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.owner_chat_id:
        raise RuntimeError("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN / PROFIT_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    subscribers = SubscriberStore(cfg.subscribers_file, cfg.owner_chat_id)
    offset = _load_offset(_OFFSET_FILE)
    print(f"[v5_sentinel.profit_alerts_listener] listening for subscriber commands, "
          f"{len(subscribers.approved_chat_ids())} approved subscriber(s)")

    while True:
        try:
            updates = get_updates(cfg.telegram_bot_token, offset)
        except Exception as exc:
            print(f"[v5_sentinel.profit_alerts_listener] getUpdates ERROR: {exc}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message")
            if message is None:
                continue
            try:
                _handle_message(cfg, subscribers, message)
            except Exception as exc:
                print(f"[v5_sentinel.profit_alerts_listener] error handling message: {exc}")

        if updates:
            _save_offset(_OFFSET_FILE, offset)


if __name__ == "__main__":
    main()
