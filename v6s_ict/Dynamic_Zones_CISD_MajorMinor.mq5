//+------------------------------------------------------------------+
//|   Dynamic_Zones_CISD_MajorMinor.mq5                                |
//|   Merge of three originally-separate indicators, purely for       |
//|   VISUAL/chart reference -- NO bridge file is published by any    |
//|   of the three blocks below (confirmed with the user 2026-09-27:  |
//|   Python already computes Major/Minor and Dynamic Zones natively  |
//|   via copy_rates -- see v7_sentinel/rates.py's own                |
//|   read_all_major_minor()/read_dynamic_zones() -- and CISD_AlgoAlpha|
//|   .mq5 keeps running UNTOUCHED as the live bridge source           |
//|   cisd_bridge.py actually depends on; this file is a separate,     |
//|   additional chart convenience, not a replacement for it).         |
//|                                                                    |
//|   1. DYNAMIC ZONES -- verbatim from Dynamic Zones.mq5 (itself a    |
//|      port of the "Dynamic Zone - Suraj v5" Pine script). Own       |
//|      Inp*-prefixed inputs, unchanged.                              |
//|   2. CISD -- verbatim from CISD_AlgoAlpha.mq5's own detection/     |
//|      drawing logic, MINUS its bridge-publish plumbing (PublishTo   |
//|      File/FileBridgeFolder/BridgeSymbol/PublishEverySeconds inputs,|
//|      EffectiveSymbol(), PublishBridgeFile(), and the 250ms timer   |
//|      that only existed to keep that publish fresh (see the NO      |
//|      TIMER note further down). Everything else (swing lines, origin|
//|      lines, sweep markers, status label, the exact confirm/discard |
//|      loop) is unchanged.                                           |
//|   3. MAJOR/MINOR -- verbatim from                                  |
//|      ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5's own    |
//|      Major/Minor block (ZigZag pivots -> Major/Minor S/R rays).    |
//|      Own EnableMajorMinor toggle/inputs, unchanged.                |
//|                                                                    |
//|   No input-name collisions between the three blocks -- each keeps  |
//|   its own original input names verbatim, same as how ST_/HS_ only  |
//|   got their own prefixes in the ATR merge because THEY would have  |
//|   collided with that file's base ATR-Dual inputs.                 |
//|                                                                    |
//|   Each block has its own Enable* master switch (EnableDynamicZones/|
//|   EnableCISD/EnableMajorMinor), same "pause computation + clear     |
//|   drawn objects/buffers on toggle-off, resume cleanly on re-enable"|
//|   pattern the ATR merge file already established for Supertrend/   |
//|   Major-Minor.                                                     |
//|                                                                    |
//|   NO TIMER: unlike CISD_AlgoAlpha.mq5 (which needs one purely to   |
//|   keep its bridge-publish fresh on an inactive/background chart),  |
//|   nothing here publishes anything -- purely tick-driven via        |
//|   OnCalculate, matching Dynamic Zones.mq5's and the original Major/|
//|   Minor block's own already-lightweight, timer-free design (see    |
//|   this project's own standing "MT5 indicators must stay             |
//|   lightweight" convention -- heavy per-tick/timer work causes real  |
//|   chart lag).                                                      |
//|                                                                    |
//|   NOT compiled/attached in this session (no MetaEditor available   |
//|   here) -- please compile + attach in MetaTrader and confirm it     |
//|   builds clean before relying on it.                                |
//+------------------------------------------------------------------+
#property copyright "Suraj_ARK"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 10
#property indicator_plots   8

//===================== Plots: Dynamic Zones (buffers 0-7, plots 0-5) =====================
#property indicator_type1   DRAW_FILLING
#property indicator_label1  "Z1;Z2"
#property indicator_type2   DRAW_FILLING
#property indicator_label2  "Z3;Z4"
#property indicator_type3   DRAW_LINE
#property indicator_label3  "Z1"
#property indicator_type4   DRAW_LINE
#property indicator_label4  "Z2"
#property indicator_type5   DRAW_LINE
#property indicator_label5  "Z3"
#property indicator_type6   DRAW_LINE
#property indicator_label6  "Z4"

//===================== Plots: CISD (buffers 8-9, plots 6-7) =====================
#property indicator_label7  "CISD Swing High"
#property indicator_type7   DRAW_ARROW
#property indicator_width7  1
#property indicator_label8  "CISD Swing Low"
#property indicator_type8   DRAW_ARROW
#property indicator_width8  1

//===================== Inputs: Dynamic Zones (unchanged names) =====================
input bool            EnableDynamicZones = true;       // master on/off for the whole Dynamic Zones block
input ENUM_TIMEFRAMES InpZoneTF    = PERIOD_D1;     // Zone timeframe
input int             InpShortLen  = 5;             // Short range average (Z1/Z3)
input int             InpLongLen   = 10;            // Long range average (Z2/Z4)
input bool            InpShowFill  = true;          // Fill zones
input color           InpFillColor = C'0,0,60';     // Fill color
input color           InpLineColor = clrBlue;       // Line color
input bool            InpAlerts    = false;         // Alert on Z2 / Z4 cross (closed bars)

//===================== Inputs: CISD (unchanged names, bridge inputs dropped) =====================
input bool   EnableCISD          = true;   // master on/off for the whole CISD block
input double Tolerance          = 0.7;   // "Noise Filter" -- larger = less noise
input int    SwingPeriod        = 12;     // "Swing Period" -- pivot left/right bars
input int    ExpiryBars         = 100;    // "Expiry Bars" -- liquidity lines stop updating past this age
input int    LiquidityLookback  = 10;     // "Liquidity Lookback" -- how recent a wick mitigation must be to count as a sweep

input color  BullColor = C'0,255,187';    // Pine default #00ffbb
input color  BearColor = C'255,17,0';     // Pine default #ff1100
input bool   HideExpiredLevels   = true;
input bool   HideMitigatedLevels = false;
input int    MaxSwingLines       = 100;   // Pine hardcodes this cap (while size()>100: pop)
input bool   ShowSwingMarkers    = true;

//===================== Inputs: Major/Minor (unchanged names) =====================
input bool  EnableMajorMinor = true;   // master on/off for the whole Major/Minor block -- default on
input int   PivotPeriod      = 5;      // Short Term S&R Pivot Period
input bool  ShowMajor        = true;
input bool  ShowMinor        = true;
input color LineColor        = clrYellow;
input ENUM_LINE_STYLE MajorStyle = STYLE_SOLID;
input ENUM_LINE_STYLE MinorStyle = STYLE_SOLID;
input int   MajorWidth   = 2;
input int   MinorWidth   = 1;
input int   MajorMinorEverySeconds = 2;   // throttles CopyConfirmedToWorking/ProcessBar/DrawFromWorking

//===================== Buffers: Dynamic Zones =====================
double FillUp1[], FillUp2[], FillDn1[], FillDn2[];
double Z1[], Z2[], Z3[], Z4[];

datetime dz_day = 0;          // zone bar currently cached
double   dz_open, dz_half5, dz_half10;

//===================== Buffers/state: CISD =====================
double SwingHighMarker[];
double SwingLowMarker[];

#define DOT_MARKER_CODE 159   // wingdings solid filled circle

#define PREFIX_SWING_HIGH  "CISD_SH_"
#define PREFIX_SWING_LOW   "CISD_SL_"
#define PREFIX_ORIGIN_LINE "CISD_ORIGIN_"
#define PREFIX_SWEEP       "CISD_SWEEP_"

double sh_level[]; int sh_startIdx[];
double sl_level[]; int sl_startIdx[];

datetime g_last_wicked_high_time = 0;  double g_last_wicked_high_level = 0.0;  bool g_have_wicked_high = false;
datetime g_last_wicked_low_time  = 0;  double g_last_wicked_low_level  = 0.0;  bool g_have_wicked_low  = false;
int      g_last_wicked_high_bar  = -1;
int      g_last_wicked_low_bar   = -1;

double bear_open[]; int bear_idx[];
double bull_open[]; int bull_idx[];

int      g_trend = 0;

int      g_last_cisd_type = 0;
double   g_last_cisd_level = 0.0;
datetime g_last_cisd_time  = 0;
bool     g_last_sweep = false;

bool     g_last_cisd_has_swing = false;
double   g_last_cisd_swing_level = 0.0;

int      g_confirmed_upto = -1;
datetime g_bar0_time = 0;

//===================== Major/Minor confirmed (committed) state =====================
string gc_Type[],    gc_TypeAdv[];
double gc_Value[],   gc_ValueAdv[];
int    gc_Index[],   gc_IndexAdv[];
double gc_MajorHighLevel, gc_MajorLowLevel;
int    gc_MajorHighIndex, gc_MajorLowIndex;
string gc_MajorHighType,  gc_MajorLowType;
bool   gc_MajorLevelsSet;
bool   gc_Lock0, gc_Lock1;
double gc_LastHighValue, gc_LastLowValue;
int    gc_LastHighIndex, gc_LastLowIndex;
int    gc_ConfirmedUpTo;
int    gc_MajSupX=-1, gc_MajResX=-1, gc_MinSupX=-1, gc_MinResX=-1;
double gc_MajSupY,    gc_MajResY,    gc_MinSupY,    gc_MinResY;

//===================== Major/Minor working (this-call scratch) state =====================
string w_Type[],    w_TypeAdv[];
double w_Value[],   w_ValueAdv[];
int    w_Index[],   w_IndexAdv[];
double w_MajorHighLevel, w_MajorLowLevel;
int    w_MajorHighIndex, w_MajorLowIndex;
string w_MajorHighType,  w_MajorLowType;
bool   w_MajorLevelsSet;
bool   w_Lock0, w_Lock1;
double w_LastHighValue, w_LastLowValue;
int    w_LastHighIndex, w_LastLowIndex;
int    w_MajSupX=-1, w_MajResX=-1, w_MinSupX=-1, w_MinResX=-1;
double w_MajSupY,    w_MajResY,    w_MinSupY,    w_MinResY;

int    g_drawnMajorSupX=-1000000, g_drawnMajorResX=-1000000, g_drawnMinorSupX=-1000000, g_drawnMinorResX=-1000000;
datetime g_last_mm_time = 0;

#define NAME_MAJOR_SUPPORT    "MmSecret_MajorSupport"
#define NAME_MAJOR_RESISTANCE "MmSecret_MajorResistance"
#define NAME_MINOR_SUPPORT    "MmSecret_MinorSupport"
#define NAME_MINOR_RESISTANCE "MmSecret_MinorResistance"

//+==================================================================+
//|                      DYNAMIC ZONES -- verbatim                   |
//+==================================================================+

bool DZ_CalcZone(const int shift)
{
   int maxLen = MathMax(InpShortLen, InpLongLen);
   if(shift + maxLen >= iBars(_Symbol, InpZoneTF))
      return(false);

   double o = iOpen(_Symbol, InpZoneTF, shift);
   if(o <= 0.0)
      return(false);

   double sumShort = 0.0, sumLong = 0.0;
   for(int k = 1; k <= maxLen; k++)
   {
      double h = iHigh(_Symbol, InpZoneTF, shift + k);
      double l = iLow(_Symbol, InpZoneTF, shift + k);
      if(h <= 0.0 || l <= 0.0)
         return(false);
      double r = h - l;
      if(k <= InpShortLen) sumShort += r;
      if(k <= InpLongLen)  sumLong  += r;
   }

   dz_open   = o;
   dz_half5  = sumShort / InpShortLen / 2.0;
   dz_half10 = sumLong  / InpLongLen  / 2.0;
   return(true);
}

void DZ_SetEmpty(const int i)
{
   FillUp1[i] = FillUp2[i] = FillDn1[i] = FillDn2[i] = EMPTY_VALUE;
   Z1[i] = Z2[i] = Z3[i] = Z4[i] = EMPTY_VALUE;
}

void DZ_ClearAll(const int rates_total)
{
   for(int i = 0; i < rates_total; i++)
      DZ_SetEmpty(i);
   dz_day = 0;
}

void RunDynamicZones(const int rates_total, const int prev_calculated, const datetime &time[], const double &close[])
{
   if(prev_calculated == 0)
   {
      if(iBars(_Symbol, InpZoneTF) < MathMax(InpShortLen, InpLongLen) + 2)
         return;
      dz_day = 0;
   }

   int start = (prev_calculated > 0) ? prev_calculated - 1 : 0;

   for(int i = start; i < rates_total; i++)
   {
      int shift = iBarShift(_Symbol, InpZoneTF, time[i], false);
      if(shift < 0) { DZ_SetEmpty(i); continue; }

      datetime zt = iTime(_Symbol, InpZoneTF, shift);
      if(zt != dz_day)
      {
         if(!DZ_CalcZone(shift)) { DZ_SetEmpty(i); dz_day = 0; continue; }
         dz_day = zt;
      }

      Z1[i] = dz_open + dz_half5;
      Z2[i] = dz_open + dz_half10;
      Z3[i] = dz_open - dz_half5;
      Z4[i] = dz_open - dz_half10;
      FillUp1[i] = Z1[i]; FillUp2[i] = Z2[i];
      FillDn1[i] = Z3[i]; FillDn2[i] = Z4[i];
   }

   if(InpAlerts && prev_calculated > 0 && rates_total > prev_calculated && rates_total >= 3)
   {
      int c = rates_total - 2, p = rates_total - 3;
      if(Z2[c] != EMPTY_VALUE && Z2[p] != EMPTY_VALUE &&
         close[c] > Z2[c] && close[p] <= Z2[p])
         Alert("Dynamic Zones BUY: ", _Symbol, " closed above Z2, price = ", DoubleToString(close[c], _Digits));
      if(Z4[c] != EMPTY_VALUE && Z4[p] != EMPTY_VALUE &&
         close[c] < Z4[c] && close[p] >= Z4[p])
         Alert("Dynamic Zones SELL: ", _Symbol, " closed below Z4, price = ", DoubleToString(close[c], _Digits));
   }
}

//+==================================================================+
//|                      CISD -- verbatim minus bridge                |
//+==================================================================+

void InsertFrontD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void InsertFrontI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void RemoveFrontD(double &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveFrontI(int    &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveAtD(double &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveAtI(int    &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

bool CISD_IsPivotHigh(const double &high[], int c, int len, int total)
{
   if(c-len<0 || c+len>=total) return false;
   double v=high[c];
   for(int k=c-len;k<=c+len;k++)
      if(k!=c && high[k]>=v) return false;
   return true;
}
bool CISD_IsPivotLow(const double &low[], int c, int len, int total)
{
   if(c-len<0 || c+len>=total) return false;
   double v=low[c];
   for(int k=c-len;k<=c+len;k++)
      if(k!=c && low[k]<=v) return false;
   return true;
}

long CurrentPeriodObjectMask()
{
   switch(_Period)
   {
      case PERIOD_M1:   return OBJ_PERIOD_M1;
      case PERIOD_M2:   return OBJ_PERIOD_M2;
      case PERIOD_M3:   return OBJ_PERIOD_M3;
      case PERIOD_M4:   return OBJ_PERIOD_M4;
      case PERIOD_M5:   return OBJ_PERIOD_M5;
      case PERIOD_M6:   return OBJ_PERIOD_M6;
      case PERIOD_M10:  return OBJ_PERIOD_M10;
      case PERIOD_M12:  return OBJ_PERIOD_M12;
      case PERIOD_M15:  return OBJ_PERIOD_M15;
      case PERIOD_M20:  return OBJ_PERIOD_M20;
      case PERIOD_M30:  return OBJ_PERIOD_M30;
      case PERIOD_H1:   return OBJ_PERIOD_H1;
      case PERIOD_H2:   return OBJ_PERIOD_H2;
      case PERIOD_H3:   return OBJ_PERIOD_H3;
      case PERIOD_H4:   return OBJ_PERIOD_H4;
      case PERIOD_H6:   return OBJ_PERIOD_H6;
      case PERIOD_H8:   return OBJ_PERIOD_H8;
      case PERIOD_H12:  return OBJ_PERIOD_H12;
      case PERIOD_D1:   return OBJ_PERIOD_D1;
      case PERIOD_W1:   return OBJ_PERIOD_W1;
      case PERIOD_MN1:  return OBJ_PERIOD_MN1;
   }
   return OBJ_ALL_PERIODS;
}

void DrawOrMoveSwingLine(const string prefix, const int startIdx, const double level,
                          const datetime &time[], const int endIdx, const color clr)
{
   string name = prefix + IntegerToString(startIdx);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, time[startIdx], level, time[endIdx], level);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
   }
   else
      ObjectMove(0, name, 1, time[endIdx], level);
}
void DeleteSwingLine(const string prefix, const int startIdx)
{
   ObjectDelete(0, prefix + IntegerToString(startIdx));
}

void AddSwingHigh(const int startIdx, const double level){ InsertFrontI(sh_startIdx, startIdx); InsertFrontD(sh_level, level); }
void AddSwingLow (const int startIdx, const double level){ InsertFrontI(sl_startIdx, startIdx); InsertFrontD(sl_level,  level); }

void ScanSwingHighs(const int i, const datetime &time[], const double &high[],
                     bool &wicked_high, double &wicked_level)
{
   wicked_high = false;
   for(int j = ArraySize(sh_startIdx) - 1; j >= 0; j--)
   {
      int    startIdx = sh_startIdx[j];
      double level     = sh_level[j];

      if(i - startIdx < ExpiryBars)
      {
         DrawOrMoveSwingLine(PREFIX_SWING_HIGH, startIdx, level, time, i, (color)ChartGetInteger(0, CHART_COLOR_FOREGROUND));
         if(high[i] >= level)
         {
            if(HideMitigatedLevels) DeleteSwingLine(PREFIX_SWING_HIGH, startIdx);
            RemoveAtI(sh_startIdx, j); RemoveAtD(sh_level, j);
            wicked_high = true;
            wicked_level = level;
         }
      }
      else
      {
         if(HideExpiredLevels) DeleteSwingLine(PREFIX_SWING_HIGH, startIdx);
         RemoveAtI(sh_startIdx, j); RemoveAtD(sh_level, j);
      }
   }

   while(ArraySize(sh_startIdx) > MaxSwingLines)
   {
      int last = ArraySize(sh_startIdx) - 1;
      DeleteSwingLine(PREFIX_SWING_HIGH, sh_startIdx[last]);
      ArrayResize(sh_startIdx, last); ArrayResize(sh_level, last);
   }
}
void ScanSwingLows(const int i, const datetime &time[], const double &low[],
                    bool &wicked_low, double &wicked_level)
{
   wicked_low = false;
   for(int j = ArraySize(sl_startIdx) - 1; j >= 0; j--)
   {
      int    startIdx = sl_startIdx[j];
      double level     = sl_level[j];

      if(i - startIdx < ExpiryBars)
      {
         DrawOrMoveSwingLine(PREFIX_SWING_LOW, startIdx, level, time, i, (color)ChartGetInteger(0, CHART_COLOR_FOREGROUND));
         if(low[i] <= level)
         {
            if(HideMitigatedLevels) DeleteSwingLine(PREFIX_SWING_LOW, startIdx);
            RemoveAtI(sl_startIdx, j); RemoveAtD(sl_level, j);
            wicked_low = true;
            wicked_level = level;
         }
      }
      else
      {
         if(HideExpiredLevels) DeleteSwingLine(PREFIX_SWING_LOW, startIdx);
         RemoveAtI(sl_startIdx, j); RemoveAtD(sl_level, j);
      }
   }

   while(ArraySize(sl_startIdx) > MaxSwingLines)
   {
      int last = ArraySize(sl_startIdx) - 1;
      DeleteSwingLine(PREFIX_SWING_LOW, sl_startIdx[last]);
      ArrayResize(sl_startIdx, last); ArrayResize(sl_level, last);
   }
}

bool ConfirmBearCISD(const int i, const double &open[], const double &close[],
                      double &origin_lvl, int &origin_idx)
{
   while(ArraySize(bear_open) > 0)
   {
      double candOpen = bear_open[0];
      int    candIdx  = bear_idx[0];

      if(close[i] < candOpen)
      {
         double highest = 0.0;
         for(int k = candIdx; k <= i; k++)
            if(close[k] > highest) highest = close[k];

         double top = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && close[k] < open[k])
         {
            top = open[k];
            k--;
         }

         double denom = top - candOpen;
         if(denom != 0.0 && (highest - candOpen) / denom > Tolerance)
         {
            origin_lvl = candOpen;
            origin_idx = candIdx;
            ArrayResize(bear_open, 0); ArrayResize(bear_idx, 0);
            return true;
         }
         else
         {
            RemoveFrontD(bear_open); RemoveFrontI(bear_idx);
         }
      }
      else
         break;
   }
   return false;
}
bool ConfirmBullCISD(const int i, const double &open[], const double &close[],
                      double &origin_lvl, int &origin_idx)
{
   while(ArraySize(bull_open) > 0)
   {
      double candOpen = bull_open[0];
      int    candIdx  = bull_idx[0];

      if(close[i] > candOpen)
      {
         double lowest = close[i];
         for(int k = candIdx; k <= i; k++)
            if(close[k] < lowest) lowest = close[k];

         double bottom = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && close[k] > open[k])
         {
            bottom = open[k];
            k--;
         }

         double denom = candOpen - bottom;
         if(denom != 0.0 && (candOpen - lowest) / denom > Tolerance)
         {
            origin_lvl = candOpen;
            origin_idx = candIdx;
            ArrayResize(bull_open, 0); ArrayResize(bull_idx, 0);
            return true;
         }
         else
         {
            RemoveFrontD(bull_open); RemoveFrontI(bull_idx);
         }
      }
      else
         break;
   }
   return false;
}

void DrawOriginLine(const int origin_idx, const double level, const int i, const datetime &time[], const color clr)
{
   string name = PREFIX_ORIGIN_LINE + IntegerToString(origin_idx) + "_" + IntegerToString(i);
   if(ObjectFind(0, name) >= 0) return;
   ObjectCreate(0, name, OBJ_TREND, 0, time[origin_idx], level, time[i], level);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 3);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
}

void DrawSweepMarker(const int i, const datetime &time[], const double price,
                      const string text, const color clr, const int anchor)
{
   string name = PREFIX_SWEEP + IntegerToString(i);
   if(ObjectFind(0, name) >= 0) return;
   ObjectCreate(0, name, OBJ_TEXT, 0, time[i], price);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

#define STATUS_LABEL_NAME  "CISD_StatusLabel"
#define STATUS_LABEL_NAME2 "CISD_StatusLabel2"

void SetStatusLine(const string name, const int yDistance, const string text, const color clr)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, yDistance);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 11);
      ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

void DisplayStatus()
{
   string trend_text = (g_trend > 0) ? "BULLISH" : (g_trend < 0) ? "BEARISH" : "NONE";
   string cisd_text  = (g_last_cisd_type == 1) ? "Bearish" : (g_last_cisd_type == 2) ? "Bullish" : "none yet";
   color  clr = (g_trend > 0) ? BullColor : (g_trend < 0) ? BearColor : clrSilver;

   string line1 = StringFormat("CISD  SH:%d SL:%d  Trend:%s", ArraySize(sh_startIdx), ArraySize(sl_startIdx), trend_text);
   string line2 = StringFormat("Last CISD: %s%s", cisd_text, g_last_sweep ? " (sweep)" : "");

   SetStatusLine(STATUS_LABEL_NAME,  70, line1, clr);
   SetStatusLine(STATUS_LABEL_NAME2, 90, line2, clr);
}

void ProcessCISDBar(const int i, const int rates_total, const datetime &time[],
                     const double &open[], const double &high[], const double &low[], const double &close[])
{
   int c = i - SwingPeriod;
   if(c >= 0)
   {
      if(CISD_IsPivotHigh(high, c, SwingPeriod, rates_total))
      {
         AddSwingHigh(c, high[c]);
         if(ShowSwingMarkers) SwingHighMarker[c] = high[c];
      }
      if(CISD_IsPivotLow(low, c, SwingPeriod, rates_total))
      {
         AddSwingLow(c, low[c]);
         if(ShowSwingMarkers) SwingLowMarker[c] = low[c];
      }
   }

   bool   wicked_high, wicked_low;
   double wicked_high_level, wicked_low_level;
   ScanSwingHighs(i, time, high, wicked_high, wicked_high_level);
   ScanSwingLows(i, time, low, wicked_low, wicked_low_level);

   if(wicked_high) { g_have_wicked_high = true; g_last_wicked_high_level = wicked_high_level; g_last_wicked_high_time = time[i]; g_last_wicked_high_bar = i; }
   if(wicked_low)  { g_have_wicked_low  = true; g_last_wicked_low_level  = wicked_low_level;  g_last_wicked_low_time  = time[i]; g_last_wicked_low_bar  = i; }

   if(i >= 1)
   {
      if(close[i-1] < open[i-1] && close[i] > open[i])
      {
         InsertFrontI(bear_idx, i);
         InsertFrontD(bear_open, open[i]);
      }
      if(close[i-1] > open[i-1] && close[i] < open[i])
      {
         InsertFrontI(bull_idx, i);
         InsertFrontD(bull_open, open[i]);
      }
   }

   double origin_lvl = 0.0; int origin_idx = 0;
   int cisd = 0;
   if(ConfirmBearCISD(i, open, close, origin_lvl, origin_idx)) cisd = 1;
   double origin_lvl2 = 0.0; int origin_idx2 = 0;
   if(ConfirmBullCISD(i, open, close, origin_lvl2, origin_idx2))
   {
      cisd = 2;
      origin_lvl = origin_lvl2;
      origin_idx = origin_idx2;
   }

   if(cisd == 1)
   {
      g_trend = -1;
      DrawOriginLine(origin_idx, origin_lvl, i, time, BearColor);
      g_last_cisd_type = 1; g_last_cisd_level = origin_lvl; g_last_cisd_time = time[i];
      g_last_sweep = false;
      g_last_cisd_has_swing = (ArraySize(sh_level) > 0);
      g_last_cisd_swing_level = g_last_cisd_has_swing ? sh_level[0] : 0.0;

      if(g_have_wicked_high && (i - g_last_wicked_high_bar) <= LiquidityLookback && close[i] < g_last_wicked_high_level)
      {
         g_last_sweep = true;
         DrawSweepMarker(i, time, high[i], "\x25BC", BearColor, ANCHOR_LOWER);
      }
   }
   if(cisd == 2)
   {
      g_trend = 1;
      DrawOriginLine(origin_idx, origin_lvl, i, time, BullColor);
      g_last_cisd_type = 2; g_last_cisd_level = origin_lvl; g_last_cisd_time = time[i];
      g_last_sweep = false;
      g_last_cisd_has_swing = (ArraySize(sl_level) > 0);
      g_last_cisd_swing_level = g_last_cisd_has_swing ? sl_level[0] : 0.0;

      if(g_have_wicked_low && (i - g_last_wicked_low_bar) <= LiquidityLookback && close[i] > g_last_wicked_low_level)
      {
         g_last_sweep = true;
         DrawSweepMarker(i, time, low[i], "\x25B2", BullColor, ANCHOR_UPPER);
      }
   }
}

void ResetAllCISDState(const int rates_total)
{
   ArrayResize(sh_level,0); ArrayResize(sh_startIdx,0);
   ArrayResize(sl_level,0); ArrayResize(sl_startIdx,0);
   ArrayResize(bear_open,0); ArrayResize(bear_idx,0);
   ArrayResize(bull_open,0); ArrayResize(bull_idx,0);

   g_have_wicked_high = false; g_last_wicked_high_bar = -1;
   g_have_wicked_low  = false; g_last_wicked_low_bar  = -1;
   g_trend = 0;
   g_last_cisd_type = 0; g_last_cisd_level = 0.0; g_last_cisd_time = 0; g_last_sweep = false;
   g_last_cisd_has_swing = false; g_last_cisd_swing_level = 0.0;
   g_confirmed_upto = -1;

   for(int k = 0; k < rates_total; k++)
   {
      SwingHighMarker[k] = EMPTY_VALUE;
      SwingLowMarker[k]  = EMPTY_VALUE;
   }

   ObjectsDeleteAll(0, PREFIX_SWING_HIGH);
   ObjectsDeleteAll(0, PREFIX_SWING_LOW);
   ObjectsDeleteAll(0, PREFIX_ORIGIN_LINE);
   ObjectsDeleteAll(0, PREFIX_SWEEP);
}

void ClearCISDVisuals(const int rates_total)
{
   // Toggle-off cleanup only -- clears what's ON SCREEN (objects + the two
   // marker buffers) but deliberately leaves g_confirmed_upto/sh_level/
   // sl_level/bear_open/bull_open/g_trend untouched, so re-enabling resumes
   // exactly where it left off instead of replaying history from scratch.
   for(int k = 0; k < rates_total; k++)
   {
      SwingHighMarker[k] = EMPTY_VALUE;
      SwingLowMarker[k]  = EMPTY_VALUE;
   }
   ObjectsDeleteAll(0, PREFIX_SWING_HIGH);
   ObjectsDeleteAll(0, PREFIX_SWING_LOW);
   ObjectsDeleteAll(0, PREFIX_ORIGIN_LINE);
   ObjectsDeleteAll(0, PREFIX_SWEEP);
   ObjectDelete(0, STATUS_LABEL_NAME);
   ObjectDelete(0, STATUS_LABEL_NAME2);
}

void RunCISD(const int rates_total, const int prev_calculated, const datetime &time[],
             const double &open[], const double &high[], const double &low[], const double &close[])
{
   if(rates_total < 2*SwingPeriod + 2)
      return;

   bool history_shifted = (time[0] != g_bar0_time);

   if(prev_calculated <= 0 || history_shifted)
      ResetAllCISDState(rates_total);

   g_bar0_time = time[0];

   int last_closed = rates_total - 2;
   int watermark_before = g_confirmed_upto;

   for(int i = g_confirmed_upto + 1; i <= last_closed; i++)
   {
      ProcessCISDBar(i, rates_total, time, open, high, low, close);
      g_confirmed_upto = i;
   }

   if(g_confirmed_upto != watermark_before)
      DisplayStatus();
}

//+==================================================================+
//|                      MAJOR/MINOR -- verbatim                     |
//+==================================================================+

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

bool IsPivotHigh(const double &high[], int c, int PP, int total)
{
   if(c-PP<0 || c+PP>=total) return false;
   double v=high[c];
   for(int k=c-PP;k<=c+PP;k++)
      if(k!=c && high[k]>=v) return false;
   return true;
}
bool IsPivotLow(const double &low[], int c, int PP, int total)
{
   if(c-PP<0 || c+PP>=total) return false;
   double v=low[c];
   for(int k=c-PP;k<=c+PP;k++)
      if(k!=c && low[k]<=v) return false;
   return true;
}

void PushHighType()
{
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastHighValue) ? "HH":"LH") : "H";
   PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
}
void PushLowType()
{
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastLowValue) ? "HL":"LL") : "L";
   PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
}
void ReplaceLastWithHighType()
{
   RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastHighValue) ? "HH":"LH") : "H";
   PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
}
void ReplaceLastWithLowType()
{
   RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastLowValue) ? "HL":"LL") : "L";
   PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
}

void ClassifyPivot(bool hasHigh, bool hasLow, double thisClose)
{
   int N=ArraySize(w_Type);

   if(hasHigh && hasLow)
   {
      if(N==0)
      {
         // Pine: PASS=1 -- both pivots land on an empty sequence; original discards them.
      }
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

void ProcessBar(int i, int rates_total, int PP,
                 const double &high[], const double &low[], const double &close[])
{
   int c=i-PP;
   bool hasHigh=false, hasLow=false;

   if(c>=PP && c+PP<rates_total)
   {
      hasHigh=IsPivotHigh(high,c,PP,rates_total);
      hasLow =IsPivotLow(low,c,PP,rates_total);
   }

   if(hasHigh){ w_LastHighValue=high[c]; w_LastHighIndex=c; }
   if(hasLow) { w_LastLowValue =low[c];  w_LastLowIndex =c; }

   double thisClose=close[i];

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

   int n=ArraySize(w_Value);
   if(n>1)
   {
      double curLastValue=w_Value[n-1];
      string curLastType =w_Type[n-1];
      if(curLastValue!=prevLastValue)
      {
         string prevFamily=(StringLen(prevLastType)>0) ? StringSubstr(prevLastType,StringLen(prevLastType)-1,1) : "";
         string curFamily =StringSubstr(curLastType,StringLen(curLastType)-1,1);
         if(prevFamily!=curFamily)
         {
            PushS(w_TypeAdv,"m"+curLastType); PushD(w_ValueAdv,curLastValue); PushI(w_IndexAdv,w_Index[n-1]);
         }
         else
         {
            int nA=ArraySize(w_ValueAdv);
            if(nA>0){ w_ValueAdv[nA-1]=curLastValue; w_IndexAdv[nA-1]=w_Index[n-1]; }
         }
      }
   }

   if(w_MajorLevelsSet) UpdateMajorLevels(thisClose);
}

void CopyConfirmedToWorking()
{
   ArrayCopy(w_Type,gc_Type);       ArrayCopy(w_Value,gc_Value);       ArrayCopy(w_Index,gc_Index);
   ArrayCopy(w_TypeAdv,gc_TypeAdv); ArrayCopy(w_ValueAdv,gc_ValueAdv); ArrayCopy(w_IndexAdv,gc_IndexAdv);
   w_MajorHighLevel=gc_MajorHighLevel; w_MajorLowLevel=gc_MajorLowLevel;
   w_MajorHighIndex=gc_MajorHighIndex; w_MajorLowIndex=gc_MajorLowIndex;
   w_MajorHighType=gc_MajorHighType;   w_MajorLowType=gc_MajorLowType;
   w_MajorLevelsSet=gc_MajorLevelsSet;
   w_Lock0=gc_Lock0; w_Lock1=gc_Lock1;
   w_LastHighValue=gc_LastHighValue; w_LastLowValue=gc_LastLowValue;
   w_LastHighIndex=gc_LastHighIndex; w_LastLowIndex=gc_LastLowIndex;
   w_MajSupX=gc_MajSupX; w_MajSupY=gc_MajSupY; w_MajResX=gc_MajResX; w_MajResY=gc_MajResY;
   w_MinSupX=gc_MinSupX; w_MinSupY=gc_MinSupY; w_MinResX=gc_MinResX; w_MinResY=gc_MinResY;
}
void CommitWorkingToConfirmed()
{
   ArrayCopy(gc_Type,w_Type);       ArrayCopy(gc_Value,w_Value);       ArrayCopy(gc_Index,w_Index);
   ArrayCopy(gc_TypeAdv,w_TypeAdv); ArrayCopy(gc_ValueAdv,w_ValueAdv); ArrayCopy(gc_IndexAdv,w_IndexAdv);
   gc_MajorHighLevel=w_MajorHighLevel; gc_MajorLowLevel=w_MajorLowLevel;
   gc_MajorHighIndex=w_MajorHighIndex; gc_MajorLowIndex=w_MajorLowIndex;
   gc_MajorHighType=w_MajorHighType;   gc_MajorLowType=w_MajorLowType;
   gc_MajorLevelsSet=w_MajorLevelsSet;
   gc_Lock0=w_Lock0; gc_Lock1=w_Lock1;
   gc_LastHighValue=w_LastHighValue; gc_LastLowValue=w_LastLowValue;
   gc_LastHighIndex=w_LastHighIndex; gc_LastLowIndex=w_LastLowIndex;
   gc_MajSupX=w_MajSupX; gc_MajSupY=w_MajSupY; gc_MajResX=w_MajResX; gc_MajResY=w_MajResY;
   gc_MinSupX=w_MinSupX; gc_MinSupY=w_MinSupY; gc_MinResX=w_MinResX; gc_MinResY=w_MinResY;
}
void ResetAllConfirmed()
{
   ArrayResize(gc_Type,0);    ArrayResize(gc_Value,0);    ArrayResize(gc_Index,0);
   ArrayResize(gc_TypeAdv,0); ArrayResize(gc_ValueAdv,0); ArrayResize(gc_IndexAdv,0);
   gc_MajorHighLevel=0; gc_MajorLowLevel=0;
   gc_MajorHighIndex=-1; gc_MajorLowIndex=-1;
   gc_MajorHighType=""; gc_MajorLowType="";
   gc_MajorLevelsSet=false;
   gc_Lock0=true; gc_Lock1=true;
   gc_LastHighValue=0; gc_LastLowValue=0;
   gc_LastHighIndex=-1; gc_LastLowIndex=-1;
   gc_ConfirmedUpTo=-1;
   gc_MajSupX=-1; gc_MajResX=-1; gc_MinSupX=-1; gc_MinResX=-1;
   gc_MajSupY=0;  gc_MajResY=0;  gc_MinSupY=0;  gc_MinResY=0;

   ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
   ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
   g_drawnMajorSupX=-1000000; g_drawnMajorResX=-1000000; g_drawnMinorSupX=-1000000; g_drawnMinorResX=-1000000;
}

void DrawSRLine(string name, int &lastX, int x, double y, const datetime &time[], int rates_total,
                 color clr, int width, ENUM_LINE_STYLE style)
{
   if(x<0 || x>=rates_total) return;
   if(x==lastX) return;

   datetime t1=time[x];
   datetime t2=t1+PeriodSeconds();

   if(ObjectFind(0,name)<0)
      ObjectCreate(0,name,OBJ_TREND,0,t1,y,t2,y);
   else
   {
      ObjectMove(0,name,0,t1,y);
      ObjectMove(0,name,1,t2,y);
   }
   ObjectSetInteger(0,name,OBJPROP_RAY_RIGHT,true);
   ObjectSetInteger(0,name,OBJPROP_RAY_LEFT,false);
   ObjectSetInteger(0,name,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,name,OBJPROP_WIDTH,width);
   ObjectSetInteger(0,name,OBJPROP_STYLE,style);
   ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,name,OBJPROP_HIDDEN,true);
   ObjectSetInteger(0,name,OBJPROP_BACK,false);

   lastX=x;
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

void DrawFromWorking(const datetime &time[], int rates_total)
{
   if(ShowMajor)
   {
      if(w_MajSupX>=0) DrawSRLine(NAME_MAJOR_SUPPORT,    g_drawnMajorSupX, w_MajSupX, w_MajSupY, time, rates_total, LineColor, MajorWidth, MajorStyle);
      if(w_MajResX>=0) DrawSRLine(NAME_MAJOR_RESISTANCE, g_drawnMajorResX, w_MajResX, w_MajResY, time, rates_total, LineColor, MajorWidth, MajorStyle);
   }
   if(ShowMinor)
   {
      if(w_MinSupX>=0) DrawSRLine(NAME_MINOR_SUPPORT,    g_drawnMinorSupX, w_MinSupX, w_MinSupY, time, rates_total, LineColor, MinorWidth, MinorStyle);
      if(w_MinResX>=0) DrawSRLine(NAME_MINOR_RESISTANCE, g_drawnMinorResX, w_MinResX, w_MinResY, time, rates_total, LineColor, MinorWidth, MinorStyle);
   }
}

void RunMajorMinor(const int rates_total, const int prev_calculated, const datetime &time[],
                    const double &high[], const double &low[], const double &close[])
{
   static bool mm_was_enabled = true;   // matches EnableMajorMinor's own default

   if(!EnableMajorMinor)
   {
      if(mm_was_enabled)
      {
         ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
         ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
         g_drawnMajorSupX=-1000000; g_drawnMajorResX=-1000000; g_drawnMinorSupX=-1000000; g_drawnMinorResX=-1000000;
         mm_was_enabled=false;
      }
      return;
   }

   mm_was_enabled=true;

   if(rates_total < 2*PivotPeriod+2)
      return;

   if(TimeCurrent() - g_last_mm_time < MajorMinorEverySeconds)
      return;
   g_last_mm_time = TimeCurrent();

   if(prev_calculated<=0) ResetAllConfirmed();

   CopyConfirmedToWorking();

   int startBar=gc_ConfirmedUpTo+1;
   if(startBar<0) startBar=0;

   int lastClosedProcessed=-1;

   for(int i=startBar; i<rates_total; i++)
   {
      ProcessBar(i, rates_total, PivotPeriod, high, low, close);
      TrackLatestPositions();

      if(i<rates_total-1)
         lastClosedProcessed=i;
   }

   if(lastClosedProcessed>=0)
   {
      CommitWorkingToConfirmed();
      gc_ConfirmedUpTo=lastClosedProcessed;
   }

   DrawFromWorking(time, rates_total);
}

//+==================================================================+
//|                      Shared OnInit/OnCalculate/OnDeinit           |
//+==================================================================+

int OnInit()
{
   SetIndexBuffer(0, FillUp1, INDICATOR_DATA);
   SetIndexBuffer(1, FillUp2, INDICATOR_DATA);
   SetIndexBuffer(2, FillDn1, INDICATOR_DATA);
   SetIndexBuffer(3, FillDn2, INDICATOR_DATA);
   SetIndexBuffer(4, Z1, INDICATOR_DATA);
   SetIndexBuffer(5, Z2, INDICATOR_DATA);
   SetIndexBuffer(6, Z3, INDICATOR_DATA);
   SetIndexBuffer(7, Z4, INDICATOR_DATA);
   SetIndexBuffer(8, SwingHighMarker, INDICATOR_DATA);
   SetIndexBuffer(9, SwingLowMarker,  INDICATOR_DATA);

   for(int p = 0; p < 8; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   for(int p = 0; p < 2; p++)
   {
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, 0, InpFillColor);
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, 1, InpFillColor);
      if(!InpShowFill)
         PlotIndexSetInteger(p, PLOT_DRAW_TYPE, DRAW_NONE);
   }
   for(int p = 2; p < 6; p++)
   {
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, InpLineColor);
      PlotIndexSetInteger(p, PLOT_LINE_WIDTH, 1);
   }

   PlotIndexSetInteger(6, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetInteger(7, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetInteger(6, PLOT_LINE_COLOR, BearColor); // swing HIGH marked in the bear/red colour
   PlotIndexSetInteger(7, PLOT_LINE_COLOR, BullColor); // swing LOW marked in the bull/green colour

   IndicatorSetString(INDICATOR_SHORTNAME, "Dynamic Zones + CISD + Major/Minor");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   ResetAllConfirmed();

   return(INIT_SUCCEEDED);
}

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   //===================== Dynamic Zones -- togglable =====================
   static bool dz_was_enabled = true;   // matches EnableDynamicZones's own default

   if(!EnableDynamicZones)
   {
      if(dz_was_enabled)
      {
         DZ_ClearAll(rates_total);
         dz_was_enabled = false;
      }
   }
   else
   {
      dz_was_enabled = true;
      RunDynamicZones(rates_total, prev_calculated, time, close);
   }

   //===================== CISD -- togglable =====================
   static bool cisd_was_enabled = true;   // matches EnableCISD's own default

   if(!EnableCISD)
   {
      if(cisd_was_enabled)
      {
         ClearCISDVisuals(rates_total);
         cisd_was_enabled = false;
      }
   }
   else
   {
      cisd_was_enabled = true;
      RunCISD(rates_total, prev_calculated, time, open, high, low, close);
   }

   //===================== Major/Minor -- togglable (handles its own toggle-off internally) =====================
   RunMajorMinor(rates_total, prev_calculated, time, high, low, close);

   return(rates_total);
}

void OnDeinit(const int reason)
{
   ObjectDelete(0, STATUS_LABEL_NAME);
   ObjectDelete(0, STATUS_LABEL_NAME2);
   ObjectDelete(0, NAME_MAJOR_SUPPORT); ObjectDelete(0, NAME_MAJOR_RESISTANCE);
   ObjectDelete(0, NAME_MINOR_SUPPORT); ObjectDelete(0, NAME_MINOR_RESISTANCE);

   if(reason == REASON_REMOVE)
   {
      ObjectsDeleteAll(0, PREFIX_SWING_HIGH);
      ObjectsDeleteAll(0, PREFIX_SWING_LOW);
      ObjectsDeleteAll(0, PREFIX_ORIGIN_LINE);
      ObjectsDeleteAll(0, PREFIX_SWEEP);
   }
}
//+------------------------------------------------------------------+
