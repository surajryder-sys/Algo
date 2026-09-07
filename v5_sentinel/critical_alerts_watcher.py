"""Critical-alerts bot -- renamed and fully repurposed 2026-09-07 from
profit_alerts_watcher.py: "stop sending the profit alerts, now it is no
longer needed, remove the logic and change the name to critical alerts."

No longer position/profit-based at all. Instead: watches the SAME 9 HTF
support/resistance levels Reversal Manager trades off of (htf_levels.py
-- D1, H8, H6, H4, H3, H2, H1, M30, M15, each timeframe's own two trail
lines), and sends one Telegram alert the moment LIVE price touches any
of them -- independent of whether Reversal Manager has traded that
level or not, this is a pure observation feed.

Dedup rule (confirmed with the user, critical_alerts_state.py): ONE
alert per level per CLOSED BAR ("once per bar is fine"), not per touch
-- price can touch the same level any number of times within one bar
without a repeat alert, but the moment that timeframe's own bar closes
again, a fresh touch can alert again, whether or not the level's value
actually moved. This was originally deduped on the level's own float
VALUE instead, but that's fed by the same path-dependent copy_rates
recompute already documented elsewhere as capable of tiny cycle-to-cycle
jitter -- found live the same day ("i received same alert twice") when
that jitter alone was enough to look like a new level. Bar-time dedup is
immune to that and still satisfies the original ask ("M30 support at
4430... a new price value of 4435 -- alerts again"), since a level's
value only ever actually changes on a bar close in the first place.

Run alongside critical_alerts_listener.py (separate process, handles
subscriber approval commands -- see that module's own docstring for why
they must be separate).

Run with: python -m v5_sentinel.critical_alerts_watcher
"""
from __future__ import annotations

import time

from v5_sentinel import critical_alerts_mt5 as mt5_price
from v5_sentinel import htf_levels
from v5_sentinel.critical_alerts_config import Config, load_config
from v5_sentinel.critical_alerts_state import CriticalAlertState
from v5_sentinel.critical_alerts_subscribers import SubscriberStore
from v5_sentinel.critical_alerts_telegram import send_message


def _format_alert(tf_minutes: int, level: "htf_levels.HTFLevel", touch_price: float) -> str:
    name = htf_levels.TIMEFRAME_NAMES.get(tf_minutes, f"M{tf_minutes}")
    icon = "\U0001F7E2" if level.role == "SUPPORT" else "\U0001F534"  # green/red circle
    return (
        f"{icon} {name} {level.role} touched\n"
        f"Level: {level.value:.3f}\n"
        f"Price: {touch_price:.3f}"
    )


def run_once(cfg: Config, state: CriticalAlertState, subscribers: SubscriberStore) -> None:
    htf_states = htf_levels.compute_all_htf_states(cfg.symbol)

    tick = mt5_price.get_tick_price(cfg.symbol)
    if tick is None:
        print(f"[v5_sentinel.critical_alerts] {cfg.symbol} no live tick right now")
        return
    bid, ask = tick

    for tf, htf_state in htf_states.items():
        if htf_state is None:
            continue
        for level in htf_state.levels:
            touch_price = bid if level.role == "SUPPORT" else ask
            reached = (touch_price <= level.value) if level.role == "SUPPORT" else (touch_price >= level.value)
            if not reached:
                continue
            if state.already_alerted(tf, level.line_no, htf_state.last_time):
                continue

            text = _format_alert(tf, level, touch_price)
            recipients = subscribers.approved_chat_ids()
            all_ok = True
            for chat_id in recipients:
                try:
                    send_message(cfg.telegram_bot_token, chat_id, text)
                except Exception as exc:
                    all_ok = False
                    print(f"[v5_sentinel.critical_alerts] Telegram send ERROR (chat_id={chat_id}): {exc}")
            if all_ok:
                # Marked alerted only once ALL current subscribers got it
                # -- a partial-send failure retries the WHOLE level next
                # cycle rather than risk silently skipping someone, same
                # convention the old profit-alerts watcher used.
                state.mark_alerted(tf, level.line_no, htf_state.last_time)
                name = htf_levels.TIMEFRAME_NAMES.get(tf, f"M{tf}")
                print(f"[v5_sentinel.critical_alerts] sent alert: {cfg.symbol} {name} {level.role} "
                      f"@ {level.value:.3f} recipients={len(recipients)}")


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.owner_chat_id:
        raise RuntimeError("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN / CRITICAL_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_price.connect(cfg)
    state = CriticalAlertState(cfg.state_file)
    subscribers = SubscriberStore(cfg.subscribers_file, cfg.owner_chat_id)

    print(f"[v5_sentinel.critical_alerts] watching {cfg.symbol} support/resistance touches "
          f"(D1/H8/H6/H4/H3/H2/H1/M30/M15), polling every {cfg.poll_seconds}s, "
          f"{len(subscribers.approved_chat_ids())} approved subscriber(s)")
    try:
        while True:
            try:
                run_once(cfg, state, subscribers)
            except Exception as exc:
                print(f"[v5_sentinel.critical_alerts] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_price.shutdown()


if __name__ == "__main__":
    main()
