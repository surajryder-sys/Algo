"""Configuration for the TradingView browser-scraper bot, loaded from
environment variables (.env). Independent of tv_bridge/tradingview_bot (the
alert/webhook path) -- this pulls current state directly from a persistently
logged-in TradingView chart instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    chart_url: str
    symbol: str
    timeframe: str
    profile_dir: str
    poll_seconds: float
    zone_state_file: str
    atr_state_file: str
    first_seen_state_file: str
    retest_state_file: str
    trend_state_file: str
    browser_executable_path: Optional[str]
    grid_rows: int
    grid_cols: int


def load_config() -> Config:
    return Config(
        chart_url=os.getenv("TV_SCRAPER_CHART_URL", "https://www.tradingview.com/chart/8MSjxMEZ/"),
        symbol=os.getenv("TV_SCRAPER_SYMBOL", "XAUUSD"),
        timeframe=os.getenv("TV_SCRAPER_TIMEFRAME", "5"),
        profile_dir=os.getenv("TV_SCRAPER_PROFILE_DIR", "tv_scraper_profile"),
        poll_seconds=float(os.getenv("TV_SCRAPER_POLL_SECONDS", "5")),
        # Deliberately its OWN files, not TV_ZONE_STATE_FILE/TV_ATR_STATE_FILE
        # (the alert path's files) -- ZoneStore/AtrStore each load once into
        # memory and do a full unconditional overwrite on every save, so two
        # processes sharing one file would silently clobber each other's
        # zones instead of combining them. algo_v2_tv_xauusd's reader.py
        # merges both sources at read time instead.
        zone_state_file=os.getenv("TV_SCRAPER_ZONE_STATE_FILE", "tv_scraper_zones.json"),
        atr_state_file=os.getenv("TV_SCRAPER_ATR_STATE_FILE", "tv_scraper_atr.json"),
        first_seen_state_file=os.getenv("TV_SCRAPER_FIRST_SEEN_FILE", "tv_scraper_first_seen.json"),
        retest_state_file=os.getenv("TV_SCRAPER_RETEST_FILE", "tv_scraper_retest.json"),
        trend_state_file=os.getenv("TV_SCRAPER_TREND_STATE_FILE", "tv_scraper_trend.json"),
        browser_executable_path=os.getenv("TV_SCRAPER_BROWSER_PATH") or None,
        # Pane grid on the chart layout at chart_url -- e.g. 2x2 for a
        # 4-pane grid (M1/M3/M5/M15, one per pane), each pane self-detecting
        # its own symbol/timeframe from the Data Window (see run_once_pane).
        # Was hardcoded to a 1x2 (left/right) split before this; default
        # kept at 1x2 so existing setups don't change behavior unannounced.
        grid_rows=int(os.getenv("TV_SCRAPER_GRID_ROWS", "1")),
        grid_cols=int(os.getenv("TV_SCRAPER_GRID_COLS", "2")),
    )
