//+------------------------------------------------------------------+
//| OB_EA_v1.01.mq5                                                  |
//| Basic order block + CISD EA (chart timeframe, e.g. M3 or M5).    |
//|                                                                  |
//| Zones: mql5/OB_Detector_v1.06 (iCustom, same timeframe as the    |
//| chart, drawing ON so the zones plot in the visual tester too).   |
//|                                                                  |
//| v1.01 -- the MOST RECENT OB TAKES CONTROL (sell mirrored):       |
//|  1. whichever OB formed last (bullish or bearish, candle close)  |
//|     is in control; when control flips, this EA's open trades on  |
//|     the other side are closed at that candle close               |
//|  2. a LATER closed candle is a CISD in the controlling OB's      |
//|     direction (AlgoAlpha port, same as V6S-ICT, tolerance 0.7)   |
//|     -> market entry, SL = OB bottom - SLBufferPrice (buy) /      |
//|     OB top + SLBufferPrice (sell), TP = RiskReward x risk (1:2)  |
//|  3. one trade per OB; the controlling OB mitigated -> no control |
//|     (no entries) until the next OB forms                         |
//|  OnePositionAtATime: no new entry while this EA has a position.  |
//|  No retest of the zone is required.                              |
//|  Every CISD is drawn on the chart (arrow + the level it broke),  |
//|  also in the visual tester.                                      |
//|                                                                  |
//| Decisions on closed candles only (first tick of a new candle).   |
//+------------------------------------------------------------------+
#property version   "1.01"
#property tester_indicator "OB_Detector_v1.06.ex5"
#include <Trade/Trade.mqh>

input group "Trade"
input double Lots               = 0.01;     // lot size
input double SLBufferPrice      = 0.5;      // SL buffer beyond the OB (price units)
input double RiskReward         = 2.0;      // TP = RiskReward x risk
input bool   OnePositionAtATime = true;     // no new entry while this EA has a position open
input long   MagicNumber        = 26100101;

input group "Order blocks (OB_Detector_v1.06)"
input int    OBLength           = 5;        // volume pivot length
input ENUM_APPLIED_VOLUME OBVolume = VOLUME_TICK;
input int    OBMitigation       = 0;        // 0 = wick, 1 = close
input bool   ShowZones          = true;     // draw the zones (also in the visual tester)

input group "CISD"
input double CISDTolerance      = 0.7;      // AlgoAlpha "Noise Filter"
input bool   ShowCISD           = true;     // draw every CISD (arrow + broken level)
input int    WarmupBars         = 3000;     // closed candles replayed at start (no trades)

#define SLOTS  3
#define FIELDS 5
enum { F_TOP = 0, F_BTM = 1, F_OBT = 2, F_FORMED = 3 };

CTrade   g_trade;
int      g_ob = INVALID_HANDLE;
datetime g_lastBar = 0;

struct ArmedOB
  {
   bool     on;
   datetime obTime;
   datetime formed;
   double   top;
   double   btm;
  };
ArmedOB  g_ctl;                // the controlling (most recent) OB
int      g_ctlDir = 0;        // +1 bullish OB in control, -1 bearish, 0 none
datetime g_seen[2];           // newest OB time already seen per side ([0] bull, [1] bear)
datetime g_usedOB = 0;        // OB time of the last OB traded (one trade per OB)
datetime g_t[];               // candle times (for drawing CISD levels)
double   g_cisdLvl = 0.0;     // level the last confirmed CISD broke
int      g_cisdLvlIdx = 0;    // candle index of that level

//===================== CISD (AlgoAlpha port, identical to V6S-ICT) =====================
double g_o[], g_c[];
int    g_n = 0;
double g_bearOpen[]; int g_bearIdx[];
double g_bullOpen[]; int g_bullIdx[];

void InsertFrontD(double &a[], const double v) { int n = ArraySize(a); ArrayResize(a, n + 1); for(int k = n; k > 0; k--) a[k] = a[k-1]; a[0] = v; }
void InsertFrontI(int &a[], const int v)       { int n = ArraySize(a); ArrayResize(a, n + 1); for(int k = n; k > 0; k--) a[k] = a[k-1]; a[0] = v; }
void RemoveFrontD(double &a[]) { int n = ArraySize(a); if(n == 0) return; for(int k = 0; k < n - 1; k++) a[k] = a[k+1]; ArrayResize(a, n - 1); }
void RemoveFrontI(int &a[])    { int n = ArraySize(a); if(n == 0) return; for(int k = 0; k < n - 1; k++) a[k] = a[k+1]; ArrayResize(a, n - 1); }

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
            g_cisdLvl = candOpen; g_cisdLvlIdx = candIdx;
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
            g_cisdLvl = candOpen; g_cisdLvlIdx = candIdx;
            ArrayResize(g_bullOpen, 0); ArrayResize(g_bullIdx, 0);
            return true;
           }
         RemoveFrontD(g_bullOpen); RemoveFrontI(g_bullIdx);
        }
      else break;
     }
   return false;
  }

// Append one closed candle; +1 bullish CISD, -1 bearish, 0 none (bull wins a tie, as V6S-ICT).
int CisdStep(const datetime t, const double o, const double h, const double l, const double c)
  {
   int i = g_n;
   ArrayResize(g_o, i + 1, 100000); ArrayResize(g_c, i + 1, 100000); ArrayResize(g_t, i + 1, 100000);
   g_o[i] = o; g_c[i] = c; g_t[i] = t; g_n = i + 1;
   if(i >= 1)
     {
      if(g_c[i-1] < g_o[i-1] && c > o) { InsertFrontI(g_bearIdx, i); InsertFrontD(g_bearOpen, o); }
      if(g_c[i-1] > g_o[i-1] && c < o) { InsertFrontI(g_bullIdx, i); InsertFrontD(g_bullOpen, o); }
     }
   int cisd = 0;
   if(ConfirmBearCISD(i)) cisd = -1;
   if(ConfirmBullCISD(i)) cisd = 1;
   if(cisd != 0 && ShowCISD) DrawCISD(cisd, i, h, l);
   return cisd;
  }

void DrawCISD(const int dir, const int i, const double h, const double l)
  {
   color  cl = dir > 0 ? clrLime : clrOrangeRed;
   string id = "OBEA_CISD_" + IntegerToString((long)g_t[i]);
   string ar = id + "_a", ln = id + "_l";
   ObjectCreate(0, ar, OBJ_ARROW, 0, g_t[i], dir > 0 ? l : h);
   ObjectSetInteger(0, ar, OBJPROP_ARROWCODE, dir > 0 ? 233 : 234);
   ObjectSetInteger(0, ar, OBJPROP_ANCHOR, dir > 0 ? ANCHOR_TOP : ANCHOR_BOTTOM);
   ObjectSetInteger(0, ar, OBJPROP_COLOR, cl);
   ObjectSetInteger(0, ar, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, ar, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, ar, OBJPROP_HIDDEN, true);
   ObjectCreate(0, ln, OBJ_TREND, 0, g_t[g_cisdLvlIdx], g_cisdLvl, g_t[i], g_cisdLvl);
   ObjectSetInteger(0, ln, OBJPROP_COLOR, cl);
   ObjectSetInteger(0, ln, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, ln, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, ln, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, ln, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, ln, OBJPROP_HIDDEN, true);
   ObjectSetString(0, ln, OBJPROP_TOOLTIP, (dir > 0 ? "Bullish" : "Bearish") + " CISD " + DoubleToString(g_cisdLvl, _Digits));
  }

//===================== helpers =====================
bool ReadSlot(const int side, const int slot, double &v[])
  {
   ArrayResize(v, FIELDS);
   int base = (side * SLOTS + slot) * FIELDS;
   for(int f = 0; f < FIELDS; f++)
     {
      double b[];
      if(CopyBuffer(g_ob, base + f, 1, 1, b) != 1) return false;
      v[f] = b[0];
     }
   return v[F_TOP] != EMPTY_VALUE;
  }

// Is the OB with this OB time still among the indicator's active zones on the last closed candle?
bool StillActive(const int side, const datetime obTime)
  {
   double v[];
   for(int s = 0; s < SLOTS; s++)
      if(ReadSlot(side, s, v) && (datetime)(long)v[F_OBT] == obTime) return true;
   return false;
  }

// dir = 0: any position of this EA; +1 / -1: only buys / sells
bool HasPosition(const int dir = 0)
  {
   for(int k = PositionsTotal() - 1; k >= 0; k--)
     {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      long pt = PositionGetInteger(POSITION_TYPE);
      if(dir == 0 || (dir > 0 && pt == POSITION_TYPE_BUY) || (dir < 0 && pt == POSITION_TYPE_SELL)) return true;
     }
   return false;
  }

void CloseSide(const int dir, const string why)
  {
   for(int k = PositionsTotal() - 1; k >= 0; k--)
     {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      long pt = PositionGetInteger(POSITION_TYPE);
      if((dir > 0 && pt == POSITION_TYPE_BUY) || (dir < 0 && pt == POSITION_TYPE_SELL))
        {
         bool ok = g_trade.PositionClose(t);
         PrintFormat("OB_EA close %s #%I64u (%s) %s", dir > 0 ? "BUY" : "SELL", t, why, ok ? "done" : g_trade.ResultRetcodeDescription());
        }
     }
  }

// Newest OB on either side takes control; returns true if control flipped this candle.
bool UpdateControl()
  {
   bool flipped = false;
   for(int side = 0; side < 2; side++)
     {
      double v[];
      if(!ReadSlot(side, 0, v)) continue;
      datetime obt = (datetime)(long)v[F_OBT], formed = (datetime)(long)v[F_FORMED];
      if(obt == g_seen[side]) continue;
      g_seen[side] = obt;
      if(g_ctlDir != 0 && formed <= g_ctl.formed) continue;   // an older OB resurfacing after a mitigation
      int dir = side == 0 ? 1 : -1;
      if(dir != g_ctlDir) flipped = true;
      g_ctl.on = true; g_ctl.obTime = obt; g_ctl.formed = formed; g_ctl.top = v[F_TOP]; g_ctl.btm = v[F_BTM];
      g_ctlDir = dir;
     }
   if(g_ctl.on && !StillActive(g_ctlDir > 0 ? 0 : 1, g_ctl.obTime))
      g_ctl.on = false;   // controlling OB mitigated -> no entries until the next OB forms
   return flipped;
  }

void Enter(const int dir, const datetime barTime)
  {
   ArmedOB a = g_ctl;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = dir > 0 ? ask : bid;
   double sl    = dir > 0 ? a.btm - SLBufferPrice : a.top + SLBufferPrice;
   double risk  = (entry - sl) * dir;
   g_usedOB = a.obTime;
   if(risk <= 0)
     {
      PrintFormat("OB_EA skip %s: price already beyond the SL (entry %.2f, SL %.2f)", dir > 0 ? "BUY" : "SELL", entry, sl);
      return;
     }
   double tp = entry + dir * RiskReward * risk;
   long   stopsLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(risk < stopsLvl * _Point)
     {
      PrintFormat("OB_EA skip: SL %.2f closer than the stops level", risk);
      return;
     }
   sl = NormalizeDouble(sl, _Digits); tp = NormalizeDouble(tp, _Digits);
   string cm = StringFormat("OBEA %s %s", dir > 0 ? "B" : "S", StringSubstr(EnumToString((ENUM_TIMEFRAMES)_Period), 7));
   bool ok = dir > 0 ? g_trade.Buy(Lots, _Symbol, 0.0, sl, tp, cm) : g_trade.Sell(Lots, _Symbol, 0.0, sl, tp, cm);
   PrintFormat("OB_EA %s %s | OB %s zone %.2f-%.2f formed %s | CISD candle %s | entry %.2f SL %.2f TP %.2f (risk %.2f) | %s",
               dir > 0 ? "BUY" : "SELL", ok ? "sent" : "FAILED", TimeToString(a.obTime), a.btm, a.top,
               TimeToString(a.formed), TimeToString(barTime), entry, sl, tp, risk,
               ok ? "" : g_trade.ResultRetcodeDescription());
  }

//===================== events =====================
int OnInit()
  {
   if(Lots <= 0 || RiskReward <= 0 || SLBufferPrice < 0) return INIT_PARAMETERS_INCORRECT;
   g_ob = iCustom(_Symbol, PERIOD_CURRENT, "OB_Detector_v1.06", OBLength, OBVolume, OBMitigation, ShowZones);
   if(g_ob == INVALID_HANDLE)
     {
      Print("OB_EA: cannot load indicator OB_Detector_v1.06 (must be in MQL5\\Indicators)");
      return INIT_FAILED;
     }
   if(ShowZones && !MQLInfoInteger(MQL_TESTER))
      ChartIndicatorAdd(0, 0, g_ob);
   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   // reset state (also on symbol / timeframe change)
   g_n = 0; ArrayResize(g_o, 0); ArrayResize(g_c, 0);
   ArrayResize(g_bearOpen, 0); ArrayResize(g_bearIdx, 0); ArrayResize(g_bullOpen, 0); ArrayResize(g_bullIdx, 0);
   g_ctl.on = false; g_ctl.obTime = 0; g_ctl.formed = 0; g_ctlDir = 0; g_usedOB = 0; g_seen[0] = 0; g_seen[1] = 0;
   ArrayResize(g_t, 0);
   ObjectsDeleteAll(0, "OBEA_CISD_");

   // warm up the CISD state on closed candles (no trades)
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_CURRENT, 1, WarmupBars, r);   // oldest first
   for(int k = 0; k < got; k++) CisdStep(r[k].time, r[k].open, r[k].high, r[k].low, r[k].close);
   g_lastBar = iTime(_Symbol, PERIOD_CURRENT, 0);
   PrintFormat("OB_EA v1.01 started on %s %s | warm-up %d candles | lots %.2f, SL buffer %.2f, 1:%.1f, magic %I64d",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period), got, Lots, SLBufferPrice, RiskReward, MagicNumber);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_ob != INVALID_HANDLE) IndicatorRelease(g_ob);
   if(!MQLInfoInteger(MQL_TESTER)) ObjectsDeleteAll(0, "OBEA_CISD_");
  }

void OnTick()
  {
   datetime t0 = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(t0 == 0 || t0 == g_lastBar) return;
   if(BarsCalculated(g_ob) < 10) return;           // indicator not ready -- retry next tick
   g_lastBar = t0;

   // the candle that just closed
   MqlRates r[];
   if(CopyRates(_Symbol, PERIOD_CURRENT, 1, 1, r) != 1) return;
   int cisd = CisdStep(r[0].time, r[0].open, r[0].high, r[0].low, r[0].close);

   if(UpdateControl() && g_ctlDir != 0)
      CloseSide(-g_ctlDir, "opposite OB took control: " + TimeToString(g_ctl.obTime));
   if(cisd == 0 || cisd != g_ctlDir || !g_ctl.on) return;
   if(g_ctl.obTime == g_usedOB) return;              // one trade per OB
   if(r[0].time < g_ctl.formed) return;              // CISD must come on a candle after the OB formed
   if(OnePositionAtATime && HasPosition()) return;
   Enter(cisd, r[0].time);
  }
//+------------------------------------------------------------------+
