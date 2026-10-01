//+------------------------------------------------------------------+
//| OB_EA_v1.00.mq5                                                  |
//| Basic order block + CISD EA (chart timeframe, e.g. M3 or M5).    |
//|                                                                  |
//| Zones: mql5/OB_Detector_v1.06 (iCustom, same timeframe as the    |
//| chart, drawing ON so the zones plot in the visual tester too).   |
//|                                                                  |
//| BUY (sell mirrored):                                             |
//|  1. a bullish OB forms (candle close) -> it becomes the armed    |
//|     bullish OB (a newer bullish OB replaces it)                  |
//|  2. a LATER closed candle is a bullish CISD (AlgoAlpha port,     |
//|     same as V6S-ICT, tolerance 0.7) while that OB is still       |
//|     active (not mitigated)                                       |
//|  3. market buy, SL = OB bottom - SLBufferPrice,                  |
//|     TP = entry + RiskReward x risk (1:2)                         |
//|  One trade per OB. OnePositionAtATime: no new entry while this   |
//|  EA's position is open. No retest of the zone is required.       |
//|                                                                  |
//| Decisions on closed candles only (first tick of a new candle).   |
//+------------------------------------------------------------------+
#property version   "1.00"
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
ArmedOB  g_armed[2];          // [0] bullish, [1] bearish
datetime g_usedOB[2];         // OB time of the last OB traded per side (one trade per OB)

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

// Append one closed candle; +1 bullish CISD, -1 bearish, 0 none (bull wins a tie, as V6S-ICT).
int CisdStep(const double o, const double c)
  {
   int i = g_n;
   ArrayResize(g_o, i + 1, 100000); ArrayResize(g_c, i + 1, 100000);
   g_o[i] = o; g_c[i] = c; g_n = i + 1;
   if(i >= 1)
     {
      if(g_c[i-1] < g_o[i-1] && c > o) { InsertFrontI(g_bearIdx, i); InsertFrontD(g_bearOpen, o); }
      if(g_c[i-1] > g_o[i-1] && c < o) { InsertFrontI(g_bullIdx, i); InsertFrontD(g_bullOpen, o); }
     }
   int cisd = 0;
   if(ConfirmBearCISD(i)) cisd = -1;
   if(ConfirmBullCISD(i)) cisd = 1;
   return cisd;
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

bool HasPosition()
  {
   for(int k = PositionsTotal() - 1; k >= 0; k--)
     {
      ulong t = PositionGetTicket(k);
      if(t > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return true;
     }
   return false;
  }

void UpdateArmed(const int side)
  {
   double v[];
   if(ReadSlot(side, 0, v))
     {
      datetime obt = (datetime)(long)v[F_OBT];
      if(obt != g_armed[side].obTime && obt != g_usedOB[side])
        {
         g_armed[side].on     = true;
         g_armed[side].obTime = obt;
         g_armed[side].formed = (datetime)(long)v[F_FORMED];
         g_armed[side].top    = v[F_TOP];
         g_armed[side].btm    = v[F_BTM];
        }
     }
   if(g_armed[side].on && !StillActive(side, g_armed[side].obTime))
      g_armed[side].on = false;   // mitigated
  }

void Enter(const int dir, const datetime barTime)
  {
   int side = dir > 0 ? 0 : 1;
   ArmedOB a = g_armed[side];
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = dir > 0 ? ask : bid;
   double sl    = dir > 0 ? a.btm - SLBufferPrice : a.top + SLBufferPrice;
   double risk  = (entry - sl) * dir;
   g_usedOB[side] = a.obTime;
   g_armed[side].on = false;
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
   for(int s = 0; s < 2; s++) { g_armed[s].on = false; g_armed[s].obTime = 0; g_usedOB[s] = 0; }

   // warm up the CISD state on closed candles (no trades)
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_CURRENT, 1, WarmupBars, r);   // oldest first
   for(int k = 0; k < got; k++) CisdStep(r[k].open, r[k].close);
   g_lastBar = iTime(_Symbol, PERIOD_CURRENT, 0);
   PrintFormat("OB_EA v1.00 started on %s %s | warm-up %d candles | lots %.2f, SL buffer %.2f, 1:%.1f, magic %I64d",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period), got, Lots, SLBufferPrice, RiskReward, MagicNumber);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_ob != INVALID_HANDLE) IndicatorRelease(g_ob);
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
   int cisd = CisdStep(r[0].open, r[0].close);

   UpdateArmed(0);
   UpdateArmed(1);
   if(cisd == 0) return;

   int side = cisd > 0 ? 0 : 1;
   if(!g_armed[side].on) return;
   if(r[0].time < g_armed[side].formed) return;     // CISD must come on a candle after the OB formed
   if(OnePositionAtATime && HasPosition()) return;
   Enter(cisd, r[0].time);
  }
//+------------------------------------------------------------------+
