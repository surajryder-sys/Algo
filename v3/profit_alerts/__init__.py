"""Profit-milestone alerts -- independent observer, peer to
v3/alert_manager/ (retest alerts) and part of V3 Sentinel, but with its
own separate Telegram bot ("SecretTrader_Critical_Bot", added
2026-08-25) so profit pings never get lost in the same feed as retest
alerts. Reads MT5 positions directly (read-only -- never places,
modifies, or closes anything), filtered to this system's own trades by
magic number (Trend Manager's + Reversal Manager's). Sends one alert
per (position, milestone) the first time that position's floating
profit reaches each configured points threshold.
"""
