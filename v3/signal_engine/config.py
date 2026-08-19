"""Configuration for Signal Engine's Managers (Trend Manager first).
Own small config, separate from v3/alert_manager/config.py's, even
though the symbol -> zone_state_file mapping is the same underlying
files -- each Manager/bot in this repo owns its own config rather than
importing another bot's (see CLAUDE.md), and Signal Engine is a peer to
Alert Manager, not a dependent of it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str  # MT5 symbol name (plain, no broker suffix -- XAUUSD/BTCUSD/ETHUSD)
    zone_state_file: str  # tv_scraper's zone store for this symbol
    # tv_scraper's live per-timeframe snapshot (close price etc) -- used
    # for entry/distance math. Deliberately TradingView-sourced, not
    # MT5, per explicit user call 2026-08-17: "through tv scraper is
    # best, mt5 only for placing orders and getting live price" --
    # keeps Trend Manager's own decision-making MT5-free, consistent
    # with Signal Engine's "no MT5 order touched at this layer" rule.
    live_state_file: str
    # The two "parent" timeframes trend_manager.py compares -- whichever
    # has the newer eligible OB wins and opens the trade. Differs per
    # symbol: XAUUSD (M5/M15) vs BTCUSD/ETHUSD (M15/M30), per explicit
    # user request 2026-08-17 -- crypto's own tv_scraper grid only scrapes
    # H4/H2/H1/M30/M15/M5 (no M1/M3 at all, see
    # project_tv_scraper_multi_symbol_setup memory), so XAUUSD's M5/M3/M1
    # scheme simply doesn't apply there; everything shifts one tier up.
    # Was a fixed 2-tuple until USOIL/USTEC (2026-08-19), whose own
    # parent scheme is three-wide (1h/30m/15m) rather than two --
    # _best_parent_candidate/_newest_eligible_start_time already just
    # iterate this, so widening the type is the only change needed.
    parent_timeframes: Tuple[str, ...]
    # Pure execution triggers -- never get their own watermark, just
    # need ANY confirmed OB in the parent's direction to fire. XAUUSD:
    # M5/M3/M1 ("whichever forms first"). BTCUSD/ETHUSD: M15/M5 (same
    # "whichever gets the early entry" idea, shifted for the TFs crypto
    # actually has). USOIL/USTEC: M3 only.
    trigger_timeframes: Tuple[str, ...]
    # Set only for USOIL/USTEC (2026-08-19, user's explicit rule) --
    # when present, _try_fire_entry uses a DIFFERENT firing mechanism
    # entirely: instead of the pullback/market distance math every other
    # symbol uses, it fires a MARKET order the instant EITHER a fresh OB
    # forms on this timeframe (matching bias direction) OR this
    # timeframe's own ATR trend flips to match bias direction --
    # whichever happens first ("m3 is the only execution timeframe...
    # fresh ob's trend manager will trade, based on the confirmation of
    # atr in m3... also ATR flip or a fresh ob on m3, whichever confirms
    # first... fresh ob on m3 also market entry, as its lower time
    # frame... or ATR flip also market entry"). None (default) keeps
    # every existing symbol on the original pullback-distance mechanism,
    # untouched.
    atr_confirm_timeframe: Optional[str] = None
    # Where to read this symbol's ATR trend/event_time from (AtrStore) --
    # only needed when atr_confirm_timeframe is set. Shares the SAME
    # zone-store-shaped file convention as the other per-symbol state
    # files.
    atr_state_file: Optional[str] = None


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float
    trade_state_file: str
    # Reserved ahead of Execution Bridge actually placing MT5 orders off
    # Trend Manager's signals -- not used for anything yet (nothing here
    # touches MT5). Settled now, following this repo's existing
    # YYMMDDNN magic-number convention (see .env.example's other bots),
    # so it's already decided and collision-free before it's ever live.
    magic_number: int
    # Execution Bridge writes here (v3/execution_bridge/manual_events.py)
    # the moment it detects a REAL manual cancel/close in MT5 -- read
    # here, never written here (see trade_tracker.py's
    # should_react_to_close_event for the consumption side).
    manual_events_file: str


def load_config() -> Config:
    return Config(
        symbols=[
            SymbolConfig(
                "XAUUSD",
                os.getenv("SIGNAL_ENGINE_XAUUSD_ZONE_FILE", "tv_scraper_xauusd_zones.json"),
                live_state_file=os.getenv("SIGNAL_ENGINE_XAUUSD_LIVE_FILE", "tv_scraper_xauusd_live.json"),
                parent_timeframes=("5", "15"),
                trigger_timeframes=("5", "3", "1"),
            ),
            SymbolConfig(
                "BTCUSD",
                os.getenv("SIGNAL_ENGINE_BTCUSD_ZONE_FILE", "tv_scraper_zones.json"),
                live_state_file=os.getenv("SIGNAL_ENGINE_BTCUSD_LIVE_FILE", "tv_scraper_live.json"),
                parent_timeframes=("15", "30"),
                trigger_timeframes=("15", "5"),
            ),
            SymbolConfig(
                "ETHUSD",
                os.getenv("SIGNAL_ENGINE_ETHUSD_ZONE_FILE", "tv_scraper_ethusd_zones.json"),
                live_state_file=os.getenv("SIGNAL_ENGINE_ETHUSD_LIVE_FILE", "tv_scraper_ethusd_live.json"),
                parent_timeframes=("15", "30"),
                trigger_timeframes=("15", "5"),
            ),
            # USOIL/USTEC (added 2026-08-19) -- one shared tv_scraper
            # process/window serves both (same "Scrpr_USOIL/USTEC" 2x4
            # layout, one browser tab), so both symbols share the same
            # zone/live/atr state files -- harmless, ZoneStore/AtrStore
            # key everything by (symbol, ...) internally regardless of
            # which files are shared. NOT yet wired into Execution
            # Bridge (v3/execution_bridge/config.py) or entries.py's
            # SYMBOL_SL_BUFFER -- SL buffers/lots are still pending from
            # the user, so these two can compute bias/signals but can't
            # actually fire a real order yet (trend_manager.py's
            # ATR-confirm path explicitly checks for this and skips
            # rather than raising -- see its own comment).
            SymbolConfig(
                "USOIL",
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_ZONE_FILE", "tv_scraper_usoil_ustec_zones.json"),
                live_state_file=os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                parent_timeframes=("60", "30", "15"),
                trigger_timeframes=("3",),
                atr_confirm_timeframe="3",
                atr_state_file=os.getenv("SIGNAL_ENGINE_USOIL_USTEC_ATR_FILE", "tv_scraper_usoil_ustec_atr.json"),
            ),
            SymbolConfig(
                "USTEC",
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_ZONE_FILE", "tv_scraper_usoil_ustec_zones.json"),
                live_state_file=os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                parent_timeframes=("60", "30", "15"),
                trigger_timeframes=("3",),
                atr_confirm_timeframe="3",
                atr_state_file=os.getenv("SIGNAL_ENGINE_USOIL_USTEC_ATR_FILE", "tv_scraper_usoil_ustec_atr.json"),
            ),
        ],
        poll_seconds=float(os.getenv("SIGNAL_ENGINE_POLL_SECONDS", "5.0")),
        trade_state_file=os.getenv("SIGNAL_ENGINE_TRADE_STATE_FILE", "trend_manager_trade_state.json"),
        magic_number=int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
        manual_events_file=os.getenv("EXECUTION_BRIDGE_MANUAL_EVENTS_FILE", "execution_bridge_manual_events.json"),
    )
