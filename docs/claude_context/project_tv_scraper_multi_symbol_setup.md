---
name: project-tv-scraper-multi-symbol-setup
description: "tv_scraper runs 3 separate processes (BTCUSD, XAUUSD, ETHUSD), each its own browser window and state files, sharing one CDP port/profile; BTCUSD/ETHUSD grid is H2/H1/M30/M15/M5+M1(ATR-only) since 2026-08-29"
metadata: 
  node_type: memory
  type: project
  originSessionId: beca6970-4dba-4bca-8c27-e6b6f4892269
  modified: 2026-08-29T15:01:17.966Z
---

As of 2026-08-17, `v3/tv_scraper/scraper.py` runs as **three independent
processes**, one per symbol, each attached to the same Brave
profile/CDP port (9222) but each on its **own separate browser window**
(not a tab — see [[project_tv_scraper_window_maximized]]'s sibling
lesson: two processes sharing tabs in one window race for which tab is
frontmost and silently misread each other's data; confirmed live when
BTCUSD+XAUUSD briefly shared one window's tabs).

**BTCUSD** — the base `.env` config, no env var overrides needed to launch:
```
python -u -m v3.tv_scraper.scraper >> v3/tv_scraper_btcusd_run.log 2>&1
```
- Chart: `https://www.tradingview.com/chart/zrJmxwfd/`
- Grid: `TV_SCRAPER_GRID_ROWS=6`, `GRID_COLS=1` (vertical stack — the
  window on this monitor is tall/narrow, portrait-shaped, not wide, so
  a 1x6 horizontal layout doesn't fit; 6 rows x 1 col matches the actual
  window shape). **Updated 2026-08-29**: H4 removed entirely, replaced
  with M1 running ONLY the ATR Flip indicator (no OB detector on that
  pane) -- confirmed live, that pane correctly reads `bull=0 bear=0`.
  Timeframes top-to-bottom (order doesn't matter functionally -- each
  pane self-detects its own symbol/timeframe/content, see
  `v3/tv_scraper/config.py`'s own comment): **H2, H1, M30, M15, M5, M1**.
  Zone history is now naturally H2-M5 only, since M1's pane has no zone
  data to ever produce. This needed ZERO code/config changes -- pane
  count stayed 6 (H4 out, M1 in), only the physical TradingView chart
  layout changed.
- State files: the default names (`tv_scraper_zones.json`,
  `tv_scraper_live.json`, etc. — no symbol suffix).
- Window: user has moved this to a dedicated monitor and intends to
  leave it there permanently; keep maximized (same lesson as
  [[project_tv_scraper_window_maximized]]).
- Own log added 2026-08-29: `v3/tv_scraper_btcusd_run.log` (didn't exist
  before; process previously ran without one).

**XAUUSD** — needs explicit env var overrides to launch (own window):
```
TV_SCRAPER_CHART_URL="https://www.tradingview.com/chart/mL7E68j4/" \
TV_SCRAPER_SYMBOL="XAUUSD" TV_SCRAPER_GRID_ROWS=2 TV_SCRAPER_GRID_COLS=4 \
TV_SCRAPER_ZONE_STATE_FILE="tv_scraper_xauusd_zones.json" \
TV_SCRAPER_ATR_STATE_FILE="tv_scraper_xauusd_atr.json" \
TV_SCRAPER_FIRST_SEEN_FILE="tv_scraper_xauusd_first_seen.json" \
TV_SCRAPER_RETEST_FILE="tv_scraper_xauusd_retest.json" \
TV_SCRAPER_TREND_STATE_FILE="tv_scraper_xauusd_trend.json" \
TV_SCRAPER_LIVE_SNAPSHOT_FILE="tv_scraper_xauusd_live.json" \
TV_SCRAPER_MITIGATION_TRACK_FILE="tv_scraper_xauusd_mitigation_track.json" \
python -u -m v3.tv_scraper.scraper >> v3/tv_scraper_xauusd_run.log 2>&1
```
- Grid still 2x4 (8 timeframes: H4/H2/H1/M30/M15/M5/M3/M1) as of this
  note — has NOT been reduced to 6 like BTCUSD/ETHUSD. Ask the user if
  this should change to match.
- Own log: `v3/tv_scraper_xauusd_run.log`.

**ETHUSD** — same pattern as XAUUSD, reduced to 6 timeframes matching BTCUSD:
```
TV_SCRAPER_CHART_URL="https://www.tradingview.com/chart/bU7HOC3K/" \
TV_SCRAPER_SYMBOL="ETHUSD" TV_SCRAPER_GRID_ROWS=6 TV_SCRAPER_GRID_COLS=1 \
TV_SCRAPER_ZONE_STATE_FILE="tv_scraper_ethusd_zones.json" \
TV_SCRAPER_ATR_STATE_FILE="tv_scraper_ethusd_atr.json" \
TV_SCRAPER_FIRST_SEEN_FILE="tv_scraper_ethusd_first_seen.json" \
TV_SCRAPER_RETEST_FILE="tv_scraper_ethusd_retest.json" \
TV_SCRAPER_TREND_STATE_FILE="tv_scraper_ethusd_trend.json" \
TV_SCRAPER_LIVE_SNAPSHOT_FILE="tv_scraper_ethusd_live.json" \
TV_SCRAPER_MITIGATION_TRACK_FILE="tv_scraper_ethusd_mitigation_track.json" \
python -u -m v3.tv_scraper.scraper >> v3/tv_scraper_ethusd_run.log 2>&1
```
- Grid: 6x1 vertical, same layout change as BTCUSD 2026-08-29 -- H4
  dropped, M1 (ATR Flip indicator only, no zones) added: **H2, H1, M30,
  M15, M5, M1**. Same monitor resolution as BTCUSD's monitor (per
  explicit user request — window shape/geometry should match BTCUSD's).
- Own log: `v3/tv_scraper_ethusd_run.log`.

**2026-08-29 status**: BTCUSD and ETHUSD's tv_scraper processes were
both stopped earlier this session (part of a broader "stop all old v3
processes, only keep XAUUSD's scraper for V4" cleanup) and were just
relaunched fresh with the H4->M1 swap above -- confirmed live, all 6
panes read correctly on both (H2/H1/M30/M15/M5 with zones, M1 with
`bull=0 bear=0`). XAUUSD's own tv_scraper is the one still running
continuously for [[project_v4_xauusd_architecture]] and was NOT touched
by this change -- still its own 2x4/8-timeframe grid (H4/H2/H1/M30/M15/
M5/M3/M1) as of the last time that was checked; ask before assuming it
still matches, this note doesn't track it going forward.

**Restarting any one of these**: identify its PID pair via
`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where CommandLine -like '*tv_scraper*'`
cross-referenced with `Get-Process -Id ... | Select StartTime` (each
symbol's pair shares a start time distinct from the others) — do NOT
kill all tv_scraper processes when only one symbol needs restarting.

**All three `.env`/env-var grid and file settings are gitignored** (see
[[project_ob_mtf_bot_strategy]]-adjacent `.gitignore` entries for
`tv_scraper_xauusd_*`/`tv_scraper_ethusd_*`, added in commit
`2d8b5c5`) — nothing about this multi-symbol runtime setup lives in git
except those `.gitignore` lines and the underlying `mitigation_track_store.py`
persistence-fix code (commit `53174d3`). Reconfiguring grid/timeframes
for any symbol is a `.env`-or-launch-env-var change only, never a code
change.
