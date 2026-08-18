"""Execution Bridge -- the layer of V3 Sentinel that takes Signal
Engine's decisions and manages the actual MT5 order lifecycle. See the
project_v3_crypto_architecture memory note for the full layering
(Data Bridge -> Signal Engine -> Execution Bridge, Alert Manager beside
both as an independent observer).

Reads v3/signal_engine/trend_manager.py's own persisted state
(trend_manager_trade_state.json) as its source of truth for what should
currently be pending/open -- same interchange-file pattern used
throughout this system (tv_scraper's zone files feed both Alert Manager
and Signal Engine the same way). Never decides direction, entry price,
or SL itself -- purely reconciles real MT5 state against what Signal
Engine has already decided.

Trading disabled by default (EXECUTION_BRIDGE_ENABLE_TRADING=false) --
same convention as every other bot in this repo (see CLAUDE.md). With
it unset, every decision is printed but nothing touches the account.
"""
