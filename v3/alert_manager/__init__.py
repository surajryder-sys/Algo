"""Alert Manager -- an independent observer, not part of the Signal
Engine / Execution Bridge decision chain. See the project architecture
note (memory: project_v3_crypto_architecture) for how this fits: reads
the Data Bridge's State Store (tv_scraper's zone files) and MT5's own
live tick price independently, and never gates or feeds into any
trading decision. Deliberately decoupled so it can't slow down or break
the trading path, and keeps working even if Signal Engine has a bug.
"""
