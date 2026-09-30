//+------------------------------------------------------------------+
//|                                                  V6S_ICT_EA.mq5  |
//| V6S-ICT v1 -- Aligning Support/Resistance + M5 CISD reversal EA  |
//| (designed with the user 2026-09-27, rules agreed one at a time). |
//|                                                                    |
//| LEVELS: Major/Minor Support/Resistance (ZigZag pivots, PivotPeriod |
//| 5) computed on H4, H2, H1, M30, M15, M10, M5 and M3 -- ported from |
//| the Major/Minor block of Dynamic_Zones_CISD_MajorMinor.mq5 (itself |
//| verbatim from ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar).   |
//| Each timeframe exposes its current Major Support, Minor Support,   |
//| Major Resistance and Minor Resistance.                             |
//|                                                                    |
//| ALIGNING LEVEL: a support (or resistance) value that is EXACTLY    |
//| the same price on 3+ DIFFERENT timeframes (MinAlignTimeframes;     |
//| 2-TF alignments -- mostly M3+M5 sharing a swing -- lost money in   |
//| the Aug 24-Sep 24 test: 3+ TFs doubled net and cut the drop 43%).  |
//| AND at least MinMajorTimeframes (2) of those timeframes must have  |
//| it as a MAJOR level (H1 Maj + M30 Maj + M15 Min = valid; M15 Maj + |
//| H1 Min + M30 Min = not valid). 6-month real-tick test, M5 CISD,    |
//| no brake: 268 trades, +2,343, drop 846 (was +274 / 1,827).         |
//| only the support/resistance side must match). Same exact-match     |
//| rule as v7_sentinel/major_minor_watcher.py.                        |
//|                                                                    |
//| BUY SETUP (sell is the mirror):                                    |
//|   1. an M5 candle's low comes within TouchBufferPrice (2.0) of an  |
//|      aligning support, or goes below it (a TOUCH) -- price often    |
//|      reverses just short of the exact level;                       |
//|   2. on a LATER M5 candle a bullish CISD confirms AND that candle  |
//|      closes ABOVE the support -> buy LotSize at market;            |
//|   3. valid only while the level is still an aligning support.      |
//|   SL = lowest low of the candles AFTER the touch candle, up to and |
//|        including the CISD candle, minus SLBufferPrice.             |
//|   At +BreakevenR x risk -> SL to entry.                            |
//|   At +PartialR x risk   -> close everything except RunnerLots.     |
//|   No fixed TP: a SELL setup (touch of an aligning resistance +     |
//|   later bearish CISD closing below it) closes the buy and opens    |
//|   the sell. If price breaks through the level, the buy just runs.  |
//|   M10 AFTER SL (M10AfterSL, default ON, user 2026-09-27): when a   |
//|   trade from an aligning level hits its stop loss, THAT level's    |
//|   next entry needs an M10 CISD (after a fresh touch, M10 candle    |
//|   closing beyond the level) instead of M5. Every other level keeps |
//|   using M5. The flag clears once that level trades again. 6-month  |
//|   test: +274 -> +1,227, the 46 M10 re-entries alone +647.          |
//|                                                                    |
//|   LOSING-STREAK BRAKE (off by default now): after LossStreakBrake  |
//|   stop-losses in a                                                 |
//|   row in one direction, that direction is paused until a trade in  |
//|   the OTHER direction opens; a winning trade resets the count;     |
//|   breakeven stops don't count. 6-month real-tick test (Apr-Sep     |
//|   2026, 0.06): +274 -> +1,774, worst drop 1,827 -> 1,242.          |
//|   One trade at a time; a same-direction setup while in a trade is  |
//|   ignored.                                                         |
//|                                                                    |
//| CISD: same confirm/discard logic as CISD_AlgoAlpha.mq5 (swing/     |
//| sweep drawing dropped -- they never affect confirmation), on M5.   |
//|                                                                    |
//| DYNAMIC ZONES: computed (D1 open +/- half the 5/10-day average     |
//| range, verbatim from Dynamic Zones.mq5) and logged, NOT used by    |
//| any rule yet -- the user will add that logic later.                |
//|                                                                    |
//| CHART: in the visual tester (and live) every timeframe's Major/    |
//| Minor lines, the aligning levels, touches, entries, breakeven,     |
//| partial closes and the Dynamic Zones are drawn (ShowOnChart).      |
//|                                                                    |
//| DEFAULTS v1.50 ("setup D", user 2026-09-27): 2-Major alignment,    |
//| M10 after a level's SL, brake 2, breakeven 1:2, partial 1:7.       |
//| Real-tick test 1 Apr-24 Sep 2026, 0.06 lot: 188 trades, +4,911,    |
//| worst drop 922, lowest -835; only April negative (-723). Partial   |
//| grid (BE 1:2): 1:3 +3,110 / 1:4 +3,775 / 1:5 +4,040 / 1:6 +4,421 / |
//| 1:7 +4,911 / 1:10 +3,335 / none +3,458. Oct 2025-Mar 2026 could    |
//| only be tested on 1-minute bars (pessimistic for this EA): the     |
//| full year with 1:3 was +1,547 for setup D vs -2,104 without.       |
//|                                                                    |
//| v2.15 (user 2026-09-30): TRAP TRADE ("T6"). When the auto square-  |
//| off closes a reversal or breakout trade (an opposite M5 CISD       |
//| closing back beyond its aligning level = the setup failed / the    |
//| trader is trapped), the EA immediately enters the OTHER way:       |
//| SL = the failed level +/- TrapSLBuffer (4.0), TP fixed at TrapTPR  |
//| (1:2) x risk, lot = LotSize, magic = MagicNumber (same slot). Only |
//| while the original trade is still open (a trade already closed by  |
//| SL / breakeven is not flipped). The trap trade itself is never     |
//| flipped again (its own square-off just closes it); BE at           |
//| BreakevenR, SL cap MaxSLPrice, opposite setups and extra legs work |
//| as for any trade. Not blocked by the DZ filter / night block.      |
//| Comment "V6SICT TB/TS <level>". Input UseTrapTrade. Sim 2y, cent   |
//| 1 lot: +667,620 -> +685,035, drop 7.0% -> 7.0%, no losing month,   |
//| 42 trap trades, 43% win, +15,000 (old +6,584 / recent +8,416);    |
//| $5k USD 0.06: +40,223 -> +41,169. Rejected: buffer 0.5/2, TP at   |
//| aligning level, trap also 1h after SL/BE (adds a losing month),    |
//| traps on DZ trades (-7k), traps from 2-TF levels (drop 7.2%).      |
//|                                                                    |
//| v2.14 (user 2026-09-30): SESSION TP FOR REVERSALS. A reversal     |
//| trade's TP = the nearest beyond price of (a) the previous gap-      |
//| session's high (buy) / low (sell) and (b) the current session's     |
//| high / low so far (closed M5 candles), -/+ TPBufferPrice; falls     |
//| back to the aligning level if neither is ahead; the 1:1-under-1R   |
//| rule still applies; re-checked every M5 candle. Breakouts keep the |
//| aligning TP. Input UseSessionTPRev. Sim (v2.13 config, 2y):         |
//| +648,359 -> +667,620, drop 7.7% -> 7.0%, no losing month, reversal |
//| win rate 25% -> 36% (S1 prev-session-only +666,521; session TP on  |
//| breakouts too tested much worse: +531,180).                        |
//|                                                                    |
//| v2.13 (user 2026-09-30): DYNAMIC ZONES FOLLOW THE GAP-SESSION     |
//| RULE of the fixed indicator (mql5/Dynamic_Zones_CISD_MajorMinor): |
//| a new session starts wherever two consecutive H1 bars are more    |
//| than 1 hour apart (gold: the daily break -> ~03:30 IST, adjusts    |
//| itself to DST / holidays). Zone anchor = that session's first H1   |
//| open; half ranges = average High-Low of the previous 5 / 10        |
//| COMPLETE sessions. v2.05-v2.12 used plain D1 bars (00:00 server =  |
//| 05:30 IST), which anchored the zones 2 h late and averaged calendar|
//| days. Applies to the DZ breakout slot, the DZ filter and the drawn |
//| lines. No other change.                                            |
//|                                                                    |
//| v2.12 (user 2026-09-29): EXTRA LEG. When an aligning-level trade  |
//| is IN PROFIT and a new SAME-SIDE setup (reversal or breakout)      |
//| fires, one extra leg opens (own magic LegMagicNumber 26092731,     |
//| same lot, the setup's SL capped at MaxSLPrice, TP ExtraLegTPR 2.0 |
//| x its risk) and the FIRST trade's SL moves to breakeven. Max one  |
//| extra leg. An opposite setup / auto square-off that closes the     |
//| first trade closes the leg too. Not when the first trade is in     |
//| loss. Sim (v2.11 live config, 2y): +623,500 -> +657,720, both      |
//| periods better, drop 11.8% -> 11.9% (7 leg variants tested).       |
//|                                                                    |
//| v2.11 (user 2026-09-29): no new aligning-level REVERSAL entries   |
//| between RevBlockFrom and RevBlockTo IST (01:30-05:30; thin late-US |
//| / pre-Asia hours). Breakouts, DZ trades and trade management are   |
//| unaffected; a blocked reversal does not close an open trade. IST = |
//| server time + ServerToISTMinutes (330: Exness server = UTC). Sim,  |
//| cent_1lot 2y: +581,730 -> +623,500, both periods better, drop      |
//| 13.4% -> 11.8%, losing months 2 -> 1 (Monday block tested, worse).|
//|                                                                    |
//| v2.10 (user 2026-09-28): a CAPPED DZ trade (zone SL moved to 75)  |
//| is skipped if its TP is less than DZCappedMinR (1.0) x risk --     |
//| never risk 75 for a smaller target (live 28 Sep 21:45 IST: DZ sell |
//| risked 75 for 5.7). Uncapped DZ trades unchanged. Sim 0.10 lot, 2y:|
//| +28,542 -> +30,468 (18 capped trades skipped, same drops).         |
//|                                                                    |
//| v2.09 (user 2026-09-28): DZ stop CAP instead of skip. A DZ trade  |
//| whose zone-line SL is more than DZMaxSL (75) away is now TAKEN     |
//| with the SL moved to 75 from entry (v2.05-v2.08 skipped it; live   |
//| 28 Sep: three DZ sells skipped at 83-93 pts). Sim 0.10 lot, 2y:    |
//| skip-75 +25,034 -> cap-75 +28,542 (drops 2,810/973 -> 2,810/1,234, |
//| worst loss -535 -> -753 recent). Caps 50/40/30/20 tested worse     |
//| (+22,591 / +23,596 / +21,353 / +14,624).                           |
//|                                                                    |
//| v2.08 (user 2026-09-28): crash fix. Changing the chart timeframe  |
//| (or symbol / inputs) re-runs OnInit but MT5 KEEPS the EA's global  |
//| variables, so old candle/CISD/swing arrays survived next to fresh  |
//| counters -> "array out of range" (live v2.05 on XAUUSDc, 28 Sep    |
//| 20:21 IST, chart switched to M3). OnInit now calls ResetState(),   |
//| which clears every piece of in-memory state (candles, CISD queues, |
//| swings, breakouts, levels, M10/M15 CISD, DZ, tracking) before the  |
//| normal warmup + replay. No rule changes.                           |
//|                                                                    |
//| v2.07 RESTART SAFETY (user 2026-09-28) -- no rule changes:        |
//|  1. per-trade data (original SL, level, type, partial flag) is     |
//|     flushed to disk the moment it is written; if it is still       |
//|     missing after a crash, it is rebuilt from the position (level  |
//|     and type from the order comment, original SL from the opening |
//|     order, partial = volume below the opening volume).             |
//|  2. at start the candles of the current D1 session (at least       |
//|     BreakoutValidBars+1, at most ReplayMaxBars) are replayed       |
//|     through the setup logic WITHOUT orders, so touches, pending    |
//|     breakouts and the DZ filter are rebuilt; candles missed during |
//|     a disconnection are replayed the same way.                     |
//|  3. only a candle that closed within MaxSignalDelaySec (120 s)     |
//|     can open a trade -- nothing is traded late after a restart.    |
//|  Extra work: a fraction of a second at start; nothing per tick.    |
//|                                                                    |
//| v2.06 TP DISTANCE RULES (user 2026-09-28, after a live DZ sell on |
//| 28 Sep opened with its TP 0.2 from entry and closed at once):      |
//|  - aligning-level trades: if the nearest opposite aligning level   |
//|    is closer than MinTPR (1.0) x the initial risk from entry, the  |
//|    TP is 1:1 instead (MinTPR 0 = off). Sim +23,569 -> +26,238 / 2y.|
//|  - DZ trades: skipped if the nearest aligning level (the TP) is    |
//|    less than DZMinTPPoints (1.0) from price. Sim +25,034 ->        |
//|    +24,952 (removes ~55 zero-reward trades in 2 years).            |
//|    Bigger DZ minimums / next-level / 1:1 fallbacks all tested      |
//|    worse -- DZ's edge is the close targets.                        |
//|                                                                    |
//| v2.05 DYNAMIC ZONES BREAKOUTS (user 2026-09-28): a second,        |
//| independent strategy in its own slot (DZMagicNumber 26092721), so  |
//| a DZ trade and an aligning-level trade can be open together.       |
//| Zones per D1 session: resistance Z1-Z2, support Z3-Z4 (open +/-    |
//| half the 5/10-day average range). BUY when an M15 candle with a    |
//| bullish M15 CISD closes above the upper resistance line; SL = the  |
//| lower resistance line - DZSLBuffer (4.0). SELL mirrors it below    |
//| the lower support line, SL = upper support line + 4.0. Skipped if  |
//| the SL is more than DZMaxSL (75) away. TP = nearest aligning level |
//| beyond entry -/+ TPBufferPrice (1.0), else 1:1; fixed at entry.    |
//| An opposite DZ setup squares the open DZ trade off. Re-entries     |
//| allowed while price stays beyond the zone. No BE / partial / DZ    |
//| filter for these trades. Sim 0.10 lot: Oct24-Mar26 +16,024 (drop   |
//| 2,810), Apr-Sep26 real +9,009 (drop 973) = +25,034; together with  |
//| the aligning-level trades ~+48,600 over 2 years (monthly corr 0.12,|
//| combined closed-trade drop 2,527 / 1,256, 3 losing months of 25).  |
//|                                                                    |
//| v2.04 (user 2026-09-28): lots swapped -- breakouts 0.10, reversals |
//| 0.08 (breakouts earn more). Sim: Oct24-Mar26 +16,270 (drop 1,264), |
//| Apr-Sep26 real +7,299 (drop 1,437) = +23,569 over 2 years.         |
//|                                                                    |
//| v2.03 "OPTION D" (user 2026-09-28): breakouts are taken only if   |
//| the opposite-level TP is at least MinRRBreakout (1.5) x risk away  |
//| (no opposite level = allowed); reversals unfiltered. LotSize 0.10, |
//| BreakoutLots 0.08. M10-after-SL and the losing-streak brake OFF.   |
//| Sim with costs ($3.5/lot/side comm, 0.20 min spread, swap), $5k:   |
//| Oct24-Mar26 (1-min) +14,576, drop 1,434; Apr-Sep26 real ticks      |
//| +7,812, drop 1,624; 2 years +22,387 (M10+brake on: +21,585).      |
//| No news filter, no trailing (both tested, no better).              |
//|                                                                    |
//| v2.02 STOP CAP (user 2026-09-28): no stop is ever more than        |
//| MaxSLPrice (20.0) from entry -- if the rule's SL (post-touch low / |
//| breakout CISD swing) is further, the SL is placed at 20. Breakout  |
//| SLs from the CISD swing were median ~49 pts (up to 177) = 300-400+ |
//| floating loss at 0.06. Sim (v2.01 -> v2.02): Oct24-Mar26 +9,232 -> |
//| +9,452, worst drop 2,061 -> 909; Apr-Sep26 real +4,162 -> +4,020,  |
//| drop 1,504 -> 947, lowest -743 -> -164. Max loss per trade ~120.   |
//| Without the opposite-level TP (tested): Apr-Sep +5,061 but Oct24-  |
//| Mar26 only +3,862 with 8 losing months -- TP kept on.              |
//|                                                                    |
//| v2.01 DYNAMIC ZONES FILTER (user 2026-09-27): an M5 candle       |
//| closing ABOVE today's upper zone line (Z2 = D1 open + half the     |
//| 10-day average range) blocks SELLS for the rest of the day; one    |
//| closing BELOW the lower line (Z4) blocks BUYS. Between the zones   |
//| both are allowed. A new D1 session recomputes the zones and resets |
//| the filter. Applies to every new entry of the blocked direction.   |
//| v2.00 sim: Oct24-Mar26 +6,599 -> +9,232 (losing months 8 -> 2),    |
//| Apr-Sep26 real +3,966 -> +4,162, drawdowns about the same.         |
//|                                                                    |
//| v2.00 (user 2026-09-27) -- adds on top of v1.55:                  |
//|  A. OPPOSITE-LEVEL TP (every trade): buy TP = nearest aligning     |
//|     resistance above price - TPBufferPrice (1.0); sell TP =        |
//|     nearest aligning support below + 1.0; re-evaluated every M5    |
//|     candle (broker TP, so a touch mid-candle closes it).          |
//|  B. BREAKOUT / BREAKDOWN trades (BreakoutLots 0.05): an M5 candle  |
//|     CLOSES above an aligning resistance, then a LATER bullish M5   |
//|     CISD also closing above it -> buy. SL = the swing low the CISD |
//|     is based on (AlgoAlpha's nearest active swing at confirmation, |
//|     SwingPeriod 12) - SLBufferPrice. Valid BreakoutValidBars (48)  |
//|     candles, cancelled by a close back below the level. Mirror for |
//|     breakdowns. Brake / M10-after-SL don't apply to them.          |
//|  C. AUTO SQUARE-OFF (every trade): a buy closes when a bearish M5  |
//|     CISD closes below its level (the support it was bought at, or  |
//|     the resistance it broke out of); mirror for sells.             |
//|                                                                    |
//| v1.55 DEFAULTS (user 2026-09-27): REAL touch (TouchBufferPrice 0)  |
//| + v1.53's SL (lowest low of the candles AFTER the touch candle,    |
//| SLFromTouch false, SLBufferPrice 0.5). Touch-buffer test, same SL: |
//| buffer 0 / 1 / 2 / 3 -> Apr-Sep 2026 real ticks +4,278 / +4,817 /  |
//| +4,911 / +3,280; Oct 2024-Mar 2026 (1-min bars) -4,514 / -6,360 /  |
//| -8,142 / -12,042; 2-year total -236 / -1,543 / -3,231 / -8,763 --  |
//| the real touch is the most robust (near-misses were mostly bad).   |
//| The SL-at-touch-extreme idea (v1.54) cut Apr-Sep to +554: ~1.7x    |
//| wider SL = ~1.7x bigger losses, same winners.                      |
//| v1.54 (user 2026-09-27): a touch must actually reach the level      |
//| (TouchBufferPrice 0), and the SL goes to the extreme FROM THE TOUCH |
//| (lowest low / highest high from the first candle of the current    |
//| run of touching candles through the CISD candle; SLFromTouch),     |
//| no SL cushion (SLBufferPrice 0). Real-tick sim Apr-Sep 2026: +544, |
//| drop 2,067, median SL 12.9 pts -- v1.53 was +4,911 / 922 / 7.0.   |
//| Set TouchBufferPrice 2, SLFromTouch false, SLBufferPrice 0.5 to    |
//| get v1.53 behaviour back.                                          |
//| v1.53: breakeven/partial guide lines drawn at the REAL BreakevenR / |
//| PartialR (were hard-coded at 2R/3R, so "1:3 partial" showed while  |
//| the partial is at 1:7); panel moved down (PanelTopOffset).         |
//| v1.52: AttachIndicator OFF by default -- v1.51's visual test froze |
//| after history sync ("waiting for update") while loading the        |
//| indicator with parameters; the EA draws all its own levels anyway. |
//| v1.51 (display only, trading logic unchanged from v1.50): every    |
//| timeframe's Major/Minor drawn in YELLOW like the indicator (solid  |
//| Major, dotted Minor); the attached indicator loads with its CISD   |
//| block OFF (its CISD swing lines looked like levels -- levels only  |
//| ever come from the yellow Major/Minor logic); touch arrows only on |
//| the first candle of each touch.                                    |
//|                                                                    |
//| Everything is computed on CLOSED candles only; BE/partial are      |
//| checked every tick.                                                |
//+------------------------------------------------------------------+
#property copyright "V6S-ICT"
#property version   "2.15"
// the chart indicator is packaged with the EA so the Strategy Tester loads it for the visual chart
#property tester_indicator "Dynamic_Zones_CISD_MajorMinor.ex5"

#include <Trade/Trade.mqh>

//===================== Inputs =====================
input group "Trading"
input double LotSize          = 0.08;
input double RunnerLots       = 0.01;    // left open after the partial close
input double SLBufferPrice    = 0.5;     // added beyond the SL swing
input double MaxSLPrice       = 20.0;    // SL never further than this from entry (0 = no cap)
input double TouchBufferPrice = 0.0;     // a candle within this distance of a level counts as a touch (0 = must reach it; v1.53: 2.0)
input bool   SLFromTouch      = false;   // true = SL at the extreme from the touch candle itself (v1.54, tested worse)
input double BreakevenR       = 2.0;     // SL -> entry at this multiple of the initial risk
input double PartialR         = 7.0;     // partial close at this multiple of the initial risk (tested 2-10R: 6-7R best)
input long   MagicNumber      = 26092701;
input int    SlippagePoints   = 50;
input int    LossStreakBrake  = 0;       // pause a direction after this many stop-losses in a row (0 = off)
input bool   M10AfterSL       = false;   // a level whose trade hit SL needs an M10 CISD for its next entry

input group "Levels"
input int    PivotPeriod      = 5;       // Major/Minor ZigZag pivot period (indicator default)
input int    MinAlignTimeframes = 3;     // a level must be shared by at least this many DIFFERENT timeframes
input int    MinMajorTimeframes = 2;     // ...and at least this many of them must have it as a MAJOR level
input int    WarmupBars       = 3000;    // closed bars replayed per timeframe at start
input bool   UseH4 = true, UseH2 = true, UseH1 = true, UseM30 = true, UseM15 = true, UseM10 = true, UseM5 = true, UseM3 = true;

input group "CISD"
input double CISDTolerance    = 0.7;     // "Noise Filter"
input int    CISDSwingPeriod  = 12;      // swing pivot period behind each CISD (AlgoAlpha default) -- breakout SL
input int    CISDSwingExpiry  = 100;     // swing lines stop counting after this many bars (AlgoAlpha default)

input group "Extra leg (v2.12)"
input bool   UseExtraLeg      = true;    // same-side setup while the first trade is in profit -> one extra leg + first trade to BE
input double ExtraLegTPR      = 2.0;     // extra leg TP = this x its own risk
input long   LegMagicNumber   = 26092731;

input group "Trap trade (v2.15)"
input bool   UseTrapTrade     = true;    // auto square-off of a reversal/breakout -> enter the other way
input double TrapSLBuffer     = 4.0;     // trap SL = failed level + this (sell) / - this (buy)
input double TrapTPR          = 2.0;     // trap TP = this x its risk (fixed)

input group "Reversal time block (v2.11)"
input bool   UseRevTimeBlock  = true;    // no new REVERSAL entries inside the window below (breakouts / DZ unaffected)
input string RevBlockFrom     = "01:30"; // IST
input string RevBlockTo       = "05:30"; // IST
input int    ServerToISTMinutes = 330;   // minutes to add to server time to get IST (Exness server = UTC -> 330)

input group "Dynamic Zones filter"
input bool   UseDZFilter      = true;    // block sells after a close above Z2 / buys after a close below Z4 (resets daily)

input group "Exits / breakouts (v2.00)"
input bool   UseOppositeLevelTP = true;  // TP at the nearest opposite aligning level
input double TPBufferPrice    = 1.0;     // buy TP = resistance - this; sell TP = support + this
input bool   UseSessionTPRev  = true;    // v2.14: reversal TP = nearest session high/low (previous session or current so far), else aligning level
input double MinTPR           = 1.0;     // aligning-level trades: a level closer than this x risk to entry -> TP 1:1 instead (0 = off)
input bool   AutoSquareOff    = true;    // close on an opposite CISD closing beyond the trade's level
input bool   UseBreakouts     = true;    // trade breakouts/breakdowns of aligning levels
input double BreakoutLots     = 0.10;
input double MinRRBreakout    = 1.5;     // breakout needs the opposite-level TP >= this x risk (0 = off; no opposite level = allowed)
input double MinRRReversal    = 0.0;     // same check for reversal trades (0 = off)
input int    BreakoutValidBars = 48;     // M5 candles a break stays tradeable

input group "Dynamic Zones breakouts (v2.05, separate slot)"
input bool   UseDZBreakout    = true;    // M15 zone breakout/breakdown trades
input double DZLots           = 0.10;
input double DZSLBuffer       = 4.0;     // SL beyond the far zone line
input double DZCappedMinR     = 1.0;     // v2.10: a capped DZ trade needs TP >= this x risk, else skipped (0 = off)
input double DZMaxSL          = 75.0;    // v2.09: SL further than this from entry is moved to this distance (0 = no cap)
input bool   DZSquareOff      = true;    // an opposite DZ setup closes the open DZ trade
input double DZMinTPPoints    = 1.0;     // skip the DZ trade if its TP (nearest aligning level) is closer than this (0 = off)
input long   DZMagicNumber    = 26092721;

input group "Dynamic Zones"
input int    DZShortLen       = 5;
input int    DZLongLen        = 10;

input group "Display / log"
input bool   ShowOnChart      = true;    // draw levels/touches/trades (visual tester + live chart)
input bool   ShowPanel        = true;    // on-screen list of aligning levels + trade status
input int    PanelTopOffset   = 60;      // pixels from the top of the chart to the first panel line
input bool   AttachIndicator  = false;   // load Dynamic_Zones_CISD_MajorMinor on the chart (display only) -- off: v1.51 froze the visual tester
input bool   VerboseLog       = true;

input group "Restart safety (v2.07)"
input int    ReplayMaxBars    = 288;     // at start, replay up to this many recent M5 candles (no orders) to rebuild setups
input int    MaxSignalDelaySec = 120;    // a candle must have closed within this many seconds to open a trade

//===================== Small array helpers (verbatim from the indicator) =====================
void PushS(string &a[], string v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void PushD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void PushI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void RemoveLastS(string &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void RemoveLastD(double &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void RemoveLastI(int    &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void InsertAt_S(string &a[], int idx, string v){ string t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void InsertAt_D(double &a[], int idx, double v){ double t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void InsertAt_I(int    &a[], int idx, int    v){ int t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void ReplaceAtS(string &a[], int idx, string v){ if(idx>=0 && idx<ArraySize(a)) a[idx]=v; }
void InsertFrontD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void InsertFrontI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void RemoveFrontD(double &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveFrontI(int    &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

void Log(const string msg){ if(VerboseLog) Print("[V6S-ICT] ", msg); }

// v2.07 restart safety
bool     g_replay  = false;   // true while old candles are replayed: setups update, no orders are sent
datetime g_barTime = 0;       // open time of the M5 candle OnM5Bar is processing
bool     g_startup = true;    // the first catch-up after warmup is always a replay

//+------------------------------------------------------------------+
//| Major/Minor for ONE timeframe -- the indicator's w_* working state |
//| and ProcessBar/ClassifyPivot/UpdateMajorLevels/TrackLatestPositions|
//| ported verbatim as a class, fed closed bars only, in order.        |
//+------------------------------------------------------------------+
class CMajorMinor
{
public:
   ENUM_TIMEFRAMES tf;
   string   name;
   bool     ready;
   datetime lastBar;
   double   hi[], lo[], cl[];
   datetime tm[];
   int      n;

   string w_Type[], w_TypeAdv[];
   double w_Value[], w_ValueAdv[];
   int    w_Index[], w_IndexAdv[];
   double w_MajorHighLevel, w_MajorLowLevel;
   int    w_MajorHighIndex, w_MajorLowIndex;
   string w_MajorHighType, w_MajorLowType;
   bool   w_MajorLevelsSet, w_Lock0, w_Lock1;
   double w_LastHighValue, w_LastLowValue;
   int    w_LastHighIndex, w_LastLowIndex;
   int    w_MajSupX, w_MajResX, w_MinSupX, w_MinResX;
   double w_MajSupY, w_MajResY, w_MinSupY, w_MinResY;

   void Init(const ENUM_TIMEFRAMES t, const string nm)
   {
      tf = t; name = nm; ready = false; lastBar = 0; n = 0;
      ArrayResize(hi, 0); ArrayResize(lo, 0); ArrayResize(cl, 0); ArrayResize(tm, 0);
      ArrayResize(w_Type, 0); ArrayResize(w_Value, 0); ArrayResize(w_Index, 0);
      ArrayResize(w_TypeAdv, 0); ArrayResize(w_ValueAdv, 0); ArrayResize(w_IndexAdv, 0);
      w_MajorHighLevel = 0; w_MajorLowLevel = 0; w_MajorHighIndex = -1; w_MajorLowIndex = -1;
      w_MajorHighType = ""; w_MajorLowType = ""; w_MajorLevelsSet = false; w_Lock0 = true; w_Lock1 = true;
      w_LastHighValue = 0; w_LastLowValue = 0; w_LastHighIndex = -1; w_LastLowIndex = -1;
      w_MajSupX = -1; w_MajResX = -1; w_MinSupX = -1; w_MinResX = -1;
      w_MajSupY = 0; w_MajResY = 0; w_MinSupY = 0; w_MinResY = 0;
   }

   bool IsPivotHigh(int c, int PP)
   {
      if(c-PP<0 || c+PP>=n) return false;
      double v=hi[c];
      for(int k=c-PP;k<=c+PP;k++) if(k!=c && hi[k]>=v) return false;
      return true;
   }
   bool IsPivotLow(int c, int PP)
   {
      if(c-PP<0 || c+PP>=n) return false;
      double v=lo[c];
      for(int k=c-PP;k<=c+PP;k++) if(k!=c && lo[k]<=v) return false;
      return true;
   }

   void PushHighType()
   {
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastHighValue) ? "HH":"LH") : "H";
      PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
   }
   void PushLowType()
   {
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastLowValue) ? "HL":"LL") : "L";
      PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
   }
   void ReplaceLastWithHighType()
   {
      RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastHighValue) ? "HH":"LH") : "H";
      PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
   }
   void ReplaceLastWithLowType()
   {
      RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastLowValue) ? "HL":"LL") : "L";
      PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
   }

   void ClassifyPivot(bool hasHigh, bool hasLow, double thisClose)
   {
      int N=ArraySize(w_Type);
      if(hasHigh && hasLow)
      {
         if(N==0) { }
         else
         {
            string last=w_Type[N-1];
            if(last=="L" || last=="LL")
            {
               if(w_LastLowValue<w_Value[N-1]) ReplaceLastWithLowType();
               else                            PushHighType();
            }
            else if(last=="H" || last=="HH")
            {
               if(w_LastHighValue>w_Value[N-1]) ReplaceLastWithHighType();
               else                             PushLowType();
            }
            else if(last=="LH")
            {
               if(w_LastHighValue<w_Value[N-1])
                  PushLowType();
               else if(w_LastHighValue>w_Value[N-1])
               {
                  if(thisClose<w_Value[N-1])      ReplaceLastWithHighType();
                  else if(thisClose>w_Value[N-1]) PushLowType();
               }
            }
            else if(last=="HL")
            {
               if(w_LastLowValue>w_Value[N-1])
                  PushHighType();
               else if(w_LastLowValue<w_Value[N-1])
               {
                  if(thisClose>w_Value[N-1])      ReplaceLastWithLowType();
                  else if(thisClose<w_Value[N-1]) PushHighType();
               }
            }
         }
      }
      else if(hasHigh)
      {
         if(N==0)
         {
            InsertAt_S(w_Type,0,"H"); InsertAt_D(w_Value,0,w_LastHighValue); InsertAt_I(w_Index,0,w_LastHighIndex);
         }
         else
         {
            string last=w_Type[N-1];
            if(last=="L" || last=="HL" || last=="LL")
            {
               if(w_LastHighValue>w_Value[N-1])      PushHighType();
               else if(w_LastHighValue<w_Value[N-1]) ReplaceLastWithLowType();
            }
            else if(last=="H" || last=="HH" || last=="LH")
            {
               if(w_Value[N-1]<w_LastHighValue) ReplaceLastWithHighType();
            }
         }
      }
      else if(hasLow)
      {
         if(N==0)
         {
            InsertAt_S(w_Type,0,"L"); InsertAt_D(w_Value,0,w_LastLowValue); InsertAt_I(w_Index,0,w_LastLowIndex);
         }
         else
         {
            string last=w_Type[N-1];
            if(last=="H" || last=="HH" || last=="LH")
            {
               if(w_LastLowValue<w_Value[N-1])      PushLowType();
               else if(w_LastLowValue>w_Value[N-1]) ReplaceLastWithHighType();
            }
            else if(last=="L" || last=="HL" || last=="LL")
            {
               if(w_Value[N-1]>w_LastLowValue) ReplaceLastWithLowType();
            }
         }
      }
   }

   void UpdateMajorLevels(double thisClose)
   {
      int nAdv=ArraySize(w_ValueAdv);
      if(nAdv<=1) return;
      int nBase=ArraySize(w_Type);
      if(nBase<1) return;

      if(thisClose>w_MajorHighLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"ML");
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL" || t=="mLL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH" || t=="MLH" || t=="MHH")
         {
            if(nAdv>=2 && nBase>=2)
            {
               string t2=w_TypeAdv[nAdv-2];
               if(t2=="mHL" || t2=="mLL")
               {
                  ReplaceAtS(w_TypeAdv,nAdv-2,"M"+w_Type[nBase-2]);
                  w_MajorLowLevel=w_ValueAdv[nAdv-2]; w_MajorLowIndex=w_IndexAdv[nAdv-2]; w_MajorLowType=w_TypeAdv[nAdv-2];
               }
            }
         }
      }

      if(w_ValueAdv[nAdv-1]>w_MajorHighLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"MH");
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH" || t=="MHH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
      }

      if(thisClose<w_MajorLowLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"MH");
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL" || t=="mLL" || t=="MHL" || t=="MLL")
         {
            if(nAdv>=2 && nBase>=2)
            {
               string t2=w_TypeAdv[nAdv-2];
               if(t2=="mLH" || t2=="mHH")
               {
                  ReplaceAtS(w_TypeAdv,nAdv-2,"M"+w_Type[nBase-2]);
                  w_MajorHighLevel=w_ValueAdv[nAdv-2]; w_MajorHighIndex=w_IndexAdv[nAdv-2]; w_MajorHighType=w_TypeAdv[nAdv-2];
               }
            }
         }
      }

      if(w_ValueAdv[nAdv-1]<w_MajorLowLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"ML");
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLL" || t=="MLL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
      }
   }

   void ProcessBar(int i, int PP)
   {
      int c=i-PP;
      bool hasHigh=false, hasLow=false;
      if(c>=PP && c+PP<n)
      {
         hasHigh=IsPivotHigh(c,PP);
         hasLow =IsPivotLow(c,PP);
      }
      if(hasHigh){ w_LastHighValue=hi[c]; w_LastHighIndex=c; }
      if(hasLow) { w_LastLowValue =lo[c]; w_LastLowIndex =c; }

      double thisClose=cl[i];
      int prevN=ArraySize(w_Value);
      double prevLastValue=(prevN>0) ? w_Value[prevN-1] : EMPTY_VALUE;
      string prevLastType =(prevN>0) ? w_Type[prevN-1]  : "";

      ClassifyPivot(hasHigh, hasLow, thisClose);

      int N=ArraySize(w_Type);
      if(N==2)
      {
         if(w_Type[0]=="H")
         {
            w_MajorHighLevel=w_Value[0]; w_MajorLowLevel=w_Value[1];
            w_MajorHighIndex=w_Index[0]; w_MajorLowIndex=w_Index[1];
            w_MajorHighType=w_Type[0];   w_MajorLowType=w_Type[1];
         }
         else if(w_Type[0]=="L")
         {
            w_MajorHighLevel=w_Value[1]; w_MajorLowLevel=w_Value[0];
            w_MajorHighIndex=w_Index[1]; w_MajorLowIndex=w_Index[0];
            w_MajorHighType=w_Type[1];   w_MajorLowType=w_Type[0];
         }
         w_MajorLevelsSet=true;
      }

      if(ArraySize(w_Value)==1 && w_Lock0)
      {
         InsertAt_S(w_TypeAdv,0,"M"+w_Type[0]); InsertAt_D(w_ValueAdv,0,w_Value[0]); InsertAt_I(w_IndexAdv,0,w_Index[0]);
         w_Lock0=false;
      }
      if(ArraySize(w_Value)==2 && w_Lock1)
      {
         InsertAt_S(w_TypeAdv,1,"M"+w_Type[1]); InsertAt_D(w_ValueAdv,1,w_Value[1]); InsertAt_I(w_IndexAdv,1,w_Index[1]);
         w_Lock1=false;
      }

      int m=ArraySize(w_Value);
      if(m>1)
      {
         double curLastValue=w_Value[m-1];
         string curLastType =w_Type[m-1];
         if(curLastValue!=prevLastValue)
         {
            string prevFamily=(StringLen(prevLastType)>0) ? StringSubstr(prevLastType,StringLen(prevLastType)-1,1) : "";
            string curFamily =StringSubstr(curLastType,StringLen(curLastType)-1,1);
            if(prevFamily!=curFamily)
            {
               PushS(w_TypeAdv,"m"+curLastType); PushD(w_ValueAdv,curLastValue); PushI(w_IndexAdv,w_Index[m-1]);
            }
            else
            {
               int nA=ArraySize(w_ValueAdv);
               if(nA>0){ w_ValueAdv[nA-1]=curLastValue; w_IndexAdv[nA-1]=w_Index[m-1]; }
            }
         }
      }

      if(w_MajorLevelsSet) UpdateMajorLevels(thisClose);
   }

   void TrackLatestPositions()
   {
      int nAdv=ArraySize(w_TypeAdv);
      if(nAdv<=2) return;
      int x=w_IndexAdv[nAdv-1];
      double y=w_ValueAdv[nAdv-1];
      string t=w_TypeAdv[nAdv-1];
      if(t=="MLL" || t=="MHL")      { w_MajSupX=x; w_MajSupY=y; }
      else if(t=="MHH" || t=="MLH") { w_MajResX=x; w_MajResY=y; }
      else if(t=="mLL" || t=="mHL") { w_MinSupX=x; w_MinSupY=y; }
      else if(t=="mHH" || t=="mLH") { w_MinResX=x; w_MinResY=y; }
   }

   void AddBar(const double h, const double l, const double c, const datetime t)
   {
      ArrayResize(hi, n+1, 20000); ArrayResize(lo, n+1, 20000); ArrayResize(cl, n+1, 20000); ArrayResize(tm, n+1, 20000);
      hi[n]=h; lo[n]=l; cl[n]=c; tm[n]=t; n++;
      ProcessBar(n-1, PivotPeriod);
      TrackLatestPositions();
   }

   // Replay WarmupBars closed bars once.
   bool Warmup()
   {
      int avail = Bars(_Symbol, tf) - 1;
      int cnt = MathMin(WarmupBars, avail);
      if(cnt < 4*PivotPeriod + 10) return false;
      double h[], l[], c[]; datetime t[];
      if(CopyHigh(_Symbol, tf, 1, cnt, h) != cnt) return false;
      if(CopyLow(_Symbol, tf, 1, cnt, l) != cnt)  return false;
      if(CopyClose(_Symbol, tf, 1, cnt, c) != cnt) return false;
      if(CopyTime(_Symbol, tf, 1, cnt, t) != cnt) return false;
      for(int k = 0; k < cnt; k++) AddBar(h[k], l[k], c[k], t[k]);
      lastBar = t[cnt-1];
      ready = true;
      return true;
   }

   // Feed any newly closed bars; true if at least one was added.
   bool Update()
   {
      if(!ready) return Warmup();
      datetime lastClosed = iTime(_Symbol, tf, 1);
      if(lastClosed <= lastBar) return false;
      int shift = iBarShift(_Symbol, tf, lastBar, true);
      int from = (shift > 1) ? shift - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         AddBar(iHigh(_Symbol, tf, s), iLow(_Symbol, tf, s), iClose(_Symbol, tf, s), iTime(_Symbol, tf, s));
         lastBar = iTime(_Symbol, tf, s);
      }
      return true;
   }
};

//===================== Timeframes =====================
CMajorMinor g_mm[8];
int         g_mmCount = 0;

//===================== M5 bar history + CISD =====================
double   g_o[], g_h[], g_l[], g_c[];
int      g_n = 0;
datetime g_m5Last = 0;
double   g_bearOpen[]; int g_bearIdx[];
double   g_bullOpen[]; int g_bullIdx[];

bool ConfirmBearCISD(const int i)
{
   while(ArraySize(g_bearOpen) > 0)
   {
      double candOpen = g_bearOpen[0];
      int    candIdx  = g_bearIdx[0];
      if(g_c[i] < candOpen)
      {
         double highest = 0.0;
         for(int k = candIdx; k <= i; k++) if(g_c[k] > highest) highest = g_c[k];
         double top = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && g_c[k] < g_o[k]) { top = g_o[k]; k--; }
         double denom = top - candOpen;
         if(denom != 0.0 && (highest - candOpen) / denom > CISDTolerance)
         {
            ArrayResize(g_bearOpen, 0); ArrayResize(g_bearIdx, 0);
            return true;
         }
         RemoveFrontD(g_bearOpen); RemoveFrontI(g_bearIdx);
      }
      else break;
   }
   return false;
}

bool ConfirmBullCISD(const int i)
{
   while(ArraySize(g_bullOpen) > 0)
   {
      double candOpen = g_bullOpen[0];
      int    candIdx  = g_bullIdx[0];
      if(g_c[i] > candOpen)
      {
         double lowest = g_c[i];
         for(int k = candIdx; k <= i; k++) if(g_c[k] < lowest) lowest = g_c[k];
         double bottom = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && g_c[k] > g_o[k]) { bottom = g_o[k]; k--; }
         double denom = candOpen - bottom;
         if(denom != 0.0 && (candOpen - lowest) / denom > CISDTolerance)
         {
            ArrayResize(g_bullOpen, 0); ArrayResize(g_bullIdx, 0);
            return true;
         }
         RemoveFrontD(g_bullOpen); RemoveFrontI(g_bullIdx);
      }
      else break;
   }
   return false;
}

// Append one closed M5 bar; returns +1 bullish CISD, -1 bearish, 0 none (bull wins a tie, as the indicator).
// AlgoAlpha swing lines on M5 (front = newest), only to know the swing each CISD is based on.
double g_shLvl[]; int g_shIdx[];
double g_slLvl[]; int g_slIdx[];
bool   g_cisdHasSwing = false;   // swing snapshot taken at the last CISD confirmation
double g_cisdSwing = 0.0;

bool SwingPivotHigh(const int c, const int len)
{
   if(c - len < 0 || c + len >= g_n) return false;
   for(int k = c - len; k <= c + len; k++) if(k != c && g_h[k] >= g_h[c]) return false;
   return true;
}
bool SwingPivotLow(const int c, const int len)
{
   if(c - len < 0 || c + len >= g_n) return false;
   for(int k = c - len; k <= c + len; k++) if(k != c && g_l[k] <= g_l[c]) return false;
   return true;
}
void RemoveAtD(double &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveAtI(int    &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

// Same order as the indicator's ProcessCISDBar steps 1-2: new pivots, then mitigation / expiry.
void SwingStep(const int i)
{
   int c = i - CISDSwingPeriod;
   if(c >= 0)
   {
      if(SwingPivotHigh(c, CISDSwingPeriod)) { InsertFrontI(g_shIdx, c); InsertFrontD(g_shLvl, g_h[c]); }
      if(SwingPivotLow(c, CISDSwingPeriod))  { InsertFrontI(g_slIdx, c); InsertFrontD(g_slLvl, g_l[c]); }
   }
   for(int j = ArraySize(g_shIdx) - 1; j >= 0; j--)
      if(i - g_shIdx[j] >= CISDSwingExpiry || g_h[i] >= g_shLvl[j]) { RemoveAtI(g_shIdx, j); RemoveAtD(g_shLvl, j); }
   for(int j = ArraySize(g_slIdx) - 1; j >= 0; j--)
      if(i - g_slIdx[j] >= CISDSwingExpiry || g_l[i] <= g_slLvl[j]) { RemoveAtI(g_slIdx, j); RemoveAtD(g_slLvl, j); }
   while(ArraySize(g_shIdx) > 100) { ArrayResize(g_shIdx, 100); ArrayResize(g_shLvl, 100); }
   while(ArraySize(g_slIdx) > 100) { ArrayResize(g_slIdx, 100); ArrayResize(g_slLvl, 100); }
}

int M5Step(const double o, const double h, const double l, const double c)
{
   int i = g_n;
   ArrayResize(g_o, i+1, 100000); ArrayResize(g_h, i+1, 100000);
   ArrayResize(g_l, i+1, 100000); ArrayResize(g_c, i+1, 100000);
   g_o[i]=o; g_h[i]=h; g_l[i]=l; g_c[i]=c; g_n = i+1;
   SwingStep(i);
   if(i >= 1)
   {
      if(g_c[i-1] < g_o[i-1] && c > o) { InsertFrontI(g_bearIdx, i); InsertFrontD(g_bearOpen, o); }
      if(g_c[i-1] > g_o[i-1] && c < o) { InsertFrontI(g_bullIdx, i); InsertFrontD(g_bullOpen, o); }
   }
   int cisd = 0;
   if(ConfirmBearCISD(i)) cisd = -1;
   if(ConfirmBullCISD(i)) cisd = 1;
   // the swing this CISD is based on: nearest active swing HIGH for a bearish one, LOW for a bullish one
   if(cisd < 0) { g_cisdHasSwing = ArraySize(g_shLvl) > 0; g_cisdSwing = g_cisdHasSwing ? g_shLvl[0] : 0.0; }
   if(cisd > 0) { g_cisdHasSwing = ArraySize(g_slLvl) > 0; g_cisdSwing = g_cisdHasSwing ? g_slLvl[0] : 0.0; }
   return cisd;
}

//===================== Breakout candidates (v2.00) =====================
int    g_boSide[];   // +1 breakout above a resistance, -1 breakdown below a support
double g_boVal[];
int    g_boBar[];    // M5 index of the candle that closed through the level

int FindBO(const int side, const double v)
{
   for(int k = 0; k < ArraySize(g_boVal); k++) if(g_boSide[k] == side && g_boVal[k] == v) return k;
   return -1;
}
void RemoveBO(const int k)
{
   int n = ArraySize(g_boVal);
   if(k < 0 || k >= n) return;
   RemoveAtI(g_boSide, k); RemoveAtD(g_boVal, k); RemoveAtI(g_boBar, k);
}

//===================== M10 CISD (for levels that stopped out) =====================
class CCisd
{
public:
   ENUM_TIMEFRAMES tf;
   double O[], C[];
   double bearOpen[]; int bearIdx[];
   double bullOpen[]; int bullIdx[];
   datetime lastBar;
   bool ready;

   void Init(const ENUM_TIMEFRAMES t)
   {
      tf = t; lastBar = 0; ready = false;
      ArrayResize(O, 0); ArrayResize(C, 0);
      ArrayResize(bearOpen, 0); ArrayResize(bearIdx, 0); ArrayResize(bullOpen, 0); ArrayResize(bullIdx, 0);
   }

   bool ConfirmBear(const int i)
   {
      while(ArraySize(bearOpen) > 0)
      {
         double co = bearOpen[0]; int ci = bearIdx[0];
         if(C[i] < co)
         {
            double highest = 0.0;
            for(int k = ci; k <= i; k++) if(C[k] > highest) highest = C[k];
            double top = 0.0; int k = ci - 1;
            while(k >= 0 && C[k] < O[k]) { top = O[k]; k--; }
            double d = top - co;
            if(d != 0.0 && (highest - co) / d > CISDTolerance) { ArrayResize(bearOpen, 0); ArrayResize(bearIdx, 0); return true; }
            RemoveFrontD(bearOpen); RemoveFrontI(bearIdx);
         }
         else break;
      }
      return false;
   }
   bool ConfirmBull(const int i)
   {
      while(ArraySize(bullOpen) > 0)
      {
         double co = bullOpen[0]; int ci = bullIdx[0];
         if(C[i] > co)
         {
            double lowest = C[i];
            for(int k = ci; k <= i; k++) if(C[k] < lowest) lowest = C[k];
            double bottom = 0.0; int k = ci - 1;
            while(k >= 0 && C[k] > O[k]) { bottom = O[k]; k--; }
            double d = co - bottom;
            if(d != 0.0 && (co - lowest) / d > CISDTolerance) { ArrayResize(bullOpen, 0); ArrayResize(bullIdx, 0); return true; }
            RemoveFrontD(bullOpen); RemoveFrontI(bullIdx);
         }
         else break;
      }
      return false;
   }
   int Step(const double o, const double c)
   {
      int i = ArraySize(C);
      ArrayResize(O, i + 1, 50000); ArrayResize(C, i + 1, 50000); O[i] = o; C[i] = c;
      if(i >= 1)
      {
         if(C[i-1] < O[i-1] && c > o) { InsertFrontI(bearIdx, i); InsertFrontD(bearOpen, o); }
         if(C[i-1] > O[i-1] && c < o) { InsertFrontI(bullIdx, i); InsertFrontD(bullOpen, o); }
      }
      int r = 0;
      if(ConfirmBear(i)) r = -1;
      if(ConfirmBull(i)) r = 1;
      return r;
   }
   bool Warmup()
   {
      int avail = Bars(_Symbol, tf) - 1;
      int cnt = MathMin(WarmupBars, avail);
      if(cnt < 50) return false;
      double o[], c[]; datetime t[];
      if(CopyOpen(_Symbol, tf, 1, cnt, o) != cnt) return false;
      if(CopyClose(_Symbol, tf, 1, cnt, c) != cnt) return false;
      if(CopyTime(_Symbol, tf, 1, cnt, t) != cnt) return false;
      for(int k = 0; k < cnt; k++) Step(o[k], c[k]);
      lastBar = t[cnt-1]; ready = true;
      return true;
   }
   // Feed newly closed bars; returns the signal of the LAST new bar (0 if none closed), its close in lastClose.
   int Update(double &lastClose, datetime &lastTime)
   {
      int sig = 0; lastClose = 0; lastTime = 0;
      datetime lc = iTime(_Symbol, tf, 1);
      if(lc <= lastBar) return 0;
      int shift = iBarShift(_Symbol, tf, lastBar, true);
      int from = (shift > 1) ? shift - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         sig = Step(iOpen(_Symbol, tf, s), iClose(_Symbol, tf, s));
         lastClose = iClose(_Symbol, tf, s); lastTime = iTime(_Symbol, tf, s);
         lastBar = lastTime;
      }
      return sig;
   }
};

CCisd    g_c10;
int      g_c10Sig = 0;       // M10 CISD on the M10 candle that closed together with this M5 candle
double   g_c10Close = 0;

// levels (side, value) that need M10 confirmation because their last trade hit SL
int    g_m10Side[];
double g_m10Val[];
int FindM10(const int side, const double v)
{
   for(int k = 0; k < ArraySize(g_m10Val); k++) if(g_m10Side[k] == side && g_m10Val[k] == v) return k;
   return -1;
}
bool NeedM10(const int side, const double v){ return M10AfterSL && FindM10(side, v) >= 0; }
void SetM10(const int side, const double v){ if(FindM10(side, v) < 0) { PushI(g_m10Side, side); PushD(g_m10Val, v); } }
void ClearM10(const int side, const double v)
{
   int k = FindM10(side, v);
   if(k < 0) return;
   int n = ArraySize(g_m10Val);
   g_m10Side[k] = g_m10Side[n-1]; g_m10Val[k] = g_m10Val[n-1];
   ArrayResize(g_m10Side, n-1); ArrayResize(g_m10Val, n-1);
}

//===================== Aligning levels + touches =====================
// side +1 = support (buy setups), -1 = resistance (sell setups)
double g_supVal[]; int g_supTouch[]; int g_supEst[]; string g_supDesc[];   // Est = first candle of the current run of touches
double g_resVal[]; int g_resTouch[]; int g_resEst[]; string g_resDesc[];

int FindLevel(const double &vals[], const double v)
{
   for(int k = 0; k < ArraySize(vals); k++) if(vals[k] == v) return k;
   return -1;
}

// Rebuild aligning support/resistance from every timeframe's current levels,
// keeping the touch state of levels that are still aligning.
void RebuildAlignments()
{
   double sv[], rv[]; string sd[], rd[]; int sm[], rm[];   // value, description, timeframe bitmask
   int smj[], rmj[];                                     // bitmask of timeframes where it is a MAJOR level
   for(int t = 0; t < g_mmCount; t++)
   {
      CMajorMinor *mm = GetPointer(g_mm[t]);
      if(!mm.ready) continue;
      for(int side = 0; side < 2; side++)
      {
         for(int kind = 0; kind < 2; kind++)
         {
            int x; double y; string lab;
            if(side == 0) { x = kind == 0 ? mm.w_MajSupX : mm.w_MinSupX; y = kind == 0 ? mm.w_MajSupY : mm.w_MinSupY; }
            else          { x = kind == 0 ? mm.w_MajResX : mm.w_MinResX; y = kind == 0 ? mm.w_MajResY : mm.w_MinResY; }
            if(x < 0) continue;
            y = NormalizeDouble(y, _Digits);
            lab = mm.name + (kind == 0 ? " Major" : " Minor");
            if(side == 0)
            {
               int f = FindLevel(sv, y);
               if(f < 0) { PushD(sv, y); PushS(sd, lab); PushI(sm, 1 << t); PushI(smj, kind == 0 ? (1 << t) : 0); }
               else      { sd[f] += ", " + lab; sm[f] |= (1 << t); if(kind == 0) smj[f] |= (1 << t); }
            }
            else
            {
               int f = FindLevel(rv, y);
               if(f < 0) { PushD(rv, y); PushS(rd, lab); PushI(rm, 1 << t); PushI(rmj, kind == 0 ? (1 << t) : 0); }
               else      { rd[f] += ", " + lab; rm[f] |= (1 << t); if(kind == 0) rmj[f] |= (1 << t); }
            }
         }
      }
   }

   // keep only values present on MinAlignTimeframes+ DIFFERENT timeframes
   double nsv[]; int nst[]; int nse[]; string nsd[];
   for(int k = 0; k < ArraySize(sv); k++)
   {
      int bits = 0, m = sm[k]; while(m) { bits += (m & 1); m >>= 1; }
      int majBits = 0, mj = smj[k]; while(mj) { majBits += (mj & 1); mj >>= 1; }
      if(bits < MinAlignTimeframes || majBits < MinMajorTimeframes) continue;
      int old = FindLevel(g_supVal, sv[k]);
      PushD(nsv, sv[k]); PushS(nsd, sd[k]); PushI(nst, old >= 0 ? g_supTouch[old] : -1); PushI(nse, old >= 0 ? g_supEst[old] : -1);
      if(old < 0)
      {
         Log(StringFormat("new ALIGNING SUPPORT %.3f (%s)", sv[k], sd[k]));
         g_lastNew = StringFormat("%s  NEW ALIGNING SUPPORT %.3f [%s]", TimeToString(TimeCurrent(), TIME_MINUTES), sv[k], sd[k]);
      }
   }
   double nrv[]; int nrt[]; int nre[]; string nrd[];
   for(int k = 0; k < ArraySize(rv); k++)
   {
      int bits = 0, m = rm[k]; while(m) { bits += (m & 1); m >>= 1; }
      int majBits = 0, mj = rmj[k]; while(mj) { majBits += (mj & 1); mj >>= 1; }
      if(bits < MinAlignTimeframes || majBits < MinMajorTimeframes) continue;
      int old = FindLevel(g_resVal, rv[k]);
      PushD(nrv, rv[k]); PushS(nrd, rd[k]); PushI(nrt, old >= 0 ? g_resTouch[old] : -1); PushI(nre, old >= 0 ? g_resEst[old] : -1);
      if(old < 0)
      {
         Log(StringFormat("new ALIGNING RESISTANCE %.3f (%s)", rv[k], rd[k]));
         g_lastNew = StringFormat("%s  NEW ALIGNING RESISTANCE %.3f [%s]", TimeToString(TimeCurrent(), TIME_MINUTES), rv[k], rd[k]);
      }
   }
   for(int k = 0; k < ArraySize(g_supVal); k++)
      if(FindLevel(nsv, g_supVal[k]) < 0) Log(StringFormat("aligning support %.3f gone", g_supVal[k]));
   for(int k = 0; k < ArraySize(g_resVal); k++)
      if(FindLevel(nrv, g_resVal[k]) < 0) Log(StringFormat("aligning resistance %.3f gone", g_resVal[k]));

   ArrayCopy(g_supVal, nsv); ArrayResize(g_supVal, ArraySize(nsv));
   ArrayCopy(g_supTouch, nst); ArrayResize(g_supTouch, ArraySize(nst));
   ArrayCopy(g_supEst, nse); ArrayResize(g_supEst, ArraySize(nse));
   ArrayCopy(g_supDesc, nsd); ArrayResize(g_supDesc, ArraySize(nsd));
   ArrayCopy(g_resVal, nrv); ArrayResize(g_resVal, ArraySize(nrv));
   ArrayCopy(g_resTouch, nrt); ArrayResize(g_resTouch, ArraySize(nrt));
   ArrayCopy(g_resEst, nre); ArrayResize(g_resEst, ArraySize(nre));
   ArrayCopy(g_resDesc, nrd); ArrayResize(g_resDesc, ArraySize(nrd));
   DrawLevels();
}

bool ChartOn(){ return ShowOnChart && (!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE)); }

void SetLine(const string nm, const datetime t1, const double y, const color clr, const int width,
             const ENUM_LINE_STYLE style, const string tip)
{
   if(ObjectFind(0, nm) < 0)
      ObjectCreate(0, nm, OBJ_TREND, 0, t1, y, t1 + PeriodSeconds(PERIOD_M5), y);
   else
   {
      ObjectMove(0, nm, 0, t1, y);
      ObjectMove(0, nm, 1, t1 + PeriodSeconds(PERIOD_M5), y);
   }
   ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, true);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, style);
   ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nm, OBJPROP_BACK, true);
   ObjectSetString(0, nm, OBJPROP_TOOLTIP, tip);
}

void SetText(const string nm, const datetime t, const double y, const string txt, const color clr,
             const int size, const ENUM_ANCHOR_POINT anchor)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_TEXT, 0, t, y);
   else ObjectMove(0, nm, 0, t, y);
   ObjectSetString(0, nm, OBJPROP_TEXT, txt);
   ObjectSetString(0, nm, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
}

void Marker(const string nm, const datetime t, const double y, const int code, const color clr, const string tip)
{
   if(!ChartOn()) return;
   if(ObjectFind(0, nm) >= 0) return;
   ObjectCreate(0, nm, OBJ_ARROW, 0, t, y);
   ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, code);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, nm, OBJPROP_TOOLTIP, tip);
}

void Note(const string nm, const datetime t, const double y, const string txt, const color clr, const bool above)
{
   if(!ChartOn()) return;
   SetText(nm, t, y, txt, clr, 8, above ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
}

// Every timeframe's current Major/Minor support & resistance, thin, labelled at their origin.
void DrawAllTFLevels()
{
   if(!ChartOn()) return;
   for(int t = 0; t < g_mmCount; t++)
   {
      CMajorMinor *mm = GetPointer(g_mm[t]);
      if(!mm.ready) continue;
      for(int j = 0; j < 4; j++)
      {
         int x = (j == 0) ? mm.w_MajSupX : (j == 1) ? mm.w_MinSupX : (j == 2) ? mm.w_MajResX : mm.w_MinResX;
         double y = (j == 0) ? mm.w_MajSupY : (j == 1) ? mm.w_MinSupY : (j == 2) ? mm.w_MajResY : mm.w_MinResY;
         string tag = (j == 0) ? "Maj S" : (j == 1) ? "Min S" : (j == 2) ? "Maj R" : "Min R";
         string nm = "V6SICT_MM_" + mm.name + "_" + IntegerToString(j);
         if(x < 0 || x >= mm.n) continue;
         color clr = (j % 2 == 0) ? clrYellow : clrGold;   // same yellow family as the indicator's Major/Minor
         SetLine(nm, mm.tm[x], y, clr, (j % 2 == 0) ? 2 : 1, (j % 2 == 0) ? STYLE_SOLID : STYLE_DOT,
                 StringFormat("%s %s %.3f", mm.name, tag, y));
         SetText(nm + "_T", mm.tm[x], y, mm.name + " " + tag, clr, 7, j < 2 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
      }
   }
}

// Aligning levels: thick, bright, labelled with every member.
void DrawLevels()
{
   DrawAllTFLevels();
   if(!ChartOn()) return;
   ObjectsDeleteAll(0, "V6SICT_LVL_");
   datetime now = iTime(_Symbol, PERIOD_M5, 0);
   for(int k = 0; k < ArraySize(g_supVal); k++)
   {
      string nm = "V6SICT_LVL_S_" + DoubleToString(g_supVal[k], _Digits);
      ObjectCreate(0, nm, OBJ_HLINE, 0, 0, g_supVal[k]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clrLime);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetString(0, nm, OBJPROP_TOOLTIP, "ALIGNING SUPPORT: " + g_supDesc[k]);
      SetText(nm + "_T", now, g_supVal[k], "ALIGN S " + DoubleToString(g_supVal[k], 2) + " [" + g_supDesc[k] + "]",
              clrLime, 8, ANCHOR_RIGHT_UPPER);
   }
   for(int k = 0; k < ArraySize(g_resVal); k++)
   {
      string nm = "V6SICT_LVL_R_" + DoubleToString(g_resVal[k], _Digits);
      ObjectCreate(0, nm, OBJ_HLINE, 0, 0, g_resVal[k]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clrOrangeRed);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetString(0, nm, OBJPROP_TOOLTIP, "ALIGNING RESISTANCE: " + g_resDesc[k]);
      SetText(nm + "_T", now, g_resVal[k], "ALIGN R " + DoubleToString(g_resVal[k], 2) + " [" + g_resDesc[k] + "]",
              clrOrangeRed, 8, ANCHOR_RIGHT_LOWER);
   }
}

//===================== On-screen panel =====================
string g_lastNew = "";          // most recent alignment event, shown on the panel
datetime g_lastPanel = 0;

#define PANEL_FONT_SIZE  10
#define PANEL_LINE_H     17
#define PANEL_MAX_ROWS   80
#define PANEL_MAX_CHARS  63   // MT5 label text limit

void PanelRow(const int row, const string txt, const color clr)
{
   string nm = "V6SICT_PNL_" + IntegerToString(row);
   if(ObjectFind(0, nm) < 0)
   {
      ObjectCreate(0, nm, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, nm, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, 8);
      ObjectSetString(0, nm, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, PANEL_FONT_SIZE);
   ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, PanelTopOffset + row * PANEL_LINE_H);
   ObjectSetString(0, nm, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
}

// Pixel width of a panel string (same font as the labels).
int PanelTextWidth(const string s)
{
   uint w = 0, h = 0;
   TextSetFont("Consolas", -PANEL_FONT_SIZE * 10);
   TextGetSize(s, w, h);
   return (int)w;
}

// One logical panel line; wraps onto extra rows (indented) when it is wider than the chart.
void PanelLine(int &row, const string txt, const color clr)
{
   int maxW = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS) - 8 - 16;
   string rest = txt;
   bool first = true;
   while(row < PANEL_MAX_ROWS)
   {
      string line = (first ? "" : "     ") + rest;
      // MT5 shows at most 63 characters of a label's text -- a row must fit that AND the chart width
      if(StringLen(line) <= PANEL_MAX_CHARS && (maxW <= 50 || PanelTextWidth(line) <= maxW)) { PanelRow(row++, line, clr); return; }
      // longest prefix that fits, broken at a space
      int lo = 1, hi = MathMin(StringLen(line), PANEL_MAX_CHARS);
      if(maxW > 50)
         while(lo < hi) { int mid = (lo + hi + 1) / 2; if(PanelTextWidth(StringSubstr(line, 0, mid)) <= maxW) lo = mid; else hi = mid - 1; }
      else lo = hi;
      int cut = lo;
      for(int k = lo; k > (first ? 0 : 5) + 10; k--) if(StringGetCharacter(line, k) == ' ') { cut = k; break; }
      PanelRow(row++, StringSubstr(line, 0, cut), clr);
      rest = StringSubstr(line, cut);
      StringTrimLeft(rest);
      if(rest == "") return;
      first = false;
   }
}

string TouchState(const int touch)
{
   if(touch < 0) return "";
   return StringFormat("  TOUCHED %d bar(s) ago - waiting for CISD", g_n - 1 - touch);
}

void DrawPanel(const bool force)
{
   if(!ShowPanel || !ChartOn()) return;
   if(!force && TimeCurrent() - g_lastPanel < 1) return;
   g_lastPanel = TimeCurrent();
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int row = 0;
   PanelLine(row, StringFormat("V6S-ICT  |  aligning levels (%d+ TFs, %d+ Major)  |  %s  price %.2f",
                                 MinAlignTimeframes, MinMajorTimeframes, TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES), bid), clrWhite);

   // resistances above price, highest first, then supports below, highest first
   int ri[], si[];
   for(int k = 0; k < ArraySize(g_resVal); k++) PushI(ri, k);
   for(int k = 0; k < ArraySize(g_supVal); k++) PushI(si, k);
   for(int a = 0; a < ArraySize(ri); a++) for(int b = a + 1; b < ArraySize(ri); b++)
      if(g_resVal[ri[b]] > g_resVal[ri[a]]) { int t = ri[a]; ri[a] = ri[b]; ri[b] = t; }
   for(int a = 0; a < ArraySize(si); a++) for(int b = a + 1; b < ArraySize(si); b++)
      if(g_supVal[si[b]] > g_supVal[si[a]]) { int t = si[a]; si[a] = si[b]; si[b] = t; }

   PanelLine(row, StringFormat("RESISTANCE (%d)", ArraySize(ri)), clrOrangeRed);
   for(int a = 0; a < ArraySize(ri) && a < 8; a++)
   {
      int k = ri[a];
      PanelLine(row, StringFormat("  R %.3f  (%+.2f)  [%s]%s%s", g_resVal[k], g_resVal[k] - bid, g_resDesc[k], TouchState(g_resTouch[k]),
                                    NeedM10(-1, g_resVal[k]) ? "  (last trade SL -> needs M10 CISD)" : ""),
                g_resTouch[k] >= 0 ? clrYellow : clrOrangeRed);
   }
   PanelLine(row, StringFormat("SUPPORT (%d)", ArraySize(si)), clrLime);
   for(int a = 0; a < ArraySize(si) && a < 8; a++)
   {
      int k = si[a];
      PanelLine(row, StringFormat("  S %.3f  (%+.2f)  [%s]%s%s", g_supVal[k], g_supVal[k] - bid, g_supDesc[k], TouchState(g_supTouch[k]),
                                    NeedM10(1, g_supVal[k]) ? "  (last trade SL -> needs M10 CISD)" : ""),
                g_supTouch[k] >= 0 ? clrYellow : clrLime);
   }

   ulong ticket; int dir;
   if(GetPosition(ticket, dir) && PositionSelectByTicket(ticket))
   {
      double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL);
      double sl0 = GlobalVariableCheck(GvKey(ticket, "sl0")) ? GlobalVariableGet(GvKey(ticket, "sl0")) : sl;
      double risk = MathAbs(open - sl0);
      double px = dir > 0 ? bid : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double r = risk > 0 ? (px - open) * dir / risk : 0;
      bool part = GlobalVariableCheck(GvKey(ticket, "part")) && GlobalVariableGet(GvKey(ticket, "part")) > 0;
      int ptp = GlobalVariableCheck(GvKey(ticket, "type")) ? (int)GlobalVariableGet(GvKey(ticket, "type")) : 0;
      PanelLine(row, StringFormat("TRADE %s%s %.2f @ %.2f  SL %.2f  now %+.2fR  %s%s  P/L %.2f", ptp == 2 ? "TRAP " : (ptp == 1 ? "BREAKOUT " : ""),
                                    dir > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME), open, sl, r,
                                    MathAbs(sl - open) < _Point * 2 ? "[BE] " : "", part ? "[PARTIAL DONE]" : "",
                                    PositionGetDouble(POSITION_PROFIT)), dir > 0 ? clrLime : clrOrangeRed);
   }
   else PanelLine(row, "TRADE  none", clrSilver);
   ulong lgt; int lgd;
   if(UseExtraLeg && LegGetPosition(lgt, lgd) && PositionSelectByTicket(lgt))
      PanelLine(row, StringFormat("EXTRA LEG %s %.2f @ %.2f  SL %.2f  TP %.2f  P/L %.2f", lgd > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME),
                                  PositionGetDouble(POSITION_PRICE_OPEN), PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP),
                                  PositionGetDouble(POSITION_PROFIT)), lgd > 0 ? clrLime : clrOrangeRed);
   if(UseBreakouts)
   {
      string bo = "";
      for(int k = 0; k < ArraySize(g_boVal); k++)
         bo += StringFormat("  %s %.2f (%d bars ago)", g_boSide[k] > 0 ? "UP" : "DOWN", g_boVal[k], g_n - 1 - g_boBar[k]);
      PanelLine(row, "BREAKOUTS waiting for CISD:" + (bo == "" ? "  none" : bo), bo == "" ? clrSilver : clrDeepSkyBlue);
   }
   PanelLine(row, StringFormat("BRAKE  buys %s (losing streak %d)  |  sells %s (losing streak %d)",
                                 g_paused[0] ? "PAUSED" : "active", g_streak[0], g_paused[1] ? "PAUSED" : "active", g_streak[1]),
             (g_paused[0] || g_paused[1]) ? clrRed : clrSilver);
   if(UseDZFilter)
      PanelLine(row, StringFormat("DZ FILTER  upper %.2f / lower %.2f  |  buys %s  |  sells %s", g_dzfZ2, g_dzfZ4,
                                    g_dzfBlockBuy ? "BLOCKED" : "allowed", g_dzfBlockSell ? "BLOCKED" : "allowed"),
                (g_dzfBlockBuy || g_dzfBlockSell) ? clrRed : clrSilver);
   if(UseDZBreakout)
   {
      ulong dt_; int dd_;
      string dzs = "no trade";
      if(DZGetPosition(dt_, dd_) && PositionSelectByTicket(dt_))
         dzs = StringFormat("OPEN %s %.2f @ %.2f  SL %.2f  TP %.2f  P/L %.2f", dd_ > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME),
                            PositionGetDouble(POSITION_PRICE_OPEN), PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP),
                            PositionGetDouble(POSITION_PROFIT));
      if(g_dzbR[1] == 0)
      {
         double z1, z2, z3, z4;
         if(DZAllLines(TimeCurrent(), z1, z2, z3, z4))
         { g_dzbR[0] = MathMin(z1, z2); g_dzbR[1] = MathMax(z1, z2); g_dzbS[0] = MathMin(z3, z4); g_dzbS[1] = MathMax(z3, z4); }
      }
      PanelLine(row, StringFormat("DZ BREAKOUT  R zone %.2f-%.2f  |  S zone %.2f-%.2f  |  %s", g_dzbR[0], g_dzbR[1], g_dzbS[0], g_dzbS[1], dzs),
                StringFind(dzs, "OPEN") == 0 ? clrDeepSkyBlue : clrSilver);
      if(g_dzbLast != "") PanelLine(row, "DZ LAST: " + g_dzbLast, clrDeepSkyBlue);
   }
   PanelLine(row, g_lastNew == "" ? " " : "LAST: " + g_lastNew, clrAqua);

   for(int k = row; k < PANEL_MAX_ROWS; k++) ObjectDelete(0, "V6SICT_PNL_" + IntegerToString(k));
}

//===================== Dynamic Zones filter =====================
datetime g_dzfDay = 0;          // D1 session the filter state belongs to
bool     g_dzfBlockBuy = false, g_dzfBlockSell = false;
double   g_dzfZ2 = 0, g_dzfZ4 = 0;

// v2.13: gap-delimited sessions, identical to the indicator's DZ_FindSessionStarts/DZ_CalcZone.
#define DZ_BAR_SECONDS 3600
datetime g_dzsKey = 0;                      // session start the cache below belongs to
double   g_dzsZ[4] = {0, 0, 0, 0};          // Z1, Z2, Z3, Z4 of that session

// Zone lines of the session containing t. sess = that session's first H1 bar time.
bool DZSession(const datetime t, double &z1, double &z2, double &z3, double &z4, datetime &sess)
{
   int maxLen = MathMax(DZShortLen, DZLongLen);
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_H1, t, (maxLen + 5) * 24, r);   // oldest first; last = the H1 bar holding t
   if(got < 2) return false;
   int st[]; ArrayResize(st, got);
   int n = 0; st[n++] = 0;
   for(int i = 1; i < got; i++)
      if((long)r[i].time - (long)r[i-1].time > DZ_BAR_SECONDS) st[n++] = i;
   int si = n - 1;                                                       // the session t is in
   if(si < maxLen) return false;                                          // not enough complete sessions before it
   sess = r[st[si]].time;
   if(sess == g_dzsKey) { z1 = g_dzsZ[0]; z2 = g_dzsZ[1]; z3 = g_dzsZ[2]; z4 = g_dzsZ[3]; return true; }
   double sumS = 0, sumL = 0;
   for(int k = 1; k <= maxLen; k++)
   {
      int b = st[si - k], e = st[si - k + 1] - 1;
      double hi = r[b].high, lo = r[b].low;
      for(int j = b + 1; j <= e; j++) { if(r[j].high > hi) hi = r[j].high; if(r[j].low < lo) lo = r[j].low; }
      if(k <= DZShortLen) sumS += hi - lo;
      if(k <= DZLongLen)  sumL += hi - lo;
   }
   double o = r[st[si]].open, h5 = sumS / DZShortLen / 2.0, h10 = sumL / DZLongLen / 2.0;
   z1 = o + h5; z2 = o + h10; z3 = o - h5; z4 = o - h10;
   g_dzsKey = sess; g_dzsZ[0] = z1; g_dzsZ[1] = z2; g_dzsZ[2] = z3; g_dzsZ[3] = z4;
   return true;
}

// Outer zone lines for the session containing t.
bool DZOuterLines(const datetime t, double &z2, double &z4, datetime &day)
{
   double z1, z3;
   return DZSession(t, z1, z2, z3, z4, day);
}

// Called for every closed M5 candle before any setup is evaluated.
void DZFilterStep(const datetime barTime, const double close)
{
   if(!UseDZFilter) return;
   double z2, z4; datetime day;
   if(!DZOuterLines(barTime, z2, z4, day)) return;
   if(day != g_dzfDay)
   {
      if(g_dzfBlockBuy || g_dzfBlockSell) Log("DZ FILTER reset -- new session");
      g_dzfDay = day; g_dzfBlockBuy = false; g_dzfBlockSell = false;
   }
   g_dzfZ2 = z2; g_dzfZ4 = z4;
   if(close > z2 && !g_dzfBlockSell) { g_dzfBlockSell = true; Log(StringFormat("DZ FILTER: M5 closed %.3f ABOVE upper zone %.3f -- SELLS blocked for today", close, z2)); }
   if(close < z4 && !g_dzfBlockBuy)  { g_dzfBlockBuy  = true; Log(StringFormat("DZ FILTER: M5 closed %.3f BELOW lower zone %.3f -- BUYS blocked for today", close, z4)); }
}
bool DZBlocked(const int dir){ return UseDZFilter && (dir > 0 ? g_dzfBlockBuy : g_dzfBlockSell); }

// v2.11: "HH:MM" -> minutes after midnight (-1 if invalid)
int HhmmToMin(const string s)
{
   string p[];
   if(StringSplit(s, ':', p) != 2) return -1;
   int h = (int)StringToInteger(p[0]), m = (int)StringToInteger(p[1]);
   if(h < 0 || h > 23 || m < 0 || m > 59) return -1;
   return h * 60 + m;
}
// v2.11: true while new reversal entries are blocked (IST window, may wrap past midnight)
bool RevTimeBlocked()
{
   if(!UseRevTimeBlock) return false;
   int a = HhmmToMin(RevBlockFrom), b = HhmmToMin(RevBlockTo);
   if(a < 0 || b < 0 || a == b) return false;
   int now = (int)(((long)TimeCurrent() + ServerToISTMinutes * 60) % 86400) / 60;
   return a < b ? (now >= a && now < b) : (now >= a || now < b);
}

//===================== Dynamic Zones (data only) =====================
datetime g_dzDay = 0;
double   g_z1 = 0, g_z2 = 0, g_z3 = 0, g_z4 = 0;

void UpdateDynamicZones()
{
   double a1, a2, a3, a4; datetime d;
   if(!DZSession(TimeCurrent(), a1, a2, a3, a4, d) || d == g_dzDay) return;   // v2.13: gap-delimited session
   double o = (a1 + a3) / 2.0;
   g_z1 = a1; g_z2 = a2; g_z3 = a3; g_z4 = a4;
   g_dzDay = d;
   if(ChartOn())
   {
      string day = TimeToString(d, TIME_DATE) + "_" + IntegerToString((long)d);
      double zs[4]; zs[0] = g_z2; zs[1] = g_z1; zs[2] = g_z3; zs[3] = g_z4;
      string zn[4] = {"Z2", "Z1", "Z3", "Z4"};
      for(int k = 0; k < 4; k++)
      {
         string nm = "V6SICT_DZ_" + day + "_" + zn[k];
         ObjectCreate(0, nm, OBJ_TREND, 0, d, zs[k], d + 86400, zs[k]);
         ObjectSetInteger(0, nm, OBJPROP_COLOR, clrDodgerBlue);
         ObjectSetInteger(0, nm, OBJPROP_STYLE, STYLE_DASHDOT);
         ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, nm, OBJPROP_BACK, true);
         ObjectSetString(0, nm, OBJPROP_TOOLTIP, "Dynamic Zone " + zn[k] + " (session from " + TimeToString(d, TIME_DATE | TIME_MINUTES) + ")");
         SetText(nm + "_T", d, zs[k], "DZ " + zn[k], clrDodgerBlue, 7, ANCHOR_LEFT_LOWER);
      }
   }
   Log(StringFormat("Dynamic Zones -- session from %s (server): Z2 %.3f  Z1 %.3f  | open %.3f |  Z3 %.3f  Z4 %.3f",
                    TimeToString(d, TIME_DATE | TIME_MINUTES), g_z2, g_z1, o, g_z3, g_z4));
}

//===================== Trading =====================
CTrade g_trade;

// losing-streak brake, index 0 = buys, 1 = sells
int    g_streak[2] = {0, 0};
bool   g_paused[2] = {false, false};
ulong  g_trackTicket = 0;     // the position we are watching for its close
int    g_trackDir = 0;
bool   g_trackBE = false;     // SL was at breakeven the last time we looked
double g_trackLevel = 0;      // aligning level the tracked trade came from
int    g_trackType = 0;       // 0 = support/resistance reversal, 1 = breakout

int DirIdx(const int dir){ return dir > 0 ? 0 : 1; }
string DirName(const int dir){ return dir > 0 ? "buys" : "sells"; }

// Called when the tracked position is gone: classify the close and update the brake.
void OnTrackedClosed()
{
   if(g_trackTicket == 0) return;
   double profit = 0; bool bySL = false;
   if(HistorySelectByPosition(g_trackTicket))
   {
      for(int k = 0; k < HistoryDealsTotal(); k++)
      {
         ulong d = HistoryDealGetTicket(k);
         if(d == 0) continue;
         profit += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) + HistoryDealGetDouble(d, DEAL_COMMISSION);
         if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_OUT && HistoryDealGetInteger(d, DEAL_REASON) == DEAL_REASON_SL)
            bySL = true;
      }
   }
   int i = DirIdx(g_trackDir);
   if(g_trackType != 0) { g_trackTicket = 0; return; }   // breakouts: no brake / M10 bookkeeping
   if(bySL && !g_trackBE && profit < 0 && M10AfterSL && g_trackLevel > 0)
   {
      SetM10(g_trackDir, g_trackLevel);
      Log(StringFormat("#%I64u hit SL at level %.3f -- this level's next %s needs an M10 CISD",
                       g_trackTicket, g_trackLevel, g_trackDir > 0 ? "buy" : "sell"));
   }
   if(bySL && !g_trackBE && profit < 0)
   {
      g_streak[i]++;
      Log(StringFormat("#%I64u stopped out (%.2f) -- %s losing streak %d", g_trackTicket, profit, DirName(g_trackDir), g_streak[i]));
      if(LossStreakBrake > 0 && g_streak[i] >= LossStreakBrake && !g_paused[i])
      {
         g_paused[i] = true;
         Log(StringFormat("BRAKE ON: %s paused after %d stop-losses in a row (until a %s trade opens)",
                          DirName(g_trackDir), g_streak[i], DirName(-g_trackDir)));
         Marker("V6SICT_BRK_" + IntegerToString((long)TimeCurrent()), TimeCurrent(),
                SymbolInfoDouble(_Symbol, SYMBOL_BID), 251, clrRed, DirName(g_trackDir) + " paused (losing streak)");
      }
   }
   else if(profit > 0)
      g_streak[i] = 0;
   g_trackTicket = 0;
}

// v2.07: rebuild a position's saved data if a crash lost it -- level/type from the order comment
// ("V6SICT BD 4191.13"), original SL from the opening order, partial flag from the volume.
void EnsureTradeData(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return;
   string kl = GvKey(ticket, "lvl"), kt = GvKey(ticket, "type"), k0 = GvKey(ticket, "sl0"), kp = GvKey(ticket, "part");
   if(GlobalVariableCheck(kl) && GlobalVariableCheck(kt) && GlobalVariableCheck(k0) && GlobalVariableCheck(kp)) return;
   double vol = PositionGetDouble(POSITION_VOLUME), sl = PositionGetDouble(POSITION_SL);
   string cm = PositionGetString(POSITION_COMMENT);
   string what = "";
   if(!GlobalVariableCheck(kl) || !GlobalVariableCheck(kt))
   {
      string parts[];
      int n = StringSplit(cm, ' ', parts);
      if(n >= 3 && parts[0] == "V6SICT")
      {
         double lv = StringToDouble(parts[n - 1]);
         if(lv > 0 && !GlobalVariableCheck(kl)) { GlobalVariableSet(kl, lv); what += StringFormat(" level %.2f", lv); }
         if(!GlobalVariableCheck(kt)) { GlobalVariableSet(kt, (parts[1] == "BO" || parts[1] == "BD") ? 1 : ((parts[1] == "TB" || parts[1] == "TS") ? 2 : 0)); what += " type"; }
      }
   }
   if(!GlobalVariableCheck(k0) || !GlobalVariableCheck(kp))
   {
      double sl0 = sl, vol0 = vol;
      if(HistorySelectByPosition(ticket))
         for(int k = 0; k < HistoryDealsTotal(); k++)
         {
            ulong dl = HistoryDealGetTicket(k);
            if(dl == 0 || HistoryDealGetInteger(dl, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
            vol0 = HistoryDealGetDouble(dl, DEAL_VOLUME);
            ulong ord = (ulong)HistoryDealGetInteger(dl, DEAL_ORDER);
            if(HistoryOrderSelect(ord) && HistoryOrderGetDouble(ord, ORDER_SL) > 0) sl0 = HistoryOrderGetDouble(ord, ORDER_SL);
            break;
         }
      if(!GlobalVariableCheck(k0) && sl0 > 0) { GlobalVariableSet(k0, sl0); what += StringFormat(" original SL %.3f", sl0); }
      if(!GlobalVariableCheck(kp))
      {
         bool part = vol < vol0 - SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP) / 2;
         GlobalVariableSet(kp, part ? 1 : 0); what += part ? " partial=done" : " partial=not yet";
      }
   }
   if(what != "") { GlobalVariablesFlush(); Log(StringFormat("#%I64u saved data rebuilt after a restart:%s", ticket, what)); }
}

// Every tick: notice our position closing (SL, manual, reverse) and remember whether it was at BE.
void TrackPosition()
{
   ulong ticket; int dir;
   bool have = GetPosition(ticket, dir);
   if(g_trackTicket != 0 && (!have || ticket != g_trackTicket)) OnTrackedClosed();
   if(have)
   {
      if(g_trackTicket != ticket)
      {
         EnsureTradeData(ticket);
         g_trackTicket = ticket; g_trackDir = dir; g_trackBE = false;
         g_trackLevel = GlobalVariableCheck(GvKey(ticket, "lvl")) ? GlobalVariableGet(GvKey(ticket, "lvl")) : 0;
         g_trackType  = GlobalVariableCheck(GvKey(ticket, "type")) ? (int)GlobalVariableGet(GvKey(ticket, "type")) : 0;
      }
      if(PositionSelectByTicket(ticket))
         g_trackBE = MathAbs(PositionGetDouble(POSITION_SL) - PositionGetDouble(POSITION_PRICE_OPEN)) < 2 * _Point;
   }
}

bool GetPosition(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      ticket = t;
      dir = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      return true;
   }
   return false;
}

string GvKey(const ulong ticket, const string what){ return StringFormat("V6SICT_%I64u_%s", ticket, what); }

void OpenTrade(const int dir, double sl, const double level, const string why, const double lots = -1, const int type = 0)
{
   if(g_replay) return;   // v2.07: replayed candle -- the setup is used up, no order
   double vol = lots > 0 ? lots : LotSize;
   double price = dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(MaxSLPrice > 0 && MathAbs(price - sl) > MaxSLPrice)
   {
      Log(StringFormat("SL %.3f is %.2f away -- capped at %.2f", sl, MathAbs(price - sl), MaxSLPrice));
      sl = price - dir * MaxSLPrice;
   }
   if((dir > 0 && sl >= price) || (dir < 0 && sl <= price))
   {
      Log(StringFormat("%s skipped: SL %.3f on the wrong side of price %.3f", dir > 0 ? "BUY" : "SELL", sl, price));
      return;
   }
   double needRR = type == 1 ? MinRRBreakout : (type == 0 ? MinRRReversal : 0.0);   // v2.15: trap trades have their own fixed TP
   if(needRR > 0 && UseOppositeLevelTP)
   {
      double tgt = OppositeTarget(dir);
      if(tgt > 0 && MathAbs(tgt - price) < needRR * MathAbs(price - sl))
      {
         Log(StringFormat("%s%s skipped: opposite-level TP %.3f is only %.2fR (needs %.1fR)", type == 1 ? (dir > 0 ? "BREAKOUT " : "BREAKDOWN ") : "",
                          dir > 0 ? "BUY" : "SELL", tgt, MathAbs(tgt - price) / MathAbs(price - sl), needRR));
         return;
      }
   }
   sl = NormalizeDouble(sl, _Digits);
   int oi = DirIdx(-dir);
   if(g_paused[oi] || g_streak[oi] > 0)
   {
      if(g_paused[oi]) Log(StringFormat("BRAKE OFF: %s resume -- a %s trade is opening", DirName(-dir), dir > 0 ? "buy" : "sell"));
      g_paused[oi] = false; g_streak[oi] = 0;
   }
   string comment = StringFormat("V6SICT %s %.2f", dir > 0 ? "B" : "S", level);
   if(type == 1) comment = StringFormat("V6SICT %s %.2f", dir > 0 ? "BO" : "BD", level);
   if(type == 2) comment = StringFormat("V6SICT %s %.2f", dir > 0 ? "TB" : "TS", level);
   bool ok = dir > 0 ? g_trade.Buy(vol, _Symbol, 0.0, sl, 0.0, comment)
                     : g_trade.Sell(vol, _Symbol, 0.0, sl, 0.0, comment);
   uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
   {
      Print("[V6S-ICT] ", dir > 0 ? "BUY" : "SELL", " FAILED retcode=", rc, " ", g_trade.ResultRetcodeDescription());
      return;
   }
   ulong ticket = g_trade.ResultOrder();
   // the position ticket equals the opening order ticket on MT5
   GlobalVariableSet(GvKey(ticket, "sl0"), sl);
   GlobalVariableSet(GvKey(ticket, "part"), 0);
   GlobalVariableSet(GvKey(ticket, "lvl"), level);
   GlobalVariableSet(GvKey(ticket, "type"), type);
   GlobalVariablesFlush();   // v2.07: on disk now, not only at terminal shutdown
   if(type == 0) ClearM10(dir, level);   // this level just traded -- its M10 requirement (if any) is used up
   datetime bt = iTime(_Symbol, PERIOD_M5, 0);
   double ep = g_trade.ResultPrice();
   Note("V6SICT_E_" + IntegerToString((long)bt), bt, dir > 0 ? sl : sl,
        StringFormat("%s%s @%.2f SL %.2f (1R %.2f) lvl %.2f", type == 1 ? (dir > 0 ? "BREAKOUT " : "BREAKDOWN ") : (type == 2 ? "TRAP " : ""),
                     dir > 0 ? "BUY" : "SELL", ep, sl, MathAbs(ep - sl), level),
        dir > 0 ? clrLime : clrOrangeRed, dir < 0);
   if(ChartOn())
   {
      string nm = "V6SICT_SL_" + IntegerToString((long)bt);
      ObjectCreate(0, nm, OBJ_TREND, 0, bt, sl, bt + 12 * PeriodSeconds(PERIOD_M5), sl);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clrMagenta);
      ObjectSetInteger(0, nm, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetString(0, nm, OBJPROP_TOOLTIP, "initial SL");
      double r = MathAbs(ep - sl);
      for(int m = 0; m < 2; m++)
      {
         double mult = (m == 0) ? BreakevenR : PartialR;   // the multiples the EA actually uses
         string lab  = (m == 0) ? StringFormat("1:%g BE", BreakevenR) : StringFormat("1:%g partial", PartialR);
         string nm2 = "V6SICT_R" + IntegerToString(m) + "_" + IntegerToString((long)bt);
         double y = ep + dir * mult * r;
         ObjectCreate(0, nm2, OBJ_TREND, 0, bt, y, bt + 12 * PeriodSeconds(PERIOD_M5), y);
         ObjectSetInteger(0, nm2, OBJPROP_COLOR, m == 0 ? clrGold : clrAqua);
         ObjectSetInteger(0, nm2, OBJPROP_STYLE, STYLE_DOT);
         ObjectSetString(0, nm2, OBJPROP_TOOLTIP, m == 0 ? lab + " -> SL to breakeven" : lab + " -> close all but the runner");
         SetText(nm2 + "_T", bt + 12 * PeriodSeconds(PERIOD_M5), y, lab, m == 0 ? clrGold : clrAqua, 7, ANCHOR_LEFT);
      }
   }
   Log(StringFormat("%s%s %.2f @ %.3f  SL %.3f  risk %.3f  level %.3f  (%s)", type == 1 ? "BREAKOUT " : (type == 2 ? "TRAP " : ""),
                    dir > 0 ? "BUY" : "SELL", vol, g_trade.ResultPrice(), sl,
                    MathAbs(g_trade.ResultPrice() - sl), level, why));
   UpdateTP();
}

// A. TP at the nearest opposite aligning level (buy: resistance above - buffer; sell: support below + buffer).
// Nearest opposite aligning level +/- TPBufferPrice beyond current price (0 = none) -- the TP a new trade would get.
double OppositeTarget(const int dir)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double tp = 0.0;
   if(dir > 0)
   {
      for(int k = 0; k < ArraySize(g_resVal); k++)
      {  double t = g_resVal[k] - TPBufferPrice; if(t > bid && (tp == 0.0 || t < tp)) tp = t; }
   }
   else
   {
      for(int k = 0; k < ArraySize(g_supVal); k++)
      {  double t = g_supVal[k] + TPBufferPrice; if(t < ask && (tp == 0.0 || t > tp)) tp = t; }
   }
   return tp;
}

// v2.14: nearest session target for REVERSALS -- the previous gap-session's high (buy) / low (sell) or the
// current session's high / low so far (closed M5 candles), whichever is nearer beyond price, -/+ TPBufferPrice.
bool SessionTarget(const int dir, const double bid, const double ask, const double stopsLvl, double &target, string &label)
{
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_H1, 0, 96, r);          // oldest first
   if(got < 2) return false;
   int st[]; ArrayResize(st, got);
   int n = 0; st[n++] = 0;
   for(int i = 1; i < got; i++)
      if((long)r[i].time - (long)r[i-1].time > DZ_BAR_SECONDS) st[n++] = i;
   if(n < 2) return false;
   int cs = st[n - 1], ps = st[n - 2];
   double ph = r[ps].high, pl = r[ps].low;
   for(int j = ps + 1; j < cs; j++) { if(r[j].high > ph) ph = r[j].high; if(r[j].low < pl) pl = r[j].low; }
   double best = 0.0; string lab = "";
   double c1 = dir > 0 ? ph - TPBufferPrice : pl + TPBufferPrice;
   if(dir > 0 ? c1 > bid + stopsLvl : c1 < ask - stopsLvl) { best = c1; lab = StringFormat("previous session %s %.3f", dir > 0 ? "high" : "low", dir > 0 ? ph : pl); }
   int shs = iBarShift(_Symbol, PERIOD_M5, r[cs].time, false);   // M5 candle at the current session's start
   if(shs >= 1)
   {
      int k = dir > 0 ? iHighest(_Symbol, PERIOD_M5, MODE_HIGH, shs, 1) : iLowest(_Symbol, PERIOD_M5, MODE_LOW, shs, 1);
      if(k >= 1)
      {
         double ext = dir > 0 ? iHigh(_Symbol, PERIOD_M5, k) : iLow(_Symbol, PERIOD_M5, k);
         double c2 = dir > 0 ? ext - TPBufferPrice : ext + TPBufferPrice;
         if((dir > 0 ? c2 > bid + stopsLvl : c2 < ask - stopsLvl) && (best == 0.0 || (dir > 0 ? c2 < best : c2 > best)))
         { best = c2; lab = StringFormat("session %s so far %.3f", dir > 0 ? "high" : "low", ext); }
      }
   }
   if(best == 0.0) return false;
   target = best; label = lab + StringFormat(" %s %.1f", dir > 0 ? "-" : "+", TPBufferPrice);
   return true;
}

// v2.15: a trap trade keeps a fixed TP at TrapTPR x its initial risk. Returns true when the open trade is a trap trade.
bool TrapTP()
{
   ulong ticket; int dir;
   if(!GetPosition(ticket, dir) || !PositionSelectByTicket(ticket)) return false;
   if(!GlobalVariableCheck(GvKey(ticket, "type")) || (int)GlobalVariableGet(GvKey(ticket, "type")) != 2) return false;
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl0 = GlobalVariableCheck(GvKey(ticket, "sl0")) ? GlobalVariableGet(GvKey(ticket, "sl0")) : PositionGetDouble(POSITION_SL);
   double risk = MathAbs(open - sl0);
   if(risk <= 0) return true;
   double tp = NormalizeDouble(open + dir * TrapTPR * risk, _Digits);
   if(MathAbs(PositionGetDouble(POSITION_TP) - tp) < _Point) return true;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double stopsLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(dir > 0 ? tp <= bid + stopsLvl : tp >= ask - stopsLvl) return true;
   if(g_trade.PositionModify(ticket, PositionGetDouble(POSITION_SL), tp))
      Log(StringFormat("#%I64u TP -> %.3f (trap trade, fixed 1:%g)", ticket, tp, TrapTPR));
   return true;
}

void UpdateTP()
{
   if(g_replay) return;
   if(TrapTP()) return;
   if(!UseOppositeLevelTP) return;
   ulong ticket; int dir;
   if(!GetPosition(ticket, dir) || !PositionSelectByTicket(ticket)) return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double stopsLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double tp = 0.0;
   if(dir > 0)
   {
      for(int k = 0; k < ArraySize(g_resVal); k++)
      {
         double t = g_resVal[k] - TPBufferPrice;
         if(t > bid + stopsLvl && (tp == 0.0 || t < tp)) tp = t;
      }
   }
   else
   {
      for(int k = 0; k < ArraySize(g_supVal); k++)
      {
         double t = g_supVal[k] + TPBufferPrice;
         if(t < ask - stopsLvl && (tp == 0.0 || t > tp)) tp = t;
      }
   }
   // v2.14: reversals aim at the nearest session high/low instead (aligning level stays the fallback)
   string sessWhy = "";
   if(UseSessionTPRev)
   {
      int ptype = GlobalVariableCheck(GvKey(ticket, "type")) ? (int)GlobalVariableGet(GvKey(ticket, "type")) : 0;
      double st_ = 0.0; string sl_ = "";
      if(ptype == 0 && SessionTarget(dir, bid, ask, stopsLvl, st_, sl_)) { tp = st_; sessWhy = sl_; }
   }
   // v2.06: a target closer than MinTPR x the initial risk to entry is too near -> 1:1 instead
   string why = sessWhy != "" ? sessWhy : (tp > 0 ? StringFormat("nearest opposite aligning level %s %.1f", dir > 0 ? "-" : "+", TPBufferPrice) : "none (no opposite level)");
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl0  = GlobalVariableCheck(GvKey(ticket, "sl0")) ? GlobalVariableGet(GvKey(ticket, "sl0")) : 0.0;
   double risk = sl0 > 0 ? MathAbs(open - sl0) : 0.0;
   if(tp > 0 && MinTPR > 0 && risk > 0 && (tp - open) * dir < MinTPR * risk)
   {
      double t1 = open + dir * risk;
      if(dir > 0 ? t1 > bid + stopsLvl : t1 < ask - stopsLvl)
      { why = StringFormat("1:1 -- target %.3f is only %.2fR from entry", tp, (tp - open) * dir / risk); tp = t1; }
   }
   tp = NormalizeDouble(tp, _Digits);
   double cur = PositionGetDouble(POSITION_TP);
   if(MathAbs(cur - tp) < _Point) return;
   if(g_trade.PositionModify(ticket, PositionGetDouble(POSITION_SL), tp))
      Log(StringFormat("#%I64u TP -> %s", ticket, tp > 0 ? StringFormat("%.3f (%s)", tp, why) : why));
}

// Every tick: breakeven at BreakevenR, partial close at PartialR.
void ManagePosition()
{
   ulong ticket; int dir;
   if(!GetPosition(ticket, dir)) return;
   if(!PositionSelectByTicket(ticket)) return;
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl   = PositionGetDouble(POSITION_SL);
   double vol  = PositionGetDouble(POSITION_VOLUME);
   string k0 = GvKey(ticket, "sl0");
   double sl0 = GlobalVariableCheck(k0) ? GlobalVariableGet(k0) : sl;
   if(!GlobalVariableCheck(k0)) { GlobalVariableSet(k0, sl0); GlobalVariablesFlush(); }
   double risk = MathAbs(open - sl0);
   if(risk <= 0) return;
   double px = dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double gained = dir > 0 ? px - open : open - px;

   bool beDone = dir > 0 ? (sl >= open - _Point) : (sl > 0 && sl <= open + _Point);
   if(!beDone && gained >= BreakevenR * risk)
   {
      if(g_trade.PositionModify(ticket, NormalizeDouble(open, _Digits), PositionGetDouble(POSITION_TP)))
      {
         Log(StringFormat("#%I64u reached %.1fR -> SL to breakeven %.3f", ticket, BreakevenR, open));
         Marker("V6SICT_BE_" + IntegerToString((long)ticket), TimeCurrent(), px, 108, clrGold, "SL moved to breakeven");
      }
   }

   string kp = GvKey(ticket, "part");
   bool partDone = GlobalVariableCheck(kp) && GlobalVariableGet(kp) > 0;
   if(!partDone && gained >= PartialR * risk)
   {
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double closeVol = NormalizeDouble(MathFloor((vol - RunnerLots) / step + 1e-9) * step, 2);
      if(closeVol >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
      {
         if(g_trade.PositionClosePartial(ticket, closeVol, SlippagePoints))
         {
            GlobalVariableSet(kp, 1); GlobalVariablesFlush();
            Log(StringFormat("#%I64u reached %.1fR -> closed %.2f, runner %.2f left", ticket, PartialR, closeVol, vol - closeVol));
            Marker("V6SICT_PC_" + IntegerToString((long)ticket), TimeCurrent(), px, 110, clrAqua, StringFormat("partial close at 1:%g", PartialR));
         }
      }
      else { GlobalVariableSet(kp, 1); GlobalVariablesFlush(); }
   }
}

//===================== Extra leg (v2.12) =====================
CTrade g_legTrade;

bool LegGetPosition(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != LegMagicNumber) continue;
      ticket = t;
      dir = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      return true;
   }
   return false;
}

// Close the extra leg (when the first trade is closed by an opposite setup / auto square-off).
void CloseLegs(const string why)
{
   if(g_replay) return;
   ulong lt; int ld;
   for(int n = 0; n < 3 && LegGetPosition(lt, ld); n++)
   {
      if(g_legTrade.PositionClose(lt, SlippagePoints)) Log(StringFormat("LEG #%I64u closed with the first trade (%s)", lt, why));
      else { Print("[V6S-ICT] leg close #", lt, " failed retcode=", g_legTrade.ResultRetcode()); break; }
   }
}

// A same-side setup while the first trade is open: add one leg if the first trade is in profit,
// and move the first trade's SL to breakeven. Returns true if the leg was opened (setup used up).
bool TryExtraLeg(const int dir, double sl, const double level, const ulong mainTicket, const bool isBO)
{
   if(!UseExtraLeg || g_replay) return false;
   ulong lt; int ld;
   if(LegGetPosition(lt, ld)) return false;                 // max one extra leg
   if(DZBlocked(dir)) return false;
   if(!isBO && RevTimeBlocked()) return false;
   if(!PositionSelectByTicket(mainTicket)) return false;
   double open = PositionGetDouble(POSITION_PRICE_OPEN), msl = PositionGetDouble(POSITION_SL), mtp = PositionGetDouble(POSITION_TP);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double mpx = dir > 0 ? bid : ask;
   if((mpx - open) * dir <= 0) return false;                // only when the first trade is in profit
   double price = dir > 0 ? ask : bid;
   if(MaxSLPrice > 0 && MathAbs(price - sl) > MaxSLPrice) sl = price - dir * MaxSLPrice;
   if((dir > 0 && sl >= price) || (dir < 0 && sl <= price)) return false;
   double risk = MathAbs(price - sl);
   sl = NormalizeDouble(sl, _Digits);
   double tp = NormalizeDouble(price + dir * ExtraLegTPR * risk, _Digits);
   double vol = isBO ? BreakoutLots : LotSize;
   string comment = StringFormat("V6SICT LEG %.2f", level);
   bool ok = dir > 0 ? g_legTrade.Buy(vol, _Symbol, 0.0, sl, tp, comment) : g_legTrade.Sell(vol, _Symbol, 0.0, sl, tp, comment);
   uint rc = g_legTrade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
   { Print("[V6S-ICT] LEG ", dir > 0 ? "BUY" : "SELL", " FAILED retcode=", rc, " ", g_legTrade.ResultRetcodeDescription()); return false; }
   Log(StringFormat("EXTRA LEG %s %.2f @ %.3f  SL %.3f  TP %.3f (1:%.1f)  -- same-side %s at %.3f while #%I64u is in profit",
                    dir > 0 ? "BUY" : "SELL", vol, g_legTrade.ResultPrice(), sl, tp, ExtraLegTPR, isBO ? "breakout" : "setup", level, mainTicket));
   // first trade -> breakeven
   bool beNeeded = dir > 0 ? (msl < open - _Point) : (msl == 0 || msl > open + _Point);
   if(beNeeded)
   {
      if(g_trade.PositionModify(mainTicket, NormalizeDouble(open, _Digits), mtp))
         Log(StringFormat("#%I64u SL -> breakeven %.3f (extra leg added)", mainTicket, open));
      else Print("[V6S-ICT] BE modify #", mainTicket, " failed retcode=", g_trade.ResultRetcode());
   }
   datetime nt = iTime(_Symbol, PERIOD_M5, 0);
   Note("V6SICT_LEG_" + IntegerToString((long)nt), nt, sl, StringFormat("LEG %s @%.2f SL %.2f TP %.2f", dir > 0 ? "BUY" : "SELL",
        g_legTrade.ResultPrice(), sl, tp), dir > 0 ? clrLime : clrOrangeRed, dir < 0);
   return true;
}

void CloseTrade(const ulong ticket, const string why)
{
   if(g_replay) return;
   if(g_trade.PositionClose(ticket, SlippagePoints))
   {
      Log(StringFormat("#%I64u closed (%s)", ticket, why));
      CloseLegs(why);
   }
   else
      Print("[V6S-ICT] close #", ticket, " failed retcode=", g_trade.ResultRetcode());
}

//+------------------------------------------------------------------+
//| One closed M5 candle: CISD, setups, touches.                      |
//+------------------------------------------------------------------+
void OnM5Bar(const int cisd)
{
   int i = g_n - 1;
   DZFilterStep(g_barTime, g_c[i]);
   if(cisd != 0)
   {
      datetime ct = g_barTime;
      Marker("V6SICT_C_" + IntegerToString((long)ct), ct, cisd > 0 ? g_l[i] : g_h[i], 159,
             cisd > 0 ? clrAqua : clrMagenta, cisd > 0 ? "bullish CISD" : "bearish CISD");
   }
   if(g_c10Sig != 0)
   {
      datetime ct = g_barTime;
      Marker("V6SICT_C10_" + IntegerToString((long)ct), ct, g_c10Sig > 0 ? g_l[i] - 1.0 : g_h[i] + 1.0, 164,
             g_c10Sig > 0 ? clrAqua : clrMagenta, g_c10Sig > 0 ? "bullish M10 CISD" : "bearish M10 CISD");
   }
   // C. AUTO SQUARE-OFF: an opposite CISD closing beyond the open trade's level
   if(AutoSquareOff && cisd != 0)
   {
      ulong tk; int td;
      if(GetPosition(tk, td) && cisd == -td)
      {
         double tl = GlobalVariableCheck(GvKey(tk, "lvl")) ? GlobalVariableGet(GvKey(tk, "lvl")) : 0;
         if(tl > 0 && (td > 0 ? g_c[i] < tl : g_c[i] > tl))
         {
            int ttype = GlobalVariableCheck(GvKey(tk, "type")) ? (int)GlobalVariableGet(GvKey(tk, "type")) : 0;
            CloseTrade(tk, StringFormat("AUTO SQUARE-OFF: %s CISD closed %s its level %.3f", cisd > 0 ? "bullish" : "bearish",
                                        td > 0 ? "below" : "above", tl));
            TrackPosition();
            // v2.15: the setup failed -> trap trade the other way (never from a trap trade itself)
            ulong tk2; int td2;
            if(UseTrapTrade && ttype != 2 && !g_replay && !GetPosition(tk2, td2))
               OpenTrade(-td, tl + td * TrapSLBuffer, tl, StringFormat("TRAP: %s at %.3f failed (%s CISD closed back %s it)",
                         td > 0 ? "buy" : "sell", tl, cisd > 0 ? "bullish" : "bearish", td > 0 ? "below" : "above"), LotSize, 2);
         }
      }
   }

   // B. breakout candidates: expire, or cancel on a close back through the level
   for(int k = ArraySize(g_boVal) - 1; k >= 0; k--)
      if(i - g_boBar[k] > BreakoutValidBars || (g_boSide[k] > 0 ? g_c[i] < g_boVal[k] : g_c[i] > g_boVal[k]))
         RemoveBO(k);

   int sig = 0; double lvl = 0, sl = 0; int lvlIdx = -1; string how = "M5";

   // A level qualifies when it was touched on an EARLIER M5 candle and its confirmation fired:
   // an M10 CISD closing beyond it if its last trade hit SL, an M5 CISD closing beyond it otherwise.
   int bBest = -1, sBest = -1;
   for(int k = 0; k < ArraySize(g_supVal); k++)
   {
      int t = g_supTouch[k];
      if(t < 0 || t >= i) continue;
      bool ok = NeedM10(1, g_supVal[k]) ? (g_c10Sig > 0 && g_c10Close > g_supVal[k]) : (cisd > 0 && g_c[i] > g_supVal[k]);
      if(!ok) continue;
      if(bBest < 0 || t > g_supTouch[bBest] || (t == g_supTouch[bBest] && g_supVal[k] > g_supVal[bBest])) bBest = k;
   }
   for(int k = 0; k < ArraySize(g_resVal); k++)
   {
      int t = g_resTouch[k];
      if(t < 0 || t >= i) continue;
      bool ok = NeedM10(-1, g_resVal[k]) ? (g_c10Sig < 0 && g_c10Close < g_resVal[k]) : (cisd < 0 && g_c[i] < g_resVal[k]);
      if(!ok) continue;
      if(sBest < 0 || t > g_resTouch[sBest] || (t == g_resTouch[sBest] && g_resVal[k] < g_resVal[sBest])) sBest = k;
   }
   if(bBest >= 0 && (sBest < 0 || g_supTouch[bBest] >= g_resTouch[sBest]))
   {
      double lowest = DBL_MAX;
      int s0 = (SLFromTouch && g_supEst[bBest] >= 0) ? g_supEst[bBest] : g_supTouch[bBest] + 1;
      for(int k = s0; k <= i; k++) lowest = MathMin(lowest, g_l[k]);
      sig = 1; lvl = g_supVal[bBest]; sl = lowest - SLBufferPrice; lvlIdx = bBest;
      how = NeedM10(1, lvl) ? "M10" : "M5";
   }
   else if(sBest >= 0)
   {
      double highest = -DBL_MAX;
      int s0 = (SLFromTouch && g_resEst[sBest] >= 0) ? g_resEst[sBest] : g_resTouch[sBest] + 1;
      for(int k = s0; k <= i; k++) highest = MathMax(highest, g_h[k]);
      sig = -1; lvl = g_resVal[sBest]; sl = highest + SLBufferPrice; lvlIdx = sBest;
      how = NeedM10(-1, lvl) ? "M10" : "M5";
   }

   if(sig != 0)
   {
      ulong ticket; int dir;
      bool inTrade = GetPosition(ticket, dir);
      if(inTrade && dir == sig)
      {
         if(TryExtraLeg(sig, sl, lvl, ticket, false)) { if(sig > 0) g_supTouch[lvlIdx] = -1; else g_resTouch[lvlIdx] = -1; }
         else Log(StringFormat("%s setup at %.3f ignored -- already in a %s", sig > 0 ? "BUY" : "SELL", lvl, sig > 0 ? "buy" : "sell"));
      }
      else if(DZBlocked(sig))
         Log(StringFormat("%s setup at %.3f blocked by the DZ filter", sig > 0 ? "BUY" : "SELL", lvl));
      else if(RevTimeBlocked())
      {
         Log(StringFormat("%s reversal at %.3f skipped -- reversal entries blocked %s-%s IST", sig > 0 ? "BUY" : "SELL", lvl, RevBlockFrom, RevBlockTo));
         if(sig > 0) g_supTouch[lvlIdx] = -1; else g_resTouch[lvlIdx] = -1;   // setup used up (as in the backtest)
      }
      else if(g_paused[DirIdx(sig)])
         Log(StringFormat("%s setup at %.3f skipped -- BRAKE: %s paused after a losing streak", sig > 0 ? "BUY" : "SELL", lvl, DirName(sig)));
      else
      {
         if(inTrade)
         {
            CloseTrade(ticket, StringFormat("opposite %s setup at aligning level %.3f", sig > 0 ? "BUY" : "SELL", lvl));
            TrackPosition();
         }
         OpenTrade(sig, sl, lvl, StringFormat("%s %s CISD after touch", sig > 0 ? "bullish" : "bearish", how));
         // the level needs a fresh touch before it can trigger again
         if(sig > 0) g_supTouch[lvlIdx] = -1; else g_resTouch[lvlIdx] = -1;
      }
   }
   // B. breakout / breakdown entry: a CISD in the break direction, on a LATER candle, closing beyond the level
   else if(UseBreakouts && cisd != 0)
   {
      int best = -1;
      for(int k = 0; k < ArraySize(g_boVal); k++)
      {
         if(g_boSide[k] != cisd || g_boBar[k] >= i) continue;
         if(cisd > 0 ? g_c[i] <= g_boVal[k] : g_c[i] >= g_boVal[k]) continue;
         if(best < 0 || g_boBar[k] > g_boBar[best]) best = k;
      }
      if(best >= 0)
      {
         double bl = g_boVal[best];
         double bsl;
         if(g_cisdHasSwing) bsl = cisd > 0 ? g_cisdSwing - SLBufferPrice : g_cisdSwing + SLBufferPrice;
         else
         {
            // no active swing behind the CISD: fall back to the extreme since the break
            bsl = cisd > 0 ? DBL_MAX : -DBL_MAX;
            for(int k = g_boBar[best]; k <= i; k++) bsl = cisd > 0 ? MathMin(bsl, g_l[k]) : MathMax(bsl, g_h[k]);
            bsl = cisd > 0 ? bsl - SLBufferPrice : bsl + SLBufferPrice;
         }
         ulong tk; int td;
         bool inT = GetPosition(tk, td);
         if(inT && td == cisd)
         {
            if(TryExtraLeg(cisd, bsl, bl, tk, true)) RemoveBO(best);
            else Log(StringFormat("%s at %.3f ignored -- already in a %s", cisd > 0 ? "BREAKOUT" : "BREAKDOWN", bl, cisd > 0 ? "buy" : "sell"));
         }
         else if(DZBlocked(cisd))
            Log(StringFormat("%s at %.3f blocked by the DZ filter", cisd > 0 ? "BREAKOUT" : "BREAKDOWN", bl));
         else
         {
            if(inT)
            {
               CloseTrade(tk, StringFormat("opposite %s at %.3f", cisd > 0 ? "BREAKOUT" : "BREAKDOWN", bl));
               TrackPosition();
            }
            OpenTrade(cisd, bsl, bl, StringFormat("%s CISD after %s of %.3f", cisd > 0 ? "bullish" : "bearish",
                                                  cisd > 0 ? "breakout" : "breakdown", bl), BreakoutLots, 1);
            RemoveBO(best);
         }
      }
   }

   // B. new breaks: THIS candle closes through an aligning level
   if(UseBreakouts && i >= 1)
   {
      datetime bt2 = g_barTime;
      for(int k = 0; k < ArraySize(g_resVal); k++)
         if(g_c[i] > g_resVal[k] && g_c[i-1] <= g_resVal[k] && FindBO(1, g_resVal[k]) < 0)
         {
            PushI(g_boSide, 1); PushD(g_boVal, g_resVal[k]); PushI(g_boBar, i);
            Log(StringFormat("BREAKOUT: closed above aligning resistance %.3f -- waiting for a bullish CISD", g_resVal[k]));
            Note("V6SICT_BO_" + IntegerToString((long)bt2), bt2, g_resVal[k], "BREAKOUT", clrDeepSkyBlue, true);
         }
      for(int k = 0; k < ArraySize(g_supVal); k++)
         if(g_c[i] < g_supVal[k] && g_c[i-1] >= g_supVal[k] && FindBO(-1, g_supVal[k]) < 0)
         {
            PushI(g_boSide, -1); PushD(g_boVal, g_supVal[k]); PushI(g_boBar, i);
            Log(StringFormat("BREAKDOWN: closed below aligning support %.3f -- waiting for a bearish CISD", g_supVal[k]));
            Note("V6SICT_BD_" + IntegerToString((long)bt2), bt2, g_supVal[k], "BREAKDOWN", clrDeepSkyBlue, false);
         }
   }
   UpdateTP();   // levels may have moved this candle

   // record touches by THIS candle (a CISD needs a later candle than its touch);
   // within TouchBufferPrice counts -- price often reverses just short of the exact level
   datetime bt = g_barTime;
   for(int k = 0; k < ArraySize(g_supVal); k++)
      if(g_l[i] <= g_supVal[k] + TouchBufferPrice)
      {
         bool fresh = (g_supTouch[k] != i - 1);   // arrow only where a touch starts
         if(fresh) g_supEst[k] = i;               // first candle of this run of touches (SL anchor)
         g_supTouch[k] = i;
         if(fresh) Marker("V6SICT_TS_" + IntegerToString((long)bt) + "_" + DoubleToString(g_supVal[k], 2), bt, g_l[i], 233, clrLime,
                StringFormat("touch of aligning support %.3f", g_supVal[k]));
      }
   for(int k = 0; k < ArraySize(g_resVal); k++)
      if(g_h[i] >= g_resVal[k] - TouchBufferPrice)
      {
         bool fresh = (g_resTouch[k] != i - 1);
         if(fresh) g_resEst[k] = i;
         g_resTouch[k] = i;
         if(fresh) Marker("V6SICT_TR_" + IntegerToString((long)bt) + "_" + DoubleToString(g_resVal[k], 2), bt, g_h[i], 234, clrOrangeRed,
                StringFormat("touch of aligning resistance %.3f", g_resVal[k]));
      }
}

//===================== Dynamic Zones breakouts (v2.05) =====================
CTrade   g_dzTrade;
CCisd    g_c15;
double   g_dzbR[2] = {0, 0}, g_dzbS[2] = {0, 0};   // today's resistance [lo, hi] / support [lo, hi]
string   g_dzbLast = "";

// All four zone lines for the session containing t (v2.13: gap-delimited, as the indicator).
bool DZAllLines(const datetime t, double &z1, double &z2, double &z3, double &z4)
{
   datetime s;
   return DZSession(t, z1, z2, z3, z4, s);
}

bool DZGetPosition(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != DZMagicNumber) continue;
      ticket = t;
      dir = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      return true;
   }
   return false;
}

// One closed M15 candle (live = the latest one, the only one that can trade).
// Rule: a bullish M15 CISD candle closing ABOVE the upper resistance line = buy; bearish closing BELOW the lower support line = sell.
void DZBreakoutBar(const datetime bt, const double c, const int cisd, const bool live)
{
   double z1, z2, z3, z4;
   if(!DZAllLines(bt, z1, z2, z3, z4)) return;
   double rLo = MathMin(z1, z2), rHi = MathMax(z1, z2), sLo = MathMin(z3, z4), sHi = MathMax(z3, z4);
   g_dzbR[0] = rLo; g_dzbR[1] = rHi; g_dzbS[0] = sLo; g_dzbS[1] = sHi;
   if(!live || cisd == 0) return;
   int d = 0; double sl = 0; string what = "";
   if(cisd > 0 && c > rHi) { d = 1;  sl = rLo - DZSLBuffer; what = StringFormat("BREAKOUT of resistance zone %.2f-%.2f", rLo, rHi); }
   if(cisd < 0 && c < sLo) { d = -1; sl = sHi + DZSLBuffer; what = StringFormat("BREAKDOWN of support zone %.2f-%.2f", sLo, sHi); }
   if(d == 0) return;
   Log(StringFormat("DZ %s: M15 %s CISD closed %.3f", what, d > 0 ? "bullish" : "bearish", c));

   ulong tk; int td;
   bool inT = DZGetPosition(tk, td);
   if(inT && td == d) { Log(StringFormat("DZ setup ignored -- a DZ %s is already open", d > 0 ? "buy" : "sell")); return; }
   if(inT && DZSquareOff)
   {
      if(g_dzTrade.PositionClose(tk, SlippagePoints)) Log(StringFormat("DZ #%I64u SQUARED OFF by the opposite setup", tk));
      else { Print("[V6S-ICT] DZ square-off #", tk, " failed retcode=", g_dzTrade.ResultRetcode()); return; }
      inT = false;
   }
   if(inT) { Log("DZ setup ignored -- an opposite DZ trade is open (square-off off)"); return; }

   double price = d > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double risk = (price - sl) * d;
   if(risk <= 0) { Log(StringFormat("DZ %s skipped: SL %.3f on the wrong side of price %.3f", d > 0 ? "BUY" : "SELL", sl, price)); return; }
   bool capped = false;
   if(DZMaxSL > 0 && risk > DZMaxSL)
   {
      Log(StringFormat("DZ %s: zone SL %.3f is %.2f away -- capped at %.1f", d > 0 ? "BUY" : "SELL", sl, risk, DZMaxSL));
      sl = price - d * DZMaxSL; risk = DZMaxSL; capped = true;
   }
   // TP: nearest aligning level beyond entry -/+ TPBufferPrice, else 1:1 (fixed at entry)
   double tp = 0.0; string tpWhy = "1:1 (no aligning level ahead)";
   if(d > 0) { for(int k = 0; k < ArraySize(g_resVal); k++) { double t = g_resVal[k] - TPBufferPrice; if(t > price && (tp == 0.0 || t < tp)) tp = t; } }
   else      { for(int k = 0; k < ArraySize(g_supVal); k++) { double t = g_supVal[k] + TPBufferPrice; if(t < price && (tp == 0.0 || t > tp)) tp = t; } }
   if(tp != 0.0 && DZMinTPPoints > 0 && (tp - price) * d < DZMinTPPoints)
   {
      Log(StringFormat("DZ %s skipped: TP %.3f (nearest aligning level) is only %.2f from price %.3f (min %.1f)",
                       d > 0 ? "BUY" : "SELL", tp, (tp - price) * d, price, DZMinTPPoints));
      return;
   }
   if(tp != 0.0) tpWhy = StringFormat("aligning %s %.2f", d > 0 ? "resistance" : "support", d > 0 ? tp + TPBufferPrice : tp - TPBufferPrice);
   else tp = price + d * risk;
   if(capped && DZCappedMinR > 0 && (tp - price) * d < DZCappedMinR * risk)
   {
      Log(StringFormat("DZ %s skipped: SL capped at %.1f but TP %.3f is only %.2fR (needs %.1fR)", d > 0 ? "BUY" : "SELL",
                       DZMaxSL, tp, (tp - price) * d / risk, DZCappedMinR));
      return;
   }
   sl = NormalizeDouble(sl, _Digits); tp = NormalizeDouble(tp, _Digits);
   string comment = StringFormat("V6SICT DZ%s %.2f", d > 0 ? "B" : "S", d > 0 ? rHi : sLo);
   bool ok = d > 0 ? g_dzTrade.Buy(DZLots, _Symbol, 0.0, sl, tp, comment) : g_dzTrade.Sell(DZLots, _Symbol, 0.0, sl, tp, comment);
   uint rc = g_dzTrade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
   { Print("[V6S-ICT] DZ ", d > 0 ? "BUY" : "SELL", " FAILED retcode=", rc, " ", g_dzTrade.ResultRetcodeDescription()); return; }
   double ep = g_dzTrade.ResultPrice();
   g_dzbLast = StringFormat("%s DZ %s @%.2f SL %.2f TP %.2f (%s)", TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES),
                            d > 0 ? "BUY" : "SELL", ep, sl, tp, tpWhy);
   Log(g_dzbLast + " -- " + what);
   datetime nt = iTime(_Symbol, PERIOD_M5, 0);
   Note("V6SICT_DZE_" + IntegerToString((long)nt), nt, sl,
        StringFormat("DZ %s @%.2f SL %.2f TP %.2f", d > 0 ? "BUY" : "SELL", ep, sl, tp), d > 0 ? clrDeepSkyBlue : clrHotPink, d < 0);
}

// Feed newly closed M15 candles (called after the aligning levels are updated for this M5 close).
void DZBreakoutStep()
{
   if(!UseDZBreakout || !g_c15.ready) return;
   datetime lc = iTime(_Symbol, PERIOD_M15, 1);
   if(lc <= g_c15.lastBar) return;
   int shift = iBarShift(_Symbol, PERIOD_M15, g_c15.lastBar, true);
   int from = (shift > 1) ? shift - 1 : 1;
   for(int s = from; s >= 1; s--)
   {
      int sig = g_c15.Step(iOpen(_Symbol, PERIOD_M15, s), iClose(_Symbol, PERIOD_M15, s));
      g_c15.lastBar = iTime(_Symbol, PERIOD_M15, s);
      bool fresh = (s == 1) && TimeCurrent() - (g_c15.lastBar + PeriodSeconds(PERIOD_M15)) <= MaxSignalDelaySec;
      DZBreakoutBar(g_c15.lastBar, iClose(_Symbol, PERIOD_M15, s), sig, fresh);
   }
}

//===================== Init / tick =====================
bool g_warm = false;

// v2.08: MT5 keeps global variables when OnInit re-runs (timeframe / symbol / input change),
// so every piece of in-memory state is cleared here before the warmup rebuilds it.
void ResetState()
{
   ArrayResize(g_o, 0); ArrayResize(g_h, 0); ArrayResize(g_l, 0); ArrayResize(g_c, 0);
   g_n = 0; g_m5Last = 0;
   ArrayResize(g_bearOpen, 0); ArrayResize(g_bearIdx, 0); ArrayResize(g_bullOpen, 0); ArrayResize(g_bullIdx, 0);
   ArrayResize(g_shLvl, 0); ArrayResize(g_shIdx, 0); ArrayResize(g_slLvl, 0); ArrayResize(g_slIdx, 0);
   g_cisdHasSwing = false; g_cisdSwing = 0.0;
   ArrayResize(g_boSide, 0); ArrayResize(g_boVal, 0); ArrayResize(g_boBar, 0);
   ArrayResize(g_m10Side, 0); ArrayResize(g_m10Val, 0);
   ArrayResize(g_supVal, 0); ArrayResize(g_supTouch, 0); ArrayResize(g_supEst, 0); ArrayResize(g_supDesc, 0);
   ArrayResize(g_resVal, 0); ArrayResize(g_resTouch, 0); ArrayResize(g_resEst, 0); ArrayResize(g_resDesc, 0);
   g_c10Sig = 0; g_c10Close = 0;
   g_lastNew = ""; g_lastPanel = 0;
   g_dzfDay = 0; g_dzfBlockBuy = false; g_dzfBlockSell = false; g_dzfZ2 = 0; g_dzfZ4 = 0;
   g_dzsKey = 0;
   g_dzDay = 0; g_z1 = 0; g_z2 = 0; g_z3 = 0; g_z4 = 0;
   g_streak[0] = 0; g_streak[1] = 0; g_paused[0] = false; g_paused[1] = false;
   g_trackTicket = 0; g_trackDir = 0; g_trackBE = false; g_trackLevel = 0; g_trackType = 0;
   g_dzbR[0] = 0; g_dzbR[1] = 0; g_dzbS[0] = 0; g_dzbS[1] = 0; g_dzbLast = "";
   g_replay = false; g_barTime = 0; g_startup = true; g_warm = false;
}

void AddTF(const bool use, const ENUM_TIMEFRAMES tf, const string nm)
{
   if(!use) return;
   g_mm[g_mmCount].Init(tf, nm);
   g_mmCount++;
}

int OnInit()
{
   ResetState();
   g_mmCount = 0;
   AddTF(UseH4,  PERIOD_H4,  "H4");
   AddTF(UseH2,  PERIOD_H2,  "H2");
   AddTF(UseH1,  PERIOD_H1,  "H1");
   AddTF(UseM30, PERIOD_M30, "M30");
   AddTF(UseM15, PERIOD_M15, "M15");
   AddTF(UseM10, PERIOD_M10, "M10");
   AddTF(UseM5,  PERIOD_M5,  "M5");
   AddTF(UseM3,  PERIOD_M3,  "M3");
   if(g_mmCount < 2)
   {
      Print("[V6S-ICT] need at least 2 timeframes for aligning levels");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetDeviationInPoints(SlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_n = 0; g_m5Last = 0; g_warm = false;
   g_c10.Init(PERIOD_M10);
   g_c15.Init(PERIOD_M15);
   g_legTrade.SetExpertMagicNumber(LegMagicNumber);
   g_legTrade.SetDeviationInPoints(SlippagePoints);
   g_legTrade.SetTypeFillingBySymbol(_Symbol);
   g_dzTrade.SetExpertMagicNumber(DZMagicNumber);
   g_dzTrade.SetDeviationInPoints(SlippagePoints);
   g_dzTrade.SetTypeFillingBySymbol(_Symbol);

   if(AttachIndicator && ShowOnChart && (!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE)))
   {
      // display only -- the EA computes every level itself; in the visual tester an indicator
      // created by the EA is drawn automatically, live it is added to the chart explicitly
      // inputs in the indicator's own order: Dynamic Zones block unchanged, then EnableCISD = false
      int h = iCustom(_Symbol, _Period, "Dynamic_Zones_CISD_MajorMinor",
                      true, PERIOD_D1, 5, 10, true, C'0,0,60', clrBlue, false,
                      false);
      if(h == INVALID_HANDLE)
         Print("[V6S-ICT] could not load Dynamic_Zones_CISD_MajorMinor (compile it into MQL5\\Indicators) -- continuing without it");
      else if(!MQLInfoInteger(MQL_TESTER))
         ChartIndicatorAdd(0, 0, h);
   }
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(!MQLInfoInteger(MQL_TESTER)) ObjectsDeleteAll(0, "V6SICT_");
}

bool WarmupM5()
{
   int avail = Bars(_Symbol, PERIOD_M5) - 1;
   int cnt = MathMin(WarmupBars, avail);
   if(cnt < 50) return false;
   double o[], h[], l[], c[]; datetime t[];
   if(CopyOpen(_Symbol, PERIOD_M5, 1, cnt, o) != cnt)  return false;
   if(CopyHigh(_Symbol, PERIOD_M5, 1, cnt, h) != cnt)  return false;
   if(CopyLow(_Symbol, PERIOD_M5, 1, cnt, l) != cnt)   return false;
   if(CopyClose(_Symbol, PERIOD_M5, 1, cnt, c) != cnt) return false;
   if(CopyTime(_Symbol, PERIOD_M5, 1, cnt, t) != cnt)  return false;
   // v2.07: hold back the candles of the current D1 session (at least a breakout's lifetime) --
   // OnTick replays them through the setup logic without orders once the levels are built
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);
   int rep = 0;
   for(int k = cnt - 1; k >= 0 && t[k] >= dayStart; k--) rep++;
   rep = MathMax(rep, BreakoutValidBars + 1);
   rep = MathMax(0, MathMin(rep, MathMin(ReplayMaxBars, cnt - 50)));
   for(int k = 0; k < cnt - rep; k++) M5Step(o[k], h[k], l[k], c[k]);
   g_m5Last = t[cnt - rep - 1];
   return true;
}

void OnTick()
{
   if(!g_warm)
   {
      for(int t = 0; t < g_mmCount; t++) if(!g_mm[t].ready && !g_mm[t].Warmup()) return;
      if(!WarmupM5()) return;
      if(!g_c10.ready && !g_c10.Warmup()) return;
      if(UseDZBreakout && !g_c15.ready && !g_c15.Warmup()) return;
      g_warm = true;
      RebuildAlignments();
      Log(StringFormat("warmup done: %d timeframes, %d aligning supports, %d aligning resistances",
                       g_mmCount, ArraySize(g_supVal), ArraySize(g_resVal)));
      DrawPanel(true);
   }

   UpdateDynamicZones();
   TrackPosition();

   datetime lastClosed = iTime(_Symbol, PERIOD_M5, 1);
   if(lastClosed > g_m5Last)
   {
      // 1. every timeframe's newly closed bars first, so levels are current for this candle
      bool changed = false;
      for(int t = 0; t < g_mmCount; t++) if(g_mm[t].Update()) changed = true;
      if(changed) RebuildAlignments();

      // 1b. M10 CISD for levels that stopped out (an M10 candle closes with every other M5 candle)
      datetime t10;
      g_c10Sig = g_c10.Update(g_c10Close, t10);

      // 2. the M5 candle(s) themselves. Older ones (startup replay / missed during a disconnection)
      //    only update setups; the latest one can trade if it closed just now (v2.07)
      int shift = iBarShift(_Symbol, PERIOD_M5, g_m5Last, true);
      int from = (shift > 1) ? shift - 1 : 1;
      if(from > 1 || g_startup) Log(StringFormat("replaying %d M5 candle(s) to rebuild setups (no orders)", g_startup ? from : from - 1));
      int sig10 = g_c10Sig;
      for(int s = from; s >= 1; s--)
      {
         int cisd = M5Step(iOpen(_Symbol, PERIOD_M5, s), iHigh(_Symbol, PERIOD_M5, s),
                           iLow(_Symbol, PERIOD_M5, s), iClose(_Symbol, PERIOD_M5, s));
         g_m5Last = iTime(_Symbol, PERIOD_M5, s);
         g_barTime = g_m5Last;
         bool fresh = (s == 1) && !g_startup &&
                      TimeCurrent() - (g_m5Last + PeriodSeconds(PERIOD_M5)) <= MaxSignalDelaySec;
         g_replay = !fresh;
         g_c10Sig = (s == 1) ? sig10 : 0;
         OnM5Bar(cisd);
         g_replay = false;
      }
      g_startup = false;
      DZBreakoutStep();   // an M15 candle closes together with an M5 candle; levels are current now
   }

   ManagePosition();
   TrackPosition();
   DrawPanel(false);
}
//+------------------------------------------------------------------+
