"""Profit-milestone alerts for V4 -- V4's own copy, isolated from V3
(see CLAUDE.md / v4/bridge/tv_zones.py's own comment: "V4 does not
import from v3's folder (or vice versa)"). V3 is fully stopped and no
longer maintained (2026-08-28 -- complete switch to V4), so this
replaces v3/profit_alerts/ as the live profit-alert bot going forward;
that module is left in place, untouched, not deleted (same preserved-
snapshot convention as algo_v2_usoil).

Reuses the SAME Telegram bot as v3/profit_alerts/ did
(SecretTrader_Critical_Bot, PROFIT_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID) --
user's own explicit call, "the bot will be critical bot mentioned key
already." Read-only against MT5 -- never places, modifies, or closes
anything. Watches only V4's own trades, matched by magic number:
V4 XAUUSD Trend Manager (V4_MAGIC_NUMBER) and V4 crypto Trend Manager
(CRYPTO_TM_MAGIC_NUMBER, BTCUSD+ETHUSD). Milestones unchanged from V3:
XAUUSD 12/25, BTCUSD 500/1000, ETHUSD 20/40 points, each its own
separate alert.
"""
