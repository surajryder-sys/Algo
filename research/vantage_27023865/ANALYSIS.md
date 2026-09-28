# Vantage account 27023865 — trading analysis

Snapshot pulled 2026-09-25 (≈20:15 IST) from terminal `MetaTrader5-5` while logged in
read-only (investor). Account: "DS Trading Rooms", VantageMarkets-Live 21, **real**, USC
(cent) currency, 1:2000 leverage. Period covered: 2026-04-01 → 2026-09-25 (~6 months).
All times below are **IST**.

## Files

| file | what |
|---|---|
| `data/deals_history.csv` | every deal (55,152) — raw MT5 `history_deals_get` |
| `data/orders_history.csv` | every order (55,126) — raw `history_orders_get` |
| `data/positions_open.csv` | 2 positions open at snapshot time |
| `data/account.json`, `data/symbol_specs.json` | account/terminal info, XAUUSD.sc + MSTR.24H specs |
| `positions_reconstructed.csv` | 27,561 closed positions, open+close joined, P/L incl. swap |
| `grid_cycles.csv` | 16,105 grids (positions with overlapping open times), with P/L, lots, depth and manual flag |

## Headline numbers (USC; 100 USC = $1)

- Money in: **+222,032** (deposits + internal transfers). Withdrawn: **−185,870** (19 USDT withdrawals, roughly weekly, 3k–16k each).
- Net trading P/L: **+168,007** (commission 0, swap −212). Balance at snapshot: 204,158.
- Monthly P/L: Apr +7.8k · May +21.0k · Jun +42.5k · Jul +34.2k · Aug +34.7k · Sep (to 25th) +27.9k.
- **122 of 123 trading days were green.** The only red day was Apr 17 (−4,842).
- Max closed-balance drawdown, measured per whole grid: **−5,065** (the manual close on Apr 18, ~04:34 IST). The real floating drawdown was larger; closed deals can't show equity while grids were still open.

## What it trades

- Only **XAUUSD.sc** is traded by the EA (magic **12345**). There was one manual MSTR.24H test trade (May 6, −7) and one manual 2.56-lot XAUUSD sell (Aug 13, +2,148).
- 27,559 of 27,561 positions were opened by the EA. Buys and sells are almost 50/50 (13,902 / 13,659).
- **No SL or TP is ever set.** 0 of 55,126 orders carry an sl/tp price. Every exit is a market close sent by the EA (or by a person).
- Entries land on **second :00 of the minute** (98.6%), so the EA acts at M1 bar open.
- Busiest hours: 21:00–00:00 IST (US session; about 2,200–2,800 entries per hour). Quietest: 05:00–07:00 IST.

## Strategy rules, rebuilt from the data

A "grid" here means a chain of positions whose open times overlap: it starts when nothing is open and ends when the last position closes. There were 16,105 grids in the period. (An earlier version of this file grouped positions by close time. That split each grid's close sequence in two and invented "EA loss cuts" and "recovery baskets". Both were wrong and have been removed.)

**Entry**
- Every grid starts at **0.01** at an M1 bar open (99.6% of grids).
- The next grid starts about **35 s after the previous one closes** (10–90% range: 8–81 s), so on the next M1 bar or close to it.
- The next grid is in the **opposite direction 92.8% of the time**, so it flips buy ↔ sell after almost every close. The direction signal itself can't be seen in the data.
- Only one grid is open at a time. Buys and sells are never open together, so it isn't a hedged grid.

**Averaging (the grid)**
- A level is added when price has moved against the grid by at least about **$3–4** (median $3.8–4.0 from level 3 onward; 10th percentile about $3.0). It is checked on M1 bar opens.
- Lots **double each level**: 0.01 → 0.02 → 0.04 → … → 5.12.
- In 41% of grids the second position is **another 0.01**, often within 60 s and sometimes less than $1 away. That looks like a second entry trigger rather than a grid level. From there the doubling continues: 0.01, 0.01, 0.02, 0.04 …
- **Lot cap is 5.12.** After that, 5.12 is repeated (15 grids had several 5.12 levels). A lot above 5.12 appears only 3 times.
- Depth reached: 1 level in 62% of grids, 2 in 23%, 3 in 8%, 4 in 3.5%, 5–8 in 3.7%, and up to 22 (once).

**Exit: basket take-profit only**
- The whole grid is closed when price is about **$1.60 past the grid's lot-weighted breakeven price**. The median is $1.57–1.61 at every depth from 1 to 8 levels, so the rule is stable.
- Net profit per grid therefore scales with its lot total: about +1.6 USC for 1 level, +4.3 for 2, +22 for 4, +93 for 6, +420 for 9, and +2,270 for 12.
- Positions are closed one at a time, newest first, over 0–2 s. The older levels always close at a loss and the newest at a larger profit; the net is always the basket target.

**Loss booking: the EA has none**
- There is no SL or TP on any order, no stop per grid, no stop by depth, and no time exit.
- Of 16,021 grids the EA closed itself, **99.94% ended in net profit**. The other 10 lost −94 in total, which is slippage while closing.
- **Every real loss is a manual close from the phone** (close reason = mobile).

**Recovery: none as a separate mechanism**
- There is no bigger "recovery" basket after a loss. The next grid always restarts at 0.01. The only recovery is the doubling inside the grid itself.

## Where the losses came from

- By position: 29% lose and 71% win (average −30.7 vs +21.3). That's only because older levels close in the red within a profitable grid. Per grid, the EA essentially never loses.
- **84 grids were closed by hand** from the phone. 65 of them were closed in profit (+12.9k) and 19 at a loss (−7.8k), so the manual closes netted +5.1k. The two losses that matter:
  - **Apr 17 19:37 → Apr 18 04:31 IST: 22-level sell grid.** Price ran $97.7 against the first entry and the grid was closed by hand at **−5,063**. This is the only red day.
  - **Aug 13 07:39–09:19 IST: 9-level sell grid** (up to 1.28 lots) closed by hand at **−2,560**. The same morning a manual 2.56-lot sell made +2,148, which looks like a trade placed by hand to offset it.
- The largest grids by total lots were 40.95 (May 11, closed by hand at +2,342), 35.83 (Aug 5, EA-closed +2,954) and 30.71 (Jun 22, closed by hand at +1,525).
- The profit comes from the deep tail: grids that reached 5.12 lots (33 of them) made +42.7k, about a quarter of all profit.

## Risk read

- The EA's only way out of a grid is a price reversal to breakeven + $1.6. In a one-way trend the grid keeps doubling up to 5.12 lots and then keeps adding 5.12 lots. Nothing inside the EA limits the loss.
- A $10 move against a 30–40-lot grid is about −30 to −40k USC. The account balance is about 204k.
- There is no stop-loss at the broker. If the EA or VPS goes offline mid-grid, nothing limits the loss.
- In practice the risk limit is the person watching from the phone. Both real losses came from them cutting a grid by hand.
- Over 6 months: 222k deposited, 186k withdrawn, and 204k still in the account. That's 390k out plus balance against 222k in (about 1.76×), or +76% on deposited capital.

## Open at snapshot (20:07–20:10 IST, Sep 25)

Two EA buys: 0.01 @ 4303.71 and 0.02 @ 4300.29 (a 2-level basket, $3.42 apart). Floating −2.25.
