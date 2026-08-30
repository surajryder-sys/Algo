"""Profit-milestone alert loop for V4 -- see v4/profit_alerts/__init__.py
for what this is. Reads V4's own open MT5 positions (XAUUSD Trend
Manager's magic + crypto Trend Manager's magic) and fires one Telegram
message the first time each position's floating profit reaches each
configured points milestone -- read-only, never touches an order.

Run with: python -m v4.profit_alerts.watcher
"""
from __future__ import annotations

import time

import MetaTrader5 as mt5

from v4.profit_alerts import mt5_positions
from v4.profit_alerts.config import Config, load_config
from v4.profit_alerts.profit_state import ProfitState
from v4.profit_alerts.telegram_client import send_message


def _profit_points(position) -> float:
    """Floating profit in raw price-distance points -- direction-aware.
    Deliberately price-distance, not position.profit's own account-
    currency P&L, matching the unit every symbol-scaled distance in V4
    itself already uses."""
    if position.type == mt5.POSITION_TYPE_BUY:
        return position.price_current - position.price_open
    return position.price_open - position.price_current


def _format_alert(position, milestone: float, profit_points: float) -> str:
    direction_label = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
    return (
        f"\U0001F4B0 {position.symbol} {direction_label} +{milestone:g} points\n"
        f"Entry: {position.price_open:.2f}  Current: {position.price_current:.2f}\n"
        f"Profit: {profit_points:.2f} points\n"
        f"Ticket: {position.ticket}"
    )


def run_once(cfg: Config, state: ProfitState) -> None:
    open_tickets = set()

    for sym_cfg in cfg.symbols:
        try:
            positions = mt5_positions.get_positions(sym_cfg.symbol, sym_cfg.magic_number)
        except Exception as exc:
            print(f"[v4.profit_alerts] {sym_cfg.symbol} position ERROR: {exc}")
            continue

        for position in positions:
            open_tickets.add(position.ticket)
            profit_points = _profit_points(position)

            for milestone in sym_cfg.milestones:
                if profit_points < milestone:
                    continue
                if state.already_alerted(position.ticket, milestone):
                    continue

                text = _format_alert(position, milestone, profit_points)
                try:
                    send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
                    state.mark_alerted(position.ticket, milestone)
                    print(f"[v4.profit_alerts] sent alert: {sym_cfg.symbol} ticket={position.ticket} "
                          f"milestone={milestone:g} profit_points={profit_points:.2f}")
                except Exception as exc:
                    # Deliberately NOT marked alerted on a failed send --
                    # retry next cycle rather than silently losing it.
                    print(f"[v4.profit_alerts] Telegram send ERROR: {exc}")

    state.prune(open_tickets)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN / PROFIT_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_positions.connect(cfg)
    state = ProfitState(cfg.state_file)

    print(f"[v4.profit_alerts] watching {[(s.symbol, s.magic_number) for s in cfg.symbols]}, "
          f"polling every {cfg.poll_seconds}s")
    try:
        while True:
            try:
                run_once(cfg, state)
            except Exception as exc:
                print(f"[v4.profit_alerts] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_positions.shutdown()


if __name__ == "__main__":
    main()
