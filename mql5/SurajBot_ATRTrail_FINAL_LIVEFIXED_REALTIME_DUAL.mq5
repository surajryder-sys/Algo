//+------------------------------------------------------------------+
//|   SurajBot_ATRTrail_FINAL_LIVEFIXED_REALTIME                     |
//|   Now computes TWO independent ATR trailing stop lines           |
//|   (ATR2/KeyValue2 and ATR300/KeyValue2) instead of two copies    |
//|                                                                    |
//|   Bridge publish added for V4's MT5-side execution engine (M5/   |
//|   M3/M1): writes ATRSTATE_DUAL_<symbol>_<tf_minutes>.json to the |
//|   same Common Files bridge folder OB_ATR_Bridge_Indicator uses,  |
//|   under its own DUAL filename so it never collides with that     |
//|   indicator's single-line ATRSTATE_<symbol>_<tf_minutes>.json --|
//|   algo_v2 (which reads that file) keeps running untouched. Same |
//|   closed-bar + real-historical-event_time approach as that      |
//|   indicator's own PublishATRBridgeFile/FindEventTime (see there |
//|   for the "why closed bar, not the live one" reasoning), just   |
//|   duplicated per line plus one combined structure reading:      |
//|   STRONG only when both lines' trend agree bullish, WEAK only   |
//|   when both agree bearish, UNDECISIVE otherwise -- same rule    |
//|   the TradingView-side dual-line fix (v3/tv_scraper) uses, kept |
//|   identical across both data sources on purpose.                 |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 6
#property indicator_plots   2

//--- Plot 1: fast trail (ATRPeriod / KeyValue)
#property indicator_label1  "ATR Trailing Stop"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrGray, clrGray
#property indicator_width1  2

//--- Plot 2: slow trail (ATRPeriod2 / KeyValue2)
#property indicator_label2  "ATR Trailing Stop 2"
#property indicator_type2   DRAW_COLOR_LINE
#property indicator_color2  clrGray, clrGray
#property indicator_width2  2

//--- ATR Trailing Stop parameters (line 1)
input double KeyValue   = 2;
input int    ATRPeriod  = 2;

//--- ATR Trailing Stop parameters (line 2)
input double KeyValue2  = 2;
input int    ATRPeriod2 = 300;

//--- Bridge publish (V4 execution engine reads this)
input bool   PublishToFile     = true;
input string FileBridgeFolder  = "OBBridge";  // same Common Files folder as OB_ATR_Bridge_Indicator
input string BridgeSymbol      = "";          // empty = use the attached chart's symbol
input int    PublishEverySeconds = 2;         // throttles the bridge file write + event-time backward scan, not the trail calc itself

// Always reprocessed fully on every call, regardless of prev_calculated --
// see CalcTrail's own comment at its use site for the real 3-bar drift
// this fixes (a reattach-triggered iATR(300) settling lag).
#define SAFETY_REPROCESS_BARS 50

//--- ATR Trailing Stop buffers (line 1)
double TrailStop[];
double ColorBuffer[];
double ATRBuffer[];
double TrendBuffer[];

//--- ATR Trailing Stop buffers (line 2)
double TrailStop2[];
double ColorBuffer2[];
double ATRBuffer2[];
double TrendBuffer2[];

int ATRHandle;
int ATRHandle2;

#define LABEL_NAME  "ATR_Trail_Label"
#define LABEL_NAME2 "ATR_Trail_Label2"

//--- one-shot diagnostic flags so the Journal doesn't get spammed every tick
bool loggedInsufficientBars1 = false;
bool loggedInsufficientBars2 = false;
bool loggedCopyFail1 = false;
bool loggedCopyFail2 = false;

//+------------------------------------------------------------------+
//| Display ATR Trailing Stop value (top-left, colored by trend)     |
//+------------------------------------------------------------------+
void DisplayTrailValue(string name, int yDistance, string prefix, double value, int trend)
{
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, yDistance);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 13);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);

   color txtColor = (trend > 0) ? clrLime : clrRed;

   string text = prefix + DoubleToString(value, _Digits);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, txtColor);
}

//+------------------------------------------------------------------+
//| Indicator initialization                                         |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Plot buffers (data+color) must come first, in plot order -- MT5 maps
   // plot N to the Nth group of buffers by raw index, NOT filtered by type.
   // Calculation-only buffers go at the end, or they silently steal a slot
   // from the next plot (which is exactly what was hiding line 2 before).
   SetIndexBuffer(0, TrailStop,  INDICATOR_DATA);
   SetIndexBuffer(1, ColorBuffer, INDICATOR_COLOR_INDEX);

   SetIndexBuffer(2, TrailStop2, INDICATOR_DATA);
   SetIndexBuffer(3, ColorBuffer2, INDICATOR_COLOR_INDEX);

   SetIndexBuffer(4, TrendBuffer,  INDICATOR_CALCULATIONS);
   SetIndexBuffer(5, TrendBuffer2, INDICATOR_CALCULATIONS);

   ATRHandle = iATR(NULL, 0, ATRPeriod);
   if (ATRHandle == INVALID_HANDLE)
     {
      Print("ATR handle error (line 1, period ", ATRPeriod, ")");
      return(INIT_FAILED);
     }

   ATRHandle2 = iATR(NULL, 0, ATRPeriod2);
   if (ATRHandle2 == INVALID_HANDLE)
     {
      Print("ATR handle error (line 2, period ", ATRPeriod2, ")");
      return(INIT_FAILED);
     }

   IndicatorSetString(INDICATOR_SHORTNAME, "ATR Trail Dual");

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Compute one trail line into the given buffers                    |
//+------------------------------------------------------------------+
void CalcTrail(string tag, int rates_total, int prev_calculated, int atrPeriod, double keyValue,
               int atrHandle, double &atrBuf[],
               double &trailBuf[], double &colorBuf[], double &trendBuf[],
               const double &close[],
               bool &loggedInsufficientBars, bool &loggedCopyFail)
{
   if (rates_total < atrPeriod + 2)
     {
      if (!loggedInsufficientBars)
        {
         Print(tag, ": waiting for history -- have ", rates_total,
               " bars, need ", atrPeriod + 2, " (ATRPeriod=", atrPeriod, ")");
         loggedInsufficientBars = true;
        }
      return;
     }

   int copied = CopyBuffer(atrHandle, 0, 0, rates_total, atrBuf);
   if (copied <= 0)
     {
      if (!loggedCopyFail)
        {
         Print(tag, ": CopyBuffer failed, returned ", copied, ", error ", GetLastError());
         loggedCopyFail = true;
        }
      return;
     }

   // Incremental, not full-history: trailBuf/colorBuf/trendBuf are real
   // indicator buffers (SetIndexBuffer'd in OnInit), so MT5 keeps every
   // already-computed bar intact between calls -- closed-bar trail values
   // never change once written. Recomputing all of rates_total from bar 0
   // on every tick (the previous behavior) was the dominant per-tick cost
   // on a chart with a long history, run on EVERY tick since OnCalculate
   // calls this, times two lines, times three attached charts -- flagged
   // as the likely main cause of the chart lag/candle-freeze this fixes.
   // -1 on prev_calculated re-touches the last bar we already had, since
   // it may still be the currently-forming (not yet closed) bar whose
   // close has moved since -- standard MQL5 incremental-indicator pattern.
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;

   // Safety margin, added 2026-08-28: confirmed live a real 3-bar-late
   // structure_event_time (reported 22:13, true flip was 22:10, verified
   // by independently reconstructing this exact trail formula against raw
   // bar history in Python -- both a 400-bar and a 700-bar reconstruction
   // agreed on 22:10). Root cause: every time this indicator gets
   // reattached/recompiled (happened several times today alone), MT5's
   // built-in iATR(300) needs a handful of bars to fully settle its
   // Wilder-smoothed value from a fresh attach -- a bar processed during
   // that unsettled window gets its trail/trend "locked in" by the
   // incremental logic above and never revisited, even once iATR's own
   // value for that same historical index later stabilizes to something
   // slightly different. Always reprocessing at least the last
   // SAFETY_REPROCESS_BARS bars (regardless of what prev_calculated says)
   // means any such drift self-corrects within a few ticks instead of
   // staying wrong forever -- still trivially cheap next to the thousands
   // of bars the incremental fix above eliminated recomputing.
   int safety_start = rates_total - SAFETY_REPROCESS_BARS;
   if(safety_start < 0)
      safety_start = 0;
   if(safety_start < start)
      start = safety_start;

   for (int i = start; i < rates_total; i++)
     {
      double src  = close[i];
      double src1 = (i > 0) ? close[i - 1] : close[i];
      double prevStop = (i > 0) ? trailBuf[i - 1] : src;
      int trendPrev   = (i > 0) ? (int)trendBuf[i - 1] : 1;

      double nLoss = keyValue * atrBuf[i];

      double stop;
      if (src > prevStop && src1 > prevStop)
         stop = MathMax(prevStop, src - nLoss);
      else if (src < prevStop && src1 < prevStop)
         stop = MathMin(prevStop, src + nLoss);
      else if (src > prevStop)
         stop = src - nLoss;
      else
         stop = src + nLoss;

      trailBuf[i] = stop;

      int trend = trendPrev;
      if (src1 < prevStop && src > prevStop)
         trend = 1;
      else if (src1 > prevStop && src < prevStop)
         trend = -1;

      colorBuf[i] = (trend > 0) ? 0 : 1;
      trendBuf[i] = trend;
     }
}

//+------------------------------------------------------------------+
//| Chart symbol, or BridgeSymbol override if one is set              |
//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "") ? _Symbol : BridgeSymbol;
}

//+------------------------------------------------------------------+
//| Bar time of the most recent trend flip in ONE line's trend buffer|
//| -- same logic as OB_ATR_Bridge_Indicator's own FindEventTime,    |
//| just taking the buffer as a parameter so it works for either     |
//| line. reference_idx must be the last CLOSED bar, not the forming |
//| one -- see this file's own header comment for why.                |
//+------------------------------------------------------------------+
datetime FindEventTime(const int reference_idx, const datetime &time[], const double &trendBuf[])
{
   int current_trend = (int)trendBuf[reference_idx];
   for(int i = reference_idx - 1; i >= 0; i--)
     {
      if((int)trendBuf[i] != current_trend)
         return time[i + 1];
     }
   // Trend has been constant across all available history -- the earliest
   // bar we have is the closest thing to an event time.
   return time[0];
}

//+------------------------------------------------------------------+
//| STRONG only when both lines agree bullish, WEAK only when both   |
//| agree bearish, UNDECISIVE otherwise -- same rule the TradingView |
//| side (v3/tv_scraper/atr_trend_tracker.py) uses, kept identical.  |
//+------------------------------------------------------------------+
string CombinedState(int trend1, int trend2)
{
   if(trend1 == 1 && trend2 == 1)
      return "STRONG";
   if(trend1 == -1 && trend2 == -1)
      return "WEAK";
   return "UNDECISIVE";
}

//+------------------------------------------------------------------+
//| Bar time the COMBINED state (above) last actually changed --      |
//| scans backward until either line's trend differs from its        |
//| current value, i.e. until the combined label itself would have   |
//| read differently. Same closed-bar contract as FindEventTime.     |
//+------------------------------------------------------------------+
datetime FindStructureEventTime(const int reference_idx, const datetime &time[],
                                 const double &trend1Buf[], const double &trend2Buf[])
{
   int t1 = (int)trend1Buf[reference_idx];
   int t2 = (int)trend2Buf[reference_idx];
   for(int i = reference_idx - 1; i >= 0; i--)
     {
      if((int)trend1Buf[i] != t1 || (int)trend2Buf[i] != t2)
         return time[i + 1];
     }
   return time[0];
}

//+------------------------------------------------------------------+
//| Publish both lines + combined structure to the bridge, write-    |
//| then-rename so an external reader never observes a half-written  |
//| file mid-scan -- same pattern as OB_ATR_Bridge_Indicator's own   |
//| PublishATRBridgeFile. Deliberately the LAST CLOSED bar (rates_    |
//| total-2), not the forming one -- see this file's own header.     |
//+------------------------------------------------------------------+
datetime g_last_publish_time = 0;

void PublishATRBridgeFile(const int rates_total, const datetime &time[])
{
   if(!PublishToFile)
      return;
   // Throttled to PublishEverySeconds -- FindEventTime/FindStructureEventTime
   // below scan backward through history until they find a differing bar
   // (worst case, the full history, if a line hasn't flipped in a very
   // long time), plus the file write itself -- same class of per-tick
   // cost as CalcTrail above, just now gated on wall-clock time instead
   // of being incremental, since "which bar flipped" isn't something a
   // buffer index can trivially resume from like the trail calc can.
   if(TimeCurrent() - g_last_publish_time < PublishEverySeconds)
      return;
   g_last_publish_time = TimeCurrent();

   if(rates_total < 2)
      return;  // need at least one fully closed bar to publish anything
   int closed_idx = rates_total - 2;

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0)
      tf_minutes = (int)_Period;

   int trend1 = (int)TrendBuffer[closed_idx];
   int trend2 = (int)TrendBuffer2[closed_idx];
   datetime event_time1 = FindEventTime(closed_idx, time, TrendBuffer);
   datetime event_time2 = FindEventTime(closed_idx, time, TrendBuffer2);
   string structure = CombinedState(trend1, trend2);
   datetime structure_event_time = FindStructureEventTime(closed_idx, time, TrendBuffer, TrendBuffer2);

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"line1\":{";
   j += "\"trail_stop\":" + DoubleToString(TrailStop[closed_idx], 8) + ",";
   j += "\"trend\":" + IntegerToString(trend1) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time1);
   j += "},";
   j += "\"line2\":{";
   j += "\"trail_stop\":" + DoubleToString(TrailStop2[closed_idx], 8) + ",";
   j += "\"trend\":" + IntegerToString(trend2) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time2);
   j += "},";
   j += "\"structure\":\"" + structure + "\",";
   j += "\"structure_event_time\":" + IntegerToString((long)structure_event_time);
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\ATRSTATE_DUAL_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      Print("ATR dual bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
     }

   FileWriteString(handle, j);
   FileClose(handle);

   if(!FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE))
      Print("ATR dual bridge file publish failed to finalize: ", final_name, " | error=", GetLastError());
}

//+------------------------------------------------------------------+
//| Indicator calculation                                            |
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
   CalcTrail("Line1(ATR" + IntegerToString(ATRPeriod) + ")", rates_total, prev_calculated, ATRPeriod,  KeyValue,  ATRHandle,  ATRBuffer,  TrailStop,  ColorBuffer,  TrendBuffer,  close, loggedInsufficientBars1, loggedCopyFail1);
   CalcTrail("Line2(ATR" + IntegerToString(ATRPeriod2) + ")", rates_total, prev_calculated, ATRPeriod2, KeyValue2, ATRHandle2, ATRBuffer2, TrailStop2, ColorBuffer2, TrendBuffer2, close, loggedInsufficientBars2, loggedCopyFail2);

   if (rates_total >= ATRPeriod + 2)
      DisplayTrailValue(LABEL_NAME, 20, "ATR" + IntegerToString(ATRPeriod) + " Trail: ",
                         TrailStop[rates_total - 1], (int)TrendBuffer[rates_total - 1]);

   if (rates_total >= ATRPeriod2 + 2)
      DisplayTrailValue(LABEL_NAME2, 40, "ATR" + IntegerToString(ATRPeriod2) + " Trail: ",
                         TrailStop2[rates_total - 1], (int)TrendBuffer2[rates_total - 1]);

   // Guard matches OB_ATR_Bridge_Indicator's own convention: only publish
   // once BOTH lines have enough closed-bar history to have real (not
   // zero-initialized garbage) values at closed_idx -- whichever ATR
   // period needs more bars sets the floor.
   if (rates_total >= MathMax(ATRPeriod, ATRPeriod2) + 2)
      PublishATRBridgeFile(rates_total, time);

   return(rates_total);
}

//+------------------------------------------------------------------+
//| Cleanup                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectDelete(0, LABEL_NAME);
   ObjectDelete(0, LABEL_NAME2);
}
