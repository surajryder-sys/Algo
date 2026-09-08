"""Configuration for V5-Sentinel's own TradingView OB-zone scraper --
feeds the second Reversal Manager component. Own env var prefix, own
state file names, own CDP port and browser profile settings so this
runs as a fully self-contained V5-Sentinel process.

Deliberately narrow: no ATR/trend state file fields at all -- "MT5 data
is only for execution, ATR based" (user, 2026-09-07) -- this scraper's
only job is OB zone detection (formed/retested/top/btm) for the second
Reversal Manager component. LTF confirmation + SL still comes entirely
from the MT5 bridge, unchanged from STR.
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
        # User-supplied chart URL (2026-09-07) -- same OB indicator
        # (OBD_SecretTrader.pine) as the rest of this project's
        # TradingView-sourced data.
        chart_url=os.getenv("V5S_TV_SCRAPER_CHART_URL", "https://www.tradingview.com/chart/mL7E68j4/"),
        symbol=os.getenv("V5S_TV_SCRAPER_SYMBOL", "XAUUSD"),
        timeframe=os.getenv("V5S_TV_SCRAPER_TIMEFRAME", "5"),
        # The user's real, already-logged-in Brave profile -- confirmed
        # live 2026-09-09: a fresh, separate profile has no saved
        # TradingView login, defeating the point of "already logged in."
        # Chromium only ever lets ONE process hold a given profile
        # directory open at a time, so nothing else should point a
        # separate browser launch at this same profile while this
        # scraper is running.
        profile_dir=os.getenv("V5S_TV_SCRAPER_PROFILE_DIR",
                               r"C:\Users\ARK\AppData\Local\BraveSoftware\Brave-Browser\User Data"),
        poll_seconds=float(os.getenv("V5S_TV_SCRAPER_POLL_SECONDS", "5")),
        zone_state_file=os.getenv("V5S_TV_SCRAPER_ZONE_STATE_FILE", "v5s_tv_scraper_zones.json"),
        first_seen_state_file=os.getenv("V5S_TV_SCRAPER_FIRST_SEEN_FILE", "v5s_tv_scraper_first_seen.json"),
        retest_state_file=os.getenv("V5S_TV_SCRAPER_RETEST_FILE", "v5s_tv_scraper_retest.json"),
        live_snapshot_file=os.getenv("V5S_TV_SCRAPER_LIVE_SNAPSHOT_FILE", "v5s_tv_scraper_live.json"),
        mitigation_track_file=os.getenv("V5S_TV_SCRAPER_MITIGATION_TRACK_FILE", "v5s_tv_scraper_mitigation_track.json"),
        zone_history_log_file=os.getenv("V5S_TV_SCRAPER_ZONE_HISTORY_LOG_FILE", "v5s_tv_scraper_zone_history.jsonl"),
        browser_executable_path=os.getenv("V5S_TV_SCRAPER_BROWSER_PATH") or None,
        # 4 columns x 2 rows -- confirmed live 2026-09-09 via a screenshot
        # of the actual chart (H4/H2/H1/M30 across the top row, M15/M5/
        # M3/M1 across the bottom). An earlier, wrongly-guessed single-
        # column layout caused every pane to sample only 2 of the 8 real
        # panes repeatedly (always the center column of whichever real
        # row the click landed in), confirmed live before this fix.
        grid_rows=int(os.getenv("V5S_TV_SCRAPER_GRID_ROWS", "2")),
        grid_cols=int(os.getenv("V5S_TV_SCRAPER_GRID_COLS", "4")),
        # Window position/size tuned to this machine's real monitor
        # layout -- confirmed live 2026-09-09 that a smaller, untested
        # default window caused panes to overlap/misread each other
        # (documented failure mode, see project_tv_scraper_window_maximized
        # memory: "a shrunk window silently duplicates data across grid
        # panes with no error logged").
        window_x=int(os.getenv("V5S_TV_SCRAPER_WINDOW_X", "822")),
        window_y=int(os.getenv("V5S_TV_SCRAPER_WINDOW_Y", "-1440")),
        window_width=int(os.getenv("V5S_TV_SCRAPER_WINDOW_WIDTH", "3440")),
        window_height=int(os.getenv("V5S_TV_SCRAPER_WINDOW_HEIGHT", "1440")),
        cdp_port=int(os.getenv("V5S_TV_SCRAPER_CDP_PORT", "9223")),
    )
