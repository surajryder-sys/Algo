"""Exit Manager -- ALERT-ONLY component: Zone Touch Alert. Built 2026-09-22
(user's own rule): "whenever a price touches a htf zone during in trade,
only when in trade, send alert only once per candle" -- confirmed "virgin
only" (2026-09-22) for which zones count.

Sends NO orders, ever -- purely a Telegram notification, so unlike every
other Exit Manager component this one is NOT gated by
cfg.enable_trading (that flag only controls whether a CLOSE is actually
sent). It's gated only by whether the shared alerts bot is configured at
all (cfg.alerts_bot_token/alerts_chat_id both set -- see
exit_manager_config.py; the SAME bot components 1/2/3's own exit alerts
use, see each of their own docstrings).

RULE:
  - ZONE SOURCE: the SAME OB zone Block RM-ICT itself reads
    (nlb_nsb_block.BlockStore, cfg.ict_block_state_file) -- read-only,
    same relationship component 3 (exit_manager_candle.py) already has to
    it.
  - "ONLY UNTESTED (VIRGIN) ZONES": zone.retested == False -- same filter
    component 3's own zone check uses (see that module's docstring for why
    BlockZone has no separate "virgin" field; retested IS the tested/
    untested flag).
  - TOUCH: live price (wick-aware via broker.price_extremes_since, same
    mechanism RM-STR/RM-ICT/component 2 all use, so a touch shorter than
    one poll interval still counts) overlapping the zone's own
    [btm, top] range -- the same "price entered the range" test the
    watcher's own retest detection uses, just done independently here
    rather than read off zone.retested_at (that field belongs to the
    watcher's own live tracking, not this alert's own dedup -- see below).
  - "ONLY WHEN IN TRADE": at least one open position across ANY of the
    three managers (TM-STR/RM-STR/RM-ICT, cfg.sources) -- no direction
    pairing with the touched zone's own role, just "something is open at
    all." Checked BEFORE anything else each cycle -- no positions open,
    no zone/touch computation even happens.
  - "ONCE PER CANDLE": deliberately wall-clock MINUTE-bucketed (not tied
    to any specific MT5 timeframe's own bar boundary -- the rule as given
    doesn't name one, and a plain price-touch has no natural CISD/pattern
    timeframe to anchor to the way every other component's own trigger
    does) -- per zone_id, at most one alert per calendar minute while that
    zone stays touched. A zone that's touched continuously across several
    minutes gets one alert per minute it stays touched, not one ever; a
    zone price has moved away from and later comes back to is treated
    fresh again (no re-arm delay beyond the minute bucket itself).
    Persisted only in memory (ZoneTouchAlertRuntime) -- a restart simply
    starts the dedup clock over, never a trading decision so nothing is
    lost by that.

Failure to send (network error, bad token, etc.) is caught and printed,
never raised into the caller's own poll loop -- same fail-soft contract
this project's other alert senders already use.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from v7_sentinel import broker, telegram_alerts
from v7_sentinel.nlb_nsb_block import BlockStore

if TYPE_CHECKING:
    from v7_sentinel.exit_manager_config import ExitManagerSymbolConfig


@dataclass
class ZoneTouchAlertRuntime:
    last_tick_msc: int = 0
    _last_alert_minute: dict[str, int] = field(default_factory=dict)   # zone_id -> minute bucket


def build_runtime() -> ZoneTouchAlertRuntime:
    return ZoneTouchAlertRuntime()


def _any_open_position(cfg: "ExitManagerSymbolConfig") -> bool:
    return any(broker.get_positions(cfg.symbol, source.magic_number) for source in cfg.sources)


def run_once(cfg: "ExitManagerSymbolConfig", rt: ZoneTouchAlertRuntime) -> None:
    if not cfg.alerts_bot_token or not cfg.alerts_chat_id:
        return   # not configured -- silently off, same convention as every unset-optional-feature here
    if not _any_open_position(cfg):
        return   # "only when in trade"

    bid, ask = broker.get_tick_price(cfg.symbol)
    bid_low, ask_high = bid, ask
    if rt.last_tick_msc:
        lo, hi, newest = broker.price_extremes_since(cfg.symbol, rt.last_tick_msc)
        if lo is not None:
            bid_low, ask_high = min(bid, lo), max(ask, hi)
        rt.last_tick_msc = max(rt.last_tick_msc, newest)
    else:                                              # first cycle: start looking from now, never from history
        tick = mt5.symbol_info_tick(cfg.symbol)
        rt.last_tick_msc = int(tick.time_msc) if tick is not None else 0

    block = BlockStore(cfg.ict_block_state_file)   # read-only, same as component 3
    minute_bucket = int(time.time()) // 60

    for zone in block.zones():
        if zone.retested:
            continue   # only untested (still virgin) zones
        touched = bid_low <= zone.top and ask_high >= zone.btm
        if not touched:
            continue
        if rt._last_alert_minute.get(zone.zone_id) == minute_bucket:
            continue   # already alerted this candle (minute)
        rt._last_alert_minute[zone.zone_id] = minute_bucket

        side = "demand (BUY-side)" if zone.role == "no_short_buffer" else "supply (SELL-side)"
        text = (f"[V7S] {cfg.symbol}: price touched {zone.timeframe_name} OB zone "
                f"{zone.btm:.3f}-{zone.top:.3f} ({side}), virgin, while a trade is open.")
        print(f"[V7S-ZONE-ALERT] {text}")
        telegram_alerts.send_if_configured(cfg.alerts_bot_token, cfg.alerts_chat_id, text)
