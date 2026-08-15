# Algo — multi-bot MT5 trading system

Each bot below lives in its own top-level folder and is fully independent:
own MT5 terminal connection (or explicitly shares one, noted below), own
magic number, own state/block files, own `.env` prefix. No bot imports
from another's folder except where noted. Every bot defaults to
`*_ENABLE_TRADING=false` (or equivalent) — nothing sends a real order
until that's explicitly set `true`.

## Bots

### algo_v2/ — XAUUSD, SMC (Order Block + ATR Trail)
The primary, most actively maintained bot. Reads the OB/ATR bridge
published by `mql5/OB_StatePublisher_Indicator_v2.00.mq5` (+
`OB_ATR_Bridge_Indicator_v1.00.mq5`) via `ob_bridge/`/`atr_bridge/`.
Both directions tried every cycle (zone's *effective direction* decides
eligibility, not a single fixed bias) — see `algo_v2/zone.py`.
Run: `python -m algo_v2.main`

### algo_v2_fx/ — 9 FX cross-pairs, H1 order-block pullback
One process loops over every symbol in `FX_SYMBOLS` each poll instead of
one bot per instrument. Pending-order entries only (no market orders),
plus trailing SL and a bias/opposite-OB exit once filled. Own dedicated
MT5 terminal.
Run: `python -m algo_v2_fx.main`

### algo_v2_usoil_btc_eth/ — USOIL + BTCUSD + ETHUSD, M5+M15
The **active** config for these three symbols — one merged process, one
shared MT5 connection, reads `mql5/OB_State_Multi_2.0.mq5` (same compiled
indicator on all three symbols' charts). Own per-symbol magic
number/state files; `ENABLE_TRADING` is all three or none.
Run: `python -m algo_v2_usoil_btc_eth.main`

### algo_v2_usoil/ — USOIL standalone (preserved snapshot)
**Not the active USOIL bot** — kept as the standalone, single-symbol
version exactly as it existed just before being merged into
`algo_v2_usoil_btc_eth` above, ready to run independently again if ever
needed. Day-to-day USOIL runs through the merged bot instead.
Run: `python -m algo_v2_usoil.main`

### v3/ — TradingView-sourced bot lineage (dry-run)
A separate, independent lineage: sources OB/ATR data entirely from
TradingView instead of an MT5-native indicator. Fully separate from
`algo_v2/` — no shared imports either direction.
- `v3/tv_scraper/` — polls a live TradingView chart's Data Window directly
  (pull), reading `v3/pine/OBD_SecretTrader.pine`'s plots.
- `v3/tv_bridge/` + `v3/tradingview_bot/` — receives/reads TradingView
  webhook alerts instead (push alternative to tv_scraper).
- `v3/algo_v2_tv_xauusd/` — algo_v2's exact XAUUSD strategy, fed by the
  above instead of the MT5 bridge. **Still dry-run** (`TVX_ENABLE_TRADING`
  unset) — no order has been sent from this bot yet.
Run: `python -m v3.tv_scraper.scraper`, `python -m v3.algo_v2_tv_xauusd.main`,
`python -m v3.algo_v2_tv_xauusd.event_watcher` (event/bias history logger,
no MT5 connection, runs independently of main.py).

## Shared support libraries (not standalone bots)

- **`atr_bridge/`** — reads `ATRSTATE_<symbol>_<tf>.json`, published by
  `mql5/OB_ATR_Bridge_Indicator_v1.00.mq5` via MT5's Common Files folder.
  Used by `algo_v2/`.
- **`ob_bridge/`** — reads `OBSTATE_<symbol>_<tf>.json`, published by
  `mql5/OB_StatePublisher_Indicator_v2.00.mq5`. Used by `algo_v2/`.
- **`mql5/`** — the MT5 indicator (`.mq5`) source files that publish the
  bridge files the Python bots above read. Compiled and attached to
  charts manually in MetaTrader — not run by Python.
- **`pine/`** — TradingView Pine scripts. `atr_trail_webhook.pine` /
  `ob_detector_webhook.pine` are the alert-push path `v3/tv_bridge`
  reads; `v3/pine/OBD_SecretTrader.pine` (moved under `v3/`) is the
  scrape-pull path `v3/tv_scraper` reads instead.

## Conventions worth knowing before touching any bot

- **Never** run a bot's live-trading process without the user's explicit
  go-ahead each time, even against a demo account — see this project's
  saved collaboration notes.
- A bot's own `main.py` docstring is the authoritative source for its
  exact behavior/entry rules — read it before assuming based on a
  similarly-named bot elsewhere in this repo; they diverge in real ways
  (e.g. algo_v2 vs the V1 `algo/` bot it replaced).
- State/log files are per-bot and gitignored (see `.gitignore`) — never
  committed, always regenerated at runtime.
