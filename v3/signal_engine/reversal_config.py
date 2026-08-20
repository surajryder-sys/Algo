"""Configuration for Reversal Manager. Own config, separate from Trend
Manager's (see CLAUDE.md -- each Manager owns its own).

Zone/ATR data source switched 2026-08-20 from tv_scraper's per-symbol
polled files to the TradingView webhook path instead (tv_bridge.receiver
-> tradingview_bot.main -> ZoneStore/AtrStore) -- user's explicit rule:
this Manager's whole mechanism is retest-driven, and only the webhook
path can carry ob_zone_retested's exact retest time; tv_scraper only ever
approximated a retest from its own next-poll Close reading. Unlike
tv_scraper's per-symbol zone files, tradingview_bot.main writes ONE
shared file for every symbol (ZoneStore/AtrStore are internally keyed by
"symbol|timeframe|direction"/"symbol|timeframe"), so all five
SymbolConfig entries below point at the SAME zone_state_file/
atr_state_file rather than each having their own.

live_state_file is intentionally UNCHANGED -- still tv_scraper's own
per-symbol live Close polling. The webhook schema has no live-price-tick
event at all (only sparse zone/ATR events), so Reversal Manager's
_read_live_close() still needs tv_scraper running alongside the webhook
pipeline, just for price -- see the module docstring on why this split
is intentional, not a leftover.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# Same five HTF timeframes for every symbol -- user's own list, no
# per-symbol variation given (unlike Trend Manager's parent timeframes).
HTF_TIMEFRAMES: Tuple[str, ...] = ("240", "120", "60", "30", "15")


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    zone_state_file: str
    live_state_file: str
    # LTF confirmation timeframes -- XAUUSD has M1/M3/M5; BTCUSD/ETHUSD's
    # own tv_scraper grid has no M1/M3 at all (see
    # project_tv_scraper_multi_symbol_setup memory), so only M5 applies.
    ltf_timeframes: Tuple[str, ...]
    # Trend Manager's own two parent (bias) timeframes for this symbol --
    # None (default) means the M5-immediate/mitigation-close rules below
    # are NOT enabled for this symbol, keeping the original 2026-08-18
    # behavior (M5 always fires immediately, mitigation always closes).
    # Added 2026-08-19, user's explicit XAUUSD-only rule set:
    # - M5 retest whose direction agrees with AT LEAST ONE parent still
    #   fires immediately, same as before.
    # - M5 retest agreeing with NEITHER parent no longer fires OR gets
    #   dropped -- it's registered as a waiting retest instead, resolved
    #   by the SAME M1/M3/M5 LTF confirmation/invalidation machinery the
    #   HTF (H4/H2/H1/M30/M15) zones already use. SL for a
    #   confirmed-via-LTF fire naturally still comes from the M5 zone's
    #   own edge via the existing multi-waiting-zone SL logic in
    #   _check_direction -- no separate code path needed for that.
    # - Once filled, mitigation of the entry OB no longer auto-closes
    #   the trade -- only a fresh OPPOSITE-direction OB on M1 or M3 does
    #   (see _close_if_opposite_ltf_ob).
    parent_timeframes: Optional[Tuple[str, str]] = None
    # Hard cap on initial SL distance (price units, symbol's own scale)
    # -- None means uncomputed/no cap (BTCUSD/ETHUSD, unchanged). Added
    # same day as parent_timeframes above, same XAUUSD-only scope.
    max_sl_points: Optional[float] = None
    # USOIL/USTEC only (2026-08-19) -- when set, _check_direction's
    # LTF confirmation/invalidation ALSO accepts an ATR trend flip on
    # this timeframe as a peer to a fresh LTF OB (either one confirms or
    # invalidates a waiting retest), and a confirmed fire is always
    # MARKET, skipping the pullback/distance math entirely -- same
    # "m3 is the only execution timeframe... market entry, as its lower
    # time frame" reasoning as Trend Manager's own atr_confirm_timeframe
    # (see that module's SymbolConfig docstring for the full user quote).
    # These two symbols have no M5-immediate tier at all -- harmless
    # by construction, not a separate flag: their tv_scraper grid has no
    # M5 pane, so _fire_m5_immediate's own zone lookup simply never
    # finds anything and no-ops every cycle.
    atr_confirm_timeframe: Optional[str] = None
    atr_state_file: Optional[str] = None


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float
    state_file: str
    magic_number: int
    # Execution Bridge writes here (v3/execution_bridge/manual_events.py)
    # the moment it detects a REAL manual cancel/close or SL/TP hit for
    # a Reversal-Manager-sourced position -- read here, never written
    # here. Added 2026-08-18 after a real SL hit left this Manager's
    # own state showing FILLED forever with nothing real behind it.
    manual_events_file: str


def load_config() -> Config:
    # Shared webhook-sourced zone/ATR files -- ONE file each for every
    # symbol (see module docstring). Same env var names tradingview_bot's
    # own config.py uses (TV_ZONE_STATE_FILE/TV_ATR_STATE_FILE), so
    # pointing both processes at the same .env values keeps them in sync
    # by construction rather than by two separately-maintained defaults.
    tv_zone_file = os.getenv("TV_ZONE_STATE_FILE", "tradingview_bot_zones.json")
    tv_atr_file = os.getenv("TV_ATR_STATE_FILE", "tradingview_bot_atr.json")
    return Config(
        symbols=[
            SymbolConfig(
                "XAUUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_XAUUSD_LIVE_FILE", "tv_scraper_xauusd_live.json"),
                ltf_timeframes=("1", "3", "5"),
                parent_timeframes=("5", "15"),
                max_sl_points=20.0,
            ),
            SymbolConfig(
                "BTCUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_BTCUSD_LIVE_FILE", "tv_scraper_live.json"),
                ltf_timeframes=("5",),
            ),
            SymbolConfig(
                "ETHUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_ETHUSD_LIVE_FILE", "tv_scraper_ethusd_live.json"),
                ltf_timeframes=("5",),
            ),
            # USOIL/USTEC (added 2026-08-19) -- see trend_manager's own
            # config.py for the shared-tv_scraper-process rationale.
            # HTF_TIMEFRAMES above still gets used unchanged for these
            # two (_register_htf_retests iterates it for every symbol) --
            # harmless: no H4/H2 alerts are expected to be configured for
            # these two, so those two entries simply never find zone data
            # and no-op, leaving H1/M30/M15 (their real 3 parents) as the
            # only ones that ever actually register a wait. NOT yet wired
            # into Execution Bridge or entries.py's SYMBOL_SL_BUFFER --
            # same "SL buffers pending" gap as Trend Manager's own entries.
            SymbolConfig(
                "USOIL",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                ltf_timeframes=("3",),
                atr_confirm_timeframe="3",
                atr_state_file=tv_atr_file,
            ),
            SymbolConfig(
                "USTEC",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                ltf_timeframes=("3",),
                atr_confirm_timeframe="3",
                atr_state_file=tv_atr_file,
            ),
        ],
        poll_seconds=float(os.getenv("REVERSAL_MANAGER_POLL_SECONDS", "5.0")),
        state_file=os.getenv("REVERSAL_MANAGER_STATE_FILE", "reversal_manager_state.json"),
        magic_number=int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
        manual_events_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_MANUAL_EVENTS_FILE",
                                      "execution_bridge_manual_events_reversal.json"),
    )
