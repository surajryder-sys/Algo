---
name: project_v6s_ict_btc
description: "V6S-ICT on BTCUSD -- parked 2026-09-29 (user will come back); DZ-only M30 is the only part that works; best config, all test results, and what the EA would need"
metadata:
  node_type: memory
  type: project
  originSessionId: 55f6ad35-c61d-42ac-81be-09537ab7a106
  modified: 2026-09-28T19:52:25.183Z
---

User asked to test the live XAUUSD EA ([[project_v6s_ict_ea]], v2.10) on BTCUSD (~82,000) on 2026-09-29, then PARKED it: "save this, let me come back later". Nothing built; no BTC EA exists yet.

**Data / costs used (sim only, 1-min bars, no real ticks):** Exness Real7 BTCUSD .hcc (to 2026-07-07, terminal 2A968AC344F5A9A1B3E3DB21B82AC033) + Exness Trial12 (to 2026-09-26, terminal 4DB333B24A74B726D7AA441A9D0137DC), period Oct 2024 - Sep 2026, weekends included. BTCUSDc on the cent account: 1.00 lot = 1 USC per $1, spread $10, swap long ~-18.9 USC/lot/day. Tested at 2.00 lots (same USC risk per trade as 1.00 lot gold), start 77,581 USC. Point settings scaled from gold by volatility: median daily range BTC 2,866 vs gold 55.5 = 51.6x.

**Findings:**
- Full EA (both slots, x50): only +23,778 over 2y, drop 39.5%, 12/24 losing months; aligning reversals/breakouts ~0 on BTC. DZ is the only part with an edge.
- DZ-only: trigger M30 CISD >> M15. M30 at x35/x50/x75 ~+72k..+81k (robust across scales).
- Targets (M30): x35 fixed 1:1 best overall (+103,740, drop 15.6%, 6/24 losing) but nearest-level target stronger in the recent 6 months (x50 LEVEL +44.5k recent vs ~+23k for 1:1); 1:2/1:3 worse.
- Per trade (x35 1:1): avg +169 pts (win +1,093 / loss -1,035), median stop 855 pts, losing days 44%, worst day -7,961 USC. Weekends positive (28 trades, +15.2k). Sessions IST: New York 18:30-01:30 carries it (+99k), late night 01:30-05:30 +17k, Asia +7k, London 12:30-18:30 -19.6k.
- London-off filter (no new entries 12:30-18:30 IST): x35 1:1 +103,740 -> +115,983 (drop 14.8%); gains only in the older period, recent neutral -- modest, not proven. User asked why the gain < London's -19.6k: one-trade slot frees up and replacement trades partly lose.

**Best candidate if resumed:** DZ only, M30 CISD, x35 (SL buffer 140, cap 2,625, TP buffer 35, min TP 35, capped trades need >= 1R), fixed 1:1 TP, London entries off -> +115,983 / 2y at 2.00 lots, drop 14.8%, 6/24 losing months, ~1 trade/day.

**EA changes needed to run it:** a DZ trigger-timeframe input (M15 is hard-coded), a switch to turn off aligning-level REVERSALS (only UseBreakouts exists), a fixed-RR TP option for DZ, a London no-entry window; BTC-sized defaults. Scratchpad sims (bt_dz3.py, btc_m1.npy) are session-temp and will likely be gone -- rebuild data with the build2y.py-style .hcc parser (price filter 5,000-500,000, spike thresholds x20).

**Aligning-level BREAKOUTS only on BTC (2026-09-30):** trigger M5/M15/M30 x scale x20/x35/x50 (2.00 lots, 2y): M5 -13k..-62k, M15 -0.4k..-26k, M30 x20 -4.4k; only M30 x35/x50 positive (+9.7k / +8.3k) but on just 19-21 trades in 2 years -- too few to trust. Conclusion: aligning breakouts add nothing reliable on BTC; keep BTC as DZ-only.

**ETHUSD test (2026-09-30, sim, Oct 2024 - 27 Aug 2026, 1-min bars):** data Exness Real7 ETHUSD (terminal 18BADC67994D2B45C82D43724355D20B, to 2026-07-27) + Trial12 (4DB333..., to 2026-08-27). ETHUSDc: 1.00 lot = 1 USC per $1, min lot 0.10, spread $1.00, swap long ~-0.61 USC/lot/day. Vol ratio ETH/gold daily range 134.8/54.7 = 2.46x -> settings x2.5; 40 ETHUSDc lots = same USC risk as 1.00 gold lot; start 77,581 USC. Full EA x2.5: +118,330 but recent -27,434, drop 28%; reversals -17k, aligning breakouts +38k (old only), DZ M15 +98k (recent -9k). **Best: DZ only, M30 trigger, NEAREST-LEVEL target** -- x1.75 +85,014 (drop 22.5%, 5/23 losing, old +71.2k, recent +13.8k); x2.5 +78,772; x3.5 +75,112 (recent +19.3k) -- the only configs positive in the recent period. Fixed 1:1 on ETH is weak recently (unlike BTC). M15 DZ loses recently at every setting. Aligning breakouts only: M15 +18.6k on 46 trades, M30 -11.4k -- not usable.

**USOIL test (2026-09-30, sim, Jan 2025 - 7 Aug 2026, 1-min bars):** data Exness Trial7 USOIL (terminal 4DB333..., to 2026-03-02) + Trial12 (2A968..., to 2026-08-07). USOILc: 1.00 lot = 1,000 USC per $1, spread 0.02, swap long 0 / SHORT -186.2 USC per lot per night (huge: ~0.19 $/bbl). Vol ratio 0.029x gold -> settings x0.03 (cap $0.60), 3.30 lots, no weekend carry (force close Fri 20:00 UTC, no entries from 19:00). Results: aligning M5 reversals +2k / breakouts +29k (drops ~40%); M15/M30 aligning weak/few trades; FULL EA -47,785 (drop >100%); DZ-only loses almost everywhere (M5/M15 -6k..-244k, drawdowns >100%); best M30 DZ nearest-level x0.042 +14,564 but drop 69%. Even with zero swap: DZ M15 -26.6k, M30 +35.8k. Conclusion: strategy does NOT suit USOIL.
