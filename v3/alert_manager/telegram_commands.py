"""Listens for incoming Telegram messages and replies to `/bias` commands
with an on-demand condensed bias/zone summary -- the pull counterpart to
watcher.py's push alerts. Runs as its own separate process so a slow or
stuck command reply can never delay/block the price-triggered alert
loop, and vice versa.

Run with: python -m v3.alert_manager.telegram_commands

Usage from Telegram: "/bias BTCUSD", "/bias XAUUSD", "/bias ETHUSD", or
just "/bias" for all three.
"""
from __future__ import annotations

import time

from v3.alert_manager import mt5_price
from v3.alert_manager.bias_report import build_report
from v3.alert_manager.config import Config, load_config
from v3.alert_manager.telegram_client import get_updates, send_message
from v3.alert_manager.update_offset_store import UpdateOffsetStore

_SYMBOL_FILES = {}  # populated from cfg.symbols in main()


def _handle_bias_command(cfg: Config, arg: str) -> str:
    arg = arg.strip().upper()
    targets = [arg] if arg else [s.symbol for s in cfg.symbols]
    unknown = [t for t in targets if t not in _SYMBOL_FILES]
    if unknown:
        known = ", ".join(_SYMBOL_FILES.keys())
        return f"Unknown symbol(s): {', '.join(unknown)}. Known: {known}"

    reports = []
    for sym in targets:
        try:
            price = mt5_price.get_mid_price(sym)
        except Exception:
            price = None
        reports.append(build_report(sym, _SYMBOL_FILES[sym], price))
    return "\n\n".join(reports)


def run_once(cfg: Config, offsets: UpdateOffsetStore) -> None:
    updates = get_updates(cfg.telegram_bot_token, offsets.get())
    if not updates:
        return

    max_update_id = offsets.get() - 1
    for update in updates:
        update_id = update.get("update_id", 0)
        max_update_id = max(max_update_id, update_id)

        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        # Only ever respond to the configured chat -- this bot/token is
        # private to this user; ignoring any other chat_id means a
        # leaked token can't be used to query live trading zone data.
        if str(message.get("chat", {}).get("id")) != str(cfg.telegram_chat_id):
            continue

        text = (message.get("text") or "").strip()
        if not text.lower().startswith("/bias"):
            continue

        arg = text[len("/bias"):].strip()
        try:
            reply = _handle_bias_command(cfg, arg)
        except Exception as exc:
            reply = f"Error building report: {exc}"

        try:
            send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, reply)
        except Exception as exc:
            print(f"[alert_manager.commands] Telegram send ERROR: {exc}")

    offsets.set(max_update_id + 1)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID must be set in .env")

    global _SYMBOL_FILES
    _SYMBOL_FILES = {s.symbol: s.zone_state_file for s in cfg.symbols}

    mt5_price.connect(cfg)
    offsets = UpdateOffsetStore("alert_manager_command_offset.json")

    print(f"[alert_manager.commands] listening for /bias commands from chat {cfg.telegram_chat_id}")
    try:
        while True:
            try:
                run_once(cfg, offsets)
            except Exception as exc:
                print(f"[alert_manager.commands] ERROR: {exc}")
                time.sleep(2)  # avoid a tight error loop hammering the API
    except KeyboardInterrupt:
        pass
    finally:
        mt5_price.shutdown()


if __name__ == "__main__":
    main()
