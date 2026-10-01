//+------------------------------------------------------------------+
//| OB_Detector_v1.07.mq5                                            |
//| Volume-pivot order block detector.                               |
//|                                                                  |
//| Rules (per closed bar i, c = i - Length = candidate OB bar):     |
//|  upper/lower = highest high / lowest low of the last Length bars |
//|  os = 0 if high[c] > upper, 1 if low[c] < lower, else unchanged  |
//|  volume pivot high at c (Length bars each side) ->               |
//|    os == 1 : BULL OB  top = hl2[c],  bottom = low[c]             |
//|    os == 0 : BEAR OB  top = high[c], bottom = hl2[c]             |
//|  Mitigation (Wick: lowest low / highest high of last Length bars |
//|              Close: lowest / highest close of last Length bars): |
//|    bull OB removed when target < bottom                          |
//|    bear OB removed when target > top                             |
//|  The newest N unmitigated OBs per side are drawn, extended right.|
//|                                                                  |
//| OB formation + mitigation: candle close only.                    |
//| Retest: LIVE touch -- the first tick after the OB formed where   |
//| price reaches the zone (bull: low <= top, bear: high >= bottom). |
//| Only the first retest of each zone is recorded.                  |
//|                                                                  |
//| Times (all server time):                                         |
//|  OB time      = open time of the OB candle (bar c)               |
//|  Formed time  = close time of bar i, the candle that confirmed   |
//|                 it (time[i] + period)                            |
//|  Retest time  = server time of the touching tick when seen live. |
//|                 For touches already in history when the          |
//|                 indicator loads, the exact tick is unknown and   |
//|                 the touching candle's open time is used instead. |
//|                 0 = not retested yet.                            |
//|                                                                  |
//| EA access -- iCustom(sym, tf, "OB_Detector_v1.07",               |
//|   Length, VolumeSource, Mitigation, DrawObjects=false)           |
//|  then CopyBuffer(handle, buffer, 0, 1, v) for the live bar, or   |
//|  shift 1 for the last closed bar. Per side, 3 slots (0 = newest  |
//|  active zone), 5 buffers per slot:                               |
//|    bull slot s: 0+5s top, 1+5s bottom, 2+5s OB time,             |
//|                 3+5s formed time, 4+5s retest time               |
//|    bear slot s: 15+5s top, 16+5s bottom, 17+5s OB time,          |
//|                 18+5s formed time, 19+5s retest time             |
//|  Empty slot = EMPTY_VALUE in all five. Times are datetime cast   |
//|  to double. A new OB = slot 0's formed time changed.             |
//|                                                                  |
//| v1.07: fixed object prefix OBD_<Length>_ (was random per load,   |
//| so zones saved in the chart profile by an earlier load -- e.g.   |
//| on a terminal restart -- were never deleted and stayed on the    |
//| chart). On load, every OBD_<Length>_ object left on the chart is |
//| removed (also v1.06's random-prefix leftovers). Instances with   |
//| DrawObjects=false (EA background copies) never delete objects.   |
//|                                                                  |
//| v1.06 (from v1.05): retest is live-touch (was closed candle),    |
//| first retest only (every-touch mode dropped); formed / retest    |
//| times published per zone in 30 EA-readable buffers (replaces the |
//| old 8); DrawObjects input so an EA loading several timeframes    |
//| doesn't paint its chart. CSV export (Pine check) unchanged.      |
//+------------------------------------------------------------------+
#property version   "1.07"
#property indicator_chart_window
#property indicator_buffers 30
#property indicator_plots   30

#define OB_SLOTS  3
#define OB_FIELDS 5

enum ENUM_OB_MITIGATION
  {
   MIT_WICK  = 0, // Wick
   MIT_CLOSE = 1  // Close
  };

input int                 InpLength       = 5;                // Volume Pivot Length
input ENUM_APPLIED_VOLUME InpVolume       = VOLUME_TICK;      // Volume Source
input ENUM_OB_MITIGATION  InpMitigation   = MIT_WICK;         // Mitigation Method
input bool                InpDrawObjects  = true;             // Draw Zones (false when used by an EA)
input int                 InpBullExtLast  = 3;                // Bullish OBs Shown
input color               InpBullColor    = C'0,55,0';        // Bullish OB Color
input int                 InpBearExtLast  = 3;                // Bearish OBs Shown
input color               InpBearColor    = C'70,0,0';        // Bearish OB Color
input color               InpAvgColor     = clrGray;          // Average Color
input ENUM_LINE_STYLE     InpLineStyle    = STYLE_DASH;       // Average Line Style
input int                 InpLineWidth    = 1;                // Average Line Width (dash/dot need 1)
input bool                InpDrawOutline  = false;            // Draw Outline (false = filled box)
input bool                InpShowRetests  = true;             // Show Retest Dots
input color               InpRetestColor  = clrYellow;        // Retest Dot Color
input bool                InpExportCsv    = false;            // Export zones to CSV (Common Files)

struct OBZone
  {
   double   top;
   double   btm;
   double   avg;
   datetime left;       // OB candle open time
   datetime formedBar;  // open time of the confirming candle
   datetime formed;     // close time of the confirming candle
   datetime retest;     // first retest time, 0 = none
   datetime retestBar;  // open time of the retest candle (dot position)
  };

struct SlotBuf
  {
   double v[];
  };

SlotBuf g_buf[2 * OB_SLOTS * OB_FIELDS];

OBZone g_bull[];   // index 0 = newest
OBZone g_bear[];
int    g_os   = 0;
int    g_next = 0; // next bar index (chronological) to process
string g_prefix;
int    g_csv = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpLength < 1 || InpBullExtLast < 1 || InpBearExtLast < 1 || InpLineWidth < 1)
      return INIT_PARAMETERS_INCORRECT;

   string fld[OB_FIELDS] = {"Top", "Bottom", "OB Time", "Formed Time", "Retest Time"};
   for(int b = 0; b < 2 * OB_SLOTS * OB_FIELDS; b++)
     {
      SetIndexBuffer(b, g_buf[b].v, INDICATOR_DATA);
      PlotIndexSetInteger(b, PLOT_DRAW_TYPE, DRAW_NONE);
      PlotIndexSetDouble(b, PLOT_EMPTY_VALUE, EMPTY_VALUE);
      int side = b / (OB_SLOTS * OB_FIELDS), slot = (b / OB_FIELDS) % OB_SLOTS;
      PlotIndexSetString(b, PLOT_LABEL, (side == 0 ? "Bull" : "Bear") + IntegerToString(slot) + " " + fld[b % OB_FIELDS]);
     }

   g_prefix = "OBD_" + IntegerToString(InpLength) + "_";
   if(InpDrawObjects)
      ObjectsDeleteAll(0, g_prefix);   // leftovers from an earlier load (incl. v1.06 random prefixes)
   IndicatorSetString(INDICATOR_SHORTNAME, "Order Block Detector (" + IntegerToString(InpLength) + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(!InpDrawObjects)
      return;
   ObjectsDeleteAll(0, g_prefix);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
void Unshift(OBZone &arr[], const OBZone &z)
  {
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1, 64);
   for(int k = n; k > 0; k--)
      arr[k] = arr[k - 1];
   arr[0] = z;
  }

//+------------------------------------------------------------------+
// Stamps the first retest of every eligible zone touched by a candle
// with this high/low. Returns true if any zone got a new retest.
bool CheckRetests(OBZone &arr[], const bool bull, const datetime barTime,
                  const double hi, const double lo, const datetime stamp)
  {
   bool any = false;
   int  n = ArraySize(arr);
   for(int k = 0; k < n; k++)
     {
      if(arr[k].retest != 0 || arr[k].formedBar >= barTime)
         continue;
      bool touch = bull ? (lo <= arr[k].top) : (hi >= arr[k].btm);
      if(!touch)
         continue;
      arr[k].retest    = stamp;
      arr[k].retestBar = barTime;
      any = true;
     }
   return any;
  }

//+------------------------------------------------------------------+
// Removes every mitigated zone; returns true if any was removed.
bool RemoveMitigated(OBZone &arr[], const double target, const bool bull, const datetime now)
  {
   bool mitigated = false;
   int  n = ArraySize(arr);
   int  w = 0;
   for(int k = 0; k < n; k++)
     {
      bool hit = bull ? (target < arr[k].btm) : (target > arr[k].top);
      if(hit)
        {
         mitigated = true;
         if(g_csv != INVALID_HANDLE)
            FileWrite(g_csv, "MIT", bull ? "BULL" : "BEAR", TimeToString(arr[k].left, TIME_DATE | TIME_SECONDS),
                      TimeToString(now, TIME_DATE | TIME_SECONDS), "", "");
         continue;
        }
      if(w != k)
         arr[w] = arr[k];
      w++;
     }
   if(w != n)
      ArrayResize(arr, w, 64);
   return mitigated;
  }

//+------------------------------------------------------------------+
// Pivot high at c: strictly above the Length bars to its left,
// greater-or-equal to the Length bars to its right.
bool IsVolumePivotHigh(const long &vol[], const int c, const int len)
  {
   long v = vol[c];
   for(int j = 1; j <= len; j++)
     {
      if(vol[c - j] >= v)
         return false;
      if(vol[c + j] > v)
         return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
void WriteSlots(const int i)
  {
   for(int side = 0; side < 2; side++)
     {
      int n = side == 0 ? ArraySize(g_bull) : ArraySize(g_bear);
      for(int s = 0; s < OB_SLOTS; s++)
        {
         int b = (side * OB_SLOTS + s) * OB_FIELDS;
         if(s >= n)
           {
            for(int f = 0; f < OB_FIELDS; f++)
               g_buf[b + f].v[i] = EMPTY_VALUE;
            continue;
           }
         OBZone z;
         if(side == 0)
            z = g_bull[s];
         else
            z = g_bear[s];
         g_buf[b + 0].v[i] = z.top;
         g_buf[b + 1].v[i] = z.btm;
         g_buf[b + 2].v[i] = (double)z.left;
         g_buf[b + 3].v[i] = (double)z.formed;
         g_buf[b + 4].v[i] = (double)z.retest;
        }
     }
  }

//+------------------------------------------------------------------+
void ProcessBar(const int i,
                const datetime &time[], const double &high[],
                const double &low[], const double &close[], const long &vol[])
  {
   int len = InpLength;
   if(i < len)
     {
      WriteSlots(i);
      return;   // (Pine: high[length] is na before bar Length, so os can't change)
     }

   int    c     = i - len;
   double upper = high[i], lower = low[i];
   double hiC   = close[i], loC  = close[i];
   for(int j = 1; j < len; j++)
     {
      upper = MathMax(upper, high[i - j]);
      lower = MathMin(lower, low[i - j]);
      hiC   = MathMax(hiC, close[i - j]);
      loC   = MathMin(loC, close[i - j]);
     }
   double targetBull = (InpMitigation == MIT_CLOSE) ? loC : lower;
   double targetBear = (InpMitigation == MIT_CLOSE) ? hiC : upper;

   if(high[c] > upper)
      g_os = 0;
   else
      if(low[c] < lower)
         g_os = 1;

   // Retests on this candle for zones formed earlier -- before mitigation,
   // since a live touch always happens before the close that mitigates.
   // No-op for zones already stamped live while this candle was forming.
   CheckRetests(g_bull, true,  time[i], high[i], low[i], time[i]);
   CheckRetests(g_bear, false, time[i], high[i], low[i], time[i]);

   if(c >= len && IsVolumePivotHigh(vol, c, len))
     {
      double hl2 = (high[c] + low[c]) / 2.0;
      OBZone z;
      z.left      = time[c];
      z.formedBar = time[i];
      z.formed    = time[i] + (datetime)PeriodSeconds();
      z.retest    = 0;
      z.retestBar = 0;
      if(g_os == 1)
        {
         z.top = hl2;
         z.btm = low[c];
         z.avg = (z.top + z.btm) / 2.0;
         Unshift(g_bull, z);
         if(g_csv != INVALID_HANDLE)
            FileWrite(g_csv, "FORM", "BULL", TimeToString(z.left, TIME_DATE | TIME_SECONDS),
                      TimeToString(z.formedBar, TIME_DATE | TIME_SECONDS),
                      DoubleToString(z.top, _Digits + 1), DoubleToString(z.btm, _Digits));
        }
      else
        {
         z.top = high[c];
         z.btm = hl2;
         z.avg = (z.top + z.btm) / 2.0;
         Unshift(g_bear, z);
         if(g_csv != INVALID_HANDLE)
            FileWrite(g_csv, "FORM", "BEAR", TimeToString(z.left, TIME_DATE | TIME_SECONDS),
                      TimeToString(z.formedBar, TIME_DATE | TIME_SECONDS),
                      DoubleToString(z.top, _Digits), DoubleToString(z.btm, _Digits + 1));
        }
     }

   RemoveMitigated(g_bull, targetBull, true, time[i]);
   RemoveMitigated(g_bear, targetBear, false, time[i]);

   WriteSlots(i);
  }

//+------------------------------------------------------------------+
void DrawSide(const OBZone &arr[], const int ext, const string side,
              const color boxCss, const datetime right)
  {
   int n = ArraySize(arr);
   for(int k = 0; k < ext; k++)
     {
      string boxName = g_prefix + side + "_Box_" + IntegerToString(k);
      string lvlName = g_prefix + side + "_Avg_" + IntegerToString(k);
      string dotName = g_prefix + side + "_Dot_" + IntegerToString(k);
      if(k >= n)
        {
         ObjectDelete(0, boxName);
         ObjectDelete(0, lvlName);
         ObjectDelete(0, dotName);
         continue;
        }

      if(ObjectFind(0, boxName) < 0)
        {
         ObjectCreate(0, boxName, OBJ_RECTANGLE, 0, 0, 0, 0, 0);
         ObjectSetInteger(0, boxName, OBJPROP_BACK, true);
         ObjectSetInteger(0, boxName, OBJPROP_STYLE, STYLE_SOLID);
         ObjectSetInteger(0, boxName, OBJPROP_WIDTH, 1);
         ObjectSetInteger(0, boxName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, boxName, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(0, boxName, OBJPROP_FILL, !InpDrawOutline);
      ObjectSetInteger(0, boxName, OBJPROP_COLOR, boxCss);
      ObjectSetInteger(0, boxName, OBJPROP_TIME, 0, arr[k].left);
      ObjectSetDouble(0, boxName, OBJPROP_PRICE, 0, arr[k].top);
      ObjectSetInteger(0, boxName, OBJPROP_TIME, 1, right);
      ObjectSetDouble(0, boxName, OBJPROP_PRICE, 1, arr[k].btm);

      if(ObjectFind(0, lvlName) < 0)
        {
         ObjectCreate(0, lvlName, OBJ_TREND, 0, 0, 0, 0, 0);
         ObjectSetInteger(0, lvlName, OBJPROP_RAY_RIGHT, true);
         ObjectSetInteger(0, lvlName, OBJPROP_BACK, false);
         ObjectSetInteger(0, lvlName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, lvlName, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(0, lvlName, OBJPROP_COLOR, InpAvgColor);
      ObjectSetInteger(0, lvlName, OBJPROP_STYLE, InpLineStyle);
      ObjectSetInteger(0, lvlName, OBJPROP_WIDTH, InpLineWidth);
      ObjectSetInteger(0, lvlName, OBJPROP_TIME, 0, arr[k].left);
      ObjectSetDouble(0, lvlName, OBJPROP_PRICE, 0, arr[k].avg);
      ObjectSetInteger(0, lvlName, OBJPROP_TIME, 1, arr[k].left + PeriodSeconds());
      ObjectSetDouble(0, lvlName, OBJPROP_PRICE, 1, arr[k].avg);

      if(!InpShowRetests || arr[k].retest == 0)
        {
         ObjectDelete(0, dotName);
         continue;
        }
      bool bull = (side == "Bull");
      if(ObjectFind(0, dotName) < 0)
        {
         ObjectCreate(0, dotName, OBJ_ARROW, 0, 0, 0);
         ObjectSetInteger(0, dotName, OBJPROP_ARROWCODE, 159);
         ObjectSetInteger(0, dotName, OBJPROP_WIDTH, 1);
         ObjectSetInteger(0, dotName, OBJPROP_BACK, false);
         ObjectSetInteger(0, dotName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, dotName, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(0, dotName, OBJPROP_COLOR, InpRetestColor);
      ObjectSetInteger(0, dotName, OBJPROP_ANCHOR, bull ? ANCHOR_BOTTOM : ANCHOR_TOP);
      ObjectSetInteger(0, dotName, OBJPROP_TIME, 0, arr[k].retestBar);
      ObjectSetDouble(0, dotName, OBJPROP_PRICE, 0, bull ? arr[k].top : arr[k].btm);
     }
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
   if(prev_calculated == 0)
     {
      ArrayResize(g_bull, 0, 64);
      ArrayResize(g_bear, 0, 64);
      g_os   = 0;
      g_next = 0;
      if(InpDrawObjects)
         ObjectsDeleteAll(0, g_prefix);
     }

   int lastClosed = rates_total - 2;
   if(lastClosed < 0)
      return rates_total;

   bool changed = (prev_calculated == 0);
   if(prev_calculated == 0 && InpExportCsv)
     {
      string fn = "OBD_" + _Symbol + "_" + StringSubstr(EnumToString(_Period), 7) + ".csv";
      g_csv = FileOpen(fn, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
      if(g_csv != INVALID_HANDLE)
        {
         FileWrite(g_csv, "#first_bar", TimeToString(time[0], TIME_DATE | TIME_SECONDS),
                   "#last_closed", TimeToString(time[lastClosed], TIME_DATE | TIME_SECONDS), "", "");
         FileWrite(g_csv, "event", "side", "ob_time", "event_time", "top", "btm");
        }
     }
   for(int i = g_next; i <= lastClosed; i++)
     {
      if(InpVolume == VOLUME_REAL)
         ProcessBar(i, time, high, low, close, volume);
      else
         ProcessBar(i, time, high, low, close, tick_volume);
      changed = true;
     }
   if(g_csv != INVALID_HANDLE)
     {
      FileClose(g_csv);
      g_csv = INVALID_HANDLE;
     }
   if(lastClosed + 1 > g_next)
      g_next = lastClosed + 1;

   // Forming candle: live retest check on every tick. On the first load
   // the touch may predate the indicator, so it gets the candle's open time.
   int      f     = rates_total - 1;
   datetime stamp = (prev_calculated == 0) ? time[f] : TimeCurrent();
   if(CheckRetests(g_bull, true,  time[f], high[f], low[f], stamp))
      changed = true;
   if(CheckRetests(g_bear, false, time[f], high[f], low[f], stamp))
      changed = true;
   WriteSlots(f);

   if(changed && InpDrawObjects)
     {
      datetime farRight = time[f] + (datetime)PeriodSeconds() * 10000;
      DrawSide(g_bull, InpBullExtLast, "Bull", InpBullColor, farRight);
      DrawSide(g_bear, InpBearExtLast, "Bear", InpBearColor, farRight);
      ChartRedraw();
     }
   return rates_total;
  }
//+------------------------------------------------------------------+
