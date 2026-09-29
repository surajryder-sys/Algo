---
name: project_v5s_ict_grid_bot
description: "V5S-ICT -- standalone MQL5 EA (v5s_ict/V5S_ICT_EA.mq5), hedged ladder grid on M3 dual-ATR flip + CISD, separate sell/buy baskets; full agreed rule set (2026-09-25)"
metadata:
  node_type: memory
  type: project
  originSessionId: 55f6ad35-c61d-42ac-81be-09537ab7a106
  modified: 2026-09-24T20:37:05.472Z
---

V5S-ICT is a new standalone bot built as an **MQL5 EA** so the user can backtest it first in the Strategy Tester: `v5s_ict/V5S_ICT_EA.mq5`. It is NOT related to v5_sentinel's RM-ICT/TM-ICT despite the name. It computes the dual ATR (2x2 / 300x2) and CISD (AlgoAlpha port) internally, with no bridge files. No OBs for now (the user chose this explicitly). Targets a cent account later. Written and compiled 2026-09-25, not yet backtested or committed.

Rules agreed one question at a time (SELL basket shown; BUY is the mirror, each basket has its own magic, 26092501 sell / 26092502 buy):
- Signal TF M3, closed candles. Weak = close below both ATR lines, strong = close above both.
- Start: no basket + (flip-to-weak candle OR a bearish CISD while weak) -> sell 0.01.
- Bearish CISD after a hedge -> next ladder sell 0.02, 0.03 ... 0.10 (separate positions).
- Bearish CISD while the last sell is still unhedged -> extra 0.01 leg; the ladder doesn't advance.
- Bullish CISD OR flip to strong -> hedge buy = everything sold since the last hedge (e.g. 0.04+0.01 -> 0.05). Hedges stay open and belong to the basket. A repeat bullish CISD while already hedged is ignored.
- Strong state = the sell basket is frozen (no adds). It resumes where it stopped on the next weak state; the flip candle opens nothing for a resumed basket.
- After the 0.10 sell: stop adding; the next bullish CISD/flip closes the whole basket.
- Targets: a lone first 0.01 closes at +10 price points (=$10); otherwise the whole basket closes at +$10 while the ladder is <= 0.05, and at breakeven from the 0.06 step onward, then resets.

Backtest findings 2026-09-25 (XAUUSD, $1000; the user's MT5-5 tester run and a tick-level Python mirror of the EA that matched it event for event):
- Original rules: 1-23 Sep ended at -67% (tester). 144 of 152 baskets won about $10 each, and 8 baskets that reached the 0.10 cap lost -$1,904. Hedges lock losses in, and long hedges pay swap (about -$0.55 per 0.01 lot per night, triple on Wednesday).
- v1.10 inputs: BasketMaxLossMoney, MaxExtraLegs, NoTradeWindows / WeekOpenBlockMinutes / MaxSpreadPrice, UseHTFFilter, HedgeRelease. Defaults are loss 100 + 1 extra leg + time/spread, HTF off: Sep -5%, Aug 20-31 +41%.
- The M15 HTF filter made results WORSE (Sep $40), so it ships off.
- HedgeRelease scored best (+31% / +35%) but stops the ladder at step 1, so it is a different strategy. The user chose to try it (2026-09-25), so the EA defaults are now HedgeRelease ON + BasketMaxLossMoney 400. Continuous 20 Aug-24 Sep: $1000 -> $1626 (max DD $332, lowest equity $871); the ladder config reached $1382 and the old rules $576.
- The MT5-3/-2/-4 demo login 83111022 is expired, so the Strategy Tester can only run on MT5-5, and MT5-5 is live (V7S uses it). The user runs the backtests there themselves.

- 3-month sim (24 Jun-24 Sep, $1000): HedgeRelease+stop400 ended at $1450 (+45%), but max DD $839 and lowest equity $570. The ladder config ended at $518 (equity low $116), and the old rules would have blown the account. The HedgeRelease result swings a lot with the stop size (150: $853, 200: $1172, 300: $1045, 400: $1450; no stop blows up), so the edge is NOT robust yet. The 5-week tests had overstated both configs.

- CHOSEN CONFIG (user, 2026-09-25; later changed to LotUnit 0.20 + SignalTF M5, see below): cent account at 200,000 USC, HedgeRelease ON, NO basket stop (BasketMaxLossMoney 0), LotUnit 0.10, BasketTargetMoney 100 USC, plus 1 extra leg and the time/spread filter; these are now the EA defaults. 3-month sim: +20% (240,180 USC), worst drop 52,617 USC, lowest equity 151,755, max 9.0 lots open. P/L and drawdown scale linearly with LotUnit; around 0.38 it would wipe the account out. Not run live and not committed.

- M5 vs M3 (same config): M5 gets about the same profit with half the drawdown, so the EA default SignalTF is now M5. M5 6-month real-tick sim (1 Apr-24 Sep 2026): 200,000 -> 256,948 USC (+28.5%), worst drop 27,502, lowest equity 193,058, max 7.2 lots. Exness tick history only starts 2026-04-01, and MT5-5 maxbars=50000 (M5 only back to 2026-01-12), so a full-year test has to run in MT5-5's own Strategy Tester (bars from 2025-01-01).

- Lot x target grid (6-month M5 sim): what matters is the target-to-lot ratio. Targets of 500-1000 cents pile up lots and deepen drops faster than they add profit. Best profit per cent of drop is LotUnit 0.20-0.30 with a 100 USC target. EA DEFAULTS NOW (2026-09-25): SignalTF M5, LotUnit 0.20, BasketTargetMoney 100, FirstTargetPrice 10, no stop, HedgeRelease on. Sim expects 200,000 -> ~275,042 over 1 Apr-24 Sep; the user is verifying manually in the MT5-5 tester.

- Final ranking (sim, 13 Apr-24 Sep 2026, 0.20 lot, 200k USC, hedge release, no stop). Tier 1 safest: M5 dual-ATR flips+CISD at T100 (+37%, drop 23.7k, 8 lots) and M5 CISD + Supertrend(10,3) state filter at T200 (+53%, drop 33k). Tier 2: M3 CISD-only at T100 (+85%, 0 losing months; at T200+ equity fell to 31-47k, so keep 100). Tier 3 aggressive: M5 CISD-only T500 (+160%, but August alone +83%) and M3 CISD + ST(10,2) filter T500 (+104%). Supertrend replacing the dual-ATR flips lost to the dual ATR on M5 and to CISD-only on M3 at both multipliers 3 and 2. The user likes M5 dual-ATR+CISD and M3 CISD-only. The CISD-only and Supertrend modes exist only in the sim (UseFlips/UseState/StateSource), NOT in the EA yet.

- User decision (2026-09-25): DROP M5 CISD-only at T500. It's not consistent (August alone gave +83%). Shortlist: (1) M5 dual-ATR flips+CISD at T100 = safest; (2) M3 CISD-only at T100 = most consistent. Also rejected after testing: the user's "flip decides structure / unlimited CISD adds / hedge only on flips" rules (hedges kept wiped the account out; released only +17-20% with big drops) and Supertrend replacements (mult 2 and 3). For quick checks use a single tough month (May 2026, ~2 min) before running 6 months.

- 2-YEAR TESTS (Oct 2024-Sep 2026; 1-minute-bar sim from MetaTrader 5's Exness-MT5Real7 .hcc history, ~25-35% optimistic on profit, constant 0.09 spread): M3 CISD-only wiped out (959 lots). M5 dual-ATR survived only at 0.20/100, and 11 of 12 nearby lot/target settings blew up (fragile, luck). Lot caps that BLOCK new legs made it worse. The fix is an EMERGENCY CLOSE of the whole basket at N lots: at 3-4 lots all 12 lot/target settings survived. User's IST windows (23:00-04:00, 17:56-18:05, 18:56-19:05 no trade/no release; 22:30-23:00 no new basket) cost ~10-20 pts of profit in the sim (which can't model spread spikes).
- FINAL EA DEFAULTS (2026-09-25, user: "no risk no money"): M5 dual-ATR+CISD, hedge release, LotUnit 0.30, BasketTargetMoney 500, EmergencyBasketLots 4, UseTradeWindows=false (user said default off), 300,000 USC deposit. 2y sim: +84%, worst drop 64k, worst month -5.2%. Compiled and installed in MT5-5, not committed, not run live.

- REALITY CHECK (real ticks, 1 Apr-24 Sep 2026, 0.30 lot, 300k): the emergency-close configs make little. E4/T500 +9.2%, E4/T100 +6.2%, E3/T500 +3.8%, E3/T100 -3.8%. The 1-minute-bar sim overstated E4/T500 about 4x (+35.7%), so the 2-year +84% is NOT credible. Without an emergency close, 0.20/100 made +37% on the same real ticks but blows up over 2 years. There is no robust, meaningfully profitable config yet.
- Live target: the user's "MetaTrader 5" terminal = REAL Exness cent account 263602422 (Exness-MT5Real37, USC, balance ~34,638, hedging, symbol XAUUSDc with contract 1, AutoTrading enabled). EA + ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar indicator copied and compiled there 2026-09-25. EA defaults (0.30 lot) are sized for 300k and far too big for 34.6k; scaled equivalent is 0.03 / target 50 / emergency 0.4. NOT attached, and nothing traded.

**How to apply:** treat this rule list as the spec; the EA header mirrors it. See [[feedback_algo_trading_collab]] (one question at a time) and [[feedback_vantage_research_data]] (never use the Vantage EA as a reference).
