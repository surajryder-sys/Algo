"""Configuration for V5-Sentinel's own TradingView OB-zone scraper --
ported 2026-09-07 from v3/tv_scraper/config.py at the user's explicit
direction ("everything is same, even the chart url everything is same,
just port or copy from there and add it in v5"). Same defaults as v3's
already-proven setup (grid layout, window position, browser path, poll
interval) -- only the env var prefix, chart_url, and state file names
are v5-specific, so this can run as its OWN independent process without
colliding with v3's tv_scraper (which may or may not also be running).

Deliberately narrower than v3's config: no ATR/trend state file fields
at all -- "MT5 data is only for execution, ATR based" (user, 2026-09-07)
-- this scraper's only job is OB zone detection (formed/retested/top/
btm) for the second Reversal Manager component. LTF confirmation + SL
still comes entirely from the MT5 bridge, unchanged from STR.
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
    first_seen_state_file: str
    retest_state_file: str
    live_snapshot_file: str
    mitigation_track_file: str
    zone_history_log_file: str
    browser_executable_path: Optional[str]
    grid_rows: int
    grid_cols: int
    window_x: int
    window_y: int
    window_width: int
    window_height: int
    cdp_port: int


def load_config() -> Config:
    return Config(
        # User-supplied chart URL (2026-09-07) -- "same layout, same
        # indicator (OBD_SecretTrader.pine), same everything" as v3's own
        # already-built setup.
        chart_url=os.getenv("V5S_TV_SCRAPER_CHART_URL", "https://www.tradingview.com/chart/mL7E68j4/"),
        symbol=os.getenv("V5S_TV_SCRAPER_SYMBOL", "XAUUSD"),
        timeframe=os.getenv("V5S_TV_SCRAPER_TIMEFRAME", "5"),
        # Deliberately its OWN Chrome/Brave profile dir + CDP port (below)
        # -- an independent browser instance from v3's, so both can run at
        # once without fighting over the same profile lock or DevTools port.
        profile_dir=os.getenv("V5S_TV_SCRAPER_PROFILE_DIR", "v5s_tv_scraper_profile"),
        poll_seconds=float(os.getenv("V5S_TV_SCRAPER_POLL_SECONDS", "5")),
        zone_state_file=os.getenv("V5S_TV_SCRAPER_ZONE_STATE_FILE", "v5s_tv_scraper_zones.json"),
        first_seen_state_file=os.getenv("V5S_TV_SCRAPER_FIRST_SEEN_FILE", "v5s_tv_scraper_first_seen.json"),
        retest_state_file=os.getenv("V5S_TV_SCRAPER_RETEST_FILE", "v5s_tv_scraper_retest.json"),
        live_snapshot_file=os.getenv("V5S_TV_SCRAPER_LIVE_SNAPSHOT_FILE", "v5s_tv_scraper_live.json"),
        mitigation_track_file=os.getenv("V5S_TV_SCRAPER_MITIGATION_TRACK_FILE", "v5s_tv_scraper_mitigation_track.json"),
        zone_history_log_file=os.getenv("V5S_TV_SCRAPER_ZONE_HISTORY_LOG_FILE", "v5s_tv_scraper_zone_history.jsonl"),
        browser_executable_path=os.getenv("V5S_TV_SCRAPER_BROWSER_PATH") or None,
        # Same 6x1 stacked-timeframe grid as v3's own already-proven XAUUSD
        # setup, per "everything is same" -- override via env if this
        # chart's actual layout differs.
        grid_rows=int(os.getenv("V5S_TV_SCRAPER_GRID_ROWS", "6")),
        grid_cols=int(os.getenv("V5S_TV_SCRAPER_GRID_COLS", "1")),
        window_x=int(os.getenv("V5S_TV_SCRAPER_WINDOW_X", "0")),
        window_y=int(os.getenv("V5S_TV_SCRAPER_WINDOW_Y", "0")),
        window_width=int(os.getenv("V5S_TV_SCRAPER_WINDOW_WIDTH", "1720")),
        window_height=int(os.getenv("V5S_TV_SCRAPER_WINDOW_HEIGHT", "1392")),
        # Own CDP port, distinct from v3 tv_scraper's default 9222 -- lets
        # both run simultaneously as fully independent browser instances.
        cdp_port=int(os.getenv("V5S_TV_SCRAPER_CDP_PORT", "9223")),
    )
