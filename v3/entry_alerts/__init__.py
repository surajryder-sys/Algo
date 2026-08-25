"""Entry-execution alerts -- independent observer, peer to
v3/alert_manager/ (retest alerts) and v3/profit_alerts/ (profit-
milestone alerts), with its own separate Telegram bot
("SecretTrader_EntryBot", added 2026-08-25). Fires ONE alert the first
time a Trend Manager trade actually fills (a real open MT5 position
appears carrying Trend Manager's own magic number) -- symbol, direction,
entry price, lot size, SL, ticket. Read-only against MT5 -- never
places, modifies, or closes anything.

Scoped to Trend Manager only (not Reversal Manager) per the user's own
words -- "we need create one more bot for trade manager."
"""
