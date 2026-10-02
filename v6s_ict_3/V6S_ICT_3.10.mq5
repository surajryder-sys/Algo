//+------------------------------------------------------------------+
//| V6S_ICT_3.10.mq5                                                 |
//| V6S_ICT_3 -- v3.01's live data / display + first ENTRY RULES.    |
//| Separate EA -- V6S-ICT v2.x (v2.21 live) is not touched.         |
//|                                                                  |
//| Timeframes: H4 H2 H1 M30 M15 M10 M5 M3 (same 8 as V6S-ICT).      |
//|  * Order blocks: OB_Detector_v1.07 on every timeframe (iCustom,  |
//|    no drawing) -- per TF the newest active bullish / bearish     |
//|    zones: bottom-top, formed time (+age), first retest time or   |
//|    UNTESTED (retest = live first touch, from the indicator).     |
//|    The chart's own timeframe zones are drawn by a separate copy. |
//|  * Major/Minor support & resistance per TF: V6S-ICT v2.21's      |
//|    CMajorMinor verbatim (pivot 5) + the time each level was set. |
//|  * CISD per TF: last AlgoAlpha (tol 0.7) and last LuxAlgo        |
//|    Classic CISD -- direction, candle close time, level broken.   |
//|  * Aligning levels (>= 3 TFs, >= 2 Major, exactly as v2.21) with |
//|    FORMED time = when the member level that completed the rule   |
//|    was set, and RETEST time = first M1 candle (live: first tick) |
//|    reaching the level after it formed; ages shown.               |
//|  * Breakout candidates: M5 close through an aligning level,      |
//|    valid 48 M5 candles, cancelled by a close back (as v2.21).    |
//|  * Dynamic Zones Z1-Z4 of the gap-delimited session + DZ filter  |
//|    state (as v2.21); previous / current session high-low; the    |
//|    classic daily pivot PP R1-3 S1-3 (as v2.21's TP logic).       |
//|  * v2.21 time windows: night reversal block, Monday, London.     |
//| All times on screen in IST (server + ServerToISTMinutes).        |
//| Panel refreshes once per M1 candle (or on a new event), never    |
//| per tick; it flows into extra columns when it is taller than the |
//| chart.                                                           |
//|                                                                  |
//| v3.02 ENTRY RULES (sell mirrored; magic 26100301):               |
//|  * aligning level = same price on >= 2 timeframes, Major or      |
//|    Minor (MinAlignTimeframes 2, MinMajorTimeframes 0); its ZONE  |
//|    = level +/- ZoneBuffer (2.5), drawn on the chart              |
//|  * ARM: a closed M5 candle touches the zone (support: low <=     |
//|    level + 2.5). One trade per touch; a new touch re-arms.       |
//|  * BUY: a LATER candle is a bullish CISD on M5 or M10 (AlgoAlpha |
//|    default, LuxAlgo selectable) closing ABOVE the support level  |
//|    -> market buy, no TP.                                         |
//|  * SL: a new M5 Major support set after the touch -> that Major  |
//|    - SLBuffer (1.0); otherwise the aligning level - SLBuffer.    |
//|  * EXIT: SL, or auto-close when a sell signal fires (the buy is  |
//|    closed and the sell opened). One position at a time.          |
//|  Several armed levels -> the most recently touched one.          |
//|                                                                  |
//| v3.03: aligning = same price on >= 3 timeframes, Major or Minor  |
//|  in any mix (MinAlignTimeframes 3, MinMajorTimeframes 0).        |
//|  SL: a NEW level aligned on >= SLNewAlignTFs (2) timeframes that |
//|  formed after the touch and lies beyond the entry side (buy:     |
//|  below the CISD close) -> SL beyond it +/- SLBuffer (newest such |
//|  level); otherwise beyond the traded aligning level +/- SLBuffer.|
//|  (replaces v3.02's "new M5 Major" SL)                            |
//|                                                                  |
//| v3.04:                                                           |
//|  * TP = nearest aligning RESISTANCE above the entry - TPBuffer   |
//|    (buy) / nearest aligning SUPPORT below the entry + TPBuffer   |
//|    (sell); none -> no TP. Re-checked every closed M5 candle.     |
//|  * ORDER BLOCK trigger: after the zone touch, a bullish OB that  |
//|    forms (OB_Detector_v1.07, candle close) on M5 / M10 (M3       |
//|    optional) while price is back above the support level -> buy  |
//|    (bearish OB below resistance -> sell). The OB and the M5/M10  |
//|    CISD trigger race: whichever comes first takes the touch.     |
//|    Same SL rule for both triggers.                               |
//|                                                                  |
//| v3.05:                                                           |
//|  * M5 and M3 align ONLY with their MAJOR levels (their Minors    |
//|    are ignored); M10 and above with Major or Minor. Applies to   |
//|    the aligning levels and the new-aligned-level SL anchor.      |
//|  * BREAKOUTS: an M5 close UP through ANY aligning level (support |
//|    or resistance) arms a breakout buy; a close DOWN through any  |
//|    level arms a breakout sell (valid BreakoutValidBars M5        |
//|    candles, cancelled by a close back through). Confirmation =   |
//|    a later M5/M10 CISD closing beyond the level, or an M5/M10 OB |
//|    forming while price is beyond it -- whichever first. SL: new  |
//|    aligned level after the break, else the broken level -/+     |
//|    SLBuffer. TP as for reversals. Reversal entries unchanged     |
//|    and take priority when one signal fits both.                  |
//|                                                                  |
//| v3.06: M3 LEVELS IGNORED COMPLETELY (no M3 Major/Minor in any    |
//|  aligning level, SL anchor or chart line). M3 is used only to    |
//|  CONFIRM entries: an M3 CISD (on M3 candle close) and an M3 OB   |
//|  join the M5 / M10 triggers for reversals and breakouts. The M3  |
//|  CISD candle must open after the touch / break candle closed.    |
//|                                                                  |
//| v3.07: entries confirm on the LuxAlgo Classic CISD by default    |
//|  (EntryCISD; AlgoAlpha selectable). TP = the nearest aligning    |
//|  level of EITHER type beyond the entry that is shared by at      |
//|  least TPMinTFs (3) timeframes, -/+ TPBuffer (never a 2-TF       |
//|  level). After a TP, a close back through that level is a new    |
//|  breakout (any level, either way) -> re-entry on an M3/M5/M10    |
//|  CISD or OB, TP the next 3-TF level beyond.                      |
//|                                                                  |
//| v3.08:                                                           |
//|  * BREAKEVEN at +BEPoints (10): SL -> entry; then every          |
//|    TrailStep (1.0) of extra profit moves the SL the same amount  |
//|    (trails BEPoints behind price), checked every tick.           |
//|  * TP FALLBACK when no 3-TF aligning level lies beyond the entry: |
//|    the nearest UNTESTED order block on M15 / M30 beyond it --    |
//|    buy: bearish OB above, TP = its low - OBTPBuffer (1.0);       |
//|    sell: bullish OB below, TP = its top + OBTPBuffer.            |
//|                                                                  |
//| v3.09: the NEW-aligned-level SL is used only when it is within   |
//|  SLNewMaxDist (10) points of the entry; otherwise the SL goes at |
//|  the traded / broken level +/- SLBuffer. Opposite-signal exit    |
//|  unchanged (a confirmed opposite signal closes, then opens).     |
//|                                                                  |
//| v3.10 -- rules PORTED from V6S-ICT v2.21 (verified on clean      |
//| data, see v6s_ict/HANDOVER.md 5a/5b):                            |
//|  * DZ filter: an M5 close above Z2 blocks sells / below Z4       |
//|    blocks buys for the rest of the gap-session (all entries).    |
//|  * No REVERSAL entries 01:30-05:30 IST or on Mondays (IST); no   |
//|    entries at all 12:30-18:30 IST (London). A blocked setup is   |
//|    NOT used up.                                                  |
//|  * SL never further than MaxSLPrice (20) from entry; breakout SL |
//|    = the broken level -/+ BreakoutSLBuffer (1.5).                |
//|  * Breakouts need TP >= MinRRBreakout (1.5) x risk (no TP ahead  |
//|    = allowed); any TP closer than MinTPR (1.0) x the initial     |
//|    risk -> TP 1:1 (at entry and on every M5 re-check).           |
//|  * DZ BREAKOUT SLOT (separate, magic DZMagicNumber): an M15      |
//|    LuxAlgo CISD closing above the upper zone (Z1-Z2) = buy,      |
//|    below the lower zone (Z3-Z4) = sell; SL = far zone line -/+   |
//|    DZSLBuffer (6) capped at DZMaxSL (75) (a capped trade needs   |
//|    TP >= 1R); TP = nearest aligning level -/+ DZTPBuffer (2.0)   |
//|    fixed at entry, 1:1 if none, skipped if < DZMinTPPoints; an   |
//|    AlgoAlpha M15 opposite setup squares it off.                  |
//+------------------------------------------------------------------+
#property version   "3.10"
#include <Trade/Trade.mqh>
#property tester_indicator "OB_Detector_v1.07.ex5"

input group "Levels (as V6S-ICT v2.21)"
input int    PivotPeriod        = 5;       // Major/Minor ZigZag pivot period
input int    MinAlignTimeframes = 3;       // aligning: shared by at least this many timeframes (v3.03: 3)
input int    MinMajorTimeframes = 0;       // ...at least this many of them Major (v3.02: 0 = Major or Minor)
input int    WarmupBars         = 3000;    // closed bars replayed per timeframe at start
input int    BreakoutValidBars  = 48;      // M5 candles a break stays a candidate
input bool   LTFMajorOnly       = true;    // v3.05: M5 levels align only when they are MAJOR
input bool   UseM3Levels        = false;   // v3.06: false = M3 Major/Minor levels ignored completely

enum ENUM_ENTRY_CISD
  {
   ENTRY_AA  = 0, // AlgoAlpha
   ENTRY_LUX = 1  // LuxAlgo (Classic)
  };

input group "Trading (v3.02)"
input bool   EnableTrading      = true;    // false = signals on screen / in the log only
input double Lots               = 0.01;
input long   MagicNumber        = 26100301;
input double ZoneBuffer         = 2.5;     // aligning zone = level +/- this
input double SLBuffer           = 1.0;     // SL beyond the aligning level / the new aligned level
input double SLNewMaxDist       = 10.0;    // v3.09: new-aligned-level SL only if within this many points of entry
input int    SLNewAlignTFs      = 2;       // a NEW level shared by at least this many TFs after the touch anchors the SL
input ENUM_ENTRY_CISD EntryCISD = ENTRY_LUX;  // v3.07: LuxAlgo
input bool   UseM5CISD          = true;
input bool   UseM10CISD         = true;
input bool   UseM3CISD          = true;    // v3.06: M3 CISD confirms entries
input bool   UseOBTrigger       = true;    // v3.04: an OB forming after the touch also triggers the entry
input bool   OBTrigM3           = true;    // v3.06: M3 OB confirms entries
input bool   OBTrigM5           = true;
input bool   OBTrigM10          = true;
input double TPBuffer           = 1.0;     // v3.04: TP = nearest aligning level beyond the entry -/+ this
input int    TPMinTFs           = 3;       // v3.07: a TP level must be shared by at least this many timeframes
input bool   UpdateTPEachM5     = true;    // re-check the TP on every closed M5 candle
input double BEPoints           = 10.0;    // v3.08: SL -> entry at this profit (price units), then trail
input double TrailStep          = 1.0;     // v3.08: after breakeven the SL follows in steps of this
input bool   OBTPFallback       = true;    // v3.08: no 3-TF level -> TP at an untested M15/M30 OB
input double OBTPBuffer         = 1.0;     // v3.08: buy TP = bearish OB low - this; sell TP = bullish OB top + this
input bool   UseBreakoutTrades  = true;    // v3.05: trade confirmed breaks of aligning levels
input bool   CloseOnOpposite    = true;

input group "Ported from V6S-ICT v2.21 (v3.10)"
input bool   UseDZFilter        = true;    // M5 close beyond Z2 / Z4 blocks the opposite direction for the session
input bool   UseRevTimeBlock    = true;    // no reversal entries in RevBlockFrom-RevBlockTo (IST)
input bool   UseMondayRevBlock  = true;    // no reversal entries on Monday (IST)
input bool   UseLondonBlock     = true;    // no entries in LondonBlockFrom-LondonBlockTo (IST)
input double MaxSLPrice         = 20.0;    // SL never further than this from entry (0 = no cap)
input bool   BreakoutSLAtLevel  = true;    // breakout SL = broken level -/+ BreakoutSLBuffer
input double BreakoutSLBuffer   = 1.5;
input double MinRRBreakout      = 1.5;     // breakout needs TP >= this x risk (0 = off; no TP ahead = allowed)
input double MinTPR             = 1.0;     // TP closer than this x initial risk -> 1:1 (0 = off)

input group "DZ breakout slot (ported from V6S-ICT v2.21)"
input bool   UseDZBreakout      = true;
input double DZLots             = 0.01;
input long   DZMagicNumber      = 26100321;
input double DZSLBuffer         = 6.0;
input double DZMaxSL            = 75.0;
input double DZCappedMinR       = 1.0;
input bool   DZSquareOff        = true;    // an AlgoAlpha M15 opposite setup closes the DZ trade
input double DZTPBuffer         = 2.0;
input double DZMinTPPoints      = 1.0;    // an opposite signal closes the open trade (then opens its own)
input int    MaxSignalDelaySec  = 120;     // only act on a candle that closed within this many seconds

input group "CISD"
input double CISDTolerance      = 0.7;     // AlgoAlpha "Noise Filter"
input int    LuxMaxBars         = 100;     // LuxAlgo "Maximum Swing Validity"

input group "Order blocks (OB_Detector_v1.07)"
input int    OBLength           = 5;
input ENUM_APPLIED_VOLUME OBVolume = VOLUME_TICK;
input int    OBMitigation       = 0;       // 0 = wick, 1 = close
input int    OBSlotsShown       = 3;       // zones shown per side per timeframe (1-3)
input bool   DrawChartTFZones   = true;    // draw the chart timeframe's OB zones

input group "Dynamic Zones"
input int    DZShortLen         = 5;
input int    DZLongLen          = 10;

input group "Time windows (IST, as v2.21)"
input string RevBlockFrom       = "01:30";
input string RevBlockTo         = "05:30";
input string LondonBlockFrom    = "12:30";
input string LondonBlockTo      = "18:30";
input int    ServerToISTMinutes = 330;     // Exness server = UTC

input group "Display"
input bool   ShowPanel          = true;
input bool   ShowAllTFLevels    = true;    // thin Major/Minor lines of every timeframe
input int    PanelFontSize      = 8;
input int    PanelColWidth      = 450;     // pixels per panel column
input int    PanelTop           = 20;
input int    StartReplayM5      = 300;     // M5 candles replayed at start for the DZ filter / breakouts

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
   datetime sT[4];          // V6S_ICT_3: close time of the bar that set MajSup / MinSup / MajRes / MinRes
   int      pX[4];
   double   pY[4];

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
      for(int j = 0; j < 4; j++) { sT[j] = 0; pX[j] = -1; pY[j] = 0; }
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
      int    xs[4]; xs[0] = w_MajSupX; xs[1] = w_MinSupX; xs[2] = w_MajResX; xs[3] = w_MinResX;
      double ys[4]; ys[0] = w_MajSupY; ys[1] = w_MinSupY; ys[2] = w_MajResY; ys[3] = w_MinResY;
      for(int j = 0; j < 4; j++)
         if(xs[j] != pX[j] || ys[j] != pY[j]) { pX[j] = xs[j]; pY[j] = ys[j]; if(xs[j] >= 0) sT[j] = t + PeriodSeconds(tf); }
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
#define NTF 8
ENUM_TIMEFRAMES g_tf[NTF] = {PERIOD_H4, PERIOD_H2, PERIOD_H1, PERIOD_M30, PERIOD_M15, PERIOD_M10, PERIOD_M5, PERIOD_M3};
string          g_tfn[NTF] = {"H4", "H2", "H1", "M30", "M15", "M10", "M5", "M3"};
CMajorMinor     g_mm[NTF];

//===================== CISD per timeframe (AlgoAlpha + LuxAlgo, same ports as V6S-ICT v2.21) =====================
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

//===================== Order blocks (OB_Detector_v1.07 on every timeframe) =====================
#define OB_SLOTS  3
#define OB_FIELDS 5
int g_obH[NTF];
int g_obChart = INVALID_HANDLE;   // chart-timeframe copy that draws the zones

//===================== helpers =====================
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

//===================== Aligning levels with formed / retest times =====================
struct AlignLvl
{
   int      side;       // +1 support, -1 resistance
   double   v;
   string   desc;       // member timeframes
   datetime formed;     // when it became aligning (the member level that completed the rule was set)
   datetime retest;     // first touch after it formed (0 = untested)
   datetime armT;       // v3.02: open time of the M5 candle that last touched the zone (0 = not armed / used)
   int      ntf;        // v3.07: number of timeframes sharing it
};
AlignLvl g_al[];
string   g_lastEvent = "";

// first M1 candle at/after 'from' that reaches v (support: low <= v, resistance: high >= v); 0 = none yet
datetime FirstTouch(const int side, const double v, const datetime from)
{
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_M1, from, TimeCurrent(), r);
   for(int k = 0; k < got; k++)
      if(side > 0 ? r[k].low <= v : r[k].high >= v) return MathMax(r[k].time, from);
   return 0;
}

void RebuildAlignments()
{
   // collect every timeframe's current Major/Minor support & resistance
   double vals[]; int sides[]; int tfMask[]; int majMask[]; string desc[];
   int memLvl[]; int memTf[]; int memMaj[]; datetime memT[];
   for(int t = 0; t < NTF; t++)
   {
      if(!g_mm[t].ready) continue;
      for(int j = 0; j < 4; j++)   // 0 MajSup, 1 MinSup, 2 MajRes, 3 MinRes
      {
         if(t == 7 && !UseM3Levels) continue;                                // v3.06: M3 levels ignored
         if(LTFMajorOnly && (t == 6 || t == 7) && (j % 2 == 1)) continue;   // v3.05: M5 / M3 Minor levels don't align
         int x = (j == 0) ? g_mm[t].w_MajSupX : (j == 1) ? g_mm[t].w_MinSupX : (j == 2) ? g_mm[t].w_MajResX : g_mm[t].w_MinResX;
         double y = (j == 0) ? g_mm[t].w_MajSupY : (j == 1) ? g_mm[t].w_MinSupY : (j == 2) ? g_mm[t].w_MajResY : g_mm[t].w_MinResY;
         if(x < 0) continue;
         y = NormalizeDouble(y, _Digits);
         int side = j < 2 ? 1 : -1; bool maj = (j % 2 == 0);
         int f = -1;
         for(int k = 0; k < ArraySize(vals); k++) if(vals[k] == y && sides[k] == side) { f = k; break; }
         string lab = g_tfn[t] + (maj ? " Maj" : " Min");
         if(f < 0)
         {
            f = ArraySize(vals);
            PushD(vals, y); PushI(sides, side); PushI(tfMask, 0); PushI(majMask, 0); PushS(desc, lab);
         }
         else desc[f] += ", " + lab;
         tfMask[f] |= (1 << t); if(maj) majMask[f] |= (1 << t);
         PushI(memLvl, f); PushI(memTf, t); PushI(memMaj, maj ? 1 : 0);
         ArrayResize(memT, ArraySize(memT) + 1); memT[ArraySize(memT) - 1] = g_mm[t].sT[j];
      }
   }

   AlignLvl na[];
   for(int k = 0; k < ArraySize(vals); k++)
   {
      int bits = 0, m = tfMask[k]; while(m) { bits += (m & 1); m >>= 1; }
      int mb = 0, mj = majMask[k]; while(mj) { mb += (mj & 1); mj >>= 1; }
      if(bits < MinAlignTimeframes || mb < MinMajorTimeframes) continue;
      // formed = time the rule was first met, walking this level's members in the order their levels were set
      int idx[];
      for(int q = 0; q < ArraySize(memLvl); q++) if(memLvl[q] == k) PushI(idx, q);
      for(int a = 0; a < ArraySize(idx); a++) for(int b = a + 1; b < ArraySize(idx); b++)
         if(memT[idx[b]] < memT[idx[a]]) { int tmp = idx[a]; idx[a] = idx[b]; idx[b] = tmp; }
      int tm = 0, jm = 0; datetime formed = 0;
      for(int a = 0; a < ArraySize(idx); a++)
      {
         tm |= (1 << memTf[idx[a]]); if(memMaj[idx[a]]) jm |= (1 << memTf[idx[a]]);
         int c1 = 0, c2 = 0, x1 = tm, x2 = jm; while(x1) { c1 += (x1 & 1); x1 >>= 1; } while(x2) { c2 += (x2 & 1); x2 >>= 1; }
         if(c1 >= MinAlignTimeframes && c2 >= MinMajorTimeframes) { formed = memT[idx[a]]; break; }
      }
      AlignLvl L; L.side = sides[k]; L.v = vals[k]; L.desc = desc[k]; L.formed = formed; L.retest = 0; L.armT = 0; L.ntf = bits;
      bool known = false;
      for(int o = 0; o < ArraySize(g_al); o++)
         if(g_al[o].side == L.side && g_al[o].v == L.v && g_al[o].formed == L.formed) { L.retest = g_al[o].retest; L.armT = g_al[o].armT; known = true; break; }
      if(!known)
      {
         if(formed > 0) L.retest = FirstTouch(L.side, L.v, formed);
         g_lastEvent = StringFormat("%s new aligning %s %s [%s]", IST(TimeCurrent()), L.side > 0 ? "S" : "R", Px(L.v), L.desc);
      }
      int n = ArraySize(na); ArrayResize(na, n + 1); na[n] = L;
   }
   ArrayResize(g_al, ArraySize(na));
   for(int k = 0; k < ArraySize(na); k++) g_al[k] = na[k];
   DrawLevels();
}

// live: first touch of an untested aligning level
bool LiveRetests()
{
   bool any = false;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      if(g_al[k].retest != 0 || g_al[k].formed <= 0 || TimeCurrent() < g_al[k].formed) continue;
      if(g_al[k].side > 0 ? bid <= g_al[k].v : bid >= g_al[k].v)
      {
         g_al[k].retest = TimeCurrent(); any = true;
         g_lastEvent = StringFormat("%s RETEST aligning %s %s", IST(TimeCurrent()), g_al[k].side > 0 ? "S" : "R", Px(g_al[k].v));
      }
   }
   return any;
}

//===================== Breakout candidates (as v2.21: M5 close through an aligning level) =====================
int      g_boSide[];
double   g_boVal[];
datetime g_boTime[];   // close time of the breaking M5 candle

void BreakoutStep(const datetime bt, const double c, const double pc)
{
   datetime ct = bt + PeriodSeconds(PERIOD_M5);
   for(int k = ArraySize(g_boVal) - 1; k >= 0; k--)
   {
      int age = iBarShift(_Symbol, PERIOD_M5, g_boTime[k] - 1, false) - iBarShift(_Symbol, PERIOD_M5, bt, false);
      if(age > BreakoutValidBars || (g_boSide[k] > 0 ? c < g_boVal[k] : c > g_boVal[k]))
      {
         int n = ArraySize(g_boVal);
         for(int q = k; q < n - 1; q++) { g_boSide[q] = g_boSide[q+1]; g_boVal[q] = g_boVal[q+1]; g_boTime[q] = g_boTime[q+1]; }
         ArrayResize(g_boSide, n - 1); ArrayResize(g_boVal, n - 1); ArrayResize(g_boTime, n - 1);
      }
   }
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      double v = g_al[k].v;
      int bs = (c > v && pc <= v) ? 1 : (c < v && pc >= v) ? -1 : 0;   // v3.05: ANY aligning level, either way
      if(bs == 0) continue;
      bool dup = false;
      for(int q = 0; q < ArraySize(g_boVal); q++) if(g_boSide[q] == bs && g_boVal[q] == v) { dup = true; break; }
      if(dup) continue;
      PushI(g_boSide, bs); PushD(g_boVal, v);
      ArrayResize(g_boTime, ArraySize(g_boTime) + 1); g_boTime[ArraySize(g_boTime) - 1] = ct;
      g_lastEvent = StringFormat("%s %s of aligning %s", IST(ct), bs > 0 ? "BREAKOUT" : "BREAKDOWN", Px(v));
   }
}

//===================== Dynamic Zones (v2.21 DZSession, gap-delimited sessions) =====================
#define DZ_BAR_SECONDS 3600
datetime g_dzsKey = 0;
double   g_dzsZ[4] = {0, 0, 0, 0};
bool DZSession(const datetime t, double &z1, double &z2, double &z3, double &z4, datetime &sess)
{
   int maxLen = MathMax(DZShortLen, DZLongLen);
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_H1, t, (maxLen + 5) * 24, r);
   if(got < 2) return false;
   int st[]; ArrayResize(st, got);
   int n = 0; st[n++] = 0;
   for(int i = 1; i < got; i++)
      if((long)r[i].time - (long)r[i-1].time > DZ_BAR_SECONDS) st[n++] = i;
   int si = n - 1;
   if(si < maxLen) return false;
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

datetime g_dzfSess = 0;
bool     g_dzfBlockBuy = false, g_dzfBlockSell = false;
datetime g_dzfBuyT = 0, g_dzfSellT = 0;
void DZFilterStep(const datetime bt, const double c)
{
   double z1, z2, z3, z4; datetime s;
   if(!DZSession(bt, z1, z2, z3, z4, s)) return;
   if(s != g_dzfSess) { g_dzfSess = s; g_dzfBlockBuy = false; g_dzfBlockSell = false; g_dzfBuyT = 0; g_dzfSellT = 0; }
   datetime ct = bt + PeriodSeconds(PERIOD_M5);
   if(c > z2 && !g_dzfBlockSell) { g_dzfBlockSell = true; g_dzfSellT = ct; g_lastEvent = StringFormat("%s DZ: M5 closed above Z2 -> sells blocked", IST(ct)); }
   if(c < z4 && !g_dzfBlockBuy)  { g_dzfBlockBuy = true;  g_dzfBuyT = ct;  g_lastEvent = StringFormat("%s DZ: M5 closed below Z4 -> buys blocked", IST(ct)); }
}

//===================== Sessions + classic daily pivot (as v2.21) =====================
bool SessionInfo(double &ph, double &pl, double &pc, datetime &curStart, double &ch, double &cl)
{
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_H1, 0, 96, r);
   if(got < 2) return false;
   int st[]; ArrayResize(st, got);
   int n = 0; st[n++] = 0;
   for(int i = 1; i < got; i++)
      if((long)r[i].time - (long)r[i-1].time > DZ_BAR_SECONDS) st[n++] = i;
   if(n < 2) return false;
   int cs = st[n - 1], ps = st[n - 2];
   ph = r[ps].high; pl = r[ps].low; pc = r[cs - 1].close;
   for(int j = ps + 1; j < cs; j++) { if(r[j].high > ph) ph = r[j].high; if(r[j].low < pl) pl = r[j].low; }
   curStart = r[cs].time;
   ch = 0; cl = 0;
   int shs = iBarShift(_Symbol, PERIOD_M5, curStart, false);
   if(shs >= 1)
   {
      int kh = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, shs, 1), kl = iLowest(_Symbol, PERIOD_M5, MODE_LOW, shs, 1);
      if(kh >= 1) ch = iHigh(_Symbol, PERIOD_M5, kh);
      if(kl >= 1) cl = iLow(_Symbol, PERIOD_M5, kl);
   }
   return true;
}

//===================== Chart drawing =====================
void SetText(const string nm, const datetime t, const double y, const string txt, const color clr, const int size, const ENUM_ANCHOR_POINT anchor)
{
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_TEXT, 0, t, y);
   else ObjectMove(0, nm, 0, t, y);
   ObjectSetString(0, nm, OBJPROP_TEXT, txt);
   ObjectSetString(0, nm, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
}

void DrawLevels()
{
   if(!ChartOn()) return;
   ObjectsDeleteAll(0, "V6S3_LV_");
   if(ShowAllTFLevels)
      for(int t = 0; t < NTF; t++)
      {
         if(!g_mm[t].ready || (t == 7 && !UseM3Levels)) continue;
         for(int j = 0; j < 4; j++)
         {
            int x = (j == 0) ? g_mm[t].w_MajSupX : (j == 1) ? g_mm[t].w_MinSupX : (j == 2) ? g_mm[t].w_MajResX : g_mm[t].w_MinResX;
            double y = (j == 0) ? g_mm[t].w_MajSupY : (j == 1) ? g_mm[t].w_MinSupY : (j == 2) ? g_mm[t].w_MajResY : g_mm[t].w_MinResY;
            if(x < 0 || x >= g_mm[t].n) continue;
            string tag = (j == 0) ? "Maj S" : (j == 1) ? "Min S" : (j == 2) ? "Maj R" : "Min R";
            string nm = "V6S3_LV_TF_" + g_tfn[t] + "_" + IntegerToString(j);
            color clr = (j % 2 == 0) ? clrYellow : clrGold;
            ObjectCreate(0, nm, OBJ_TREND, 0, g_mm[t].tm[x], y, g_mm[t].tm[x] + PeriodSeconds(PERIOD_M5), y);
            ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, true);
            ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
            ObjectSetInteger(0, nm, OBJPROP_WIDTH, (j % 2 == 0) ? 2 : 1);
            ObjectSetInteger(0, nm, OBJPROP_STYLE, (j % 2 == 0) ? STYLE_SOLID : STYLE_DOT);
            ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, nm, OBJPROP_BACK, true);
            ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
            ObjectSetString(0, nm, OBJPROP_TOOLTIP, StringFormat("%s %s %.3f (set %s IST)", g_tfn[t], tag, y, IST(g_mm[t].sT[j])));
            SetText(nm + "_T", g_mm[t].tm[x], y, g_tfn[t] + " " + tag, clr, 7, j < 2 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
         }
      }
   datetime now = iTime(_Symbol, PERIOD_M5, 0);
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      bool s = g_al[k].side > 0; color clr = s ? clrLime : clrOrangeRed;
      string nm = "V6S3_LV_AL_" + (s ? "S_" : "R_") + DoubleToString(g_al[k].v, _Digits);
      ObjectCreate(0, nm, OBJ_HLINE, 0, 0, g_al[k].v);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetString(0, nm, OBJPROP_TOOLTIP, StringFormat("ALIGNING %s %s [%s] formed %s IST, %s", s ? "SUPPORT" : "RESISTANCE",
                      Px(g_al[k].v), g_al[k].desc, IST(g_al[k].formed), g_al[k].retest ? "retested " + IST(g_al[k].retest) + " IST" : "untested"));
      string zn = "V6S3_LV_ZN_" + (s ? "S_" : "R_") + DoubleToString(g_al[k].v, _Digits);
      ObjectCreate(0, zn, OBJ_RECTANGLE, 0, g_al[k].formed > 0 ? g_al[k].formed : now, g_al[k].v + ZoneBuffer,
                   now + 200 * PeriodSeconds(PERIOD_M5), g_al[k].v - ZoneBuffer);
      ObjectSetInteger(0, zn, OBJPROP_COLOR, s ? C'0,45,0' : C'60,0,0');
      ObjectSetInteger(0, zn, OBJPROP_FILL, true);
      ObjectSetInteger(0, zn, OBJPROP_BACK, true);
      ObjectSetInteger(0, zn, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, zn, OBJPROP_HIDDEN, true);
      SetText(nm + "_T", now, g_al[k].v, StringFormat("ALIGN %s %s  f %s  %s", s ? "S" : "R", Px(g_al[k].v), IST(g_al[k].formed),
              g_al[k].retest ? "T " + IST(g_al[k].retest) : "untested"), clr, 8, s ? ANCHOR_RIGHT_UPPER : ANCHOR_RIGHT_LOWER);
   }
}

datetime g_dzDrawn = 0;
void DrawDZ()
{
   if(!ChartOn()) return;
   double z1, z2, z3, z4; datetime s;
   if(!DZSession(TimeCurrent(), z1, z2, z3, z4, s) || s == g_dzDrawn) return;
   g_dzDrawn = s;
   double zs[4]; zs[0] = z2; zs[1] = z1; zs[2] = z3; zs[3] = z4;
   string zn[4] = {"Z2", "Z1", "Z3", "Z4"};
   for(int k = 0; k < 4; k++)
   {
      string nm = "V6S3_DZ_" + IntegerToString((long)s) + "_" + zn[k];
      ObjectCreate(0, nm, OBJ_TREND, 0, s, zs[k], s + 86400, zs[k]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, nm, OBJPROP_STYLE, STYLE_DASHDOT);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetString(0, nm, OBJPROP_TOOLTIP, "Dynamic Zone " + zn[k] + " (session from " + IST(s) + " IST)");
      SetText(nm + "_T", s, zs[k], "DZ " + zn[k], clrDodgerBlue, 7, ANCHOR_LEFT_LOWER);
   }
}

//===================== On-screen panel (columns, refreshed once per M1 candle / on events) =====================
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

void DrawPanel()
{
   if(!ShowPanel || !ChartOn()) return;
   ArrayResize(g_pTxt, 0); ArrayResize(g_pClr, 0);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   P(StringFormat("V6S_ICT_3.10  %s IST  bid %s", IST(TimeCurrent()), Px(bid)), clrWhite);
   P("times IST (dd hh:mm), age in ( )", clrSilver);
   ulong ptk; int pdir;
   if(GetPos(ptk, pdir) && PositionSelectByTicket(ptk))
      P(StringFormat("TRADE %s %.2f @ %s SL %s TP %s P/L %.2f", pdir > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME),
                     Px(PositionGetDouble(POSITION_PRICE_OPEN)), Px(PositionGetDouble(POSITION_SL)),
                     PositionGetDouble(POSITION_TP) > 0 ? Px(PositionGetDouble(POSITION_TP)) : "none", PositionGetDouble(POSITION_PROFIT)),
        pdir > 0 ? clrLime : clrOrangeRed);
   else P("TRADE none", clrSilver);
   string arm = "";
   for(int k = 0; k < ArraySize(g_al); k++)
      if(g_al[k].armT > 0) arm += StringFormat(" %s %s@%s", g_al[k].side > 0 ? "S" : "R", Px(g_al[k].v), IST(g_al[k].armT));
   P("ARMED (zone touched, waiting OB / CISD):" + (arm == "" ? " none" : arm), arm == "" ? clrSilver : clrYellow);
   if(g_tradeEvent != "") P("SIGNAL: " + g_tradeEvent, clrAqua);

   // ---- order blocks
   P("ORDER BLOCKS  zone  formed  retest", clrWhite);
   for(int t = 0; t < NTF; t++)
   {
      if(g_obH[t] == INVALID_HANDLE || BarsCalculated(g_obH[t]) < 10) { P(g_tfn[t] + "  loading...", clrSilver); continue; }
      double v[30];
      bool ok = true;
      for(int b = 0; b < 30 && ok; b++) { double x[]; if(CopyBuffer(g_obH[t], b, 0, 1, x) != 1) ok = false; else v[b] = x[0]; }
      if(!ok) { P(g_tfn[t] + "  loading...", clrSilver); continue; }
      int shown = 0;
      for(int side = 0; side < 2; side++)
         for(int s = 0; s < OBSlotsShown && s < OB_SLOTS; s++)
         {
            int bb = (side * OB_SLOTS + s) * OB_FIELDS;
            if(v[bb] == EMPTY_VALUE) continue;
            datetime fm = (datetime)(long)v[bb + 3], rt = (datetime)(long)v[bb + 4];
            P(StringFormat("%-3s %s %s-%s f%s(%s) %s", shown == 0 ? g_tfn[t] : "", side == 0 ? "BU" : "BE", Px(v[bb + 1]), Px(v[bb]),
                           IST(fm), Age(fm), rt ? "T" + IST(rt) : "UNTESTED"),
              side == 0 ? (rt ? clrSeaGreen : clrLime) : (rt ? clrIndianRed : clrOrangeRed));
            shown++;
         }
      if(shown == 0) P(g_tfn[t] + "  no active OB", clrSilver);
   }

   // ---- Major/Minor + CISD per timeframe
   P("MAJOR/MINOR (set time)  +  CISD (candle close)", clrWhite);
   for(int t = 0; t < NTF; t++)
   {
      if(!g_mm[t].ready) { P(g_tfn[t] + "  loading...", clrSilver); continue; }
      if(t == 7 && !UseM3Levels) P("M3  levels not used (CISD / OB confirmation only)", clrSilver);
      else {
      P(StringFormat("%-3s S Maj %s %s | Min %s %s", g_tfn[t], Px(g_mm[t].w_MajSupY), IST(g_mm[t].sT[0]), Px(g_mm[t].w_MinSupY), IST(g_mm[t].sT[1])), clrYellowGreen);
      P(StringFormat("    R Maj %s %s | Min %s %s", Px(g_mm[t].w_MajResY), IST(g_mm[t].sT[2]), Px(g_mm[t].w_MinResY), IST(g_mm[t].sT[3])), clrGold);
      }
      CCisdTF *c = GetPointer(g_cs[t]);
      P(StringFormat("    AA %s %s @%s | Lux %s %s @%s", c.aaDir > 0 ? "UP" : c.aaDir < 0 ? "DN" : "--", IST(c.aaTime), Px(c.aaLvl),
                     c.lxDir > 0 ? "UP" : c.lxDir < 0 ? "DN" : "--", IST(c.lxTime), Px(c.lxLvl)), clrDeepSkyBlue);
   }

   // ---- aligning levels
   int ri[], si[];
   for(int k = 0; k < ArraySize(g_al); k++) { if(g_al[k].side < 0) PushI(ri, k); else PushI(si, k); }
   for(int a = 0; a < ArraySize(ri); a++) for(int b = a + 1; b < ArraySize(ri); b++) if(g_al[ri[b]].v > g_al[ri[a]].v) { int x = ri[a]; ri[a] = ri[b]; ri[b] = x; }
   for(int a = 0; a < ArraySize(si); a++) for(int b = a + 1; b < ArraySize(si); b++) if(g_al[si[b]].v > g_al[si[a]].v) { int x = si[a]; si[a] = si[b]; si[b] = x; }
   P(StringFormat("ALIGNING (%d+ TFs, %d+ Major)  R %d  S %d", MinAlignTimeframes, MinMajorTimeframes, ArraySize(ri), ArraySize(si)), clrWhite);
   for(int pass = 0; pass < 2; pass++)
   {
      int cnt = pass == 0 ? ArraySize(ri) : ArraySize(si);
      for(int a = 0; a < cnt; a++)
      {
         int k = pass == 0 ? ri[a] : si[a];
         AlignLvl L = g_al[k];
         color cl = pass == 0 ? (L.retest ? clrIndianRed : clrOrangeRed) : (L.retest ? clrSeaGreen : clrLime);
         P(StringFormat("%s %s (%+.2f) f%s(%s) %s%s", pass == 0 ? "R" : "S", Px(L.v), L.v - bid, IST(L.formed), Age(L.formed),
                        L.retest ? "T" + IST(L.retest) + "(" + Age(L.retest) + ")" : "UNTESTED", L.armT ? " ARMED" : ""), cl);
         P("   [" + L.desc + "]", clrSilver);
      }
   }
   string bo = "";
   for(int k = 0; k < ArraySize(g_boVal); k++) bo += StringFormat(" %s %s@%s", g_boSide[k] > 0 ? "UP" : "DN", Px(g_boVal[k]), IST(g_boTime[k]));
   P("BREAKOUTS waiting CISD/OB (<= " + IntegerToString(BreakoutValidBars) + " M5):" + (bo == "" ? " none" : bo), bo == "" ? clrSilver : clrDeepSkyBlue);

   // ---- dynamic zones, sessions, pivots, windows
   double z1, z2, z3, z4; datetime ds;
   if(DZSession(TimeCurrent(), z1, z2, z3, z4, ds))
   {
      P(StringFormat("DZ session %s  Z2 %s  Z1 %s", IST(ds), Px(z2), Px(z1)), clrDodgerBlue);
      P(StringFormat("            Z3 %s  Z4 %s", Px(z3), Px(z4)), clrDodgerBlue);
      ulong dzt; int dzd;
      if(UseDZBreakout && DZGetPosition(dzt, dzd) && PositionSelectByTicket(dzt))
         P(StringFormat("DZ SLOT %s %.2f @ %s SL %s TP %s P/L %.2f", dzd > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME),
                        Px(PositionGetDouble(POSITION_PRICE_OPEN)), Px(PositionGetDouble(POSITION_SL)), Px(PositionGetDouble(POSITION_TP)),
                        PositionGetDouble(POSITION_PROFIT)), dzd > 0 ? clrLime : clrOrangeRed);
      else if(UseDZBreakout) P("DZ SLOT none", clrSilver);
      if(g_dzbLast != "") P("DZ LAST: " + g_dzbLast, clrDeepSkyBlue);
      P(StringFormat("DZ filter buys %s  sells %s", g_dzfBlockBuy ? "BLOCKED " + IST(g_dzfBuyT) : "ok", g_dzfBlockSell ? "BLOCKED " + IST(g_dzfSellT) : "ok"),
        (g_dzfBlockBuy || g_dzfBlockSell) ? clrRed : clrSilver);
   }
   double ph, pl, pc, ch, cl; datetime cs;
   if(SessionInfo(ph, pl, pc, cs, ch, cl))
   {
      P(StringFormat("PREV SESSION H %s  L %s  C %s", Px(ph), Px(pl), Px(pc)), clrPlum);
      P(StringFormat("THIS SESSION (from %s) H %s  L %s", IST(cs), Px(ch), Px(cl)), clrPlum);
      double pp = (ph + pl + pc) / 3.0;
      P(StringFormat("PIVOT PP %s R1 %s S1 %s", Px(pp), Px(2 * pp - pl), Px(2 * pp - ph)), clrKhaki);
      P(StringFormat("  R2 %s S2 %s R3 %s S3 %s", Px(pp + (ph - pl)), Px(pp - (ph - pl)), Px(ph + 2 * (pp - pl)), Px(pl - 2 * (ph - pp))), clrKhaki);
   }
   MqlDateTime it; TimeToStruct((datetime)((long)TimeCurrent() + ServerToISTMinutes * 60), it);
   P(StringFormat("WINDOWS night %s-%s %s | Mon %s | London %s-%s %s", RevBlockFrom, RevBlockTo, InISTWindow(RevBlockFrom, RevBlockTo) ? "ON" : "off",
                  it.day_of_week == 1 ? "ON" : "off", LondonBlockFrom, LondonBlockTo, InISTWindow(LondonBlockFrom, LondonBlockTo) ? "ON" : "off"), clrSilver);
   if(g_lastEvent != "") P("LAST: " + g_lastEvent, clrAqua);
   RenderPanel();
}

//===================== Trading (v3.02) =====================
CTrade   g_trade;
string   g_tradeEvent = "";

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

// record zone touches by the M5 candle that opened at bt (a CISD must come on a LATER candle)
void RecordTouches(const datetime bt, const double h, const double l)
{
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      if(g_al[k].formed > 0 && bt + PeriodSeconds(PERIOD_M5) <= g_al[k].formed) continue;   // level did not exist yet
      bool touch = g_al[k].side > 0 ? l <= g_al[k].v + ZoneBuffer : h >= g_al[k].v - ZoneBuffer;
      if(touch) g_al[k].armT = bt;
   }
}

// Newest level (support for sig>0, resistance for sig<0) shared by >= SLNewAlignTFs timeframes (Major or Minor)
// that became aligned AFTER 'after' (time the needed member TF level was set) and lies beyond 'ref'
// (buy: below the CISD close). v = level, f = when it aligned, d = members.
bool NewAlignedAnchor(const int sig, const datetime after, const double ref, double &v, datetime &f, string &d)
{
   double vals[]; string desc[]; int mask[];
   int memK[]; int memTf[]; datetime memT[];
   for(int t = 0; t < NTF; t++)
   {
      if(!g_mm[t].ready) continue;
      for(int j = (sig > 0 ? 0 : 2); j < (sig > 0 ? 2 : 4); j++)
      {
         if(t == 7 && !UseM3Levels) continue;                                // v3.06: M3 levels ignored
         if(LTFMajorOnly && (t == 6 || t == 7) && (j % 2 == 1)) continue;   // v3.05: M5 / M3 Minor levels don't align
         int x = (j == 0) ? g_mm[t].w_MajSupX : (j == 1) ? g_mm[t].w_MinSupX : (j == 2) ? g_mm[t].w_MajResX : g_mm[t].w_MinResX;
         double y = (j == 0) ? g_mm[t].w_MajSupY : (j == 1) ? g_mm[t].w_MinSupY : (j == 2) ? g_mm[t].w_MajResY : g_mm[t].w_MinResY;
         if(x < 0) continue;
         y = NormalizeDouble(y, _Digits);
         int k = -1;
         for(int q = 0; q < ArraySize(vals); q++) if(vals[q] == y) { k = q; break; }
         string lab = g_tfn[t] + ((j % 2 == 0) ? " Maj" : " Min");
         if(k < 0) { k = ArraySize(vals); PushD(vals, y); PushS(desc, lab); PushI(mask, 0); }
         else desc[k] += ", " + lab;
         mask[k] |= (1 << t);
         PushI(memK, k); PushI(memTf, t);
         ArrayResize(memT, ArraySize(memT) + 1); memT[ArraySize(memT) - 1] = g_mm[t].sT[j];
      }
   }
   bool found = false;
   for(int k = 0; k < ArraySize(vals); k++)
   {
      if(sig > 0 ? vals[k] >= ref : vals[k] <= ref) continue;
      int idx[];
      for(int q = 0; q < ArraySize(memK); q++) if(memK[q] == k) PushI(idx, q);
      for(int a = 0; a < ArraySize(idx); a++) for(int b = a + 1; b < ArraySize(idx); b++)
         if(memT[idx[b]] < memT[idx[a]]) { int tmp = idx[a]; idx[a] = idx[b]; idx[b] = tmp; }
      int tm = 0; datetime fk = 0;
      for(int a = 0; a < ArraySize(idx); a++)
      {
         tm |= (1 << memTf[idx[a]]);
         int c = 0, x = tm; while(x) { c += (x & 1); x >>= 1; }
         if(c >= SLNewAlignTFs) { fk = memT[idx[a]]; break; }
      }
      if(fk <= after) continue;
      if(!found || fk > f || (fk == f && MathAbs(vals[k] - ref) < MathAbs(v - ref))) { v = vals[k]; f = fk; d = desc[k]; found = true; }
   }
   return found;
}

// newest CISD of the entry type on timeframe index t, if its candle is the one that just closed (close time ct)
int FreshCISD(const int t, const datetime ct)
{
   CCisdTF *c = GetPointer(g_cs[t]);
   if(EntryCISD == ENTRY_AA) return c.aaTime == ct ? c.aaDir : 0;
   return c.lxTime == ct ? c.lxDir : 0;
}

// v3.08 TP fallback: nearest UNTESTED opposite OB on M15 / M30 beyond the entry
// (buy: bearish OB above -> its low - OBTPBuffer; sell: bullish OB below -> its top + OBTPBuffer)
double OBTarget(const int sig, const double entry, string &why)
{
   why = "no 3-TF level, no untested M15/M30 OB";
   if(!OBTPFallback) { why = "no 3-TF level"; return 0.0; }
   int tfs[2] = {4, 3};   // M15, M30
   double best = 0.0;
   for(int i = 0; i < 2; i++)
   {
      int t = tfs[i];
      if(g_obH[t] == INVALID_HANDLE || BarsCalculated(g_obH[t]) < 10) continue;
      int side = sig > 0 ? 1 : 0;   // buy -> bearish OBs, sell -> bullish OBs
      for(int sl = 0; sl < OB_SLOTS; sl++)
      {
         int b = (side * OB_SLOTS + sl) * OB_FIELDS;
         double top[], btm[], rt[];
         if(CopyBuffer(g_obH[t], b + 0, 0, 1, top) != 1 || CopyBuffer(g_obH[t], b + 1, 0, 1, btm) != 1 || CopyBuffer(g_obH[t], b + 4, 0, 1, rt) != 1) continue;
         if(top[0] == EMPTY_VALUE || rt[0] != 0.0) continue;   // empty slot or already retested
         double tp = sig > 0 ? btm[0] - OBTPBuffer : top[0] + OBTPBuffer;
         if(sig > 0 ? tp <= entry : tp >= entry) continue;
         if(best == 0.0 || (sig > 0 ? tp < best : tp > best))
         {
            best = tp;
            why = StringFormat("untested %s %s OB %s-%s %s %.1f", g_tfn[t], sig > 0 ? "bear" : "bull", Px(btm[0]), Px(top[0]), sig > 0 ? "-" : "+", OBTPBuffer);
         }
      }
   }
   return best;
}

// v3.08: breakeven at +BEPoints, then trail BEPoints behind price in TrailStep steps (every tick)
void ManageStops()
{
   if(!EnableTrading || BEPoints <= 0) return;
   ulong tk; int pd;
   if(!GetPos(tk, pd) || !PositionSelectByTicket(tk)) return;
   double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
   double px = pd > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double gain = (px - open) * pd;
   if(gain < BEPoints) return;
   double step = TrailStep > 0 ? TrailStep : 1.0;
   double want = open + pd * MathFloor((gain - BEPoints) / step + 1e-9) * step;
   want = NormalizeDouble(want, _Digits);
   if(sl != 0.0 && (want - sl) * pd < _Point) return;                                   // not better than the current SL
   double stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if((px - want) * pd < stops) return;
   if(g_trade.PositionModify(tk, want, tp))
      PrintFormat("V6S_ICT_3 %s SL %s -> %s (%s, +%.2f)", pd > 0 ? "BUY" : "SELL", sl > 0 ? Px(sl) : "none", Px(want),
                  MathAbs(want - open) < _Point ? "breakeven" : "trail", gain);
}

// TP for an entry at 'entry': buy -> nearest aligning resistance above it - TPBuffer; sell -> nearest support below + TPBuffer
double NearestTP(const int sig, const double entry, string &why)
{
   int bi = -1;
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      if(g_al[k].ntf < TPMinTFs) continue;   // v3.07: 3-TF levels only, either type
      double tp = g_al[k].v - sig * TPBuffer;
      if(sig > 0 ? tp <= entry : tp >= entry) continue;
      if(bi < 0 || (sig > 0 ? g_al[k].v < g_al[bi].v : g_al[k].v > g_al[bi].v)) bi = k;
   }
   if(bi < 0) return OBTarget(sig, entry, why);
   why = StringFormat("%d-TF %s %s %s %.1f", g_al[bi].ntf, g_al[bi].side > 0 ? "S" : "R", Px(g_al[bi].v), sig > 0 ? "-" : "+", TPBuffer);
   return g_al[bi].v - sig * TPBuffer;
}

// shared: SL from a new aligned level after 'after' (else the level), TP, close-opposite, send
void Execute(const string kind, const string trig, const string tfn, const int sig, const double lvl, const datetime after,
             const double ref, const string lvlTxt)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double entry = sig > 0 ? ask : bid;
   double sl; string slWhy;
   double nv; datetime nf; string nd;
   bool haveNew = NewAlignedAnchor(sig, after, ref, nv, nf, nd);
   double nsl = haveNew ? nv - sig * SLBuffer : 0.0;
   if(haveNew && (entry - nsl) * sig <= SLNewMaxDist)
   { sl = nsl; slWhy = StringFormat("new aligned %s %s f%s [%s]", sig > 0 ? "S" : "R", Px(nv), IST(nf), nd); }
   else
   {
      sl = lvl - sig * SLBuffer;
      slWhy = "level " + Px(lvl) + (haveNew ? StringFormat(" (new aligned %s is %.1f > %.0f away)", Px(nv), (entry - nsl) * sig, SLNewMaxDist) : "");
   }
   if(kind == "BO" && BreakoutSLAtLevel)   // v3.10 (v2.18): breakout SL at the broken level
   { sl = lvl - sig * BreakoutSLBuffer; slWhy = StringFormat("broken level %s %s %.1f", Px(lvl), sig > 0 ? "-" : "+", BreakoutSLBuffer); }
   if(MaxSLPrice > 0 && (entry - sl) * sig > MaxSLPrice)   // v3.10 (v2.02): SL cap
   { slWhy += StringFormat(" capped %.0f (was %.1f)", MaxSLPrice, (entry - sl) * sig); sl = entry - sig * MaxSLPrice; }
   string tpWhy; double tp = NearestTP(sig, entry, tpWhy);
   double risk = (entry - sl) * sig;
   if(kind == "BO" && MinRRBreakout > 0 && tp > 0 && risk > 0 && (tp - entry) * sig < MinRRBreakout * risk)
   {
      g_tradeEvent = StringFormat("%s SKIP BO %s at broken %s: TP %s only %.2fR (needs %.1fR)", IST(TimeCurrent()), sig > 0 ? "BUY" : "SELL",
                                  Px(lvl), Px(tp), (tp - entry) * sig / risk, MinRRBreakout);
      Print("V6S_ICT_3 ", g_tradeEvent);
      return;
   }
   if(tp > 0 && MinTPR > 0 && risk > 0 && (tp - entry) * sig < MinTPR * risk)
   { tpWhy = StringFormat("1:1 (%s only %.2fR)", tpWhy, (tp - entry) * sig / risk); tp = entry + sig * risk; }
   string what = StringFormat("%s %s %s at %s %s %s SL %s [%s] TP %s [%s]", kind, sig > 0 ? "BUY" : "SELL", trig, Px(ref),
                              sig > 0 ? ">" : "<", lvlTxt, Px(sl), slWhy, tp > 0 ? Px(tp) : "none", tpWhy);
   if((entry - sl) * sig <= 0) { g_tradeEvent = IST(TimeCurrent()) + " SKIP " + what + " -- price beyond SL"; Print("V6S_ICT_3 ", g_tradeEvent); return; }

   ulong tk; int pd;
   if(GetPos(tk, pd))
   {
      if(pd == sig) { g_tradeEvent = IST(TimeCurrent()) + " IGNORED (already in a " + (sig > 0 ? "buy" : "sell") + ") " + what; Print("V6S_ICT_3 ", g_tradeEvent); return; }
      if(!CloseOnOpposite) { g_tradeEvent = IST(TimeCurrent()) + " IGNORED (opposite trade open) " + what; Print("V6S_ICT_3 ", g_tradeEvent); return; }
      if(EnableTrading)
      {
         bool ok = g_trade.PositionClose(tk);
         PrintFormat("V6S_ICT_3 close %s #%I64u -- opposite signal: %s", pd > 0 ? "BUY" : "SELL", tk, ok ? "done" : g_trade.ResultRetcodeDescription());
      }
   }
   bool sent = false;
   if(EnableTrading)
   {
      string cm = StringFormat("V6S3 %s%s %s %s", kind == "BO" ? "BO " : "", sig > 0 ? "B" : "S", tfn, DoubleToString(lvl, 1));
      sl = NormalizeDouble(sl, _Digits); tp = tp > 0 ? NormalizeDouble(tp, _Digits) : 0.0;
      sent = sig > 0 ? g_trade.Buy(Lots, _Symbol, 0.0, sl, tp, cm) : g_trade.Sell(Lots, _Symbol, 0.0, sl, tp, cm);
   }
   if(sent)
   {
      ulong nt; int nd2;
      if(GetPos(nt, nd2)) GlobalVariableSet(StringFormat("V6S3_%I64u_sl0", nt), sl);   // initial SL for the 1:1 rule
   }
   g_tradeEvent = IST(TimeCurrent()) + " " + what + (EnableTrading ? (sent ? " -- SENT" : " -- FAILED " + g_trade.ResultRetcodeDescription()) : " -- (trading off)");
   Print("V6S_ICT_3 ", g_tradeEvent);
}

// v3.10 (v2.21 blocks): true = this entry is blocked right now (the setup is NOT used up)
bool Blocked(const string kind, const int sig, string &why)
{
   if(UseDZFilter && (sig > 0 ? g_dzfBlockBuy : g_dzfBlockSell)) { why = "DZ filter"; return true; }
   if(UseLondonBlock && InISTWindow(LondonBlockFrom, LondonBlockTo)) { why = "London block"; return true; }
   if(kind == "REV" && UseRevTimeBlock && InISTWindow(RevBlockFrom, RevBlockTo)) { why = "night reversal block"; return true; }
   if(kind == "REV" && UseMondayRevBlock)
   {
      MqlDateTime it; TimeToStruct((datetime)((long)TimeCurrent() + ServerToISTMinutes * 60), it);
      if(it.day_of_week == 1) { why = "Monday reversal block"; return true; }
   }
   return false;
}

// REVERSAL: an armed (zone-touched) level of this side, touched before 'touchBefore', with ref beyond the level.
// Returns true if a level was used.
bool TryEntry(const string trig, const string tfn, const int sig, const datetime touchBefore, const double ref)
{
   int best = -1;
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      if(g_al[k].side != sig || g_al[k].armT == 0 || g_al[k].armT >= touchBefore) continue;
      if(sig > 0 ? ref <= g_al[k].v : ref >= g_al[k].v) continue;
      if(best < 0 || g_al[k].armT > g_al[best].armT || (g_al[k].armT == g_al[best].armT && MathAbs(g_al[k].v - ref) < MathAbs(g_al[best].v - ref))) best = k;
   }
   if(best < 0) return false;
   string bw;
   if(Blocked("REV", sig, bw))
   {
      g_tradeEvent = StringFormat("%s BLOCKED (%s) REV %s %s at %s, level %s kept armed", IST(TimeCurrent()), bw, sig > 0 ? "BUY" : "SELL", trig, Px(ref), Px(g_al[best].v));
      return false;
   }
   AlignLvl L = g_al[best];
   g_al[best].armT = 0;   // this touch is used up (first trigger wins)
   Execute("REV", trig, tfn, sig, L.v, L.armT, ref, StringFormat("%s %s (touch %s)", sig > 0 ? "S" : "R", Px(L.v), IST(L.armT)));
   return true;
}

// BREAKOUT (v3.05): a break of this direction that closed at or before 'breakBy', with ref still beyond the level.
bool TryBreakout(const string trig, const string tfn, const int sig, const datetime breakBy, const double ref)
{
   if(!UseBreakoutTrades) return false;
   int best = -1;
   for(int k = 0; k < ArraySize(g_boVal); k++)
   {
      if(g_boSide[k] != sig || g_boTime[k] > breakBy) continue;
      if(sig > 0 ? ref <= g_boVal[k] : ref >= g_boVal[k]) continue;
      if(best < 0 || g_boTime[k] > g_boTime[best]) best = k;
   }
   if(best < 0) return false;
   string bw;
   if(Blocked("BO", sig, bw))
   {
      g_tradeEvent = StringFormat("%s BLOCKED (%s) BO %s %s at %s, break %s kept", IST(TimeCurrent()), bw, sig > 0 ? "BUY" : "SELL", trig, Px(ref), Px(g_boVal[best]));
      return false;
   }
   double v = g_boVal[best]; datetime bt = g_boTime[best];
   int n = ArraySize(g_boVal);
   for(int q = best; q < n - 1; q++) { g_boSide[q] = g_boSide[q+1]; g_boVal[q] = g_boVal[q+1]; g_boTime[q] = g_boTime[q+1]; }
   ArrayResize(g_boSide, n - 1); ArrayResize(g_boVal, n - 1); ArrayResize(g_boTime, n - 1);
   Execute("BO", trig, tfn, sig, v, bt - PeriodSeconds(PERIOD_M5), ref, StringFormat("broken %s (break %s)", Px(v), IST(bt)));
   return true;
}

// called once for the M5 candle that just closed (open time bt)
void EvaluateEntries(const datetime bt)
{
   datetime ct = bt + PeriodSeconds(PERIOD_M5);
   if(TimeCurrent() - ct > MaxSignalDelaySec) return;
   int m5 = 6, m10 = 5;
   string ty = EntryCISD == ENTRY_AA ? "AA" : "Lux";
   int sig5 = UseM5CISD ? FreshCISD(m5, ct) : 0;
   int sig10 = 0; datetime b10 = iTime(_Symbol, PERIOD_M10, 1);
   if(UseM10CISD && b10 + PeriodSeconds(PERIOD_M10) == ct) sig10 = FreshCISD(m10, ct);
   if(sig5 != 0 && !TryEntry("M5 CISD " + ty + " close", "M5", sig5, bt, iClose(_Symbol, PERIOD_M5, 1)))
      TryBreakout("M5 CISD " + ty + " close", "M5", sig5, bt, iClose(_Symbol, PERIOD_M5, 1));
   if(sig10 != 0 && !TryEntry("M10 CISD " + ty + " close", "M10", sig10, b10, iClose(_Symbol, PERIOD_M10, 1)))
      TryBreakout("M10 CISD " + ty + " close", "M10", sig10, b10, iClose(_Symbol, PERIOD_M10, 1));
}

// v3.04: a new OB on M3 / M5 / M10 (slot 0 formed time changed) triggers an armed level of its side
datetime g_obSeen[NTF][2];
void CheckOBTriggers()
{
   if(!UseOBTrigger) return;
   for(int t = 0; t < NTF; t++)
   {
      bool use = (t == 7 && OBTrigM3) || (t == 6 && OBTrigM5) || (t == 5 && OBTrigM10);
      if(!use || g_obH[t] == INVALID_HANDLE || BarsCalculated(g_obH[t]) < 10) continue;
      for(int side = 0; side < 2; side++)
      {
         double x[];
         if(CopyBuffer(g_obH[t], side * OB_SLOTS * OB_FIELDS + 3, 0, 1, x) != 1 || x[0] == EMPTY_VALUE) continue;
         datetime fm = (datetime)(long)x[0];
         if(fm == g_obSeen[t][side]) continue;
         bool first = (g_obSeen[t][side] == 0);
         g_obSeen[t][side] = fm;
         if(first || TimeCurrent() - fm > MaxSignalDelaySec) continue;   // startup / stale -- just remember it
         double z[];
         string zone = "";
         if(CopyBuffer(g_obH[t], side * OB_SLOTS * OB_FIELDS + 1, 0, 1, z) == 1) zone = Px(z[0]);
         if(CopyBuffer(g_obH[t], side * OB_SLOTS * OB_FIELDS + 0, 0, 1, z) == 1) zone += "-" + Px(z[0]);
         int sig = side == 0 ? 1 : -1;
         string trg = StringFormat("%s %s OB %s formed %s, bid", g_tfn[t], sig > 0 ? "bull" : "bear", zone, IST(fm));
         double bidNow = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         if(!TryEntry(trg, g_tfn[t], sig, fm - PeriodSeconds(PERIOD_M5) + 1, bidNow))
            TryBreakout(trg, g_tfn[t], sig, fm - 1, bidNow);
      }
   }
}

// v3.04: keep the open trade's TP at the nearest opposite aligning level (levels can change)
void ManageTP()
{
   if(!UpdateTPEachM5 || !EnableTrading) return;
   ulong tk; int pd;
   if(!GetPos(tk, pd) || !PositionSelectByTicket(tk)) return;
   double open = PositionGetDouble(POSITION_PRICE_OPEN), cur = PositionGetDouble(POSITION_TP), sl = PositionGetDouble(POSITION_SL);
   double px = pd > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   string why; double tp = NearestTP(pd, open, why);
   string gk = StringFormat("V6S3_%I64u_sl0", tk);
   double risk = GlobalVariableCheck(gk) ? MathAbs(open - GlobalVariableGet(gk)) : 0.0;
   if(tp > 0 && MinTPR > 0 && risk > 0 && (tp - open) * pd < MinTPR * risk) { tp = open + pd * risk; why = "1:1 (" + why + " < 1R)"; }
   if(tp <= 0 || (pd > 0 ? tp <= px + stops : tp >= px - stops)) return;   // nothing valid -- keep what is there
   tp = NormalizeDouble(tp, _Digits);
   if(MathAbs(tp - cur) < _Point) return;
   if(g_trade.PositionModify(tk, sl, tp))
      PrintFormat("V6S_ICT_3 TP %s -> %s [%s]", cur > 0 ? Px(cur) : "none", Px(tp), why);
}

// v3.06: M3 CISD on the M3 candle that just closed (open time b3). The touch / break candle must have closed
// before this M3 candle opened.
void EvaluateM3(const datetime b3)
{
   datetime ct = b3 + PeriodSeconds(PERIOD_M3);
   if(!UseM3CISD || TimeCurrent() - ct > MaxSignalDelaySec) return;
   int sig = FreshCISD(7, ct);
   if(sig == 0) return;
   string ty = EntryCISD == ENTRY_AA ? "AA" : "Lux";
   double cc = iClose(_Symbol, PERIOD_M3, 1);
   if(!TryEntry("M3 CISD " + ty + " close", "M3", sig, b3 - PeriodSeconds(PERIOD_M5) + 1, cc))
      TryBreakout("M3 CISD " + ty + " close", "M3", sig, b3, cc);
}

//===================== DZ breakout slot (v2.21 DZBreakoutBar, separate magic) =====================
CTrade   g_dzTrade;
string   g_dzbLast = "";
datetime g_m15Done = 0;

bool DZGetPosition(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != DZMagicNumber) continue;
      ticket = t; dir = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      return true;
   }
   return false;
}

// the M15 candle that just closed (open bt, close c): entry on its LuxAlgo CISD, square-off on its AlgoAlpha CISD
void DZBreakoutBar(const datetime bt, const double c)
{
   double z1, z2, z3, z4; datetime ss;
   if(!DZSession(bt, z1, z2, z3, z4, ss)) return;
   double rLo = MathMin(z1, z2), rHi = MathMax(z1, z2), sLo = MathMin(z3, z4), sHi = MathMax(z3, z4);
   datetime ct = bt + PeriodSeconds(PERIOD_M15);
   CCisdTF *cs = GetPointer(g_cs[4]);
   int algo = cs.aaTime == ct ? cs.aaDir : 0;
   int lux  = cs.lxTime == ct ? cs.lxDir : 0;
   if(DZSquareOff && algo != 0)
   {
      int sd = (algo > 0 && c > rHi) ? 1 : (algo < 0 && c < sLo) ? -1 : 0;
      ulong qt; int qd;
      if(sd != 0 && DZGetPosition(qt, qd) && qd == -sd && EnableTrading)
         if(g_dzTrade.PositionClose(qt)) { g_dzbLast = IST(TimeCurrent()) + " DZ trade squared off (AlgoAlpha M15 opposite setup)"; Print("V6S_ICT_3 ", g_dzbLast); }
   }
   if(lux == 0) return;
   int d = 0; double sl = 0; string what = "";
   if(lux > 0 && c > rHi) { d = 1;  sl = rLo - DZSLBuffer; what = StringFormat("breakout of R zone %s-%s", Px(rLo), Px(rHi)); }
   if(lux < 0 && c < sLo) { d = -1; sl = sHi + DZSLBuffer; what = StringFormat("breakdown of S zone %s-%s", Px(sLo), Px(sHi)); }
   if(d == 0) return;
   ulong tk; int td;
   if(DZGetPosition(tk, td)) { g_dzbLast = IST(TimeCurrent()) + " DZ setup ignored -- a DZ trade is open"; return; }
   double price = d > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double risk = (price - sl) * d;
   if(risk <= 0) return;
   bool capped = false;
   if(DZMaxSL > 0 && risk > DZMaxSL) { sl = price - d * DZMaxSL; risk = DZMaxSL; capped = true; }
   double tp = 0.0; string tpWhy = "1:1 (no aligning level ahead)";
   for(int k = 0; k < ArraySize(g_al); k++)
   {
      if(g_al[k].side != -d) continue;
      double t = g_al[k].v - d * DZTPBuffer;
      if((t - price) * d > 0 && (tp == 0.0 || (t - tp) * d < 0)) tp = t;
   }
   if(tp != 0.0 && DZMinTPPoints > 0 && (tp - price) * d < DZMinTPPoints) { g_dzbLast = IST(TimeCurrent()) + " DZ skipped: TP too close"; return; }
   if(tp != 0.0) tpWhy = StringFormat("aligning %s %s %s %.1f", d > 0 ? "R" : "S", Px(tp + d * DZTPBuffer), d > 0 ? "-" : "+", DZTPBuffer);
   else tp = price + d * risk;
   if(capped && DZCappedMinR > 0 && (tp - price) * d < DZCappedMinR * risk) { g_dzbLast = IST(TimeCurrent()) + " DZ skipped: capped SL and TP < 1R"; return; }
   bool ok = false;
   if(EnableTrading)
   {
      string cm = StringFormat("V6S3 DZ%s %.2f", d > 0 ? "B" : "S", d > 0 ? rHi : sLo);
      sl = NormalizeDouble(sl, _Digits); tp = NormalizeDouble(tp, _Digits);
      ok = d > 0 ? g_dzTrade.Buy(DZLots, _Symbol, 0.0, sl, tp, cm) : g_dzTrade.Sell(DZLots, _Symbol, 0.0, sl, tp, cm);
   }
   g_dzbLast = StringFormat("%s DZ %s %s, M15 Lux CISD close %s, SL %s%s TP %s [%s]%s", IST(TimeCurrent()), d > 0 ? "BUY" : "SELL", what, Px(c),
                            Px(sl), capped ? " (capped)" : "", Px(tp), tpWhy, EnableTrading ? (ok ? " -- SENT" : " -- FAILED") : " -- (trading off)");
   Print("V6S_ICT_3 ", g_dzbLast);
}

//===================== events =====================
datetime g_m5Done = 0, g_m1Last = 0, g_lastPanel = 0, g_m3Done = 0;
bool     g_dirty = true;

int OnInit()
{
   for(int t = 0; t < NTF; t++)
   {
      g_mm[t].Init(g_tf[t], g_tfn[t]);
      g_cs[t].Init(g_tf[t]);
      g_obH[t] = iCustom(_Symbol, g_tf[t], "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, false);
      if(g_obH[t] == INVALID_HANDLE) PrintFormat("V6S_ICT_3.0: cannot load OB_Detector_v1.07 on %s", g_tfn[t]);
   }
   if(DrawChartTFZones && ChartOn())
   {
      g_obChart = iCustom(_Symbol, PERIOD_CURRENT, "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, true);
      if(g_obChart != INVALID_HANDLE && !MQLInfoInteger(MQL_TESTER)) ChartIndicatorAdd(0, 0, g_obChart);
   }
   ArrayResize(g_al, 0); ArrayResize(g_boSide, 0); ArrayResize(g_boVal, 0); ArrayResize(g_boTime, 0);
   g_dzsKey = 0; g_dzfSess = 0; g_dzDrawn = 0; g_m5Done = 0; g_m3Done = 0; g_m1Last = 0; g_lastEvent = ""; g_dirty = true;
   ObjectsDeleteAll(0, "V6S3_");
   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_dzTrade.SetExpertMagicNumber(DZMagicNumber);
   g_dzTrade.SetTypeFillingBySymbol(_Symbol);
   g_m15Done = 0; g_dzbLast = "";
   g_tradeEvent = "";
   for(int t = 0; t < NTF; t++) { g_obSeen[t][0] = 0; g_obSeen[t][1] = 0; }
   PrintFormat("V6S_ICT_3 v3.10 on %s %s | trading %s, lots %.2f, zone +/-%.1f, SL buffer %.1f, CISD %s on %s%s, magic %I64d",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period), EnableTrading ? "ON" : "off", Lots, ZoneBuffer, SLBuffer,
               EntryCISD == ENTRY_AA ? "AlgoAlpha" : "LuxAlgo", UseM5CISD ? "M5 " : "", UseM10CISD ? "M10" : "", MagicNumber);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   for(int t = 0; t < NTF; t++) if(g_obH[t] != INVALID_HANDLE) IndicatorRelease(g_obH[t]);
   if(g_obChart != INVALID_HANDLE) IndicatorRelease(g_obChart);
   if(!MQLInfoInteger(MQL_TESTER)) ObjectsDeleteAll(0, "V6S3_");
}

void OnTick()
{
   bool changed = false, allReady = true;
   for(int t = 0; t < NTF; t++)
   {
      if(g_mm[t].Update()) changed = true;
      g_cs[t].Update();
      if(!g_mm[t].ready || !g_cs[t].ready) allReady = false;
   }
   if(!allReady) return;
   if(changed) { RebuildAlignments(); g_dirty = true; }

   // closed M5 candles: DZ filter + breakout candidates (catch up anything missed)
   datetime m5c = iTime(_Symbol, PERIOD_M5, 1);
   if(m5c > g_m5Done)
   {
      int from = (g_m5Done == 0) ? StartReplayM5   // covers the whole current session (filter resets per session)
                                 : iBarShift(_Symbol, PERIOD_M5, g_m5Done, false) - 1;
      for(int s = MathMax(from, 1); s >= 1; s--)
      {
         datetime bt = iTime(_Symbol, PERIOD_M5, s);
         DZFilterStep(bt, iClose(_Symbol, PERIOD_M5, s));
         BreakoutStep(bt, iClose(_Symbol, PERIOD_M5, s), iClose(_Symbol, PERIOD_M5, s + 1));
         if(s == 1) { EvaluateEntries(bt); ManageTP(); }
         RecordTouches(bt, iHigh(_Symbol, PERIOD_M5, s), iLow(_Symbol, PERIOD_M5, s));
      }
      g_m5Done = m5c; g_dirty = true;
      DrawDZ();
   }

   datetime m3c = iTime(_Symbol, PERIOD_M3, 1);
   if(m3c > g_m3Done)
   {
      bool first = (g_m3Done == 0);
      g_m3Done = m3c;
      if(!first) { EvaluateM3(m3c); g_dirty = true; }
   }
   CheckOBTriggers();
   ManageStops();
   datetime m15c = iTime(_Symbol, PERIOD_M15, 1);
   if(UseDZBreakout && m15c > g_m15Done)
   {
      bool first = (g_m15Done == 0);
      g_m15Done = m15c;
      if(!first && TimeCurrent() - (m15c + PeriodSeconds(PERIOD_M15)) <= MaxSignalDelaySec) { DZBreakoutBar(m15c, iClose(_Symbol, PERIOD_M15, 1)); g_dirty = true; }
   }
   if(LiveRetests()) { g_dirty = true; DrawLevels(); }

   datetime m1 = iTime(_Symbol, PERIOD_M1, 0);
   if(m1 != g_m1Last || (g_dirty && TimeCurrent() - g_lastPanel >= 1))
   {
      g_m1Last = m1; g_lastPanel = TimeCurrent(); g_dirty = false;
      DrawPanel();
   }
}
//+------------------------------------------------------------------+
