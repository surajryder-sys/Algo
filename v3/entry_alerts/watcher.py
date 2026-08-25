"""Entry-execution alert loop -- see v3/entry_alerts/__init__.py for
what this is. Reads Trend Manager's own open MT5 positions and fires
one Telegram message the first time each position is seen -- read-only,
never touches an order.

Run with: python -m v3.entry_alerts.watcher
"""
from __future__ import annotations

import time

import MetaTrader5 as mt5

from v3.entry_alerts import mt5_positions
from v3.entry_alerts.config import Config, load_config
from v3.entry_alerts.entry_state import EntryState
from v3.alert_manager.telegram_client import send_message


def _format_alert(position) -> str:
    direction_label = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
    return (
        f"✅ {position.symbol} {direction_label} EXECUTED\n"
        f"Entry: {position.price_open:.2f}\n"
        f"Lots: {position.volume:g}\n"
        f"SL: {position.sl:.2f}\n"
        f"Ticket: {position.ticket}"
    )


def run_once(cfg: Config, state: EntryState) -> None:
    open_tickets = set()

    for symbol in cfg.symbols:
        try:
            positions = mt5_positions.get_positions(symbol, cfg.magic_number)
        except Exception as exc:
            print(f"[entry_alerts] {symbol} position ERROR: {exc}")
            continue

        for position in positions:
            open_tickets.add(position.ticket)
            if state.already_alerted(position.ticket):
                continue

            text = _format_alert(position)
            try:
                send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
                state.mark_alerted(position.ticket)
                print(f"[entry_alerts] sent alert: {symbol} ticket={position.ticket} "
                      f"entry={position.price_open:.2f}")
            except Exception as exc:
                # Deliberately NOT marked alerted on a failed send --
                # matches every other bot's own retry-next-cycle pattern
                # in this repo rather than silently losing it.
                print(f"[entry_alerts] Telegram send ERROR: {exc}")

    state.prune(open_tickets)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("ENTRY_ALERTS_TELEGRAM_BOT_TOKEN / ENTRY_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_positions.connect(cfg)
    state = EntryState(cfg.state_file)

    print(f"[entry_alerts] watching {cfg.symbols} (magic number {cfg.magic_number}), "
          f"polling every {cfg.poll_seconds}s")
    try:
        while True:
            try:
                run_once(cfg, state)
            except Exception as exc:
                print(f"[entry_alerts] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_positions.shutdown()


if __name__ == "__main__":
    main()
