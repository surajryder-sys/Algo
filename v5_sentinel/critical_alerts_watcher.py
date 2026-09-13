"""Critical-alerts bot -- renamed and fully repurposed 2026-09-07 from
profit_alerts_watcher.py: "stop sending the profit alerts, now it is no
longer needed, remove the logic and change the name to critical alerts."

No longer position/profit-based at all. TWO independent observation
feeds now, both fully independent of whether TM/RM has traded that
level/zone or not:

  1. HTF support/resistance touches -- the SAME 9 HTF levels Reversal
     Manager trades off of (htf_levels.py -- D1, H8, H6, H4, H3, H2, H1,
     M30, M15, each timeframe's own two trail lines), one alert the
     moment LIVE price touches any of them.

  2. NLB/NSB OB-zone touches (added 2026-09-14, "make sure alert manager
     sends all alerts on critical bot which includes price when touches
     resistance or support or price when touches the untested ob zones
     with their details") -- reads nlb_nsb_block.py's own Block
     (read-only, owned/written exclusively by nlb_nsb_watcher.py, same
     access pattern main.py's ICT Guard already uses) and alerts the
     moment a zone's own `retested` flag flips False -> True. No live
     bid/ask geometry of its own here -- nlb_nsb_watcher.py's own live-
     tick detection is already the authoritative source for "has this
     zone actually been touched," this watcher only OBSERVES that
     already-committed state, avoiding a second, possibly-inconsistent
     detection of the same event.

Dedup rules differ between the two feeds (see each store's own
docstring in critical_alerts_state.py): HTF levels dedup ONCE PER
CLOSED BAR (a level's value can meaningfully change again later); OB
zones dedup PERMANENTLY, once per zone_id (a zone's own retested flag
only ever flips False->True once, ever).

  Confirmed with the user 2026-09-07 for the HTF-level feed: "one alert
  per level per CLOSED BAR ('once per bar is fine')... i received same
  alert twice, once per bar is fine" -- originally deduped on the
  level's own float VALUE instead, but that's fed by the same path-
  dependent copy_rates recompute already documented elsewhere as
  capable of tiny cycle-to-cycle jitter, occasionally enough to exceed
  the epsilon and look like "a new level" when the underlying bar
  hadn't actually changed. Bar-time dedup is immune to that and still
  satisfies the original ask ("M30 support at 4430... a new price value
  of 4435 -- alerts again"), since a level's value only ever actually
  changes on a bar close in the first place.

Run alongside critical_alerts_listener.py (separate process, handles
subscriber approval commands -- see that module's own docstring for why
they must be separate).

Run with: python -m v5_sentinel.critical_alerts_watcher
"""
from __future__ import annotations

import time

from v5_sentinel import critical_alerts_mt5 as mt5_price
from v5_sentinel import heartbeat
from v5_sentinel import htf_levels
from v5_sentinel import nlb_nsb_block
from v5_sentinel.critical_alerts_config import Config, load_config
from v5_sentinel.critical_alerts_state import CriticalAlertState, OBZoneAlertState
from v5_sentinel.critical_alerts_subscribers import SubscriberStore
from v5_sentinel.critical_alerts_telegram import send_message

_ZONE_ROLE_LABEL = {
    "no_short_buffer": "NSB (bullish demand)",
    "no_long_buffer": "NLB (bearish supply)",
}


def _format_alert(tf_minutes: int, level: "htf_levels.HTFLevel", touch_price: float) -> str:
    name = htf_levels.TIMEFRAME_NAMES.get(tf_minutes, f"M{tf_minutes}")
    icon = "\U0001F7E2" if level.role == "SUPPORT" else "\U0001F534"  # green/red circle
    return (
        f"{icon} {name} {level.role} touched\n"
        f"Level: {level.value:.3f}\n"
        f"Price: {touch_price:.3f}"
    )


def _format_ob_zone_alert(zone: "nlb_nsb_block.BlockZone", touch_price: float) -> str:
    icon = "\U0001F7E2" if zone.direction == "bull" else "\U0001F534"
    label = _ZONE_ROLE_LABEL.get(zone.role, zone.role)
    return (
        f"{icon} {zone.timeframe_name} {label} zone touched\n"
        f"Zone: {zone.btm:.3f} - {zone.top:.3f}\n"
        f"Price: {touch_price:.3f}"
    )


def _broadcast(cfg: Config, subscribers: SubscriberStore, text: str) -> bool:
    """Sends to every currently-approved subscriber. Returns True only if
    ALL of them succeeded -- a partial-send failure means the caller
    should NOT mark this alert as sent, so the whole thing retries next
    cycle rather than risk silently skipping someone (same convention the
    old profit-alerts watcher used)."""
    all_ok = True
    for chat_id in subscribers.approved_chat_ids():
        try:
            send_message(cfg.telegram_bot_token, chat_id, text)
        except Exception as exc:
            all_ok = False
            print(f"[v5_sentinel.critical_alerts] Telegram send ERROR (chat_id={chat_id}): {exc}")
    return all_ok


def run_once(cfg: Config, state: CriticalAlertState, ob_zone_state: OBZoneAlertState,
            subscribers: SubscriberStore) -> None:
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
            if _broadcast(cfg, subscribers, text):
                state.mark_alerted(tf, level.line_no, htf_state.last_time)
                name = htf_levels.TIMEFRAME_NAMES.get(tf, f"M{tf}")
                print(f"[v5_sentinel.critical_alerts] sent alert: {cfg.symbol} {name} {level.role} "
                      f"@ {level.value:.3f} recipients={len(subscribers.approved_chat_ids())}")

    # NLB/NSB OB-zone touches -- read-only observation of nlb_nsb_watcher.
    # py's own already-committed retest state, see module docstring.
    for zone in nlb_nsb_block.BlockStore(cfg.nlb_nsb_block_state_file).zones():
        if not zone.retested:
            continue  # still untested -- nothing to alert on yet
        if ob_zone_state.already_alerted(zone.zone_id):
            continue

        touch_price = bid if zone.direction == "bull" else ask
        text = _format_ob_zone_alert(zone, touch_price)
        if _broadcast(cfg, subscribers, text):
            ob_zone_state.mark_alerted(zone.zone_id)
            print(f"[v5_sentinel.critical_alerts] sent OB zone alert: {cfg.symbol} {zone.timeframe_name} "
                  f"{zone.role} [{zone.btm:.3f}-{zone.top:.3f}] recipients={len(subscribers.approved_chat_ids())}")


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.owner_chat_id:
        raise RuntimeError("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN / CRITICAL_ALERTS_TELEGRAM_CHAT_ID must be set in .env")

    mt5_price.connect(cfg)
    state = CriticalAlertState(cfg.state_file)
    ob_zone_state = OBZoneAlertState(cfg.ob_zone_alert_state_file)
    subscribers = SubscriberStore(cfg.subscribers_file, cfg.owner_chat_id)

    print(f"[v5_sentinel.critical_alerts] watching {cfg.symbol} support/resistance touches "
          f"(D1/H8/H6/H4/H3/H2/H1/M30/M15) AND NLB/NSB OB-zone touches, polling every {cfg.poll_seconds}s, "
          f"{len(subscribers.approved_chat_ids())} approved subscriber(s)")
    try:
        while True:
            try:
                run_once(cfg, state, ob_zone_state, subscribers)
            except Exception as exc:
                print(f"[v5_sentinel.critical_alerts] ERROR: {exc}")
            # See heartbeat.py's own docstring -- proves the loop itself
            # is alive each cycle, added 2026-09-08 as part of the
            # watchdog build (TM and RM both independently went silently
            # inert for extended periods with no existing signal to catch
            # it).
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        mt5_price.shutdown()


if __name__ == "__main__":
    main()
