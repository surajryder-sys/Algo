//+------------------------------------------------------------------+
//| CENT ACCOUNT copy: all lots x3.                                    |
//| V6S_ICT_OB_3.25 (2026-10-03) -- OB retest EA (XAUUSD), standalone  |
//| version of the OB slot of the V6S_ICT_OB_v5.00 combination.        |
//| BUY (sell mirrored):                                               |
//|  1. H2 / H1 bullish order block (OB_Detector_v1.07, volume pivot   |
//|     5, wick mitigation) -- its FIRST retest (live touch);          |
//|  2. OB height < 20 pts and < 0.25 x avg daily range (10 D1 bars);  |
//|  3. first M5 LuxAlgo Classic bullish CISD closing within 4 h of    |
//|     the retest minute -> buy at market; no trade if the OB is      |
//|     invalidated first, on Friday (IST), if an OB trade is already  |
//|     open, or if the stop is under 5 pts;                           |
//|  4. SL = OB bottom - 0.5 (max 20 pts from entry), TP = 2R.         |
//| Magic 26100601, comment V6S-ICT-OB-H1 / V6S-ICT-OB-H2.             |
//| Sim (Real7 ticks Jan 2024 - Sep 2026, 0.10 lot): 347 trades,       |
//| +$14,999, max DD $1,153; tester (in test5.1): +$14,061.            |
//+------------------------------------------------------------------+
#property copyright "V6S-ICT"
#property version   "3.25"
#property tester_indicator "OB_Detector_v1.07.ex5"
#include <Trade/Trade.mqh>

input group "General"
input int    SlippagePoints     = 50;
input int    ServerToISTMinutes = 330;   // minutes to add to server time to get IST (Exness server = UTC -> 330)
input int    MaxSignalDelaySec  = 120;   // a candle must have closed within this many seconds to open a trade
input bool   VerboseLog         = true;

//===================== OB retest slot =====================
// H2 / H1 order block (OB_Detector_v1.07) -> FIRST retest -> first M5 LuxAlgo CISD in the bounce direction closing
// within OBCISDWindowMin after the retest minute -> market entry. SL = OB low - OBSLBuffer (buy) / OB high + OBSLBuffer
// (sell), capped at OBMaxSL; TP = OBRR x risk. Skipped: OB height >= OBMaxHeight or >= OBMaxHeightADR x avg daily range
// (last OBADRDays D1 bars); Friday (IST); OB invalidated (wick mitigation on its own TF, as the detector) before the CISD;
// an OB-slot position already open when its CISD fires (one OB trade at a time; each OB trades once).
// Sim (Real7 ticks, Nov 2024 - Sep 2026, 0.1 lot): 306 trades, +$13,810, max DD $1,132, 2 losing months of 23.
input group "OB retest slot"
input bool   UseOBSlot       = true;
input double OBLots          = 0.30;
input long   OBMagicNumber   = 26100601;
input bool   OBUseH4         = false;
input bool   OBUseH2         = true;
input bool   OBUseH1         = true;
input bool   OBUseM30        = false;
input double OBRR            = 2.0;     // TP = this x risk
input double OBSLBuffer      = 0.5;     // SL beyond the OB edge
input double OBMaxSL         = 20.0;    // SL never further than this from entry
input double OBMinStop       = 5.0;     // skip if the OB stop (entry to OB edge +/- buffer) is under this (0 = off)
input double OBMaxHeight     = 20.0;    // skip OBs at least this tall (points)
input double OBMaxHeightADR  = 0.25;    // ...or at least this x the avg daily range of the last OBADRDays D1 bars
input int    OBADRDays       = 10;
input int    OBCISDWindowMin = 240;     // the CISD candle must close within this many minutes after the retest minute
input bool   OBNoFriday      = true;    // no OB entries on Friday (IST)
input int    OBLuxMaxBars    = 100;     // LuxAlgo line validity (M5 candles)
input int    OBLength        = 5;       // OB_Detector_v1.07 volume pivot length

#define OBS_SLOTS  3
#define OBS_FIELDS 5
struct OBTrack { int tf; int d; double top; double btm; datetime obT; datetime rt; int st; };   // st 0 = wait retest, 1 = wait CISD
OBTrack  g_ot[];
long     g_otSeen[];
int      g_otN = 0;
ENUM_TIMEFRAMES g_otTF[4]; string g_otName[4]; int g_otH[4]; datetime g_otBar[4];
CTrade   g_otTrade;
datetime g_otM1 = 0, g_otM5 = 0;
bool     g_otReady = false;
double   g_oxBullP = 0, g_oxBearP = 0, g_oxPO = 0, g_oxPC = 0;
int      g_oxBullI = -1, g_oxBearI = -1, g_oxN = 0;
bool     g_oxPrev = false;

void OTLog(const string m) { if(VerboseLog) Print("[V6S-OB] ", m); }

void OTInit()
{
   g_otN = 0;
   if(OBUseH4)  { g_otTF[g_otN] = PERIOD_H4;  g_otName[g_otN] = "H4";  g_otN++; }
   if(OBUseH2)  { g_otTF[g_otN] = PERIOD_H2;  g_otName[g_otN] = "H2";  g_otN++; }
   if(OBUseH1)  { g_otTF[g_otN] = PERIOD_H1;  g_otName[g_otN] = "H1";  g_otN++; }
   if(OBUseM30) { g_otTF[g_otN] = PERIOD_M30; g_otName[g_otN] = "M30"; g_otN++; }
   for(int t = 0; t < g_otN; t++)
   {
      g_otH[t] = iCustom(_Symbol, g_otTF[t], "OB_Detector_v1.07", OBLength, VOLUME_TICK, 0, false);
      g_otBar[t] = 0;
      if(g_otH[t] == INVALID_HANDLE) Print("[V6S-OB] could not load OB_Detector_v1.07 on ", g_otName[t]);
   }
   g_otTrade.SetExpertMagicNumber(OBMagicNumber);
   g_otTrade.SetDeviationInPoints(SlippagePoints);
   g_otTrade.SetTypeFillingBySymbol(_Symbol);
   ArrayResize(g_ot, 0); ArrayResize(g_otSeen, 0);
   g_otM1 = 0; g_otM5 = 0; g_otReady = false;
   g_oxBullI = -1; g_oxBearI = -1; g_oxN = 0; g_oxPrev = false;
}

// LuxAlgo Classic CISD on M5 (same rule as the DZ slot's, own state)
int OXStep(const double o, const double c)
{
   int i = g_oxN++;
   if(g_oxPrev)
   {
      if(c > o && g_oxPC < g_oxPO) { g_oxBullP = o; g_oxBullI = i; }
      if(c < o && g_oxPC > g_oxPO) { g_oxBearP = o; g_oxBearI = i; }
   }
   int lx = 0;
   if(g_oxBullI >= 0) { if(i - g_oxBullI > OBLuxMaxBars) g_oxBullI = -1; else if(c < g_oxBullP) { lx = -1; g_oxBullI = -1; } }
   if(g_oxBearI >= 0) { if(i - g_oxBearI > OBLuxMaxBars) g_oxBearI = -1; else if(c > g_oxBearP) { lx = 1;  g_oxBearI = -1; } }
   g_oxPO = o; g_oxPC = c; g_oxPrev = true;
   return lx;
}

double OTAvgDailyRange()
{
   double s = 0; int n = 0;
   for(int k = 1; k <= OBADRDays; k++)
   {
      double h = iHigh(_Symbol, PERIOD_D1, k), l = iLow(_Symbol, PERIOD_D1, k);
      if(h <= 0 || l <= 0) continue;
      s += h - l; n++;
   }
   return n > 0 ? s / n : 0;
}

bool OTBusy()
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong tk = PositionGetTicket(k);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == OBMagicNumber) return true;
   }
   return false;
}

bool OTSeen(const long id) { for(int k = ArraySize(g_otSeen) - 1; k >= 0; k--) if(g_otSeen[k] == id) return true; return false; }
void OTRemove(const int k) { int n = ArraySize(g_ot); for(int j = k; j < n - 1; j++) g_ot[j] = g_ot[j + 1]; ArrayResize(g_ot, n - 1); }

bool OTHeightOK(const OBTrack &z)
{
   double h = z.top - z.btm, adr = OTAvgDailyRange();
   if(h >= OBMaxHeight || (adr > 0 && h >= OBMaxHeightADR * adr))
   {
      OTLog(StringFormat("%s OB %.2f-%.2f retested -- skipped, height %.1f (max %.1f / %.2f x ADR %.1f)",
                         g_otName[z.tf], z.btm, z.top, h, OBMaxHeight, OBMaxHeightADR, adr));
      return false;
   }
   return true;
}

// new OBs from the detector's slots (slot 0 = newest active zone per side)
void OTPoll(const int t)
{
   if(g_otH[t] == INVALID_HANDLE || BarsCalculated(g_otH[t]) < 10) return;
   for(int side = 0; side < 2; side++)
      for(int s = 0; s < OBS_SLOTS; s++)
      {
         int b = (side * OBS_SLOTS + s) * OBS_FIELDS;
         double v[5]; bool ok = true;
         for(int f = 0; f < 5; f++)
         {
            double x[];
            if(CopyBuffer(g_otH[t], b + f, 0, 1, x) != 1) { ok = false; break; }
            v[f] = x[0];
         }
         if(!ok || v[0] == EMPTY_VALUE) continue;
         long id = (long)v[2] * 100 + t * 10 + side;
         if(OTSeen(id)) continue;
         int ns = ArraySize(g_otSeen); ArrayResize(g_otSeen, ns + 1); g_otSeen[ns] = id;
         if(ns > 3000) ArrayRemove(g_otSeen, 0, 1000);
         OBTrack z; z.tf = t; z.d = (side == 0) ? 1 : -1; z.top = v[0]; z.btm = v[1]; z.obT = (datetime)(long)v[2];
         z.rt = 0; z.st = 0;
         datetime rtest = (datetime)(long)v[4];
         if(rtest > 0)   // already retested before the EA saw it (startup / same minute)
         {
            z.rt = (datetime)((long)rtest / 60 * 60);
            if(TimeCurrent() > z.rt + 60 + OBCISDWindowMin * 60) continue;
            if(!OTHeightOK(z)) continue;
            z.st = 1;
         }
         int n = ArraySize(g_ot); ArrayResize(g_ot, n + 1); g_ot[n] = z;
         OTLog(StringFormat("new %s %s OB %.2f-%.2f (OB candle %s)%s", g_otName[t], z.d > 0 ? "bullish" : "bearish", z.btm, z.top,
                            TimeToString(z.obT), z.st == 1 ? " -- already retested" : ""));
      }
}

// wick mitigation on the OB's own timeframe at each close (as the detector): the lowest low of the last OBLength
// closed candles below the bottom (bullish) / highest high above the top (bearish) -> OB invalid
void OTMitigate(const int t)
{
   double lo = DBL_MAX, hi = -DBL_MAX;
   for(int s = 1; s <= OBLength; s++) { lo = MathMin(lo, iLow(_Symbol, g_otTF[t], s)); hi = MathMax(hi, iHigh(_Symbol, g_otTF[t], s)); }
   for(int k = ArraySize(g_ot) - 1; k >= 0; k--)
   {
      if(g_ot[k].tf != t) continue;
      if((g_ot[k].d > 0 && lo < g_ot[k].btm) || (g_ot[k].d < 0 && hi > g_ot[k].top))
      {
         OTLog(StringFormat("%s %s OB %.2f-%.2f invalidated%s", g_otName[t], g_ot[k].d > 0 ? "bullish" : "bearish", g_ot[k].btm,
                            g_ot[k].top, g_ot[k].st == 1 ? " before its CISD -- no trade" : ""));
         OTRemove(k);
      }
   }
}

void OTEnter(const OBTrack &z, const datetime ce)
{
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   double px = z.d > 0 ? tk.ask : tk.bid;
   double obsl = z.d > 0 ? z.btm - OBSLBuffer : z.top + OBSLBuffer;
   double raw = (px - obsl) * z.d;
   if(raw <= 0.3) { OTLog("CISD but price already beyond the OB stop -- no trade"); return; }
   if(OBMinStop > 0 && raw < OBMinStop) { OTLog(StringFormat("%s OB %.2f-%.2f CISD -- skipped, stop %.1f pts < %.1f", g_otName[z.tf], z.btm, z.top, raw, OBMinStop)); return; }
   double risk = MathMin(raw, OBMaxSL);
   double sl = NormalizeDouble(px - z.d * risk, _Digits), tp = NormalizeDouble(px + z.d * OBRR * risk, _Digits);
   string cm = StringFormat("V6S-ICT-OB-%s", g_otName[z.tf]);
   bool ok = z.d > 0 ? g_otTrade.Buy(OBLots, _Symbol, 0, sl, tp, cm) : g_otTrade.Sell(OBLots, _Symbol, 0, sl, tp, cm);
   OTLog(StringFormat("%s %s OB %.2f-%.2f retested %s, M5 LuxAlgo CISD %s -> %s %.2f lot @ %.2f SL %.2f (%.1f pts%s) TP %.2f %s",
                      g_otName[z.tf], z.d > 0 ? "bullish" : "bearish", z.btm, z.top, TimeToString(z.rt), TimeToString(ce),
                      z.d > 0 ? "BUY" : "SELL", OBLots, px, sl, risk, raw > OBMaxSL ? ", capped" : "", tp,
                      ok ? "" : StringFormat("FAILED %d %s", g_otTrade.ResultRetcode(), g_otTrade.ResultComment())));
}

void OBSlotTick()
{
   if(!UseOBSlot || g_otN == 0) return;
   if(!g_otReady)
   {
      for(int t = 0; t < g_otN; t++) if(g_otH[t] == INVALID_HANDLE || BarsCalculated(g_otH[t]) < 10) return;
      int cnt = MathMin(600, Bars(_Symbol, PERIOD_M5) - 2);
      if(cnt < 200) return;
      for(int s = cnt; s >= 1; s--) OXStep(iOpen(_Symbol, PERIOD_M5, s), iClose(_Symbol, PERIOD_M5, s));
      g_otM5 = iTime(_Symbol, PERIOD_M5, 1);
      for(int t = 0; t < g_otN; t++) g_otBar[t] = iTime(_Symbol, g_otTF[t], 0);
      g_otReady = true;
      OTLog(StringFormat("OB retest slot ready (%d timeframes)", g_otN));
   }
   datetime now = TimeCurrent();
   // 1. new minute: OB-timeframe closes -> invalidations first, then new OBs from the detector
   datetime m1 = iTime(_Symbol, PERIOD_M1, 0);
   if(m1 != g_otM1)
   {
      g_otM1 = m1;
      for(int t = 0; t < g_otN; t++)
      {
         datetime tb = iTime(_Symbol, g_otTF[t], 0);
         if(tb != g_otBar[t]) { g_otBar[t] = tb; OTMitigate(t); }
         OTPoll(t);
      }
   }
   // 2. first retest (live touch)
   MqlTick tk;
   if(SymbolInfoTick(_Symbol, tk))
      for(int k = ArraySize(g_ot) - 1; k >= 0; k--)
      {
         if(g_ot[k].st != 0) continue;
         if((g_ot[k].d > 0 && tk.bid <= g_ot[k].top) || (g_ot[k].d < 0 && tk.bid >= g_ot[k].btm))
         {
            g_ot[k].rt = (datetime)((long)now / 60 * 60);
            if(!OTHeightOK(g_ot[k])) { OTRemove(k); continue; }
            g_ot[k].st = 1;
            OTLog(StringFormat("%s %s OB %.2f-%.2f retested -- waiting for an M5 LuxAlgo CISD (%d min)", g_otName[g_ot[k].tf],
                               g_ot[k].d > 0 ? "bullish" : "bearish", g_ot[k].btm, g_ot[k].top, OBCISDWindowMin));
         }
      }
   // 3. closed M5 candle(s): LuxAlgo CISD -> the first one after the retest decides each OB
   datetime last = iTime(_Symbol, PERIOD_M5, 1);
   if(last > g_otM5)
   {
      int sh = iBarShift(_Symbol, PERIOD_M5, g_otM5, true);
      int from = (sh > 1) ? sh - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         int lx = OXStep(iOpen(_Symbol, PERIOD_M5, s), iClose(_Symbol, PERIOD_M5, s));
         datetime ce = iTime(_Symbol, PERIOD_M5, s) + 300;
         bool fresh = (s == 1) && now - ce <= MaxSignalDelaySec;
         for(int t = 0; t < g_otN; t++)          // OB timeframe order: higher first
            for(int k = 0; k < ArraySize(g_ot); k++)
            {
               if(g_ot[k].tf != t || g_ot[k].st != 1) continue;
               if(ce > g_ot[k].rt + 60 + OBCISDWindowMin * 60)
               {
                  OTLog(StringFormat("%s OB %.2f-%.2f: no CISD within %d min of the retest -- dropped", g_otName[t], g_ot[k].btm, g_ot[k].top, OBCISDWindowMin));
                  OTRemove(k); k--; continue;
               }
               if(lx != g_ot[k].d || ce <= g_ot[k].rt) continue;
               MqlDateTime ist; TimeToStruct(ce + ServerToISTMinutes * 60, ist);
               if(!fresh)                                   OTLog("CISD found during replay -- no trade");
               else if(OBNoFriday && ist.day_of_week == 5)  OTLog(StringFormat("%s OB %.2f-%.2f CISD on Friday (IST) -- no trade", g_otName[t], g_ot[k].btm, g_ot[k].top));
               else if(OTBusy())                            OTLog(StringFormat("%s OB %.2f-%.2f CISD -- skipped, an OB trade is already open", g_otName[t], g_ot[k].btm, g_ot[k].top));
               else                                         OTEnter(g_ot[k], ce);
               OTRemove(k); k--;                            // first CISD only
            }
         g_otM5 = iTime(_Symbol, PERIOD_M5, s);
      }
   }
}
//===================== end OB retest slot =====================

int OnInit()
{
   OTInit();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {}

void OnTick()
{
   OBSlotTick();
}
//+------------------------------------------------------------------+
