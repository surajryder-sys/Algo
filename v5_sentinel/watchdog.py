"""Watches every V5-Sentinel bot's heartbeat file and alerts (via the
critical-alerts Telegram bot, same broadcast-to-all-approved-subscribers
pattern critical_alerts_watcher.py already uses) the moment one goes
stale -- i.e. the OS process may still be running, but its main loop has
stopped actually turning over.

Built 2026-09-08 after BOTH Trend Manager and Reversal Manager
independently went completely silent for extended periods (Reversal
Manager for over 25 HOURS) while still showing as alive in the OS
process list -- nothing existing caught either one; both were found by
chance while investigating something unrelated. See heartbeat.py's own
docstring for what each bot now writes and why a heartbeat (not any
bot's own state files) is the right signal to watch.

Run with: python -m v5_sentinel.watchdog

One alert per stuck EPISODE, not one per poll -- watchdog_state.json
tracks whether each bot is CURRENTLY considered stale, so it fires once
going stale->alive, once (a "recovered") going stale->healthy again, and
stays silent on every poll in between either state. Same pattern
StaleAlertTracker (bridge_flip.py) already uses for the same reason.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from v5_sentinel import heartbeat
from v5_sentinel.critical_alerts_subscribers import SubscriberStore
from v5_sentinel.critical_alerts_telegram import send_message

load_dotenv()


@dataclass(frozen=True)
class WatchdogConfig:
    telegram_bot_token: str
    owner_chat_id: str
    subscribers_file: str
    poll_seconds: float
    stale_after_seconds: float
    state_file: str
    # bot_name -> heartbeat file path. Same defaults each bot's own
    # config.py already uses, so a fresh install needs no extra setup --
    # override via env only if a bot's own heartbeat file was overridden
    # too.
    bots: dict[str, str]


def load_config() -> WatchdogConfig:
    return WatchdogConfig(
        telegram_bot_token=os.getenv("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        owner_chat_id=os.getenv("CRITICAL_ALERTS_TELEGRAM_CHAT_ID", ""),
        subscribers_file=os.getenv("V5S_CRITICAL_ALERTS_SUBSCRIBERS_FILE", "v5_sentinel_critical_alerts_subscribers.json"),
        poll_seconds=float(os.getenv("V5S_WATCHDOG_POLL_SECONDS", "30")),
        # Generous vs TM/RM's own ~1s poll interval (60x) and the
        # critical-alerts watcher's ~5s one (12x) -- wide enough that an
        # ordinary transient slowdown (a slow MT5 call, a GC pause)
        # never false-positives, tight enough that a real hang is caught
        # within a minute or two, not 25 hours.
        stale_after_seconds=float(os.getenv("V5S_WATCHDOG_STALE_AFTER_SECONDS", "60")),
        state_file=os.getenv("V5S_WATCHDOG_STATE_FILE", "v5s_watchdog_state.json"),
        bots={
            "Trend Manager": os.getenv("V5S_HEARTBEAT_FILE", "v5s_trend_manager_heartbeat.json"),
            "Reversal Manager": os.getenv("V5S_RM_HEARTBEAT_FILE", "v5s_reversal_manager_heartbeat.json"),
            "Critical Alerts": os.getenv("V5S_CRITICAL_ALERTS_HEARTBEAT_FILE", "v5s_critical_alerts_heartbeat.json"),
        },
    )


class WatchdogState:
    """Persists which bots are CURRENTLY considered stale, so a restart
    of the watchdog itself doesn't re-fire a duplicate "just went stale"
    alert for a bot that was already known stale before the restart --
    only a genuine state transition (healthy<->stale) ever alerts."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._stale: dict[str, bool] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._stale = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._stale = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._stale))

    def is_stale(self, bot_name: str) -> bool:
        return self._stale.get(bot_name, False)

    def set_stale(self, bot_name: str, value: bool) -> None:
        if self._stale.get(bot_name) != value:
            self._stale[bot_name] = value
            self._save()


def _format_age(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 120:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"


def run_once(cfg: WatchdogConfig, state: WatchdogState, subscribers: SubscriberStore) -> None:
    for bot_name, heartbeat_file in cfg.bots.items():
        age = heartbeat.read_age(heartbeat_file)
        # None (file missing/unreadable) is treated as infinitely stale --
        # a bot that's never started yet, or whose heartbeat file got
        # deleted, is exactly the "can't confirm it's alive" case this
        # watchdog exists to catch, not something to silently skip.
        currently_stale = age is None or age > cfg.stale_after_seconds
        was_stale = state.is_stale(bot_name)

        if currently_stale and not was_stale:
            age_text = "no heartbeat file at all" if age is None else f"last heartbeat {_format_age(age)} ago"
            text = f"\U0001F6A8 {bot_name} appears STUCK -- {age_text} (process may still show as running)"
            print(f"[V5S-WATCHDOG] {text}")
            for chat_id in subscribers.approved_chat_ids():
                try:
                    send_message(cfg.telegram_bot_token, chat_id, text)
                except Exception as exc:  # noqa: BLE001 -- alerting must never break the loop
                    print(f"[V5S-WATCHDOG] telegram send failed for {chat_id}: {exc!r}")
            state.set_stale(bot_name, True)

        elif was_stale and not currently_stale:
            text = f"✅ {bot_name} recovered -- heartbeat is fresh again"
            print(f"[V5S-WATCHDOG] {text}")
            for chat_id in subscribers.approved_chat_ids():
                try:
                    send_message(cfg.telegram_bot_token, chat_id, text)
                except Exception as exc:  # noqa: BLE001
                    print(f"[V5S-WATCHDOG] telegram send failed for {chat_id}: {exc!r}")
            state.set_stale(bot_name, False)


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.owner_chat_id:
        raise RuntimeError("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN / CRITICAL_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    state = WatchdogState(cfg.state_file)
    subscribers = SubscriberStore(cfg.subscribers_file, cfg.owner_chat_id)

    print(f"[V5S-WATCHDOG] starting -- watching {list(cfg.bots)}, "
          f"stale_after={cfg.stale_after_seconds:.0f}s, poll={cfg.poll_seconds:.0f}s, "
          f"{len(subscribers.approved_chat_ids())} approved subscriber(s)")
    try:
        while True:
            try:
                run_once(cfg, state, subscribers)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-WATCHDOG] cycle error: {exc!r}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
