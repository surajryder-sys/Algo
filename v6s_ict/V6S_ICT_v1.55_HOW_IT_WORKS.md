# V6S-ICT v1.55: how it works

File: `V6S_ICT_EA_v1.55.mq5` (MT5 Expert Advisor, magic 26092701).
This is the working copy saved on 27 Sep 2026, before v2.00 added breakouts and the opposite-level TP.

---

## 1. What it trades

XAUUSD on **M5**. It looks for **reversals at aligning support/resistance levels**: a buy when price comes down to a strong support and turns up, a sell when price comes up to a strong resistance and turns down.

**One trade at a time.** Default size **0.06 lot**.

---

## 2. The levels: Major/Minor ("yellow line" logic)

On **8 timeframes** (H4, H2, H1, M30, M15, M10, M5, M3) the EA calculates the **Major/Minor support and resistance** exactly like the yellow lines of the Major/Minor indicator:

- ZigZag pivots with **PivotPeriod 5** (a high or low that is the extreme of 5 candles on each side).
- Pivots are classified as H/HH/LH/L/HL/LL, then promoted to **Major** or left as **Minor** by the same rules as the indicator.
- Each timeframe has **4 current levels**: Major Support, Minor Support, Major Resistance, Minor Resistance.

The EA computes all of this itself from the candles. It does not read any indicator. CISD swing lines are **not** levels.

---

## 3. Aligning levels (the only levels it trades)

A price is an **aligning support** (or **aligning resistance**) when:

1. it is the **exact same price** as a support (or resistance) level on **3 or more different timeframes**, and
2. on **at least 2** of those timeframes it is a **Major** level.

Examples:

| Members | Valid? |
|---|---|
| H1 Major S + M30 Major S + M15 Minor S | Yes (3 TFs, 2 Major) |
| M15 Major S + H1 Minor S + M30 Minor S | No (only 1 Major) |
| H1 Major S + M30 Major S | No (only 2 TFs) |

The list updates whenever any timeframe closes a candle. A level that stops aligning disappears, and so does any setup waiting on it.

---

## 4. The entry (buy side; sells mirror it)

1. **Touch.** An M5 candle's **low reaches the aligning support** (a real touch, no buffer).
2. **Confirmation.** On a **later** M5 candle (never the touch candle itself) a **bullish CISD** confirms, and that candle **closes above the support**.
   - CISD = AlgoAlpha "Change in State of Delivery": a reversal candle's open is broken by a later close, with enough retracement (noise filter 0.7).
3. **Still valid?** The level must still be an aligning support at that moment.
4. **Buy 0.06 at market** on the next tick.

If a trade is already open:

- **Same direction:** the new setup is ignored.
- **Opposite direction:** the open trade is closed and the new one opened (a reversal at the opposite level).

---

## 5. Stop loss

**Lowest low of the candles after the touch candle, up to and including the CISD candle, minus 0.5.** (Sell: highest high + 0.5.)

The touch candle's own wick is not included. The v1.54 test showed including it roughly doubles the stop distance and cuts profit sharply.

---

## 6. Managing the trade

| When | What happens |
|---|---|
| Profit reaches **2 × risk** (1:2) | Stop moves to **breakeven** (entry price) |
| Profit reaches **7 × risk** (1:7) | **Close everything except 0.01** (0.05 closed, 0.01 runner stays) |
| Opposite setup at an aligning level | Trade is **closed and reversed** |
| Price breaks through the next level | Nothing; the trade keeps running |

There is **no fixed take-profit** in v1.55.

---

## 7. Protection rules

**M10 after a stop-out.** If a trade from a level hits its **stop loss** (breakeven stops don't count), that **same level** needs an **M10 CISD** (instead of M5) for its next entry. Other levels still use M5. The flag clears once that level trades again.

**Losing-streak brake.** After **2 stop-losses in a row** in the same direction, that direction is **paused** until a trade in the **other** direction opens. A winning trade resets the count.

**No new entries** in the first 60 minutes after the weekly open, or when the spread is above 0.50. (The IST time windows exist but are off.)

---

## 8. What you see on the chart (visual backtest / live)

| Drawing | Meaning |
|---|---|
| Yellow lines "H1 Maj S", "M15 Min R", … (solid = Major, dotted = Minor) | Each timeframe's current Major/Minor levels |
| **Thick lime / orange lines** "ALIGN S [...]" / "ALIGN R [...]" | Aligning levels (3+ TFs, 2+ Major) |
| Lime ▲ / orange ▼ | First candle of a touch |
| Small aqua / magenta dots | M5 CISD (bullish / bearish); bigger circles = M10 CISD |
| Entry text + magenta dotted line | Entry price, SL, 1R, level |
| Gold dotted "1:2 BE", aqua dotted "1:7 partial" | Where breakeven and the partial happen |
| Gold / aqua markers | Breakeven / partial actually done |
| Blue dash-dot "DZ Z1…Z4" | Dynamic Zones (data only, not used by any rule) |
| **Panel** (top-left) | Aligning levels + their timeframes, touch state, "needs M10" flags, open trade, brake status, last new alignment |

The red/green lines tracing every candle high/low in the visual tester come from MT5's own tester (they also appear with the MACD Sample EA). They are not from this EA.

---

## 9. Main inputs

| Input | Default | Meaning |
|---|---|---|
| LotSize / RunnerLots | 0.06 / 0.01 | Entry size / size left after the partial |
| MinAlignTimeframes / MinMajorTimeframes | 3 / 2 | Aligning-level rule |
| TouchBufferPrice | 0 | 0 = the candle must reach the level |
| SLBufferPrice / SLFromTouch | 0.5 / false | SL cushion / SL from the touch candle (v1.54 idea, off) |
| BreakevenR / PartialR | 2 / 7 | Breakeven and partial multiples of risk |
| M10AfterSL / LossStreakBrake | true / 2 | Protection rules |
| UseH4 … UseM3 | all on | Timeframes used for levels |
| ShowOnChart / ShowPanel / PanelTopOffset | on / on / 60 | Drawings and panel |

---

## 10. Test results (simulation on Exness XAUUSD, 0.06 lot)

| Period | Data | Result |
|---|---|---|
| 1 Apr – 24 Sep 2026 | real ticks | **+4,278**, worst drop 929, 140 trades |
| Oct 2024 – Mar 2026 | 1-minute bars (pessimistic for this EA) | −4,514 |
| 2 years combined | | about break-even (most robust touch setting tested) |

How the main rules were chosen (real ticks, Apr–Sep 2026, unless noted):

- **3+ TFs vs 2+:** the 2-TF levels (mostly M3 + M5) lost money.
- **2-Major rule:** +274 → +2,343 and a smaller drop.
- **M10 after SL + brake:** the most robust over the rough Oct 2025 – Apr 2026 period.
- **Partial:** 1:3 +3,110, 1:5 +4,040, **1:7 +4,911**, 1:10 +3,335, none +3,458 (with the 2-pt touch buffer).
- **Touch buffer:** 2 pts looked best in Apr–Sep, but over 2 years a real touch (0) was clearly the most robust.

**Caution:** many settings were tuned on Apr–Sep 2026. The older period is much weaker, especially the violent gold trend of Oct 2025 – Apr 2026. Verify in the MT5 Strategy Tester and on demo before real money.
