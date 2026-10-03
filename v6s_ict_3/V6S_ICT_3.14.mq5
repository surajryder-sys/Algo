//+------------------------------------------------------------------+
//| V6S_ICT_3.14.mq5                                                 |
//| V6S_ICT_3 -- STRIPPED DOWN, new concept: MULTI-TIMEFRAME ORDER   |
//| BLOCK CONFLUENCE (all earlier 3.x logic removed).                |
//| Separate EA -- V6S-ICT v2.x is not touched. Magic 26100401.      |
//|                                                                  |
//| Zones: OB_Detector_v1.07 on H4 H2 H1 M30 M15 M10 M5 (M3 option), |
//| the indicator's 3 newest active bullish / bearish OBs per TF.    |
//|                                                                  |
//| BUY (sell mirrored):                                             |
//|  1. ARM: a closed M1 candle overlaps the zones of >= MinOBs (2)  |
//|     active BULLISH OBs from DIFFERENT timeframes (low <= top and |
//|     high >= bottom). While armed, more touched OBs join the set  |
//|     and the leg's lowest low since the touch is tracked.         |
//|  2. ENTRY: a LATER LuxAlgo (default; AlgoAlpha option) bullish   |
//|     CISD on M3 / M5 / M10 -> market buy.                         |
//|  3. SL: if any touched OB is smaller than SmallOBSize (10) ->     |
//|     the lowest bottom of those small OBs - OBSLBuffer (0.5);     |
//|     otherwise the leg's lowest low since the touch - LegSLBuffer |
//|     (0.5).                                                       |
//|  4. TP = entry + RR x risk (test 1 / 2 / 3).                     |
//|  Setup ends after SetupValidM5 (48) M5 candles or when an M5     |
//|  candle closes below the lowest touched OB bottom. Each OB is    |
//|  used for one trade only. One position at a time. Breakeven /    |
//|  step trailing available but OFF by default (clean RR test).     |
//| Times on screen in IST.                                          |
//+------------------------------------------------------------------+
#property version   "3.14"
#property tester_indicator "OB_Detector_v1.07.ex5"
#include <Trade/Trade.mqh>

enum ENUM_ENTRY_CISD
  {
   ENTRY_AA  = 0, // AlgoAlpha
   ENTRY_LUX = 1  // LuxAlgo (Classic)
  };

input group "Trade"
input bool   EnableTrading      = true;
input double Lots               = 0.01;
input long   MagicNumber        = 26100401;
input double RR                 = 2.0;     // TP = RR x risk (test 1 / 2 / 3)
input int    MaxSignalDelaySec  = 120;

input group "OB confluence"
input int    MinOBs             = 2;       // touched OBs from different timeframes needed
input bool   UseH4 = true, UseH2 = true, UseH1 = true, UseM30 = true, UseM15 = true, UseM10 = true, UseM5 = true;
input bool   UseM3OBs           = false;
input double SmallOBSize        = 10.0;    // OB smaller than this -> SL at its bottom/top edge
input double OBSLBuffer         = 0.5;
input double LegSLBuffer        = 0.5;     // big OBs only -> SL beyond the leg's extreme since the touch
input int    SetupValidM5       = 48;      // M5 candles a touch stays armed

input group "Entry CISD"
input ENUM_ENTRY_CISD EntryCISD = ENTRY_LUX;
input bool   UseM3CISD          = true;
input bool   UseM5CISD          = true;
input bool   UseM10CISD         = true;
input double CISDTolerance      = 0.7;     // AlgoAlpha only
input int    LuxMaxBars         = 100;
input int    WarmupBars         = 3000;

input group "Order blocks (OB_Detector_v1.07)"
input int    OBLength           = 5;
input ENUM_APPLIED_VOLUME OBVolume = VOLUME_TICK;
input int    OBMitigation       = 0;       // 0 = wick, 1 = close
input bool   DrawChartTFZones   = true;

input group "Management (off = clean RR test)"
input double BEPoints           = 0.0;     // >0: SL -> entry at this profit, then step trail
input double TrailEvery         = 10.0;
input double TrailMove          = 5.0;

input group "Display"
input bool   ShowPanel          = true;
input int    PanelFontSize      = 8;
input int    PanelColWidth      = 450;
input int    PanelTop           = 20;
input int    ServerToISTMinutes = 330;

//===================== Small array helpers (verbatim from V6S-ICT v2.21) =====================
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


//===================== timeframes =====================
#define NTF 8
ENUM_TIMEFRAMES g_tf[NTF] = {PERIOD_H4, PERIOD_H2, PERIOD_H1, PERIOD_M30, PERIOD_M15, PERIOD_M10, PERIOD_M5, PERIOD_M3};
string          g_tfn[NTF] = {"H4", "H2", "H1", "M30", "M15", "M10", "M5", "M3"};
class CCisdTF
{
public:
   ENUM_TIMEFRAMES tf;
   double O[], C[];
   double bearOpen[]; int bearIdx[];
   double bullOpen[]; int bullIdx[];
   datetime lastBar;
   bool ready;
   double luxBullP, luxBearP; int luxBullI, luxBearI;
   int aaDir; datetime aaTime; double aaLvl;      // last AlgoAlpha CISD: +1/-1, close time of its candle, level broken
   int lxDir; datetime lxTime; double lxLvl;      // last LuxAlgo CISD

   void Init(const ENUM_TIMEFRAMES t)
   {
      tf = t; lastBar = 0; ready = false;
      luxBullI = -1; luxBearI = -1; luxBullP = 0; luxBearP = 0;
      aaDir = 0; aaTime = 0; aaLvl = 0; lxDir = 0; lxTime = 0; lxLvl = 0;
      ArrayResize(O, 0); ArrayResize(C, 0);
      ArrayResize(bearOpen, 0); ArrayResize(bearIdx, 0); ArrayResize(bullOpen, 0); ArrayResize(bullIdx, 0);
   }
   bool ConfirmBear(const int i, double &lvl)
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
            if(d != 0.0 && (highest - co) / d > CISDTolerance) { lvl = co; ArrayResize(bearOpen, 0); ArrayResize(bearIdx, 0); return true; }
            RemoveFrontD(bearOpen); RemoveFrontI(bearIdx);
         }
         else break;
      }
      return false;
   }
   bool ConfirmBull(const int i, double &lvl)
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
            if(d != 0.0 && (co - lowest) / d > CISDTolerance) { lvl = co; ArrayResize(bullOpen, 0); ArrayResize(bullIdx, 0); return true; }
            RemoveFrontD(bullOpen); RemoveFrontI(bullIdx);
         }
         else break;
      }
      return false;
   }
   void Step(const datetime t, const double o, const double c)
   {
      int i = ArraySize(C);
      ArrayResize(O, i + 1, 50000); ArrayResize(C, i + 1, 50000); O[i] = o; C[i] = c;
      if(i >= 1)
      {
         if(C[i-1] < O[i-1] && c > o) { InsertFrontI(bearIdx, i); InsertFrontD(bearOpen, o); }
         if(C[i-1] > O[i-1] && c < o) { InsertFrontI(bullIdx, i); InsertFrontD(bullOpen, o); }
      }
      datetime ct = t + PeriodSeconds(tf);
      double l1 = 0, l2 = 0;
      int r = 0; double rl = 0;
      if(ConfirmBear(i, l1)) { r = -1; rl = l1; }
      if(ConfirmBull(i, l2)) { r = 1;  rl = l2; }
      if(r != 0) { aaDir = r; aaTime = ct; aaLvl = rl; }
      if(i >= 1)
      {
         if(c > o && C[i-1] < O[i-1]) { luxBullP = o; luxBullI = i; }
         if(c < o && C[i-1] > O[i-1]) { luxBearP = o; luxBearI = i; }
      }
      if(luxBullI >= 0)
      {
         if(i - luxBullI > LuxMaxBars) luxBullI = -1;
         else if(c < luxBullP) { lxDir = -1; lxTime = ct; lxLvl = luxBullP; luxBullI = -1; }
      }
      if(luxBearI >= 0)
      {
         if(i - luxBearI > LuxMaxBars) luxBearI = -1;
         else if(c > luxBearP) { lxDir = 1; lxTime = ct; lxLvl = luxBearP; luxBearI = -1; }
      }
   }
   bool Warmup()
   {
      int avail = Bars(_Symbol, tf) - 1;
      int cnt = MathMin(WarmupBars, avail);
      if(cnt < 50) return false;
      MqlRates r[];
      if(CopyRates(_Symbol, tf, 1, cnt, r) != cnt) return false;
      for(int k = 0; k < cnt; k++) Step(r[k].time, r[k].open, r[k].close);
      lastBar = r[cnt-1].time; ready = true;
      return true;
   }
   bool Update()
   {
      if(!ready) return Warmup();
      datetime lc = iTime(_Symbol, tf, 1);
      if(lc <= lastBar) return false;
      int shift = iBarShift(_Symbol, tf, lastBar, true);
      int from = (shift > 1) ? shift - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         Step(iTime(_Symbol, tf, s), iOpen(_Symbol, tf, s), iClose(_Symbol, tf, s));
         lastBar = iTime(_Symbol, tf, s);
      }
      return true;
   }
};

CCisdTF g_cs[NTF];
#define OB_SLOTS  3
#define OB_FIELDS 5
int g_obH[NTF];
int g_obChart = INVALID_HANDLE;
bool UseOBTF(const int t)
{
   switch(t)
   {
      case 0: return UseH4;  case 1: return UseH2;  case 2: return UseH1;  case 3: return UseM30;
      case 4: return UseM15; case 5: return UseM10; case 6: return UseM5;  case 7: return UseM3OBs;
   }
   return false;
}

string IST(const datetime t)
{
   if(t <= 0) return "-";
   string s = TimeToString((datetime)((long)t + ServerToISTMinutes * 60), TIME_DATE | TIME_MINUTES);   // yyyy.mm.dd hh:mi
   return StringSubstr(s, 8);                                                                          // dd hh:mi
}
string Age(const datetime t)
{
   if(t <= 0) return "";
   long s = (long)TimeCurrent() - (long)t;
   if(s < 0) s = 0;
   if(s < 3600)  return StringFormat("%dm", (int)(s / 60));
   if(s < 86400) return StringFormat("%dh%02dm", (int)(s / 3600), (int)(s % 3600 / 60));
   return StringFormat("%dd%02dh", (int)(s / 86400), (int)(s % 86400 / 3600));
}
string Px(const double v) { return DoubleToString(v, 2); }
bool ChartOn() { return !MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE); }
int HhmmToMin(const string s)
{
   string p[];
   if(StringSplit(s, ':', p) != 2) return -1;
   int h = (int)StringToInteger(p[0]), m = (int)StringToInteger(p[1]);
   if(h < 0 || h > 23 || m < 0 || m > 59) return -1;
   return h * 60 + m;
}
bool InISTWindow(const string from, const string to)
{
   int a = HhmmToMin(from), b = HhmmToMin(to);
   if(a < 0 || b < 0 || a == b) return false;
   int now = (int)(((long)TimeCurrent() + ServerToISTMinutes * 60) % 86400) / 60;
   return a < b ? (now >= a && now < b) : (now >= a || now < b);
}

string g_pTxt[]; color g_pClr[];
// one panel line; MT5 shows at most 63 characters per label, so longer lines wrap (indented) at a space
void P(const string s, const color c)
{
   string rest = s; bool first = true;
   while(true)
   {
      string line = (first ? "" : "     ") + rest;
      int cut = StringLen(line);
      if(cut > 63)
      {
         cut = 63;
         for(int k = 62; k > 20; k--) if(StringGetCharacter(line, k) == ' ') { cut = k; break; }
      }
      PushS(g_pTxt, StringSubstr(line, 0, cut));
      int n = ArraySize(g_pClr); ArrayResize(g_pClr, n + 1); g_pClr[n] = c;
      if(cut >= StringLen(line)) return;
      rest = StringSubstr(line, cut); StringTrimLeft(rest);
      if(rest == "") return;
      first = false;
   }
}

void RenderPanel()
{
   int lineH = PanelFontSize + 5;
   int h = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
   int perCol = MathMax(10, (h - PanelTop - 10) / lineH);
   int n = ArraySize(g_pTxt);
   for(int k = 0; k < n; k++)
   {
      string nm = "V6S3_PNL_" + IntegerToString(k);
      if(ObjectFind(0, nm) < 0)
      {
         ObjectCreate(0, nm, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, nm, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(0, nm, OBJPROP_FONT, "Consolas");
         ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      }
      ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, PanelFontSize);
      ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, 8 + (k / perCol) * PanelColWidth);
      ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, PanelTop + (k % perCol) * lineH);
      ObjectSetString(0, nm, OBJPROP_TEXT, g_pTxt[k] == "" ? " " : g_pTxt[k]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, g_pClr[k]);
   }
   for(int k = n; k < 400; k++) { string nm = "V6S3_PNL_" + IntegerToString(k); if(ObjectFind(0, nm) < 0) break; ObjectDelete(0, nm); }
}


//===================== OB snapshot =====================
struct OBZ { int t; int side; double top, btm; datetime obT; };   // side 0 bull, 1 bear
OBZ g_ob[];
void ReadOBs()
{
   ArrayResize(g_ob, 0);
   for(int t = 0; t < NTF; t++)
   {
      if(!UseOBTF(t) || g_obH[t] == INVALID_HANDLE || BarsCalculated(g_obH[t]) < 10) continue;
      double v[30]; bool ok = true;
      for(int b = 0; b < 30 && ok; b++) { double x[]; if(CopyBuffer(g_obH[t], b, 0, 1, x) != 1) ok = false; else v[b] = x[0]; }
      if(!ok) continue;
      for(int side = 0; side < 2; side++)
         for(int s = 0; s < OB_SLOTS; s++)
         {
            int bb = (side * OB_SLOTS + s) * OB_FIELDS;
            if(v[bb] == EMPTY_VALUE) continue;
            int n = ArraySize(g_ob); ArrayResize(g_ob, n + 1);
            g_ob[n].t = t; g_ob[n].side = side; g_ob[n].top = v[bb]; g_ob[n].btm = v[bb + 1]; g_ob[n].obT = (datetime)(long)v[bb + 2];
         }
   }
}

//===================== setups =====================
class Setup
{
public:
   bool     on;
   datetime touchT;     // open time of the M1 candle that completed the confluence touch
   double   ext;        // leg extreme since the touch (bull: lowest low, bear: highest high)
   OBZ      obs[];      // touched OBs
};
Setup    g_su[2];        // [0] bullish, [1] bearish
datetime g_used[];       // OB ids used by a trade (obT + tf index)
string   g_event = "", g_lastTrade = "";
CTrade   g_trade;

datetime ObId(const OBZ &z) { return z.obT + z.t; }
bool Used(const OBZ &z) { datetime id = ObId(z); for(int k = 0; k < ArraySize(g_used); k++) if(g_used[k] == id) return true; return false; }
string TouchedList(const int side)
{
   string r = "";
   for(int k = 0; k < ArraySize(g_su[side].obs); k++)
      r += StringFormat("%s%s %s-%s", k ? ", " : "", g_tfn[g_su[side].obs[k].t], Px(g_su[side].obs[k].btm), Px(g_su[side].obs[k].top));
   return r;
}

// one closed M1 candle (open bt): confluence touches
void M1Step(const datetime bt, const double h, const double l)
{
   for(int side = 0; side < 2; side++)
   {
      OBZ hit[]; int tfMask = 0;
      for(int k = 0; k < ArraySize(g_ob); k++)
      {
         if(g_ob[k].side != side || Used(g_ob[k])) continue;
         if(l <= g_ob[k].top && h >= g_ob[k].btm)
         { int n = ArraySize(hit); ArrayResize(hit, n + 1); hit[n] = g_ob[k]; tfMask |= (1 << g_ob[k].t); }
      }
      int ntf = 0, m = tfMask; while(m) { ntf += (m & 1); m >>= 1; }
      Setup *S = GetPointer(g_su[side]);
      if(!S.on)
      {
         if(ntf < MinOBs) continue;
         S.on = true; S.touchT = bt; S.ext = side == 0 ? l : h;
         ArrayResize(S.obs, 0);
         for(int k = 0; k < ArraySize(hit); k++) { int n = ArraySize(S.obs); ArrayResize(S.obs, n + 1); S.obs[n] = hit[k]; }
         g_event = StringFormat("%s ARMED %s: %d OBs touched [%s]", IST(bt), side == 0 ? "BULL" : "BEAR", ArraySize(S.obs), TouchedList(side));
         Print("V6S_ICT_3 ", g_event);
      }
      else
      {
         S.ext = side == 0 ? MathMin(S.ext, l) : MathMax(S.ext, h);
         for(int k = 0; k < ArraySize(hit); k++)
         {
            bool have = false;
            for(int q = 0; q < ArraySize(S.obs); q++) if(ObId(S.obs[q]) == ObId(hit[k])) { have = true; break; }
            if(!have) { int n = ArraySize(S.obs); ArrayResize(S.obs, n + 1); S.obs[n] = hit[k]; }
         }
      }
   }
}

// one closed M5 candle: expiry / break of the touched zones
void M5Step(const datetime bt, const double c)
{
   for(int side = 0; side < 2; side++)
   {
      Setup *S = GetPointer(g_su[side]);
      if(!S.on) continue;
      double edge = side == 0 ? 1e18 : -1e18;
      for(int q = 0; q < ArraySize(S.obs); q++) edge = side == 0 ? MathMin(edge, S.obs[q].btm) : MathMax(edge, S.obs[q].top);
      int age = (int)((bt + PeriodSeconds(PERIOD_M5) - S.touchT) / PeriodSeconds(PERIOD_M5));
      string why = "";
      if(age > SetupValidM5) why = "expired";
      else if(side == 0 ? c < edge : c > edge) why = StringFormat("M5 closed %s the zones (%s)", side == 0 ? "below" : "above", Px(edge));
      if(why != "")
      {
         S.on = false;
         g_event = StringFormat("%s %s setup off -- %s", IST(bt + PeriodSeconds(PERIOD_M5)), side == 0 ? "BULL" : "BEAR", why);
      }
   }
}

bool GetPos(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      ticket = t; dir = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      return true;
   }
   return false;
}

// CISD of the entry type on TF index t whose candle (open cOpen) just closed
void TryEntry(const int t, const datetime cOpen)
{
   CCisdTF *c = GetPointer(g_cs[t]);
   datetime ct = cOpen + PeriodSeconds(g_tf[t]);
   int dir = EntryCISD == ENTRY_LUX ? (c.lxTime == ct ? c.lxDir : 0) : (c.aaTime == ct ? c.aaDir : 0);
   if(dir == 0) return;
   int side = dir > 0 ? 0 : 1;
   Setup *S = GetPointer(g_su[side]);
   if(!S.on || S.touchT + 60 > cOpen) return;          // the touch candle must close before the CISD candle opens
   ulong tk; int pd;
   if(GetPos(tk, pd)) { g_event = StringFormat("%s %s CISD %s ignored -- a trade is open", IST(ct), g_tfn[t], dir > 0 ? "bull" : "bear"); return; }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double entry = dir > 0 ? ask : bid;
   // SL: small OB edge, else the leg extreme
   double sl = 0; string slWhy = "";
   for(int q = 0; q < ArraySize(S.obs); q++)
   {
      double sz = S.obs[q].top - S.obs[q].btm;
      if(sz >= SmallOBSize) continue;
      double e = dir > 0 ? S.obs[q].btm - OBSLBuffer : S.obs[q].top + OBSLBuffer;
      if(sl == 0 || (dir > 0 ? e < sl : e > sl)) { sl = e; slWhy = StringFormat("%s OB %s-%s (size %.1f) %s %.1f", g_tfn[S.obs[q].t], Px(S.obs[q].btm), Px(S.obs[q].top), sz, dir > 0 ? "-" : "+", OBSLBuffer); }
   }
   if(sl == 0)
   {
      double ext = S.ext;
      ext = dir > 0 ? MathMin(ext, iLow(_Symbol, g_tf[t], 1)) : MathMax(ext, iHigh(_Symbol, g_tf[t], 1));
      sl = ext - dir * LegSLBuffer; slWhy = StringFormat("leg %s %s since touch %s %.1f (all OBs >= %.0f)", dir > 0 ? "low" : "high", Px(ext), dir > 0 ? "-" : "+", LegSLBuffer, SmallOBSize);
   }
   double risk = (entry - sl) * dir;
   string what = StringFormat("%s %s %s CISD %s | OBs [%s] touched %s | SL %s [%s]", dir > 0 ? "BUY" : "SELL", g_tfn[t],
                              EntryCISD == ENTRY_LUX ? "Lux" : "AA", Px(iClose(_Symbol, g_tf[t], 1)), TouchedList(side), IST(S.touchT), Px(sl), slWhy);
   S.on = false;   // setup used either way
   for(int q = 0; q < ArraySize(S.obs); q++) { int n = ArraySize(g_used); ArrayResize(g_used, n + 1); g_used[n] = ObId(S.obs[q]); }
   if(risk <= 0) { g_event = IST(ct) + " SKIP " + what + " -- price beyond SL"; Print("V6S_ICT_3 ", g_event); return; }
   double tp = entry + dir * RR * risk;
   bool ok = false;
   if(EnableTrading)
   {
      string cm = StringFormat("V6S3 OBC %s %s", dir > 0 ? "B" : "S", g_tfn[t]);
      ok = dir > 0 ? g_trade.Buy(Lots, _Symbol, 0.0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), cm)
                   : g_trade.Sell(Lots, _Symbol, 0.0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), cm);
   }
   g_lastTrade = StringFormat("%s %s TP %s (1:%.1f, risk %.2f)%s", IST(ct), what, Px(tp), RR, risk,
                              EnableTrading ? (ok ? " -- SENT" : " -- FAILED " + g_trade.ResultRetcodeDescription()) : " -- (trading off)");
   g_event = g_lastTrade;
   Print("V6S_ICT_3 ", g_lastTrade);
}

void ManageStops()
{
   if(!EnableTrading || BEPoints <= 0) return;
   ulong tk; int pd;
   if(!GetPos(tk, pd) || !PositionSelectByTicket(tk)) return;
   double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
   double px = pd > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double gain = (px - open) * pd;
   if(gain < BEPoints) return;
   double every = TrailEvery > 0 ? TrailEvery : 10.0;
   double want = NormalizeDouble(open + pd * MathFloor((gain - BEPoints) / every + 1e-9) * TrailMove, _Digits);
   if(sl != 0.0 && (want - sl) * pd < _Point) return;
   if(g_trade.PositionModify(tk, want, tp)) PrintFormat("V6S_ICT_3 SL -> %s (+%.2f)", Px(want), gain);
}

//===================== panel =====================
void DrawPanel()
{
   if(!ShowPanel || !ChartOn()) return;
   ArrayResize(g_pTxt, 0); ArrayResize(g_pClr, 0);
   P(StringFormat("V6S_ICT_3.14 OB CONFLUENCE  %s IST  bid %s", IST(TimeCurrent()), Px(SymbolInfoDouble(_Symbol, SYMBOL_BID))), clrWhite);
   P(StringFormat("min %d TFs | SL small OB<%.0f edge %.1f else leg %.1f | 1:%.1f | %s CISD", MinOBs, SmallOBSize, OBSLBuffer, LegSLBuffer, RR,
                  EntryCISD == ENTRY_LUX ? "Lux" : "AA"), clrSilver);
   ulong tk; int pd;
   if(GetPos(tk, pd) && PositionSelectByTicket(tk))
      P(StringFormat("TRADE %s @ %s SL %s TP %s P/L %.2f", pd > 0 ? "BUY" : "SELL", Px(PositionGetDouble(POSITION_PRICE_OPEN)), Px(PositionGetDouble(POSITION_SL)),
                     Px(PositionGetDouble(POSITION_TP)), PositionGetDouble(POSITION_PROFIT)), pd > 0 ? clrLime : clrOrangeRed);
   else P("TRADE none", clrSilver);
   for(int side = 0; side < 2; side++)
      P(g_su[side].on ? StringFormat("%s ARMED %s (%d OBs) ext %s: %s", side == 0 ? "BULL" : "BEAR", IST(g_su[side].touchT), ArraySize(g_su[side].obs),
                                     Px(g_su[side].ext), TouchedList(side))
                      : (side == 0 ? "BULL setup none" : "BEAR setup none"), g_su[side].on ? clrYellow : clrSilver);
   if(g_event != "") P("EVENT: " + g_event, clrAqua);
   if(g_lastTrade != "" && g_lastTrade != g_event) P("LAST TRADE: " + g_lastTrade, clrDeepSkyBlue);
   P("ACTIVE OBs (btm-top)", clrWhite);
   for(int t = 0; t < NTF; t++)
   {
      if(!UseOBTF(t)) continue;
      string bu = "", be = "";
      for(int k = 0; k < ArraySize(g_ob); k++)
      {
         if(g_ob[k].t != t) continue;
         string z = StringFormat(" %s-%s%s", Px(g_ob[k].btm), Px(g_ob[k].top), Used(g_ob[k]) ? "*" : "");
         if(g_ob[k].side == 0) bu += z; else be += z;
      }
      P(StringFormat("%-3s BU%s", g_tfn[t], bu == "" ? " -" : bu), clrLime);
      P(StringFormat("    BE%s", be == "" ? " -" : be), clrOrangeRed);
   }
   P("* = already used by a trade", clrSilver);
   RenderPanel();
}

//===================== events =====================
datetime g_m1Done = 0, g_m3Done = 0, g_m5Done = 0, g_m10Done = 0, g_lastPanel = 0;

int OnInit()
{
   for(int t = 0; t < NTF; t++)
   {
      g_cs[t].Init(g_tf[t]);
      g_obH[t] = (UseOBTF(t)) ? iCustom(_Symbol, g_tf[t], "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, false) : INVALID_HANDLE;
   }
   if(DrawChartTFZones && ChartOn())
   {
      g_obChart = iCustom(_Symbol, PERIOD_CURRENT, "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, true);
      if(g_obChart != INVALID_HANDLE && !MQLInfoInteger(MQL_TESTER)) ChartIndicatorAdd(0, 0, g_obChart);
   }
   for(int s = 0; s < 2; s++) { g_su[s].on = false; ArrayResize(g_su[s].obs, 0); }
   ArrayResize(g_used, 0); g_event = ""; g_lastTrade = "";
   g_m1Done = 0; g_m3Done = 0; g_m5Done = 0; g_m10Done = 0;
   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   ObjectsDeleteAll(0, "V6S3_");
   PrintFormat("V6S_ICT_3 v3.14 OB confluence on %s | min %d TFs, RR 1:%.1f, %s CISD, lots %.2f, magic %I64d", _Symbol, MinOBs, RR,
               EntryCISD == ENTRY_LUX ? "LuxAlgo" : "AlgoAlpha", Lots, MagicNumber);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   for(int t = 0; t < NTF; t++) if(g_obH[t] != INVALID_HANDLE) IndicatorRelease(g_obH[t]);
   if(g_obChart != INVALID_HANDLE) IndicatorRelease(g_obChart);
   if(!MQLInfoInteger(MQL_TESTER)) ObjectsDeleteAll(0, "V6S3_");
}

bool NewClose(const ENUM_TIMEFRAMES tf, datetime &done, datetime &bt)
{
   bt = iTime(_Symbol, tf, 1);
   if(bt <= done) return false;
   bool first = (done == 0);
   done = bt;
   return !first && TimeCurrent() - (bt + PeriodSeconds(tf)) <= MaxSignalDelaySec;
}

void OnTick()
{
   bool ready = true;
   for(int t = 5; t < NTF; t++) { g_cs[t].Update(); if(!g_cs[t].ready) ready = false; }   // M10, M5, M3 CISD
   if(!ready) return;
   datetime bt;
   bool dirty = false;
   if(NewClose(PERIOD_M1, g_m1Done, bt)) { ReadOBs(); M1Step(bt, iHigh(_Symbol, PERIOD_M1, 1), iLow(_Symbol, PERIOD_M1, 1)); dirty = true; }
   if(NewClose(PERIOD_M5, g_m5Done, bt))  { M5Step(bt, iClose(_Symbol, PERIOD_M5, 1)); if(UseM5CISD) TryEntry(6, bt); dirty = true; }
   if(NewClose(PERIOD_M10, g_m10Done, bt)) { if(UseM10CISD) TryEntry(5, bt); dirty = true; }
   if(NewClose(PERIOD_M3, g_m3Done, bt))  { if(UseM3CISD) TryEntry(7, bt); dirty = true; }
   ManageStops();
   if(dirty || TimeCurrent() - g_lastPanel >= 5) { g_lastPanel = TimeCurrent(); DrawPanel(); }
}
//+------------------------------------------------------------------+
