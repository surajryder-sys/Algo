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
        # Raw, uninterpreted current-Data-Window mirror -- see
        # live_snapshot_store.py's own docstring for why this is separate
        # from zone_state_file/atr_state_file (those build an interpreted
        # history; this is just "what's on screen this poll").
        live_snapshot_file=os.getenv("TV_SCRAPER_LIVE_SNAPSHOT_FILE", "tv_scraper_live.json"),
        # Mitigation-detection tracking state (which price_keys were seen
        # last poll, missing-poll streaks, pending 2-poll confirmation
        # gates) -- see mitigation_track_store.py's own docstring for why
        # this needs to survive a restart now that ZoneStore.apply_mitigated
        # deletes zones instead of just flagging them.
        mitigation_track_file=os.getenv("TV_SCRAPER_MITIGATION_TRACK_FILE", "tv_scraper_mitigation_track.json"),
        # Append-only record of every zone ever seen newly formed --
        # separate from zone_state_file, which only holds CURRENTLY LIVE
        # zones (deleted on mitigation). See zone_history_log.py's own
        # docstring for why this exists: 2026-08-19, after "where did
        # that OB come from" couldn't be answered because the zone
        # involved had already been mitigated and dropped from the live
        # state file by the time the question came up.
        zone_history_log_file=os.getenv("TV_SCRAPER_ZONE_HISTORY_LOG_FILE", "tv_scraper_zone_history.jsonl"),
        browser_executable_path=os.getenv("TV_SCRAPER_BROWSER_PATH") or None,
        # Pane grid on the chart layout at chart_url -- e.g. 2x2 for a
        # 4-pane grid (M1/M3/M5/M15, one per pane), each pane self-detecting
        # its own symbol/timeframe from the Data Window (see run_once_pane).
        # Was hardcoded to a 1x2 (left/right) split before this; default
        # kept at 1x2 so existing setups don't change behavior unannounced.
        grid_rows=int(os.getenv("TV_SCRAPER_GRID_ROWS", "1")),
        grid_cols=int(os.getenv("TV_SCRAPER_GRID_COLS", "2")),
        # Explicit window position/size instead of --start-maximized --
        # confirmed live: forcing full-screen on every launch fights a user
        # who deliberately keeps this browser pinned to half their monitor
        # (to watch it alongside Claude Code), and worse, an actual resize
        # DURING scraping (the window reflowing while _focus_pane's click
        # math -- which reads window.innerWidth/innerHeight fresh every
        # click -- has already moved on to the new size) is a real,
        # confirmed cause of panes briefly reading each other's data.
        # Launching pre-sized to a fixed, known rectangle avoids both:
        # nothing needs to resize after launch. Defaults are the left half
        # of this machine's primary monitor (3440x1440, 1392px usable
        # height below the taskbar) -- override via env if the monitor or
        # desired placement changes.
        window_x=int(os.getenv("TV_SCRAPER_WINDOW_X", "0")),
        window_y=int(os.getenv("TV_SCRAPER_WINDOW_Y", "0")),
        window_width=int(os.getenv("TV_SCRAPER_WINDOW_WIDTH", "1720")),
        window_height=int(os.getenv("TV_SCRAPER_WINDOW_HEIGHT", "1392")),
        # CDP (Chrome DevTools Protocol) port -- scraper.py now CONNECTS to
        # an already-running Brave instance on this port instead of always
        # launching (and exclusively locking) a fresh one via
        # launch_persistent_context. Confirmed live this was worth doing:
        # every "profile already in use" crash this session (forcing a
        # kill of ALL Brave processes, including the user's own unrelated
        # tabs, just to free the lock) came from exactly this exclusivity.
        # Attaching over CDP instead means the same browser window can be
        # shared -- the user can look at / interact with it directly, and
        # restarting tv_scraper for a code change no longer requires
        # closing and reopening the browser at all.
        cdp_port=int(os.getenv("TV_SCRAPER_CDP_PORT", "9222")),
    )
