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
        # SAME real Brave profile as TV_SCRAPER_PROFILE_DIR (v3's own) --
        # confirmed live 2026-09-09: a fresh, separate profile has no saved
        # TradingView login, defeating the point of "already logged in."
        # Own CDP port (below) still keeps this launch independent in
        # principle, but since both point at the SAME profile directory,
        # Chromium only ever lets one of the two scrapers hold it open at
        # a time -- don't run v3's and v5's tv_scraper simultaneously
        # until/unless this needs revisiting (e.g. a second, separately-
        # logged-in Brave profile).
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
        # M3/M1 across the bottom). The original 6x1 default here was
        # blindly copied from v3's own different chart layout ("everything
        # is same" was about the Pine script/indicator, not this chart's
        # specific grid shape) -- wrong dimensions against this real 4x2
        # layout caused every pane to sample only 2 of the 8 real panes
        # repeatedly (always the center column of whichever real row the
        # click landed in), confirmed live before this fix.
        grid_rows=int(os.getenv("V5S_TV_SCRAPER_GRID_ROWS", "2")),
        grid_cols=int(os.getenv("V5S_TV_SCRAPER_GRID_COLS", "4")),
        # Matches TV_SCRAPER_WINDOW_* (v3's own proven, already-tuned-to-
        # this-machine's-real-monitors values) -- confirmed live 2026-09-09
        # that v5's original smaller default window caused panes to
        # overlap/misread each other (documented failure mode, see
        # project_tv_scraper_window_maximized memory: "a shrunk window
        # silently duplicates data across grid panes with no error
        # logged").
        window_x=int(os.getenv("V5S_TV_SCRAPER_WINDOW_X", "822")),
        window_y=int(os.getenv("V5S_TV_SCRAPER_WINDOW_Y", "-1440")),
        window_width=int(os.getenv("V5S_TV_SCRAPER_WINDOW_WIDTH", "3440")),
        window_height=int(os.getenv("V5S_TV_SCRAPER_WINDOW_HEIGHT", "1440")),
        # Own CDP port, distinct from v3 tv_scraper's default 9222 -- lets
        # both run simultaneously as fully independent browser instances.
        cdp_port=int(os.getenv("V5S_TV_SCRAPER_CDP_PORT", "9223")),
    )
