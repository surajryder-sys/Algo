//+------------------------------------------------------------------+
//| OB_Volumetric_Detector_v1.mq5                                    |
//| First-draft, from-scratch reimplementation of a volumetric swing- |
//| pivot order block detector, built to compare zone-for-zone        |
//| against a licensed reference indicator with the same input        |
//| contract (Volume Pivot Length, per-direction OB count, wick-based |
//| mitigation). Not derived from any decompiled binary - this is an  |
//| independent implementation of the publicly documented technique: |
//| within the leg leading into a confirmed swing pivot, the highest- |
//| volume candle becomes the order block's origin.                   |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0
#property version   "1.00"

input group "Detection"
input int    InpVolumePivotLength   = 5;     // Volume Pivot Length (bars each side of a swing)
input int    InpBullishOBCount      = 4;     // Bullish OB zones to keep
input int    InpBearishOBCount      = 4;     // Bearish OB zones to keep
input int    InpMaxHistoryBars      = 2000;  // Max closed bars scanned per full rescan

enum ENUM_MITIGATION_METHOD
{
   MIT_WICK  = 0,
   MIT_CLOSE = 1
};
input ENUM_MITIGATION_METHOD InpMitigationMethod = MIT_WICK;
input int    InpRetestSkipBars      = 1;     // Bars to skip right after origin before mitigation can count

input group "Colors"
input color            InpBullishOBColor     = clrGreen;
input color            InpBearishOBColor     = clrMaroon;
input color            InpAverageColor       = clrGray;
input ENUM_LINE_STYLE   InpAverageLineStyle   = STYLE_DASH;
input int               InpAverageLineWidth   = 1;
input bool              InpDrawOutline        = true;
input bool              InpFreezeAtMitigation = false; // stop extending a zone once mitigated

input group "Publishing"
input bool   InpPublishGlobalVariables = true;
input string InpGlobalVariablePrefix   = "OBSTATE"; // matches OB_MTF_FreshTrader_EA's expected GV prefix
input bool   InpShowPanel              = true;

input string InpObjectPrefix = "OBVolDet";

//--- zone record
struct OBZone
{
   bool     bullish;
   double   high;
   double   low;
   datetime origin_time;
   long     origin_volume;
   bool     mitigated;
   datetime mitigated_time;
   string   name;
};

OBZone   g_bull[];
OBZone   g_bear[];
datetime g_last_closed_bar_time = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "OB Volumetric Detector (" + IntegerToString(InpVolumePivotLength) + ")");
   EventSetTimer(1);
   g_last_closed_bar_time = 0;
   Recalculate();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, InpObjectPrefix);
   Comment("");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   // Cheap per-second cosmetic update: extend un-mitigated zones' right
   // edge to "now" without repeating the full historical rescan.
   ExtendActiveZones();
}

//+------------------------------------------------------------------+
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
   datetime latest_closed = iTime(_Symbol, _Period, 1);
   if(latest_closed != g_last_closed_bar_time)
   {
      g_last_closed_bar_time = latest_closed;
      Recalculate();
   }
   else
   {
      ExtendActiveZones();
   }
   return rates_total;
}

//+------------------------------------------------------------------+
bool IsPivotHigh(const int shift, const int length)
{
   double h = iHigh(_Symbol, _Period, shift);
   for(int k = shift - length; k <= shift + length; k++)
   {
      if(k == shift) continue;
      if(iHigh(_Symbol, _Period, k) > h)
         return false;
   }
   return true;
}

//+------------------------------------------------------------------+
bool IsPivotLow(const int shift, const int length)
{
   double l = iLow(_Symbol, _Period, shift);
   for(int k = shift - length; k <= shift + length; k++)
   {
      if(k == shift) continue;
      if(iLow(_Symbol, _Period, k) < l)
         return false;
   }
   return true;
}

//+------------------------------------------------------------------+
// from_shift is the more recent (smaller) end of the leg, to_shift the
// older (larger) end. Returns the shift of the highest-tick-volume bar.
int FindMaxVolumeShift(const int from_shift, const int to_shift)
{
   int  best_shift  = from_shift;
   long best_volume = iTickVolume(_Symbol, _Period, from_shift);
   for(int s = from_shift + 1; s <= to_shift; s++)
   {
      long v = iTickVolume(_Symbol, _Period, s);
      if(v > best_volume)
      {
         best_volume = v;
         best_shift  = s;
      }
   }
   return best_shift;
}

//+------------------------------------------------------------------+
bool ZoneExists(const OBZone &arr[], const datetime origin_time)
{
   for(int i = 0; i < ArraySize(arr); i++)
      if(arr[i].origin_time == origin_time)
         return true;
   return false;
}

//+------------------------------------------------------------------+
void AddZone(OBZone &arr[], const bool bullish, const int origin_shift)
{
   datetime origin_time = iTime(_Symbol, _Period, origin_shift);
   if(ZoneExists(arr, origin_time))
      return;

   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n].bullish        = bullish;
   arr[n].high           = iHigh(_Symbol, _Period, origin_shift);
   arr[n].low            = iLow(_Symbol, _Period, origin_shift);
   arr[n].origin_time    = origin_time;
   arr[n].origin_volume  = iTickVolume(_Symbol, _Period, origin_shift);
   arr[n].mitigated      = false;
   arr[n].mitigated_time = 0;
   arr[n].name           = InpObjectPrefix + "_" + (bullish ? "BULL_" : "BEAR_") + IntegerToString((long)origin_time);
}

//+------------------------------------------------------------------+
void TrimZones(OBZone &arr[], const int max_count)
{
   int n = ArraySize(arr);
   if(n <= max_count)
      return;
   int excess = n - max_count;
   for(int i = 0; i < excess; i++)
   {
      ObjectDelete(0, arr[i].name);
      ObjectDelete(0, arr[i].name + "_AVG");
   }
   for(int i = 0; i < n - excess; i++)
      arr[i] = arr[i + excess];
   ArrayResize(arr, n - excess);
}

//+------------------------------------------------------------------+
void UpdateMitigation(OBZone &arr[])
{
   for(int i = 0; i < ArraySize(arr); i++)
   {
      if(arr[i].mitigated)
         continue;

      int origin_shift = iBarShift(_Symbol, _Period, arr[i].origin_time, false);
      if(origin_shift < 0)
         continue;

      int start_shift = origin_shift - MathMax(1, InpRetestSkipBars);
      for(int s = start_shift; s >= 1; s--)
      {
         bool touched;
         if(InpMitigationMethod == MIT_WICK)
         {
            double sh = iHigh(_Symbol, _Period, s);
            double sl = iLow(_Symbol, _Period, s);
            touched = (sh >= arr[i].low && sl <= arr[i].high);
         }
         else
         {
            double c = iClose(_Symbol, _Period, s);
            touched = (c >= arr[i].low && c <= arr[i].high);
         }

         if(touched)
         {
            arr[i].mitigated      = true;
            arr[i].mitigated_time = iTime(_Symbol, _Period, s);
            break;
         }
      }
   }
}

//+------------------------------------------------------------------+
void DrawZone(const OBZone &z)
{
   color    clr        = (z.bullish ? InpBullishOBColor : InpBearishOBColor);
   datetime right_edge = (z.mitigated && InpFreezeAtMitigation) ? z.mitigated_time : TimeCurrent();
   if(right_edge <= z.origin_time)
      right_edge = z.origin_time + PeriodSeconds(_Period);

   if(ObjectFind(0, z.name) < 0)
   {
      ObjectCreate(0, z.name, OBJ_RECTANGLE, 0, z.origin_time, z.high, right_edge, z.low);
      ObjectSetInteger(0, z.name, OBJPROP_BACK, true);
      ObjectSetInteger(0, z.name, OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(0, z.name, OBJPROP_TIME, 1, right_edge);
   ObjectSetDouble(0, z.name, OBJPROP_PRICE, 0, z.high);
   ObjectSetDouble(0, z.name, OBJPROP_PRICE, 1, z.low);
   ObjectSetInteger(0, z.name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, z.name, OBJPROP_FILL, true);
   ObjectSetInteger(0, z.name, OBJPROP_STYLE, InpDrawOutline ? STYLE_SOLID : STYLE_DOT);
   ObjectSetInteger(0, z.name, OBJPROP_WIDTH, 1);

   string avg_name = z.name + "_AVG";
   double mid       = (z.high + z.low) / 2.0;
   if(ObjectFind(0, avg_name) < 0)
   {
      ObjectCreate(0, avg_name, OBJ_TREND, 0, z.origin_time, mid, right_edge, mid);
      ObjectSetInteger(0, avg_name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, avg_name, OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(0, avg_name, OBJPROP_TIME, 1, right_edge);
   ObjectSetDouble(0, avg_name, OBJPROP_PRICE, 0, mid);
   ObjectSetDouble(0, avg_name, OBJPROP_PRICE, 1, mid);
   ObjectSetInteger(0, avg_name, OBJPROP_COLOR, InpAverageColor);
   ObjectSetInteger(0, avg_name, OBJPROP_STYLE, InpAverageLineStyle);
   ObjectSetInteger(0, avg_name, OBJPROP_WIDTH, InpAverageLineWidth);
}

//+------------------------------------------------------------------+
void ExtendActiveZones()
{
   for(int i = 0; i < ArraySize(g_bull); i++)
      DrawZone(g_bull[i]);
   for(int i = 0; i < ArraySize(g_bear); i++)
      DrawZone(g_bear[i]);
}

//+------------------------------------------------------------------+
string GVBase()
{
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0) tf_minutes = (int)_Period;
   return InpGlobalVariablePrefix + "_" + _Symbol + "_" + IntegerToString(tf_minutes);
}

//+------------------------------------------------------------------+
void PublishState()
{
   if(!InpPublishGlobalVariables)
      return;

   string base = GVBase();

   bool has_bull = ArraySize(g_bull) > 0;
   bool has_bear = ArraySize(g_bear) > 0;
   OBZone latest_bull, latest_bear;
   if(has_bull) latest_bull = g_bull[ArraySize(g_bull) - 1];
   if(has_bear) latest_bear = g_bear[ArraySize(g_bear) - 1];

   bool has_latest = has_bull || has_bear;
   bool use_bull_as_latest = has_bull && (!has_bear || latest_bull.origin_time >= latest_bear.origin_time);
   OBZone latest;
   if(has_latest)
      latest = use_bull_as_latest ? latest_bull : latest_bear;

   GlobalVariableSet(base + "_BIAS", has_latest ? (latest.bullish ? 1.0 : -1.0) : 0.0);
   GlobalVariableSet(base + "_LATEST_HIGH", has_latest ? latest.high : 0.0);
   GlobalVariableSet(base + "_LATEST_LOW", has_latest ? latest.low : 0.0);
   GlobalVariableSet(base + "_LATEST_VIRGIN", has_latest ? (latest.mitigated ? 0.0 : 1.0) : 0.0);
   GlobalVariableSet(base + "_LATEST_TIME", has_latest ? (double)latest.origin_time : 0.0);
   GlobalVariableSet(base + "_LATEST_DETECTED_TIME", has_latest ? (double)latest.origin_time : 0.0);
   GlobalVariableSet(base + "_LATEST_DETECTED_PRICE", has_latest ? (latest.high + latest.low) / 2.0 : 0.0);

   GlobalVariableSet(base + "_BULL_HIGH", has_bull ? latest_bull.high : 0.0);
   GlobalVariableSet(base + "_BULL_LOW", has_bull ? latest_bull.low : 0.0);
   GlobalVariableSet(base + "_BULL_VIRGIN", has_bull ? (latest_bull.mitigated ? 0.0 : 1.0) : 0.0);

   GlobalVariableSet(base + "_BEAR_HIGH", has_bear ? latest_bear.high : 0.0);
   GlobalVariableSet(base + "_BEAR_LOW", has_bear ? latest_bear.low : 0.0);
   GlobalVariableSet(base + "_BEAR_VIRGIN", has_bear ? (latest_bear.mitigated ? 0.0 : 1.0) : 0.0);

   GlobalVariableSet(base + "_UPDATED", (double)TimeCurrent());
}

//+------------------------------------------------------------------+
void UpdatePanel()
{
   if(!InpShowPanel)
   {
      Comment("");
      return;
   }

   string text = "OB VOLUMETRIC DETECTOR (draft v1) | " + _Symbol + " " + EnumToString(_Period) + "\n";
   text += "Volume Pivot Length=" + IntegerToString(InpVolumePivotLength) +
           " | Mitigation=" + (InpMitigationMethod == MIT_WICK ? "WICK" : "CLOSE") + "\n\n";

   text += "BULLISH OBs (" + IntegerToString(ArraySize(g_bull)) + ")\n";
   for(int i = ArraySize(g_bull) - 1; i >= 0; i--)
      text += "  " + TimeToString(g_bull[i].origin_time, TIME_DATE | TIME_MINUTES) +
              " | H=" + DoubleToString(g_bull[i].high, _Digits) +
              " L=" + DoubleToString(g_bull[i].low, _Digits) +
              " | Virgin=" + (g_bull[i].mitigated ? "false" : "true") +
              " | Vol=" + IntegerToString((int)g_bull[i].origin_volume) + "\n";

   text += "\nBEARISH OBs (" + IntegerToString(ArraySize(g_bear)) + ")\n";
   for(int i = ArraySize(g_bear) - 1; i >= 0; i--)
      text += "  " + TimeToString(g_bear[i].origin_time, TIME_DATE | TIME_MINUTES) +
              " | H=" + DoubleToString(g_bear[i].high, _Digits) +
              " L=" + DoubleToString(g_bear[i].low, _Digits) +
              " | Virgin=" + (g_bear[i].mitigated ? "false" : "true") +
              " | Vol=" + IntegerToString((int)g_bear[i].origin_volume) + "\n";

   Comment(text);
}

//+------------------------------------------------------------------+
void Recalculate()
{
   int bars = iBars(_Symbol, _Period);
   int L    = MathMax(1, InpVolumePivotLength);

   int max_shift = MathMin(bars - 1 - L, InpMaxHistoryBars);
   int min_shift = L + 1;
   if(max_shift < min_shift)
      return;

   int    last_pivot_high_shift = -1;
   double last_pivot_high_price = 0.0;
   int    last_pivot_low_shift  = -1;
   double last_pivot_low_price  = 0.0;

   // A candidate is the leg leading INTO a fresh pivot (the standard OB
   // location), but it is only turned into an actual zone once price later
   // closes back beyond that leg's own opposite extreme - i.e. a genuine
   // break of structure. A raw pivot alone is not enough: that fires on
   // every micro-swing inside flat/ranging price and floods the chart with
   // noise zones a real displacement never validated.
   bool   pending_bull_active = false;
   int    pending_bull_shift  = -1;
   double pending_bull_level  = 0.0; // confirms once close > this (the leg's own high)

   bool   pending_bear_active = false;
   int    pending_bear_shift  = -1;
   double pending_bear_level  = 0.0; // confirms once close < this (the leg's own low)

   // Scan chronologically: s decreases from the oldest usable candidate to
   // the most recently confirmable one.
   for(int s = max_shift; s >= min_shift; s--)
   {
      if(IsPivotHigh(s, L))
      {
         // Up-leg into this new high is the candidate bearish OB leg.
         if(last_pivot_low_shift > s)
         {
            pending_bear_active = true;
            pending_bear_shift  = FindMaxVolumeShift(s, last_pivot_low_shift);
            pending_bear_level  = last_pivot_low_price;
         }
         last_pivot_high_shift = s;
         last_pivot_high_price = iHigh(_Symbol, _Period, s);
      }

      if(IsPivotLow(s, L))
      {
         // Down-leg into this new low is the candidate bullish OB leg.
         if(last_pivot_high_shift > s)
         {
            pending_bull_active = true;
            pending_bull_shift  = FindMaxVolumeShift(s, last_pivot_high_shift);
            pending_bull_level  = last_pivot_high_price;
         }
         last_pivot_low_shift = s;
         last_pivot_low_price = iLow(_Symbol, _Period, s);
      }

      double c = iClose(_Symbol, _Period, s);

      if(pending_bull_active && c > pending_bull_level)
      {
         AddZone(g_bull, true, pending_bull_shift);
         pending_bull_active = false;
      }

      if(pending_bear_active && c < pending_bear_level)
      {
         AddZone(g_bear, false, pending_bear_shift);
         pending_bear_active = false;
      }
   }

   UpdateMitigation(g_bull);
   UpdateMitigation(g_bear);

   TrimZones(g_bull, InpBullishOBCount);
   TrimZones(g_bear, InpBearishOBCount);

   ExtendActiveZones();
   PublishState();
   UpdatePanel();
}
//+------------------------------------------------------------------+
