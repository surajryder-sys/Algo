"""Entry price / SL calculation for Trend Manager and Reversal Manager.
v3's own copy of the market/pullback shape used by both algo_v2/entries.py
(XAUUSD) and algo_v2_usoil_btc_eth/entries.py (BTCUSD/ETHUSD), NOT an
import: v3 doesn't share code with algo_v2 in either direction (see
CLAUDE.md).

Per-symbol EntryConfig, added 2026-08-18 (pulled from the two old bots'
own tuned values, per explicit user request -- "pull out sl buffers and
entry buffers for ETHUSD and BTCUSD from old algo... individual buffers
for sl"). Each symbol's price scale is wildly different (XAUUSD ~$4,400,
ETHUSD ~$1,900, BTCUSD ~$65,000), so a single set of distance constants
across all three (the module's pre-2026-08-18 shape) was never actually
correct for BTC/ETH -- it happened to go unnoticed because neither had
fired through this path yet.

XAUUSD -- three distinct tiers (M1 different from M3/M5), agreed
  2026-08-17/18, unchanged by this refactor:
  M1: market<=3, pullback 3<d<6, floor 3, sl_buffer 1.0.
  M3/M5: market<=4, pullback 4<d<12, floor 4, sl_buffer 1.0.
BTCUSD -- ONE config reused for both M15 and M5 triggers (matches
  algo_v2_usoil_btc_eth's own convention: "M15 reuses M5's exact
  numbers within each symbol" -- there's no M1/M3 tier for crypto at
  all). Pulled from algo_v2_usoil_btc_eth/entries.py's ENTRY_CONFIGS
  (the old btc_smc bot's final tuned values, commit ff4945d):
  market<=175, pullback 175<d<800, floor 175, sl_buffer 20.0.
ETHUSD -- same convention, pulled from the old eth_smc bot's final
  tuned values (commit 7713cf3): market<=4, pullback 4<d<20, floor 4,
  sl_buffer 2.0.

Real bug fixed in the same pass: the old module-level ENTRY_FUNCS only
ever mapped timeframe codes "1"/"3"/"5" -- but BTCUSD/ETHUSD's own
trigger_timeframes are ("15", "5") in both Trend Manager and Reversal
Manager's config. "15" was never in that map, so a lookup for it always
returned None and got silently skipped -- M15 triggers have NEVER
actually been able to fire for crypto, only M5 has. Fixed by keying
entry config lookup on (symbol, timeframe) instead of timeframe alone,
which naturally covers "15" for crypto now that a config exists for it.

Reversal Manager's own M1 confirmation (XAUUSD only -- BTCUSD/ETHUSD
have no M1) stays deliberately WIDER than Trend Manager's own M1 (agreed
2026-08-18: "we might catch a bottom or top... keep some space buffer,
making sure not missing the entry") -- market<=4, pullback 4<d<8,
floor 4, same sl_buffer as XAUUSD's other tiers (1.0). Reversal
Manager's M3/M5/M15/M5(crypto) confirmation all reuse the exact same
per-symbol config as Trend Manager's own ("already prescribed entry
logics") -- no separate reversal-specific widening for those.

Trailing (Stoploss Manager's point-based breakeven/trail_start/
trail_step) is NOT covered by this module -- see
v3/execution_bridge/config.py's own SymbolConfig and its comment on
why those numbers are still XAUUSD-scaled placeholders for BTC/ETH,
not yet confirmed correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

PULLBACK_PCT = 0.45


@dataclass(frozen=True)
class EntryConfig:
    sl_buffer: float
    market_max: float
    pullback_min: float
    pullback_max: float
    pullback_floor: float


_XAUUSD_M1 = EntryConfig(sl_buffer=1.0, market_max=3.0, pullback_min=3.0, pullback_max=6.0, pullback_floor=3.0)
_XAUUSD_M3_M5 = EntryConfig(sl_buffer=1.0, market_max=4.0, pullback_min=4.0, pullback_max=12.0, pullback_floor=4.0)
_XAUUSD_REVERSAL_M1 = EntryConfig(sl_buffer=1.0, market_max=4.0, pullback_min=4.0, pullback_max=8.0, pullback_floor=4.0)

# Started from algo_v2_usoil_btc_eth/entries.py's own ENTRY_CONFIGS
# (see module docstring for provenance), then user's explicit
# 2026-08-18 correction: BTCUSD's pullback_max lowered 800 -> 600
# ("BTCUSD: SL buffer 20.0, market<=175, pullback 175-600, maintain
# same" -- everything else confirmed unchanged from the old bot).
_BTCUSD = EntryConfig(sl_buffer=20.0, market_max=175.0, pullback_min=175.0, pullback_max=600.0, pullback_floor=175.0)
_ETHUSD = EntryConfig(sl_buffer=2.0, market_max=4.0, pullback_min=4.0, pullback_max=20.0, pullback_floor=4.0)

# (symbol, timeframe) -> EntryConfig, for Trend Manager's own entries.
# BTCUSD/ETHUSD's "3" entries added 2026-08-22, same day the user
# changed both symbols' actual bottom chart pane from M5 to M3
# ("change it to m3 everywhere") and trend_manager's/reversal's own
# trigger_timeframes config followed suit -- without an entry here,
# compute_entry() would have silently returned EntryMode.NONE for every
# M3 candidate, breaking entry firing for both symbols entirely. "5"
# entries kept (not removed) since the old M5 bucket, now stale, will
# just naturally stop producing any post-parent candidates once the
# scraper's orphan-reconciliation fix cleans it out -- no harm in the
# lookup entry remaining.
ENTRY_CONFIGS = {
    ("XAUUSD", "1"): _XAUUSD_M1,
    ("XAUUSD", "3"): _XAUUSD_M3_M5,
    ("XAUUSD", "5"): _XAUUSD_M3_M5,
    ("BTCUSD", "15"): _BTCUSD,
    ("BTCUSD", "5"): _BTCUSD,
    ("BTCUSD", "3"): _BTCUSD,
    ("ETHUSD", "15"): _ETHUSD,
    ("ETHUSD", "5"): _ETHUSD,
    ("ETHUSD", "3"): _ETHUSD,
}

# Reversal Manager's own LTF confirmation configs -- identical to
# ENTRY_CONFIGS except XAUUSD's M1, which is deliberately wider (see
# module docstring). Tuple keys can't go through dict(**kwargs), hence
# the explicit copy-then-override instead of dict(x, **y).
REVERSAL_CONFIRM_CONFIGS = dict(ENTRY_CONFIGS)
REVERSAL_CONFIRM_CONFIGS[("XAUUSD", "1")] = _XAUUSD_REVERSAL_M1

# SL buffer is constant per symbol regardless of which tier fired (all
# of XAUUSD's tiers already share 1.0; BTC/ETH only ever had one tier
# anyway) -- this lookup exists separately from ENTRY_CONFIGS for
# Reversal Manager's multi-zone waiting-SL case (SL is based on an HTF
# waiting zone's own timeframe, e.g. H1/M30, which has no EntryConfig
# of its own since HTF timeframes are never a trigger).
#
# USOIL/USTEC (added 2026-08-20, values from the user) don't have an
# ENTRY_CONFIGS entry at all -- their own Trend/Reversal Manager firing
# path (_try_fire_entry_atr_or_ob / _check_direction_atr_or_ob) is
# always a market order, never the pullback/distance math ENTRY_CONFIGS
# exists for, so only this buffer lookup is ever needed for them.
# USOIL's 0.100 was pulled from algo_v2_usoil_btc_eth/entries.py's own
# tuned value (itself carried over unchanged from the original
# standalone algo_v2_usoil bot, "given directly by the user, not copied
# from XAUUSD") -- confirmed by the user as still correct for v3's own
# TradingView-sourced zones. USTEC's 20.0 has no prior bot to pull from
# (never traded before this repo) -- given fresh by the user.
SYMBOL_SL_BUFFER = {
    "XAUUSD": _XAUUSD_M3_M5.sl_buffer,
    "BTCUSD": _BTCUSD.sl_buffer,
    "ETHUSD": _ETHUSD.sl_buffer,
    "USOIL": 0.100,
    "USTEC": 20.0,
}


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fills at current price) or NONE


def _compute(cfg: EntryConfig, direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    """direction: "bull" (ob_edge = OB top) or "bear" (ob_edge = OB
    bottom). distance is always measured as how far price has run away
    from the OB edge -- negative (price hasn't reached the edge at all
    yet) means no trade."""
    sign = 1 if direction == "bull" else -1
    distance = (current_price - ob_edge) * sign

    if distance < 0:
        return EntryPlan(EntryMode.NONE, None)
    if distance <= cfg.market_max:
        return EntryPlan(EntryMode.MARKET, None)
    if cfg.pullback_min < distance < cfg.pullback_max:
        # Offset from the OB edge shrinks naturally as distance shrinks
        # (raw offset = distance * (1 - PULLBACK_PCT)), floored so a
        # short-distance setup doesn't end up demanding an almost-full
        # giveback just to reach an entry that's already only a sliver
        # off the edge.
        offset = max(distance * (1 - PULLBACK_PCT), cfg.pullback_floor)
        entry = ob_edge + offset * sign
        return EntryPlan(EntryMode.PENDING, entry)
    return EntryPlan(EntryMode.NONE, None)


def compute_entry(symbol: str, timeframe: str, direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    """None-config (unsupported symbol/timeframe combination) always
    returns EntryMode.NONE rather than raising -- callers already treat
    NONE as "skip this candidate," so an unconfigured combination is
    simply never a valid trigger, same as any other NONE result."""
    cfg = ENTRY_CONFIGS.get((symbol, timeframe))
    if cfg is None:
        return EntryPlan(EntryMode.NONE, None)
    return _compute(cfg, direction, ob_edge, current_price)


def compute_reversal_confirm_entry(symbol: str, timeframe: str, direction: str,
                                    ob_edge: float, current_price: float) -> EntryPlan:
    cfg = REVERSAL_CONFIRM_CONFIGS.get((symbol, timeframe))
    if cfg is None:
        return EntryPlan(EntryMode.NONE, None)
    return _compute(cfg, direction, ob_edge, current_price)


def ob_edge(direction: str, top: float, btm: float) -> float:
    """Bull retraces down INTO the zone from above -- first contact is
    the zone's top. Bear retraces up INTO the zone from below -- first
    contact is the zone's bottom. Matches algo_v2's own ob.high/ob.low
    convention exactly."""
    return top if direction == "bull" else btm


def initial_sl(symbol: str, timeframe: str, direction: str, top: float, btm: float) -> Optional[float]:
    """SL based only on the OB the trade actually executed off -- its
    OPPOSITE edge from the entry edge (see module docstring). Bull:
    OB's own bottom, minus that symbol's own sl_buffer. Bear: OB's own
    top, plus it. None if (symbol, timeframe) has no configured buffer
    (shouldn't happen in practice -- entry and SL share the same config
    lookup, so if entry fired, SL always resolves too).

    Superseded for Trend Manager's own trades by initial_sl_from_parent
    below (2026-08-19, user's explicit correction) -- kept here as-is
    since Reversal Manager's M5-immediate case still wants SL from the
    OB it actually fired off, not a parent."""
    cfg = ENTRY_CONFIGS.get((symbol, timeframe))
    if cfg is None:
        return None
    return (btm - cfg.sl_buffer) if direction == "bull" else (top + cfg.sl_buffer)


def initial_sl_from_parent(symbol: str, direction: str, top: float, btm: float) -> float:
    """SL based on the PARENT OB's own edge -- not whichever trigger
    timeframe (M1/M3/M5 for XAUUSD, M5/M15 for BTC/ETH) actually fired
    the entry. Added 2026-08-19, user's explicit correction: "sl is
    being set as per own time frame ob, not as per parent ob... whoever
    opens the trade, they should follow parent ob sl" -- confirmed
    scoped to Trend Manager only (Reversal Manager's M5-immediate case
    correctly bases SL on its own M5 reversal zone, not a parent, and
    stays on initial_sl above).

    Uses SYMBOL_SL_BUFFER rather than ENTRY_CONFIGS -- a parent
    timeframe (M5/M15 for XAUUSD, M15/M30 for BTC/ETH) is never a
    trigger timeframe, so it has no EntryConfig of its own to look up
    (same reasoning SYMBOL_SL_BUFFER already exists for: Reversal
    Manager's HTF-waiting-zone SL case)."""
    buffer = SYMBOL_SL_BUFFER[symbol]
    return (btm - buffer) if direction == "bull" else (top + buffer)
