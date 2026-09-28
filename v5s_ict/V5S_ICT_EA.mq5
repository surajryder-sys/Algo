//+------------------------------------------------------------------+
//|                                                  V5S_ICT_EA.mq5  |
//| V5S-ICT -- signal-driven hedged ladder EA (designed 2026-09-25). |
//|                                                                    |
//| Two fully independent baskets, each with its own magic number:    |
//|   SELL basket -- sequence runs while the SignalTF dual-ATR state  |
//|                  is WEAK (confirmed close below BOTH trail lines). |
//|   BUY basket  -- mirror image, runs while the state is STRONG.     |
//|                                                                    |
//| SELL basket rules (BUY basket is the exact mirror):                |
//|   Start  : no basket open + (flip-to-weak candle OR a bearish     |
//|            CISD while weak) -> SELL 1 unit (0.01).                 |
//|   Add    : bearish CISD, last sell already hedged -> next ladder   |
//|            sell (step+1 units: 0.02, 0.03 ... 0.10).               |
//|   Extra  : bearish CISD, last sell NOT yet hedged -> one extra     |
//|            1-unit sell leg (at most MaxExtraLegs per hedge cycle); |
//|            the ladder step does not advance.                       |
//|   Hedge  : bullish CISD, or the flip to strong -> BUY the total    |
//|            volume sold since the last hedge (in the sell basket's  |
//|            own magic). A bullish CISD with nothing unhedged is     |
//|            ignored.                                                |
//|   Freeze : while state is strong the sell basket adds nothing;     |
//|            its positions stay open and still close at target. It   |
//|            resumes where it stopped once the state is weak again   |
//|            (the flip candle itself opens nothing for a resumed     |
//|            basket).                                                |
//|   Cap    : once the MaxLadderStep sell is open nothing more is     |
//|            added; the next bullish CISD or flip to strong closes   |
//|            the whole basket.                                       |
//|   Target : single first position -> +FirstTargetPrice price move   |
//|            (10.0 = $10 on 0.01 XAUUSD); otherwise the whole basket |
//|            closes at +BasketTargetMoney while the ladder is below  |
//|            BreakevenFromStep, and at +BreakevenMoney from it on.   |
//|                                                                    |
//| RISK FIXES added 2026-09-25 after the first backtest (1-23 Sep     |
//| XAUUSD: 144 of 152 baskets won ~$10 each, but 8 baskets that ran   |
//| to the 0.10 cap lost -$1,904 and took the account -67%). Each is   |
//| its own input so it can be switched off to compare:                |
//|   BasketMaxLossMoney -- hard basket stop: close everything once   |
//|            the basket's floating P/L reaches -X.                   |
//|   UseHTFFilter -- a new basket, a ladder add or an extra leg only  |
//|            fires while the FilterTF dual-ATR state agrees with the |
//|            basket's side (hedges/closes are never filtered).       |
//|   HedgeRelease -- a with-side CISD while the basket holds open     |
//|            hedges closes those hedges instead of stacking the next |
//|            (bigger) ladder leg on top of them.                     |
//|   MaxExtraLegs -- caps the repeat-CISD extra legs per hedge cycle. |
//|   Time/spread filter -- no new basket, add or extra leg during     |
//|            NoTradeWindowsIST (UseTradeWindows; releases wait too,  |
//|            hedges and target closes never do), no new basket in    |
//|            NoNewBasketWindowsIST, none in the first                |
//|            WeekOpenBlockMinutes of the week, or while the spread   |
//|            exceeds MaxSpreadPrice.                                  |
//|                                                                    |
//| Backtest 2026-09-25 (tick-level, Exness XAUUSD, $1000, swap        |
//| included; 1-23 Sep / 20-31 Aug): old rules $384 / $1237; defaults  |
//| below (loss 100 + 1 extra leg + time/spread, HTF off) $948 / $1408.|
//| UseHTFFilter alone made Sep WORSE ($40) -- baskets got stuck part- |
//| way up the ladder with adds blocked -- so it ships off. Hedge      |
//| Release ($1307 / $1347) stops the ladder growing at all (every      |
//| release resets exposure to step 1) -- a different strategy, but    |
//| the user chose to run it (2026-09-25). Over 3 months (24 Jun-24    |
//| Sep) with $1000 and a basket stop, every stop size was fragile     |
//| (150: $853 ... 400: $1450). The user then chose a cent account     |
//| (200,000 USC) with NO basket stop and LotUnit 0.10, the current    |
//| defaults: 200,000 -> 240,180 USC (+20%), worst drop 52,617 USC,    |
//| lowest equity 151,755 USC, at most 9.0 lots open at once (27 Aug). |
//| Profit AND drawdown scale linearly with LotUnit (+BasketTarget);   |
//| around 0.38 the 3-month worst drop would wipe the account out.     |
//|                                                                    |
//| 2-YEAR TEST (Oct 2024-Sep 2026, 1-minute-bar sim, 300,000 USC):    |
//| without an exit every lot/target setting eventually blew up from    |
//| runaway volume (hedge releases + legs in long trends). An           |
//| EmergencyBasketLots close fixed it: at 3-4 lots all 12 lot/target  |
//| settings survived. Chosen defaults (2026-09-25): LotUnit 0.30,     |
//| target 500, emergency 4 lots, IST windows OFF -> +84% over 2 years,|
//| worst drop 64,065, worst month -5.2% (windows on: +64%, drop 48k). |
//| The sim runs ~25-35% optimistic on profit (constant spread).       |
//|                                                                    |
//| Signals are evaluated on CLOSED candles only. Targets and the loss |
//| limit are checked every tick. One entry action per basket per      |
//| candle.                                                            |
//|                                                                    |
//| Dual-ATR trail: same math as mql5/ATR_Trial_Dual_SuperTrend_Major_ |
//| Minor_HammerStar.mq5's CalcTrail (iATR, close-based trail).        |
//| CISD: same confirm/discard logic as mql5/CISD_AlgoAlpha.mq5        |
//| (swing/sweep parts dropped -- they never affect confirmation).     |
//| Both are computed internally so this runs in the Strategy Tester   |
//| with no indicator or bridge file dependency.                       |
//|                                                                    |
//| Requires a HEDGING account (hedges and both baskets coexist).      |
//+------------------------------------------------------------------+
#property copyright "V5S-ICT"
#property version   "1.10"

#include <Trade/Trade.mqh>

//===================== Inputs =====================
input group "Signals"
input ENUM_TIMEFRAMES SignalTF   = PERIOD_M5;   // M5: same profit as M3 with half the drawdown (3-month sim)
input double KeyValue            = 2.0;    // ATR trail line 1 multiplier
input int    ATRPeriod           = 2;      // ATR trail line 1 period
input double KeyValue2           = 2.0;    // ATR trail line 2 multiplier
input int    ATRPeriod2          = 300;    // ATR trail line 2 period
input double CISDTolerance       = 0.7;    // CISD "Noise Filter"
input int    WarmupBars          = 3000;   // closed bars replayed at start to seed ATR state + CISD candidates

input group "Baskets"
input bool   EnableSellBasket    = true;
input bool   EnableBuyBasket     = true;
input long   SellMagic           = 26092501;
input long   BuyMagic            = 26092502;
input double LotUnit             = 0.30;   // one ladder unit (0.30 + target 500 + emergency 4 lots: chosen 2026-09-25)
input int    MaxLadderStep       = 10;     // last ladder sell/buy = MaxLadderStep units (0.10)
input int    MaxExtraLegs        = 1;      // extra 1-unit legs allowed per hedge cycle (-1 = unlimited)

input group "Targets"
input double FirstTargetPrice    = 10.0;   // single first position: close at this favourable price move
input double BasketTargetMoney   = 500.0;  // basket target, account currency (USC on the cent account)
input int    BreakevenFromStep   = 6;      // from this ladder step on, target = BreakevenMoney
input double BreakevenMoney      = 0.0;    // breakeven target, account currency

input group "Risk"
input double BasketMaxLossMoney  = 0.0;    // close the basket at this floating loss (0 = off), account currency
input bool   HedgeRelease        = true;   // with-side CISD closes open hedges instead of adding the next leg
input double EmergencyBasketLots = 4.0;    // close the WHOLE basket once its open lots (legs + hedges) reach this (0 = off)

input group "Higher-timeframe filter"
input bool   UseHTFFilter        = false;  // off by default: hurt results in backtest (see header)
input ENUM_TIMEFRAMES FilterTF   = PERIOD_M15;

input group "Trading windows (IST)"
input bool   UseTradeWindows     = false;  // off by default (user choice 2026-09-25)
input int    ServerToIstMinutes  = 330;    // IST minus server time, in minutes (Exness server = UTC -> +330)
input string NoTradeWindowsIST   = "23:00-04:00,17:56-18:05,18:56-19:05";  // no new basket, add, extra leg or hedge release
input string NoNewBasketWindowsIST = "22:30-23:00";  // no NEW basket only (adds/releases still allowed)
input int    WeekOpenBlockMinutes = 60;    // no new entries this long after the week's first bar (0 = off)
input double MaxSpreadPrice      = 0.50;   // no new entries above this spread, in price (0 = off)

input group "Execution"
input int    SlippagePoints      = 50;
input bool   VerboseLog          = true;

//===================== Dual-ATR state (one per timeframe) =====================
struct AtrState
{
   ENUM_TIMEFRAMES tf;
   int      h1, h2;          // iATR handles
   double   trail1, trail2;
   double   prevClose;
   bool     haveTrail;
   int      confirmed;       // +1 strong, -1 weak, 0 unknown yet
   datetime lastBar;         // open time of the last processed closed bar
};

AtrState g_sig;   // SignalTF
AtrState g_htf;   // FilterTF

//===================== CISD state (SignalTF only) =====================
// Closed-bar history in chronological order (index 0 = oldest processed).
double   g_open[];
double   g_close[];
int      g_nBars = 0;

// Pending CISD origin candidates, index 0 = newest (same as the indicator).
double   g_bearOpen[]; int g_bearIdx[];
double   g_bullOpen[]; int g_bullIdx[];

bool     g_warmedUp = false;

//===================== Basket state =====================
struct Basket
{
   int    side;        // -1 sell basket, +1 buy basket
   long   magic;
   bool   enabled;
   int    step;        // ladder step of the last ladder order (0 = no basket)
   double unhedged;    // volume opened on the basket side since the last hedge
   int    extras;      // extra legs opened since the last hedge
   bool   closing;     // a close-all is in progress
   string gvPrefix;
};

Basket g_sell;
Basket g_buy;

CTrade g_trade;

// Parsed trading windows, IST minutes of day [start, end)
int g_ntStart[], g_ntEnd[];   // NoTradeWindowsIST
int g_nbStart[], g_nbEnd[];   // NoNewBasketWindowsIST

//+------------------------------------------------------------------+
string SideName(const int side){ return side < 0 ? "SELL" : "BUY"; }
string StateName(const int s){ return s > 0 ? "STRONG" : s < 0 ? "WEAK" : "UNKNOWN"; }

void Log(const string msg)
{
   if(VerboseLog) Print("[V5S-ICT] ", msg);
}

//+------------------------------------------------------------------+
//| Array helpers (front-insert, like the indicator's)                |
//+------------------------------------------------------------------+
void InsertFrontD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void InsertFrontI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void RemoveFrontD(double &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveFrontI(int    &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

//+------------------------------------------------------------------+
//| CISD confirmation -- direct port of CISD_AlgoAlpha.mq5's          |
//| ConfirmBearCISD/ConfirmBullCISD over this EA's own bar arrays.    |
//+------------------------------------------------------------------+
bool ConfirmBearCISD(const int i)
{
   while(ArraySize(g_bearOpen) > 0)
   {
      double candOpen = g_bearOpen[0];
      int    candIdx  = g_bearIdx[0];

      if(g_close[i] < candOpen)
      {
         double highest = 0.0;
         for(int k = candIdx; k <= i; k++)
            if(g_close[k] > highest) highest = g_close[k];

         double top = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && g_close[k] < g_open[k])
         {
            top = g_open[k];
            k--;
         }

         double denom = top - candOpen;
         if(denom != 0.0 && (highest - candOpen) / denom > CISDTolerance)
         {
            ArrayResize(g_bearOpen, 0); ArrayResize(g_bearIdx, 0);
            return true;
         }
         RemoveFrontD(g_bearOpen); RemoveFrontI(g_bearIdx);
      }
      else
         break;
   }
   return false;
}

bool ConfirmBullCISD(const int i)
{
   while(ArraySize(g_bullOpen) > 0)
   {
      double candOpen = g_bullOpen[0];
      int    candIdx  = g_bullIdx[0];

      if(g_close[i] > candOpen)
      {
         double lowest = g_close[i];
         for(int k = candIdx; k <= i; k++)
            if(g_close[k] < lowest) lowest = g_close[k];

         double bottom = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && g_close[k] > g_open[k])
         {
            bottom = g_open[k];
            k--;
         }

         double denom = candOpen - bottom;
         if(denom != 0.0 && (candOpen - lowest) / denom > CISDTolerance)
         {
            ArrayResize(g_bullOpen, 0); ArrayResize(g_bullIdx, 0);
            return true;
         }
         RemoveFrontD(g_bullOpen); RemoveFrontI(g_bullIdx);
      }
      else
         break;
   }
   return false;
}

//+------------------------------------------------------------------+
//| One trail line step -- same math as the indicator's CalcTrail.    |
//+------------------------------------------------------------------+
double TrailStep(const double src, const double src1, const double prevStop, const double nLoss)
{
   if(src > prevStop && src1 > prevStop) return MathMax(prevStop, src - nLoss);
   if(src < prevStop && src1 < prevStop) return MathMin(prevStop, src + nLoss);
   if(src > prevStop)                    return src - nLoss;
   return src + nLoss;
}

//+------------------------------------------------------------------+
//| Feed one closed bar's close through a dual-ATR state. Returns the |
//| flip this bar: +1 to strong, -1 to weak, 0 none.                   |
//+------------------------------------------------------------------+
int AtrStep(AtrState &s, const double c, const double atr1, const double atr2)
{
   if(!s.haveTrail)
   {
      s.trail1 = c; s.trail2 = c; s.prevClose = c;
      s.haveTrail = true;
   }
   else
   {
      s.trail1 = TrailStep(c, s.prevClose, s.trail1, KeyValue  * atr1);
      s.trail2 = TrailStep(c, s.prevClose, s.trail2, KeyValue2 * atr2);
      s.prevClose = c;
   }

   int newConfirmed = s.confirmed;
   if(c > MathMax(s.trail1, s.trail2))      newConfirmed = 1;
   else if(c < MathMin(s.trail1, s.trail2)) newConfirmed = -1;

   int flip = 0;
   if(newConfirmed != s.confirmed)
   {
      if(s.confirmed != 0) flip = newConfirmed;
      s.confirmed = newConfirmed;
   }
   return flip;
}

//+------------------------------------------------------------------+
//| Feed ONE closed SignalTF bar through CISD. Returns +1 bullish     |
//| CISD this bar, -1 bearish, 0 none (bull wins a same-bar tie, same |
//| as the indicator).                                                 |
//+------------------------------------------------------------------+
int CisdStep(const double o, const double c)
{
   int i = g_nBars;
   ArrayResize(g_open,  i + 1, 100000);
   ArrayResize(g_close, i + 1, 100000);
   g_open[i]  = o;
   g_close[i] = c;
   g_nBars    = i + 1;

   if(i >= 1)
   {
      if(g_close[i-1] < g_open[i-1] && c > o) { InsertFrontI(g_bearIdx, i); InsertFrontD(g_bearOpen, o); }
      if(g_close[i-1] > g_open[i-1] && c < o) { InsertFrontI(g_bullIdx, i); InsertFrontD(g_bullOpen, o); }
   }

   int cisd = 0;
   if(ConfirmBearCISD(i)) cisd = -1;
   if(ConfirmBullCISD(i)) cisd = 1;
   return cisd;
}

//+------------------------------------------------------------------+
//| Replay up to WarmupBars closed bars of one timeframe (no trading).|
//| withCisd: also seed CISD candidates (SignalTF only).               |
//+------------------------------------------------------------------+
bool WarmupTF(AtrState &s, const bool withCisd)
{
   int avail = Bars(_Symbol, s.tf) - 1;   // closed bars
   int n = MathMin(WarmupBars, avail);
   if(n < ATRPeriod2 + 10)
   {
      Print("[V5S-ICT] not enough history on ", EnumToString(s.tf), ": ", avail, " closed bars");
      return false;
   }

   double o[], c[], a1[], a2[];
   datetime t[];

   // shift 1 = last closed bar; copy n bars ending there, oldest first
   if(CopyOpen(_Symbol, s.tf, 1, n, o) != n)  return false;
   if(CopyClose(_Symbol, s.tf, 1, n, c) != n) return false;
   if(CopyTime(_Symbol, s.tf, 1, n, t) != n)  return false;
   if(CopyBuffer(s.h1, 0, 1, n, a1) != n)     return false;
   if(CopyBuffer(s.h2, 0, 1, n, a2) != n)     return false;

   for(int k = 0; k < n; k++)
   {
      AtrStep(s, c[k], a1[k], a2[k]);
      if(withCisd) CisdStep(o[k], c[k]);
   }

   s.lastBar = t[n-1];
   Log(StringFormat("warmup %s: %d bars, state=%s, trail1=%.3f trail2=%.3f",
                    EnumToString(s.tf), n, StateName(s.confirmed), s.trail1, s.trail2));
   return true;
}

//+------------------------------------------------------------------+
//| Bring FilterTF state up to its last closed bar.                    |
//+------------------------------------------------------------------+
void UpdateHTF()
{
   if(!UseHTFFilter) return;
   datetime lastClosed = iTime(_Symbol, g_htf.tf, 1);
   if(lastClosed <= g_htf.lastBar) return;

   int shift = iBarShift(_Symbol, g_htf.tf, g_htf.lastBar, true);
   int from = (shift > 1) ? shift - 1 : 1;
   for(int s = from; s >= 1; s--)
   {
      double a1[1], a2[1];
      if(CopyBuffer(g_htf.h1, 0, s, 1, a1) != 1) return;
      if(CopyBuffer(g_htf.h2, 0, s, 1, a2) != 1) return;
      int flip = AtrStep(g_htf, iClose(_Symbol, g_htf.tf, s), a1[0], a2[0]);
      g_htf.lastBar = iTime(_Symbol, g_htf.tf, s);
      if(flip != 0)
         Log(StringFormat("%s bar %s: FLIP->%s", EnumToString(g_htf.tf), TimeToString(g_htf.lastBar), StateName(flip)));
   }
}

//===================== Entry filters =====================
void ParseWindowList(const string spec, int &starts[], int &ends[])
{
   ArrayResize(starts, 0);
   ArrayResize(ends, 0);
   string parts[];
   int n = StringSplit(spec, ',', parts);
   for(int k = 0; k < n; k++)
   {
      string w = parts[k];
      StringTrimLeft(w); StringTrimRight(w);
      if(StringLen(w) != 11 || StringSubstr(w, 5, 1) != "-")
      {
         if(StringLen(w) > 0) Print("[V5S-ICT] ignoring bad window entry '", w, "'");
         continue;
      }
      int m = ArraySize(starts);
      ArrayResize(starts, m + 1); ArrayResize(ends, m + 1);
      starts[m] = (int)StringToInteger(StringSubstr(w, 0, 2)) * 60 + (int)StringToInteger(StringSubstr(w, 3, 2));
      ends[m]   = (int)StringToInteger(StringSubstr(w, 6, 2)) * 60 + (int)StringToInteger(StringSubstr(w, 9, 2));
   }
}

void ParseWindows()
{
   ParseWindowList(NoTradeWindowsIST, g_ntStart, g_ntEnd);
   ParseWindowList(NoNewBasketWindowsIST, g_nbStart, g_nbEnd);
}

// Is the current server time inside one of these IST windows (end-exclusive, may wrap midnight)?
bool InIstWindows(const int &starts[], const int &ends[])
{
   if(!UseTradeWindows) return false;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int mod = ((dt.hour * 60 + dt.min + ServerToIstMinutes) % 1440 + 1440) % 1440;
   for(int k = 0; k < ArraySize(starts); k++)
   {
      bool inside = (starts[k] <= ends[k]) ? (mod >= starts[k] && mod < ends[k])
                                           : (mod >= starts[k] || mod < ends[k]);
      if(inside) return true;
   }
   return false;
}

// Why a new entry (basket start / ladder add / extra leg) is blocked right now, "" if it isn't.
// isStart: a brand-new basket, which NoNewBasketWindowsIST also blocks.
string EntryBlockReason(const int side, const bool isStart)
{
   if(UseHTFFilter && g_htf.confirmed != side)
      return StringFormat("%s %s", EnumToString(g_htf.tf), StateName(g_htf.confirmed));

   datetime now = TimeCurrent();
   if(InIstWindows(g_ntStart, g_ntEnd)) return "no-trade window";
   if(isStart && InIstWindows(g_nbStart, g_nbEnd)) return "no-new-basket window";

   if(WeekOpenBlockMinutes > 0)
   {
      // first bar of the week = first bar after a gap of more than a day
      int shiftNow = iBarShift(_Symbol, PERIOD_M1, now, false);
      for(int s = shiftNow; s < shiftNow + WeekOpenBlockMinutes + 1; s++)
      {
         datetime t0 = iTime(_Symbol, PERIOD_M1, s);
         datetime t1 = iTime(_Symbol, PERIOD_M1, s + 1);
         if(t0 == 0 || t1 == 0) break;
         if(now - t0 >= WeekOpenBlockMinutes * 60) break;
         if(t0 - t1 > 24 * 3600) return "week open";
      }
   }

   if(MaxSpreadPrice > 0.0)
   {
      double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(spread > MaxSpreadPrice) return StringFormat("spread %.2f", spread);
   }
   return "";
}

//===================== Positions / orders =====================
double NormalizeVolume(const double v)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   double r = MathRound(v / step) * step;
   if(r < minv) r = minv;
   if(r > maxv) r = maxv;
   return NormalizeDouble(r, 2);
}

bool IsBasketPosition(const long magic)
{
   return PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == magic;
}

int CountPositions(const long magic)
{
   int n = 0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
      if(PositionGetTicket(k) != 0 && IsBasketPosition(magic)) n++;
   return n;
}

// Open positions of the basket opposite to its side (= its hedges).
int CountHedges(const Basket &b)
{
   ENUM_POSITION_TYPE hedgeType = (b.side < 0) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int n = 0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
      if(PositionGetTicket(k) != 0 && IsBasketPosition(b.magic)
         && (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == hedgeType) n++;
   return n;
}

double BasketVolume(const long magic)
{
   double v = 0.0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
      if(PositionGetTicket(k) != 0 && IsBasketPosition(magic))
         v += PositionGetDouble(POSITION_VOLUME);
   return v;
}

double BasketProfit(const long magic)
{
   double p = 0.0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
      if(PositionGetTicket(k) != 0 && IsBasketPosition(magic))
         p += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   return p;
}

bool OpenOrder(Basket &b, const int dir, const double volume, const string tag)
{
   double vol = NormalizeVolume(volume);
   g_trade.SetExpertMagicNumber(b.magic);
   string comment = StringFormat("V5SICT %s %s", b.side < 0 ? "S" : "B", tag);
   bool ok = (dir > 0) ? g_trade.Buy(vol, _Symbol, 0.0, 0.0, 0.0, comment)
                       : g_trade.Sell(vol, _Symbol, 0.0, 0.0, 0.0, comment);
   uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
   {
      Print("[V5S-ICT] ", SideName(b.side), " basket: ", (dir > 0 ? "BUY " : "SELL "), DoubleToString(vol, 2),
            " (", tag, ") FAILED retcode=", rc, " ", g_trade.ResultRetcodeDescription());
      return false;
   }
   Log(StringFormat("%s basket: %s %.2f (%s) @ %.3f", SideName(b.side), dir > 0 ? "BUY" : "SELL",
                    vol, tag, g_trade.ResultPrice()));
   return true;
}

void SaveState(const Basket &b)
{
   GlobalVariableSet(b.gvPrefix + "step", b.step);
   GlobalVariableSet(b.gvPrefix + "unhedged", b.unhedged);
   GlobalVariableSet(b.gvPrefix + "extras", b.extras);
}

void ResetBasket(Basket &b)
{
   b.step = 0;
   b.unhedged = 0.0;
   b.extras = 0;
   b.closing = false;
   SaveState(b);
}

// Close every position of the basket; returns true once none remain.
bool CloseBasket(Basket &b, const string reason)
{
   if(!b.closing)
   {
      Log(StringFormat("%s basket: CLOSE ALL (%s), profit=%.2f, step=%d",
                       SideName(b.side), reason, BasketProfit(b.magic), b.step));
      b.closing = true;
   }
   g_trade.SetExpertMagicNumber(b.magic);
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong ticket = PositionGetTicket(k);
      if(ticket == 0 || !IsBasketPosition(b.magic)) continue;
      if(!g_trade.PositionClose(ticket, SlippagePoints))
         Print("[V5S-ICT] close #", ticket, " failed retcode=", g_trade.ResultRetcode());
   }
   if(CountPositions(b.magic) == 0)
   {
      ResetBasket(b);
      return true;
   }
   return false;
}

// HedgeRelease: close the basket's open hedges; returns true if any were closed.
bool ReleaseHedges(Basket &b)
{
   ENUM_POSITION_TYPE hedgeType = (b.side < 0) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   double released = 0.0, pl = 0.0;
   g_trade.SetExpertMagicNumber(b.magic);
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong ticket = PositionGetTicket(k);
      if(ticket == 0 || !IsBasketPosition(b.magic)) continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != hedgeType) continue;
      double vol = PositionGetDouble(POSITION_VOLUME);
      double p   = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(g_trade.PositionClose(ticket, SlippagePoints))
      {
         released += vol;
         pl += p;
      }
      else
         Print("[V5S-ICT] hedge release close #", ticket, " failed retcode=", g_trade.ResultRetcode());
   }
   if(released <= 0.0) return false;

   // the released volume is basket-side exposure again, waiting for the next hedge
   b.unhedged += released;
   b.extras = 0;
   SaveState(b);
   Log(StringFormat("%s basket: RELEASED hedges %.2f lots, booked %.2f", SideName(b.side), released, pl));
   return true;
}

//+------------------------------------------------------------------+
//| Candle-close logic for one basket.                                 |
//| flip/cisd are in absolute terms (+1 bull, -1 bear); "with" means   |
//| in the basket's own direction, "against" the opposite.             |
//+------------------------------------------------------------------+
void OnBasketBar(Basket &b, const int flip, const int cisd)
{
   if(!b.enabled || b.closing) return;

   const bool active        = (g_sig.confirmed == b.side);
   const bool flipWith      = (flip == b.side);
   const bool flipAgainst   = (flip == -b.side);
   const bool cisdWith      = (cisd == b.side);
   const bool cisdAgainst   = (cisd == -b.side);
   const int  dir           = b.side;       // basket-side order direction
   const bool hasBasket     = (b.step > 0);

   //--- no basket: start one on the flip candle or a with-side CISD while active
   if(!hasBasket)
   {
      if(active && (flipWith || cisdWith))
      {
         string why = EntryBlockReason(b.side, true);
         if(why != "")
         {
            Log(StringFormat("%s basket: start skipped (%s)", SideName(b.side), why));
            return;
         }
         if(OpenOrder(b, dir, LotUnit, flipWith ? "L1 flip" : "L1 cisd"))
         {
            b.step = 1;
            b.unhedged = LotUnit;
            b.extras = 0;
            SaveState(b);
         }
      }
      return;
   }

   //--- ladder complete: the next against-signal closes the whole basket
   if(b.step >= MaxLadderStep && (cisdAgainst || flipAgainst))
   {
      CloseBasket(b, flipAgainst ? "cap reached + opposite flip" : "cap reached + opposite CISD");
      return;
   }

   //--- hedge: against-CISD while active, or the flip against (which also freezes)
   if((active && cisdAgainst) || flipAgainst)
   {
      if(b.unhedged > 0.0)
      {
         if(OpenOrder(b, -dir, b.unhedged, StringFormat("H%d%s", b.step, flipAgainst ? " frz" : "")))
         {
            b.unhedged = 0.0;
            b.extras = 0;
            SaveState(b);
         }
      }
      return;
   }

   //--- frozen: nothing else happens while the state is against the basket
   if(!active || !cisdWith) return;

   //--- with-side CISD while hedged: release the hedges instead of stacking a bigger leg
   if(HedgeRelease && b.unhedged <= 0.0 && CountHedges(b) > 0)
   {
      // a hedged basket stays hedged through a no-trade window; the release waits for it to end
      if(InIstWindows(g_ntStart, g_ntEnd)) return;
      ReleaseHedges(b);
      return;
   }

   if(b.step >= MaxLadderStep) return;

   string why = EntryBlockReason(b.side, false);
   if(why != "")
   {
      Log(StringFormat("%s basket: add skipped (%s)", SideName(b.side), why));
      return;
   }

   //--- with-side CISD: next ladder order, or an extra 1-unit leg if unhedged
   if(b.unhedged > 0.0)
   {
      if(MaxExtraLegs >= 0 && b.extras >= MaxExtraLegs) return;
      if(OpenOrder(b, dir, LotUnit, StringFormat("X%d", b.step)))
      {
         b.unhedged += LotUnit;
         b.extras++;
         SaveState(b);
      }
   }
   else
   {
      int next = b.step + 1;
      if(OpenOrder(b, dir, LotUnit * next, StringFormat("L%d", next)))
      {
         b.step = next;
         b.unhedged = LotUnit * next;
         b.extras = 0;
         SaveState(b);
      }
   }
}

//+------------------------------------------------------------------+
//| Every-tick target / loss-limit check for one basket.               |
//+------------------------------------------------------------------+
void CheckTarget(Basket &b)
{
   if(!b.enabled) return;

   if(b.closing)
   {
      CloseBasket(b, "retry");
      return;
   }
   if(b.step == 0) return;

   int n = CountPositions(b.magic);
   if(n == 0)
   {
      // closed outside the EA (manual close / stop-out)
      Log(StringFormat("%s basket: no positions left, resetting", SideName(b.side)));
      ResetBasket(b);
      return;
   }

   // Emergency exit: a basket whose open volume keeps growing (legs + released hedges in a long
   // one-way trend) is closed outright, not frozen -- in the 2-year test every no-exit variant
   // eventually blew up, and blocking new legs instead (a lot cap) made it worse.
   double vol = BasketVolume(b.magic);
   if(EmergencyBasketLots > 0.0 && vol >= EmergencyBasketLots - 1e-9)
   {
      CloseBasket(b, StringFormat("EMERGENCY %.2f lots >= %.2f", vol, EmergencyBasketLots));
      return;
   }

   double profit = BasketProfit(b.magic);
   if(BasketMaxLossMoney > 0.0 && profit <= -BasketMaxLossMoney)
   {
      CloseBasket(b, StringFormat("loss limit -%.2f hit", BasketMaxLossMoney));
      return;
   }

   if(n == 1 && b.step == 1)
   {
      for(int k = PositionsTotal() - 1; k >= 0; k--)
      {
         if(PositionGetTicket(k) == 0 || !IsBasketPosition(b.magic)) continue;
         double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         double move = (b.side > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_BID) - openPrice
                                    : openPrice - SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         if(move >= FirstTargetPrice)
            CloseBasket(b, StringFormat("first position +%.2f", move));
         return;
      }
      return;
   }

   double target = (b.step >= BreakevenFromStep) ? BreakevenMoney : BasketTargetMoney;
   if(profit >= target)
      CloseBasket(b, StringFormat("basket target %.2f reached", target));
}

//+------------------------------------------------------------------+
void InitBasket(Basket &b, const int side, const long magic, const bool enabled)
{
   b.side = side;
   b.magic = magic;
   b.enabled = enabled;
   b.closing = false;
   b.gvPrefix = StringFormat("V5SICT_%s_%I64d_", _Symbol, magic);

   // Restore after a restart; no open positions = no basket.
   if(CountPositions(magic) > 0 && GlobalVariableCheck(b.gvPrefix + "step"))
   {
      b.step = (int)GlobalVariableGet(b.gvPrefix + "step");
      b.unhedged = GlobalVariableGet(b.gvPrefix + "unhedged");
      b.extras = GlobalVariableCheck(b.gvPrefix + "extras") ? (int)GlobalVariableGet(b.gvPrefix + "extras") : 0;
      Log(StringFormat("%s basket restored: step=%d unhedged=%.2f positions=%d",
                       SideName(side), b.step, b.unhedged, CountPositions(magic)));
   }
   else
   {
      b.step = 0;
      b.unhedged = 0.0;
      b.extras = 0;
      SaveState(b);
   }
}

bool InitAtrState(AtrState &s, const ENUM_TIMEFRAMES tf)
{
   s.tf = tf;
   s.h1 = iATR(_Symbol, tf, ATRPeriod);
   s.h2 = iATR(_Symbol, tf, ATRPeriod2);
   s.haveTrail = false;
   s.confirmed = 0;
   s.lastBar = 0;
   s.trail1 = s.trail2 = s.prevClose = 0.0;
   return s.h1 != INVALID_HANDLE && s.h2 != INVALID_HANDLE;
}

//+------------------------------------------------------------------+
int OnInit()
{
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("[V5S-ICT] this EA needs a HEDGING account");
      return INIT_FAILED;
   }
   if(SellMagic == BuyMagic)
   {
      Print("[V5S-ICT] SellMagic and BuyMagic must differ");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(!InitAtrState(g_sig, SignalTF) || (UseHTFFilter && !InitAtrState(g_htf, FilterTF)))
   {
      Print("[V5S-ICT] iATR handle failed");
      return INIT_FAILED;
   }

   ParseWindows();

   g_trade.SetDeviationInPoints(SlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   InitBasket(g_sell, -1, SellMagic, EnableSellBasket);
   InitBasket(g_buy,   1, BuyMagic,  EnableBuyBasket);

   g_nBars = 0;
   g_warmedUp = false;
   // Warmup runs on the first tick -- ATR buffers may not be ready yet in OnInit.
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_sig.h1 != INVALID_HANDLE) IndicatorRelease(g_sig.h1);
   if(g_sig.h2 != INVALID_HANDLE) IndicatorRelease(g_sig.h2);
   if(UseHTFFilter)
   {
      if(g_htf.h1 != INVALID_HANDLE) IndicatorRelease(g_htf.h1);
      if(g_htf.h2 != INVALID_HANDLE) IndicatorRelease(g_htf.h2);
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_warmedUp)
   {
      if(!WarmupTF(g_sig, true)) return;
      if(UseHTFFilter && !WarmupTF(g_htf, false)) return;
      g_warmedUp = true;
   }

   // Filter state first, so a SignalTF bar closing together with a FilterTF bar sees the new state.
   UpdateHTF();

   //--- new closed SignalTF bar(s)?
   datetime lastClosed = iTime(_Symbol, g_sig.tf, 1);
   if(lastClosed > g_sig.lastBar)
   {
      int shift = iBarShift(_Symbol, g_sig.tf, g_sig.lastBar, true);
      // shift of the last processed bar; bars 1..shift-1 are new. Fall back to just bar 1.
      int from = (shift > 1) ? shift - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         double a1[1], a2[1];
         if(CopyBuffer(g_sig.h1, 0, s, 1, a1) != 1) return;
         if(CopyBuffer(g_sig.h2, 0, s, 1, a2) != 1) return;

         double c = iClose(_Symbol, g_sig.tf, s);
         int flip = AtrStep(g_sig, c, a1[0], a2[0]);
         int cisd = CisdStep(iOpen(_Symbol, g_sig.tf, s), c);
         g_sig.lastBar = iTime(_Symbol, g_sig.tf, s);

         if(flip != 0 || cisd != 0)
            Log(StringFormat("bar %s: state=%s%s%s", TimeToString(g_sig.lastBar), StateName(g_sig.confirmed),
                             flip != 0 ? (flip > 0 ? " FLIP->STRONG" : " FLIP->WEAK") : "",
                             cisd != 0 ? (cisd > 0 ? " bullCISD" : " bearCISD") : ""));

         // Only the most recent closed bar trades; older catch-up bars just update state.
         if(s == 1)
         {
            OnBasketBar(g_sell, flip, cisd);
            OnBasketBar(g_buy,  flip, cisd);
         }
      }
   }

   CheckTarget(g_sell);
   CheckTarget(g_buy);
}
//+------------------------------------------------------------------+
