"""Signal Engine -- the layer of V3 Sentinel that decides *whether/what*
to trade, reading the Data Bridge's state (v3/tradingview_bot/'s
ZoneStore) but never touching MT5 itself. See the
project_v3_crypto_architecture memory note for the full layering
(Data Bridge -> Signal Engine -> Execution Bridge, Alert Manager beside
both as an independent observer).

Trend Manager (trend_manager.py) is the first Manager built here.
Reversal Manager and No Trade Manager are the other two Managers that
belong in this layer, not built yet.
"""
