//+------------------------------------------------------------------+
//|                 OB_State_XAUUSD_2.0.mq5                          |
//| Reads OB rectangles from chart objects, classifies direction,     |
//| calculates virgin status, and publishes latest OB bias/levels --  |
//| PLUS the ATR Trailing Stop (Strong/Weak zone character) for the   |
//| chart this indicator is attached to. One indicator, one chart     |
//| (attach to the M5 XAUUSD chart), two JSON bridge outputs:         |
//| OBSTATE_<symbol>_<minutes>.json per configured OB timeframe, and  |
//| ATRSTATE_<symbol>_<minutes>.json for this chart's own timeframe.  |
//| Dedicated to XAUUSD (BridgeSymbol default below); algo_v2 is the  |
//| only consumer wired up to these bridge files today.               |
//|                                                                     |
//| Single-instance multi-chart OB bridge: attach to ONE chart; it    |
//| scans every other open chart (same symbol) for each configured    |
//| timeframe by chart ID, so the publisher no longer needs to be     |
//| attached per timeframe. The LuxAlgo OB detector must still run on |
//| each of those timeframe charts (it draws the pineBox rectangles), |
//| but those charts can stay open/minimized without our indicator.   |
//| The ATR Trailing Stop math (KeyValue/ATRPeriod below) is          |
//| untouched from the original ATR_Trail.mq5 -- only the JSON        |
//| publish step is new.                                               |
//|                                                                     |
//| Panel: on-chart visual (RenderTimeframeBlock/DrawHeaderSegments)  |
//| borrowed from a parallel build (OB_State_Publisher_2.0.mq5) --    |
//| stacked per-timeframe blocks with separate Supply (left) and      |
//| Demand (right) columns, each showing latest zone + "Additional    |
//| Zones" history, plus a No Long/No Short reversal-zone column for  |
//| higher timeframes (NoTradeZoneTimeframes) showing virgin zones    |
//| only. Off by default (ShowPanel=false) -- this build's reset      |
//| buttons/block-status labels already occupy screen space and this  |
//| keeps the chart decluttered until explicitly turned on. The panel |
//| ATR Trail value shown in each block's header is computed inline   |
//| per timeframe (ComputeATRTrailInline, display only, never         |
//| published) -- deliberately NOT iCustom, since iCustom("ATR_Trail", |
//| ...) for a timeframe other than this chart's own was found to     |
//| draw that indicator's own top-left label onto this chart          |
//| regardless of the requested period.                               |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   1

#property indicator_label1  "ATR Trailing Stop"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrGray, clrGray
#property indicator_width1  2

//--- ATR Trailing Stop parameters (unchanged from ATR_Trail.mq5)
input double KeyValue                       = 2;
input int    ATRPeriod                      = 2;
input int    ATRTrailInlineBars             = 300;   // warm-up window for the per-timeframe ATR Trail shown in the panel header (display only -- never published)

input string OB_ObjectKeyword               = "pineBox";
input string BridgeSymbol                   = "XAUUSD"; // this build is dedicated to XAUUSD
input string TargetTimeframes               = "H4,H2,H1,M30,M15,M5,M3,M1";
input string NoTradeZoneTimeframes          = "H4,H2,H1,M30,M15";  // panel's Reversal Zone column: which timeframes show virgin-zone No Long/No Short entries
input bool   ShowPanel                      = false;   // rich per-timeframe visual panel -- off by default (decluttered)
input int    ScanEverySeconds               = 1;
input int    ZoneHistoryDepth               = 5;   // recent zones per direction published to the JSON bridge

input int    DirectionColorMinChannel       = 20;
input int    DirectionColorGap              = 8;

input bool   UseOverlapDirectionFix         = true;
input double OverlapDirectionMinPercent     = 60.0;
input double OverlapDirectionTieGapPercent  = 20.0;

input bool   UseClosedCandlesOnlyForRetest  = false;
input int    RetestSkipBarsFromDetection    = 1;
input bool   TreatExistingObjectsAsBaseline = true;
input bool   UseMidPriceForDetection        = true;

input bool   PublishGlobalVariables         = true;
input string GlobalVariablePrefix           = "OBSTATE";

input bool   PublishToFile                  = true;
input string FileBridgeFolder               = "OBBridge";

input bool   ShowBiasLabels                 = false;  // per-timeframe bias labels -- off by default (decluttered)
input int    BiasLabelCorner                = 0;    // 0=left top, 1=right top, 2=left bottom, 3=right bottom
input int    BiasLabelX                     = 480;
input int    BiasLabelYStart                = 20;
input int    BiasLabelRowHeight             = 20;
input int    BiasLabelFontSize              = 11;
input string BiasLabelFont                  = "Arial Bold";
input color  BullishBiasColor               = clrLime;
input color  BearishBiasColor               = clrRed;
input color  NeutralBiasColor               = clrSilver;
input color  ActiveTouchColor               = clrOrange;  // panel: Supply/Demand line turns this color while live price sits inside that zone right now

input bool   ShowVirginObList               = false;  // untested-only OB list per timeframe -- off by default (decluttered)
input int    VirginObListYGap               = 10;

input bool   ShowResetButtons               = true;   // RESET M1/M3/M5 buttons -> write flag files algo_v2 polls
input int    ResetButtonCorner              = 0;      // 0=left top, 1=right top, 2=left bottom, 3=right bottom -- matches BiasLabelCorner by default so they sit in the same column
input int    ResetButtonX                   = 480;    // matches BiasLabelX by default
input int    ResetButtonYGap                = 15;     // gap below the virgin-OB list's current bottom row

input int    PanelCorner                    = 0;     // 0=left top, 1=right top, 2=left bottom, 3=right bottom
input int    PanelX                         = 10;
input int    PanelYStart                    = 32;   // clears the chart's own native symbol/timeframe label in the top-left corner
input int    PanelHeaderFontSize            = 12;
input int    PanelContentFontSize           = 9;
input int    PanelHeaderLineHeight          = 18;
input int    PanelLineHeight                = 14;
input int    PanelBlockGap                  = 8;     // extra gap between one timeframe's block and the next
input string PanelHeaderFont                = "Arial Bold";
input string PanelContentFont               = "Arial";
input string PanelBoldContentFont           = "Arial Bold";  // used for the Supply/Demand line only -- Additional Zones stays regular weight
input int    MaxAdditionalZonesShown        = 3;      // per direction, beyond the latest already shown on the Supply/Demand line
input int    PanelRightColumnX              = 400;    // fixed x offset (from PanelX) for both the Demand column and the S column -- shared so they line up on one straight column
input int    PanelThirdColumnX              = 780;    // fixed x offset (from PanelX) for the Reversal Zone column -- safety floor; the per-row guard (widest of Demand's own lines + PanelColumnPadding) does the real work
input int    PanelColumnPadding             = 20;     // minimum gap enforced past the left column's actual text width, even if it overruns PanelRightColumnX

struct OBZone
{
   string   name;
   string   direction;
   string   signature;
   double   high;
   double   low;
   datetime start_time;
   datetime end_time;
   bool     virgin;
   datetime visit_time;
   datetime validation_time;
   datetime detected_time;
   double   detected_price;
   bool     baseline;
};

struct OBDetectionState
{
   string   signature;
   datetime detected_time;
   double   detected_price;
   bool     baseline;
   bool     live_visited;
   datetime live_visit_time;

   // Once a zone is conclusively found tested (by either the historical
   // bar-walk or a live touch), that never reverts -- virgin only ever
   // goes one direction. Caching it here means HasZoneBeenRetested's
   // per-bar walk from the zone's origin candle only ever runs ONCE per
   // zone for the rest of this indicator's lifetime, instead of being
   // re-walked from scratch on every ~1s scan forever. See ScanObjectsFor.
   bool     resolved;
   datetime resolved_visit_time;
   datetime resolved_validation_time;
};

struct TFTarget
{
   ENUM_TIMEFRAMES period;
   int              minutes;
   string           label;
};

struct TFState
{
   bool   chart_found;
   double bias;
   double latest_high, latest_low, latest_virgin, latest_time;
   double latest_detected_time, latest_detected_price;
   double latest_visit_time, latest_validation_time;
   double bull_high, bull_low, bull_virgin;
   double bear_high, bear_low, bear_virgin;
   int    zone_count;
};

OBZone zones[];
OBDetectionState detection_states[];
// Set true at every genuine write to detection_states[] (new zone,
// live-visit confirmed, resolved-cache filled in); SaveDetectionStates()
// only actually writes the file when this is set, then clears it --
// avoids a disk write every ~1s scan when nothing has changed. Ported
// from OB_State_Multi_2.0.mq5 -- see that file's identical mechanism for
// the full story (confirmed live: this shared Common\Files folder gets
// contended enough under load to fail writes outright and flag multiple
// indicators, including this one, as "too slow").
bool g_detection_states_dirty = false;

//--- ATR Trailing Stop buffers (unchanged math from ATR_Trail.mq5)
double TrailStop[];
double ColorBuffer[];
double ATRBuffer[];
double TrendBuffer[];
int    ATRHandle;

#define ATR_LABEL_NAME "ATR_Trail_Label"

TFTarget g_targets[];
TFState  g_state[];
bool     g_first_scan[];
int      g_vob_slot_count = 0;
int      g_iatr_handles[];   // one built-in iATR handle per target timeframe, created once, used only for the panel's per-timeframe header display
int      g_panel_y = 0;
int      g_panel_max_x = 0;  // widest right edge any panel label reached this scan -- lets the reset button/status column (see ScanAndPublishAll) anchor itself just past the panel instead of a fixed guess

//+------------------------------------------------------------------+
//| Display ATR Trailing Stop value (top-left, colored by trend)     |
//+------------------------------------------------------------------+
void DisplayTrailValue(double value, int trend)
{
   if (ObjectFind(0, ATR_LABEL_NAME) < 0)
      ObjectCreate(0, ATR_LABEL_NAME, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_YDISTANCE, 20);
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_FONTSIZE, 13);
   ObjectSetString(0, ATR_LABEL_NAME, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_HIDDEN, true);

   color txtColor = (trend > 0) ? clrLime : clrRed;

   string text = "ATR Trail: " + DoubleToString(value, _Digits);
   ObjectSetString(0, ATR_LABEL_NAME, OBJPROP_TEXT, text);
   ObjectSetInteger(0, ATR_LABEL_NAME, OBJPROP_COLOR, txtColor);
}

//+------------------------------------------------------------------+
//| Bar time of the most recent trend character flip (Strong<->Weak) |
//| reference_idx is the bar to treat as "current" -- pass the last  |
//| CLOSED bar's index here, not rates_total-1 (the forming bar), so |
//| this never reacts to a flip that hasn't actually confirmed yet.  |
//+------------------------------------------------------------------+
datetime FindEventTime(const int reference_idx, const datetime &time[])
{
   int current_trend = (int)TrendBuffer[reference_idx];
   for(int i = reference_idx - 1; i >= 0; i--)
   {
      if((int)TrendBuffer[i] != current_trend)
         return time[i + 1];
   }
   // Trend has been constant across all available history -- the earliest
   // bar we have is the closest thing to an event time.
   return time[0];
}

//+------------------------------------------------------------------+
//| Publish trail/trend/event-time to the JSON bridge, write-then-   |
//| rename so an external reader (e.g. Python) never observes a      |
//| half-written file mid-scan -- same pattern as the OB bridge.     |
//|                                                                    |
//| Deliberately publishes the LAST CLOSED bar (rates_total-2), not  |
//| the currently-forming one (rates_total-1) -- confirmed live that |
//| publishing the live bar let trend/event_time wobble tick-to-tick |
//| as price oscillated right at the trail line mid-candle, which    |
//| the Python bot then had to debounce against. A closed bar's      |
//| close[] never changes once it closes, so this is now genuinely   |
//| stable -- it updates once per bar close, matching "previous      |
//| candle close" from the original spec, not once per tick. The     |
//| on-chart label (DisplayTrailValue) still shows the LIVE value,   |
//| unchanged -- only what's published to the bridge changed.        |
//+------------------------------------------------------------------+
void PublishATRBridgeFile(const int rates_total, const datetime &time[])
{
   if(rates_total < 2)
      return;  // need at least one fully closed bar to publish anything
   int closed_idx = rates_total - 2;

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0)
      tf_minutes = (int)_Period;

   double trail = TrailStop[closed_idx];
   int trend = (int)TrendBuffer[closed_idx];
   datetime event_time = FindEventTime(closed_idx, time);

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"trail_stop\":" + DoubleToString(trail, 8) + ",";
   j += "\"trend\":" + IntegerToString(trend) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time);
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\ATRSTATE_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      Print("ATR bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
     }

   FileWriteString(handle, j);
   FileClose(handle);

   if(!FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE))
      Print("ATR bridge file publish failed to finalize: ", final_name, " | error=", GetLastError());
}

//+------------------------------------------------------------------+
//| Persists detection_states[] (per-zone detected_time/detected_    |
//| price/baseline/live-visited/resolved bookkeeping) across restarts |
//| -- indicator reattach, chart reload, or terminal restart. Without |
//| this, that array starts empty every time (see struct             |
//| OBDetectionState above), and AssignDetectionState() then re-      |
//| classifies EVERY zone still on the chart as a fresh "baseline"    |
//| (detected_time=0, permanently untradeable per algo_v2's own       |
//| eligibility gate) on the very first scan after restart -- even a  |
//| zone that was legitimately live-detected and still virgin the     |
//| moment before the restart. Ported from OB_State_Multi_2.0.mq5,    |
//| where this was confirmed live: a stale terminal needing a restart |
//| was silently wiping every open setup's detection history.         |
//|                                                                     |
//| Deliberately NOT JSON -- this file is purely internal (never read |
//| by the Python side), so a flat ';'-delimited line-per-zone format |
//| is more robust to hand-parse in MQL5 than nested JSON objects.    |
//| One file per symbol (not per timeframe): signature already        |
//| encodes the timeframe as its first field (see BuildSignature),    |
//| so different timeframes' zones can never collide in one file.     |
//| Only actually writes when g_detection_states_dirty is set -- not  |
//| every scan (see that flag's own comment for why this matters).   |
//+------------------------------------------------------------------+
void SaveDetectionStates(const string symbol)
{
   if(!PublishToFile || !g_detection_states_dirty)
      return;
   g_detection_states_dirty = false;

   string body = "";
   for(int i = 0; i < ArraySize(detection_states); i++)
   {
      OBDetectionState st = detection_states[i];
      body += st.signature + ";" +
              IntegerToString((long)st.detected_time) + ";" +
              DoubleToString(st.detected_price, 8) + ";" +
              (st.baseline ? "1" : "0") + ";" +
              (st.live_visited ? "1" : "0") + ";" +
              IntegerToString((long)st.live_visit_time) + ";" +
              (st.resolved ? "1" : "0") + ";" +
              IntegerToString((long)st.resolved_visit_time) + ";" +
              IntegerToString((long)st.resolved_validation_time) + "\n";
   }

   FolderCreate(FileBridgeFolder, FILE_COMMON);
   const string final_name = FileBridgeFolder + "\\OBDETECT_" + symbol + ".dat";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("Detection-state save failed: ", tmp_name, " | error=", GetLastError());
      return;
   }
   FileWriteString(handle, body);
   FileClose(handle);

   if(!FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE))
      Print("Detection-state save failed to finalize: ", final_name, " | error=", GetLastError());
}

//+------------------------------------------------------------------+
//| Loads whatever SaveDetectionStates() last wrote, straight into    |
//| detection_states[] -- must run in OnInit() BEFORE the first       |
//| ScanAndPublishAll() call, so AssignDetectionState() finds these    |
//| pre-populated entries (via FindDetectionState matching on          |
//| signature) instead of treating every currently-on-chart zone as   |
//| brand new. A zone whose signature ISN'T in this file (genuinely    |
//| new since the last save, or no file exists yet at all) still      |
//| falls through to the normal g_first_scan/TreatExistingObjectsAs-  |
//| Baseline logic exactly as before -- this only short-circuits that |
//| for zones we've already seen.                                     |
//+------------------------------------------------------------------+
void LoadDetectionStates(const string symbol)
{
   const string path = FileBridgeFolder + "\\OBDETECT_" + symbol + ".dat";
   int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;  // nothing persisted yet -- first-ever run, or file was cleared

   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle) + "\n";
   FileClose(handle);

   string lines[];
   int line_count = StringSplit(content, '\n', lines);
   int restored = 0;

   for(int i = 0; i < line_count; i++)
   {
      if(StringLen(lines[i]) == 0)
         continue;

      string parts[];
      int part_count = StringSplit(lines[i], ';', parts);
      if(part_count != 9)
         continue;  // malformed/truncated line -- skip rather than half-restore it

      OBDetectionState st;
      st.signature                 = parts[0];
      st.detected_time              = (datetime)StringToInteger(parts[1]);
      st.detected_price             = StringToDouble(parts[2]);
      st.baseline                   = (parts[3] == "1");
      st.live_visited                = (parts[4] == "1");
      st.live_visit_time             = (datetime)StringToInteger(parts[5]);
      st.resolved                    = (parts[6] == "1");
      st.resolved_visit_time         = (datetime)StringToInteger(parts[7]);
      st.resolved_validation_time    = (datetime)StringToInteger(parts[8]);

      int size = ArraySize(detection_states);
      ArrayResize(detection_states, size + 1);
      detection_states[size] = st;
      restored++;
   }

   if(restored > 0)
      Print("Restored ", restored, " OB detection state(s) for ", symbol, " from ", path);
}

//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "" ? _Symbol : BridgeSymbol);
}

//+------------------------------------------------------------------+
string TrimStr(const string s)
{
   string out = s;
   StringTrimLeft(out);
   StringTrimRight(out);
   return out;
}

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StringToPeriod(const string s)
{
   if(s == "M1")  return PERIOD_M1;
   if(s == "M2")  return PERIOD_M2;
   if(s == "M3")  return PERIOD_M3;
   if(s == "M4")  return PERIOD_M4;
   if(s == "M5")  return PERIOD_M5;
   if(s == "M6")  return PERIOD_M6;
   if(s == "M10") return PERIOD_M10;
   if(s == "M12") return PERIOD_M12;
   if(s == "M15") return PERIOD_M15;
   if(s == "M20") return PERIOD_M20;
   if(s == "M30") return PERIOD_M30;
   if(s == "H1")  return PERIOD_H1;
   if(s == "H2")  return PERIOD_H2;
   if(s == "H3")  return PERIOD_H3;
   if(s == "H4")  return PERIOD_H4;
   if(s == "H6")  return PERIOD_H6;
   if(s == "H8")  return PERIOD_H8;
   if(s == "H12") return PERIOD_H12;
   if(s == "D1")  return PERIOD_D1;
   if(s == "W1")  return PERIOD_W1;
   if(s == "MN1") return PERIOD_MN1;
   return PERIOD_CURRENT;
}

//+------------------------------------------------------------------+
string PeriodLabel(const int minutes)
{
   if(minutes > 0 && minutes % 1440 == 0) return "D" + IntegerToString(minutes / 1440);
   if(minutes > 0 && minutes % 60 == 0)   return "H" + IntegerToString(minutes / 60);
   return "M" + IntegerToString(minutes);
}

//+------------------------------------------------------------------+
void ParseTargets()
{
   ArrayResize(g_targets, 0);
   ArrayResize(g_state, 0);
   ArrayResize(g_first_scan, 0);

   string parts[];
   ushort sep = (ushort)StringGetCharacter(",", 0);
   int n = StringSplit(TargetTimeframes, sep, parts);

   for(int i = 0; i < n; i++)
   {
      string tok = TrimStr(parts[i]);
      if(tok == "")
         continue;

      ENUM_TIMEFRAMES period = StringToPeriod(tok);
      if(period == PERIOD_CURRENT)
      {
         Print("OB bridge: unknown timeframe token skipped: ", tok);
         continue;
      }

      int idx = ArraySize(g_targets);
      ArrayResize(g_targets, idx + 1);
      ArrayResize(g_state, idx + 1);
      ArrayResize(g_first_scan, idx + 1);

      g_targets[idx].period  = period;
      g_targets[idx].minutes = (int)(PeriodSeconds(period) / 60);
      g_targets[idx].label   = PeriodLabel(g_targets[idx].minutes);
      g_first_scan[idx]      = true;
      ZeroMemory(g_state[idx]);
   }
}

//+------------------------------------------------------------------+
// One built-in iATR handle per target timeframe, created once and reused
// for the indicator's lifetime -- used only to feed the panel's per-
// timeframe header display (ComputeATRTrailInline). Creating/releasing a
// fresh handle on every scan would eventually exhaust MT5's indicator
// handle pool.
void CreateATRHandles(const string symbol)
{
   for(int i = 0; i < ArraySize(g_iatr_handles); i++)
      if(g_iatr_handles[i] != INVALID_HANDLE)
         IndicatorRelease(g_iatr_handles[i]);

   ArrayResize(g_iatr_handles, ArraySize(g_targets));
   for(int i = 0; i < ArraySize(g_targets); i++)
   {
      g_iatr_handles[i] = iATR(symbol, g_targets[i].period, ATRPeriod);
      if(g_iatr_handles[i] == INVALID_HANDLE)
         Print("Panel ATR handle failed for ", g_targets[i].label, " | error=", GetLastError());
   }
}

//+------------------------------------------------------------------+
// Same recursive math as the primary ATR Trailing Stop above, run here
// directly over each timeframe's own recent history for the panel header
// display only -- deliberately NOT iCustom (see file header comment).
// Warm-starts from ATRTrailInlineBars back rather than true bar zero,
// which converges to the same value within a handful of bars for this
// KeyValue/ATRPeriod combination -- fine for a display value. Never
// published to any bridge file; the real ATR bridge (PublishATRBridgeFile)
// is untouched and remains the sole source algo_v2 reads.
bool ComputeATRTrailInline(const int atr_handle, const string symbol, const ENUM_TIMEFRAMES period, double &value, int &trend)
{
   value = 0.0;
   trend = 0;

   if(atr_handle == INVALID_HANDLE)
      return false;

   int available = iBars(symbol, period);
   int bars = MathMin(ATRTrailInlineBars, available);
   if(bars < ATRPeriod + 2)
      return false;

   double atr_buf[];
   double close_buf[];
   ArraySetAsSeries(atr_buf, false);
   ArraySetAsSeries(close_buf, false);

   bool ok = (CopyBuffer(atr_handle, 0, 0, bars, atr_buf) > 0) &&
             (CopyClose(symbol, period, 0, bars, close_buf) > 0);
   if(!ok)
      return false;

   double trail_stop[];
   int    trend_buf[];
   ArrayResize(trail_stop, bars);
   ArrayResize(trend_buf, bars);

   for(int i = 0; i < bars; i++)
   {
      double src      = close_buf[i];
      double src1     = (i > 0) ? close_buf[i - 1] : close_buf[i];
      double prevStop = (i > 0) ? trail_stop[i - 1] : src;
      int    trendPrev = (i > 0) ? trend_buf[i - 1] : 1;

      double nLoss = KeyValue * atr_buf[i];

      double stop;
      if(src > prevStop && src1 > prevStop)
         stop = MathMax(prevStop, src - nLoss);
      else if(src < prevStop && src1 < prevStop)
         stop = MathMin(prevStop, src + nLoss);
      else if(src > prevStop)
         stop = src - nLoss;
      else
         stop = src + nLoss;

      trail_stop[i] = stop;

      int tr = trendPrev;
      if(src1 < prevStop && src > prevStop)
         tr = 1;
      else if(src1 > prevStop && src < prevStop)
         tr = -1;

      trend_buf[i] = tr;
   }

   value = trail_stop[bars - 1];
   trend = trend_buf[bars - 1];
   return true;
}

//+------------------------------------------------------------------+
bool GetATRTrail(const int idx, const string symbol, double &value, int &trend)
{
   value = 0.0;
   trend = 0;
   if(idx < 0 || idx >= ArraySize(g_targets) || idx >= ArraySize(g_iatr_handles))
      return false;

   return ComputeATRTrailInline(g_iatr_handles[idx], symbol, g_targets[idx].period, value, trend);
}

//+------------------------------------------------------------------+
long FindChartForSymbolPeriod(const string symbol, const ENUM_TIMEFRAMES period)
{
   long id = ChartFirst();
   while(id >= 0)
   {
      if(ChartSymbol(id) == symbol && ChartPeriod(id) == period)
         return id;
      id = ChartNext(id);
   }
   return -1;
}

//+------------------------------------------------------------------+
void DeleteObjectsByPrefix(const string prefix)
{
   for(int i = ObjectsTotal(0, 0, -1) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, 0, -1);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, TrailStop, INDICATOR_DATA);
   SetIndexBuffer(1, ColorBuffer, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, TrendBuffer, INDICATOR_CALCULATIONS);

   ATRHandle = iATR(NULL, 0, ATRPeriod);
   if(ATRHandle == INVALID_HANDLE)
   {
      Print("ATR handle error");
      return(INIT_FAILED);
   }

   IndicatorSetString(INDICATOR_SHORTNAME, "OB State XAUUSD 2.0");

   ParseTargets();
   // _Symbol (this chart's real instrument, e.g. "GOLD.i#" on a broker whose
   // naming differs from BridgeSymbol) -- not EffectiveSymbol(), which is
   // only ever a publish-time label and may not resolve to a real symbol
   // iATR() can create a handle for. See ProcessTimeframe for the same
   // real-vs-publish split applied to chart lookups and price queries.
   CreateATRHandles(_Symbol);

   if(PublishToFile)
      FolderCreate(FileBridgeFolder, FILE_COMMON);

   // Must run before the first ScanAndPublishAll() below -- see
   // LoadDetectionStates' docstring for why the ordering matters.
   LoadDetectionStates(EffectiveSymbol());

   CreateV2ResetButtons();

   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);

   ScanAndPublishAll();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectDelete(0, ATR_LABEL_NAME);
   for(int i = 0; i < ArraySize(g_targets); i++)
      ObjectDelete(0, BiasLabelName(i));
   for(int i = 0; i < g_vob_slot_count; i++)
      ObjectDelete(0, "OBSP_VOB_" + IntegerToString(i));
   ObjectDelete(0, "OBSP_BLOCKSTATUS_V2_M1");
   ObjectDelete(0, "OBSP_BLOCKSTATUS_V2_M3");
   ObjectDelete(0, "OBSP_BLOCKSTATUS_V2_M5");
   DeleteV2ResetButtons();
   DeleteObjectsByPrefix("OBP_");
   for(int i = 0; i < ArraySize(g_iatr_handles); i++)
      if(g_iatr_handles[i] != INVALID_HANDLE)
         IndicatorRelease(g_iatr_handles[i]);
   Comment("");
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK)
      return;

   string tf = "";
   if(sparam == "OBSP_RESET_V2_M1")      tf = "M1";
   else if(sparam == "OBSP_RESET_V2_M3") tf = "M3";
   else if(sparam == "OBSP_RESET_V2_M5") tf = "M5";
   else return;

   ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
   WriteV2ResetRequest(tf);
   ChartRedraw();
}

//+------------------------------------------------------------------+
void OnTimer()
{
   ScanAndPublishAll();
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
   // ATR Trailing Stop -- math unchanged from ATR_Trail.mq5.
   if(rates_total >= ATRPeriod + 2 && CopyBuffer(ATRHandle, 0, 0, rates_total, ATRBuffer) > 0)
   {
      for(int i = 0; i < rates_total; i++)
      {
         double src  = close[i];
         double src1 = (i > 0) ? close[i - 1] : close[i];
         double prevStop = (i > 0) ? TrailStop[i - 1] : src;
         int trendPrev   = (i > 0) ? (int)TrendBuffer[i - 1] : 1;

         double nLoss = KeyValue * ATRBuffer[i];

         double stop;
         if (src > prevStop && src1 > prevStop)
            stop = MathMax(prevStop, src - nLoss);
         else if (src < prevStop && src1 < prevStop)
            stop = MathMin(prevStop, src + nLoss);
         else if (src > prevStop)
            stop = src - nLoss;
         else
            stop = src + nLoss;

         TrailStop[i] = stop;

         int trend = trendPrev;
         if (src1 < prevStop && src > prevStop)
            trend = 1;
         else if (src1 > prevStop && src < prevStop)
            trend = -1;

         ColorBuffer[i] = (trend > 0) ? 0 : 1;
         TrendBuffer[i] = trend;
      }

      DisplayTrailValue(TrailStop[rates_total - 1], (int)TrendBuffer[rates_total - 1]);

      if(PublishToFile)
         PublishATRBridgeFile(rates_total, time);
   }

   ScanAndPublishAll();
   return rates_total;
}

//+------------------------------------------------------------------+
void ScanAndPublishAll()
{
   // Two different things share the name "symbol" elsewhere in this file and
   // must NOT be collapsed into one: chart_symbol is this terminal's real
   // instrument name (_Symbol -- e.g. "GOLD.i#" on a broker whose naming
   // differs from XAUUSD), used for every chart lookup and live price/history
   // query (FindChartForSymbolPeriod, SymbolInfoDouble, iClose/iHigh/iLow,
   // iATR...) -- none of those resolve against a label that isn't a real
   // symbol on this terminal. publish_symbol is EffectiveSymbol() (the
   // BridgeSymbol override, e.g. "XAUUSD"), used ONLY when naming/labeling
   // the bridge output files, so algo_v2 keeps finding OBSTATE_XAUUSD_*.json
   // regardless of what this broker actually calls the instrument. Confirmed
   // live: collapsing these into one value broke OB publishing entirely on a
   // broker whose native symbol differs from BridgeSymbol -- every chart
   // lookup silently failed and ProcessTimeframe returned before ever
   // publishing, while ATR publishing (which never needs to *find* a chart,
   // only label its own already-attached one) kept working, masking it.
   const string chart_symbol   = _Symbol;
   const string publish_symbol = EffectiveSymbol();

   static bool s_panel_was_shown = false;

   if(ShowPanel)
   {
      g_panel_y = PanelYStart;
      g_panel_max_x = PanelX;
      s_panel_was_shown = true;
   }
   else if(s_panel_was_shown)
   {
      // Toggled off since the last scan -- clear out its objects rather
      // than leaving a stale panel frozen on the chart.
      DeleteObjectsByPrefix("OBP_");
      s_panel_was_shown = false;
   }

   for(int i = 0; i < ArraySize(g_targets); i++)
      ProcessTimeframe(i, chart_symbol, publish_symbol);

   // No-ops internally unless something actually changed this scan (see
   // SaveDetectionStates' own guard/docstring). publish_symbol, not
   // chart_symbol -- must match LoadDetectionStates(EffectiveSymbol())
   // in OnInit(), and the OBSTATE/ATRSTATE files' own symbol labeling.
   SaveDetectionStates(publish_symbol);

   UpdateBiasLabels();

   int vob_bottom_y = BiasLabelYStart + ArraySize(g_targets) * BiasLabelRowHeight + VirginObListYGap;
   if(ShowVirginObList)
      vob_bottom_y = UpdateVirginObLabels();

   // Reset buttons/status get their own column, clear of the panel: while
   // ShowPanel is on, anchor at whatever the panel's widest label reached
   // this scan (g_panel_max_x, updated live by SetPanelLabel) plus a gap --
   // never a fixed guess that a long zone/detection string could outgrow.
   // With the panel off, ResetButtonX (the original fixed position) still
   // applies exactly as before.
   int reset_x = ShowPanel ? (g_panel_max_x + PanelColumnPadding) : ResetButtonX;

   // Status labels stack vertically (one row per timeframe) since the text
   // ("M1: BLOCKED (manual_cancel)") is too wide for a side-by-side layout
   // without overlapping -- confirmed live that fixed-width side-by-side
   // spacing (sized for V1's shorter "M1: BLOCKED" text) visibly collided.
   int status_y = vob_bottom_y + ResetButtonYGap;
   UpdateV2BlockStatusLabels(status_y, reset_x);
   RepositionV2ResetButtons(status_y + 3 * BiasLabelRowHeight, reset_x);

   // ObjectSetString/ObjectSetInteger above update the label objects'
   // properties immediately, but MT5 doesn't repaint the chart just
   // because a property changed -- confirmed live that a cancelled
   // order's BLOCKED status sat updated-but-invisible until something
   // else (a manual click) forced a redraw. Force it here instead.
   ChartRedraw();
}

//+------------------------------------------------------------------+
void CreateResetButton(const string name, const string text, const int x, const int y)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, ResetButtonCorner);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, 82);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, 22);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
// Reset buttons/status for the algo_v2 bot -- object names, flag files,
// and status JSON all namespaced "_V2" purely because that's what
// algo_v2/blocking.py already reads/writes; there's only one bot wired up
// to the chart now, so the naming is just an implementation detail, not a
// V1-vs-V2 distinction on screen.
//+------------------------------------------------------------------+
void CreateV2ResetButtons()
{
   if(!ShowResetButtons)
      return;
   // Placed at a default Y here; RepositionV2ResetButtons() moves them
   // every poll, once the status labels' height above them is known.
   CreateResetButton("OBSP_RESET_V2_M1", "RESET M1", ResetButtonX, BiasLabelYStart);
   CreateResetButton("OBSP_RESET_V2_M3", "RESET M3", ResetButtonX + 87, BiasLabelYStart);
   CreateResetButton("OBSP_RESET_V2_M5", "RESET M5", ResetButtonX + 174, BiasLabelYStart);
}

//+------------------------------------------------------------------+
// Reads BLOCK_STATUS_V2.json (published by algo_v2's BlockedZoneStore) --
// same compact format and hand-parser as ReadBlockStatus, different file.
bool ReadV2BlockStatus(const string tf, bool &is_blocked, string &reason)
{
   is_blocked = false;
   reason = "";

   string path = FileBridgeFolder + "\\BLOCK_STATUS_V2.json";
   int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return false;

   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle);
   FileClose(handle);

   int tf_pos = StringFind(content, "\"" + tf + "\":{");
   if(tf_pos < 0)
      return false;

   int blocked_key_pos = StringFind(content, "\"blocked\":", tf_pos);
   if(blocked_key_pos < 0)
      return false;
   int blocked_val_pos = blocked_key_pos + StringLen("\"blocked\":");
   is_blocked = (StringSubstr(content, blocked_val_pos, 4) == "true");

   int reason_key_pos = StringFind(content, "\"reason\":", tf_pos);
   if(reason_key_pos >= 0)
   {
      int reason_val_pos = reason_key_pos + StringLen("\"reason\":");
      if(StringSubstr(content, reason_val_pos, 1) == "\"")
      {
         int end_quote = StringFind(content, "\"", reason_val_pos + 1);
         if(end_quote > reason_val_pos)
            reason = StringSubstr(content, reason_val_pos + 1, end_quote - reason_val_pos - 1);
      }
   }

   return true;
}

//+------------------------------------------------------------------+
void UpdateV2BlockStatusLabels(const int y, const int x)
{
   if(!ShowResetButtons)
      return;

   string tfs[3] = {"M1", "M3", "M5"};

   for(int i = 0; i < 3; i++)
   {
      bool   is_blocked;
      string reason;
      bool   ok = ReadV2BlockStatus(tfs[i], is_blocked, reason);

      string txt;
      color  clr;
      if(!ok)
      {
         txt = tfs[i] + ": --";
         clr = NeutralBiasColor;
      }
      else if(is_blocked)
      {
         txt = tfs[i] + ": BLOCKED" + (reason != "" ? " (" + reason + ")" : "");
         clr = clrOrange;
      }
      else
      {
         txt = tfs[i] + ": CLEAR";
         clr = clrGray;
      }

      string name = "OBSP_BLOCKSTATUS_V2_" + tfs[i];
      if(ObjectFind(0, name) < 0)
      {
         ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, name, OBJPROP_BACK, false);
      }
      // One row per timeframe (not side-by-side) -- "M1: BLOCKED
      // (manual_cancel)" is too wide to fit three across without
      // overlapping at any reasonable column spacing.
      ObjectSetInteger(0, name, OBJPROP_CORNER, ResetButtonCorner);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y + i * BiasLabelRowHeight);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
      ObjectSetString(0, name, OBJPROP_FONT, BiasLabelFont);
      ObjectSetString(0, name, OBJPROP_TEXT, txt);
   }
}

//+------------------------------------------------------------------+
void RepositionV2ResetButtons(const int y, const int x)
{
   if(!ShowResetButtons)
      return;
   ObjectSetInteger(0, "OBSP_RESET_V2_M1", OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, "OBSP_RESET_V2_M1", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "OBSP_RESET_V2_M3", OBJPROP_XDISTANCE, x + 87);
   ObjectSetInteger(0, "OBSP_RESET_V2_M3", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "OBSP_RESET_V2_M5", OBJPROP_XDISTANCE, x + 174);
   ObjectSetInteger(0, "OBSP_RESET_V2_M5", OBJPROP_YDISTANCE, y);
}

//+------------------------------------------------------------------+
void DeleteV2ResetButtons()
{
   ObjectDelete(0, "OBSP_RESET_V2_M1");
   ObjectDelete(0, "OBSP_RESET_V2_M3");
   ObjectDelete(0, "OBSP_RESET_V2_M5");
}

//+------------------------------------------------------------------+
void WriteV2ResetRequest(const string tf)
{
   FolderCreate(FileBridgeFolder, FILE_COMMON);
   string name = FileBridgeFolder + "\\RESET_V2_" + tf + ".flag";
   int handle = FileOpen(name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("V2 reset request write failed: ", name, " | error=", GetLastError());
      return;
   }
   FileWriteString(handle, IntegerToString((long)TimeCurrent()));
   FileClose(handle);
   Print("V2 reset requested for ", tf, " -- flag written: ", name);
}

//+------------------------------------------------------------------+
void SetVirginObLabel(const int slot, const string text, const color clr, const int y)
{
   string name = "OBSP_VOB_" + IntegerToString(slot);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
   }
   ObjectSetInteger(0, name, OBJPROP_CORNER, BiasLabelCorner);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, BiasLabelX);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, BiasLabelFontSize);
   ObjectSetString(0, name, OBJPROP_FONT, BiasLabelFont);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
// Returns the Y position immediately after the last row drawn, so callers
// can place further UI elements below this list without overlapping it.
int UpdateVirginObLabels()
{
   int slot = 0;
   int y = BiasLabelYStart + ArraySize(g_targets) * BiasLabelRowHeight + VirginObListYGap;

   for(int i = 0; i < ArraySize(g_targets); i++)
   {
      if(g_state[i].bull_virgin > 0.5)
      {
         SetVirginObLabel(slot, g_targets[i].label + " Demand: " + PriceText(g_state[i].bull_high, g_state[i].bull_low),
                          BullishBiasColor, y);
         y += BiasLabelRowHeight;
         slot++;
      }

      if(g_state[i].bear_virgin > 0.5)
      {
         SetVirginObLabel(slot, g_targets[i].label + " Supply: " + PriceText(g_state[i].bear_high, g_state[i].bear_low),
                          BearishBiasColor, y);
         y += BiasLabelRowHeight;
         slot++;
      }
   }

   for(int i = slot; i < g_vob_slot_count; i++)
      ObjectDelete(0, "OBSP_VOB_" + IntegerToString(i));

   g_vob_slot_count = slot;
   return y;
}

//+------------------------------------------------------------------+
void ProcessTimeframe(const int idx, const string chart_symbol, const string publish_symbol)
{
   const ENUM_TIMEFRAMES period  = g_targets[idx].period;
   const int              minutes = g_targets[idx].minutes;

   long chart_id = FindChartForSymbolPeriod(chart_symbol, period);
   if(chart_id < 0)
   {
      // Chart not open for this timeframe. Leave the last published state
      // untouched rather than overwriting it with zeros on a transient miss.
      g_state[idx].chart_found = false;
      if(ShowPanel)
      {
         OBZone empty_zone;
         OBZone empty_hist[];
         g_panel_y = RenderTimeframeBlock(idx, chart_symbol, g_panel_y, false,
                                          false, empty_zone, false, empty_zone,
                                          empty_hist, empty_hist, false, 0.0, 0);
      }
      return;
   }
   g_state[idx].chart_found = true;

   ScanObjectsFor(chart_id, period, chart_symbol, minutes, idx);

   OBZone latest, latest_bull, latest_bear;
   bool has_latest = GetLatestZone("", latest);
   bool has_bull   = GetLatestZone("BULLISH", latest_bull);
   bool has_bear   = GetLatestZone("BEARISH", latest_bear);

   TFState st;
   ZeroMemory(st);
   st.chart_found = true;

   if(has_latest)
   {
      if(latest.direction == "BULLISH") st.bias = 1.0;
      else if(latest.direction == "BEARISH") st.bias = -1.0;

      st.latest_high            = latest.high;
      st.latest_low             = latest.low;
      st.latest_virgin          = (latest.virgin ? 1.0 : 0.0);
      st.latest_time            = (double)latest.start_time;
      st.latest_detected_time   = (double)latest.detected_time;
      st.latest_detected_price  = latest.detected_price;
      st.latest_visit_time      = (double)latest.visit_time;
      st.latest_validation_time = (double)latest.validation_time;
   }

   if(has_bull)
   {
      st.bull_high   = latest_bull.high;
      st.bull_low    = latest_bull.low;
      st.bull_virgin = (latest_bull.virgin ? 1.0 : 0.0);
   }

   if(has_bear)
   {
      st.bear_high   = latest_bear.high;
      st.bear_low    = latest_bear.low;
      st.bear_virgin = (latest_bear.virgin ? 1.0 : 0.0);
   }

   st.zone_count = ArraySize(zones);
   g_state[idx] = st;

   OBZone bull_history[];
   OBZone bear_history[];
   CollectRecentZones("BULLISH", ZoneHistoryDepth, bull_history);
   CollectRecentZones("BEARISH", ZoneHistoryDepth, bear_history);

   if(PublishGlobalVariables)
      PublishGVFor(st, publish_symbol, minutes);

   if(PublishToFile)
      PublishFileFor(st, publish_symbol, minutes, bull_history, bear_history);

   if(ShowPanel)
   {
      double atr_value; int atr_trend;
      bool atr_ok = GetATRTrail(idx, chart_symbol, atr_value, atr_trend);

      g_panel_y = RenderTimeframeBlock(idx, chart_symbol, g_panel_y, true,
                                       has_bull, latest_bull, has_bear, latest_bear,
                                       bull_history, bear_history, atr_ok, atr_value, atr_trend);
   }
}

//+------------------------------------------------------------------+
long CurrentPeriodObjectMaskFor(const ENUM_TIMEFRAMES period)
{
   switch(period)
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

//+------------------------------------------------------------------+
bool IsObjectVisibleOnPeriod(const long chart_id, const string name, const ENUM_TIMEFRAMES period)
{
   const long visibility = ObjectGetInteger(chart_id, name, OBJPROP_TIMEFRAMES);

   if(visibility == OBJ_ALL_PERIODS)
      return true;

   if(visibility == OBJ_NO_PERIODS)
      return false;

   const long current_mask = CurrentPeriodObjectMaskFor(period);
   return ((visibility & current_mask) != 0);
}

//+------------------------------------------------------------------+
int FindZoneBySignature(const string signature)
{
   for(int i = 0; i < ArraySize(zones); i++)
      if(zones[i].signature == signature)
         return i;

   return -1;
}

//+------------------------------------------------------------------+
void ScanObjectsFor(const long chart_id, const ENUM_TIMEFRAMES period, const string symbol,
                    const int minutes, const int tf_idx)
{
   ArrayResize(zones, 0);

   int total = ObjectsTotal(chart_id, 0, -1);

   for(int i = 0; i < total; i++)
   {
      string name = ObjectName(chart_id, i, 0, -1);
      if(name == "")
         continue;

      if(OB_ObjectKeyword != "" && StringFind(name, OB_ObjectKeyword) < 0)
         continue;

      ENUM_OBJECT type = (ENUM_OBJECT)ObjectGetInteger(chart_id, name, OBJPROP_TYPE);
      if(type != OBJ_RECTANGLE)
         continue;

      // Objects that exist on the chart but are hidden on this timeframe must
      // not be published as active zones. The source OB indicator may retain
      // such rectangles internally while showing only its configured maximum.
      if(!IsObjectVisibleOnPeriod(chart_id, name, period))
         continue;

      double p1 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 0);
      double p2 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 1);
      if(p1 <= 0.0 || p2 <= 0.0)
         continue;

      datetime t1 = (datetime)ObjectGetInteger(chart_id, name, OBJPROP_TIME, 0);
      datetime t2 = (datetime)ObjectGetInteger(chart_id, name, OBJPROP_TIME, 1);
      color c = (color)ObjectGetInteger(chart_id, name, OBJPROP_COLOR);

      OBZone z;
      z.name       = name;
      z.high       = MathMax(p1, p2);
      z.low        = MathMin(p1, p2);
      z.start_time = (t1 < t2 ? t1 : t2);
      z.end_time   = (t1 > t2 ? t1 : t2);
      z.direction  = DetectDirection(name, c);
      z.signature  = BuildSignature(z, minutes);
      z.virgin         = true;
      z.visit_time      = 0;
      z.validation_time = 0;
      z.detected_time   = 0;
      z.detected_price  = 0.0;
      z.baseline        = false;

      AssignDetectionState(z, symbol, tf_idx);

      // During indicator startup the source may briefly expose duplicate
      // rectangle objects with different names but identical zone geometry.
      // Keep only one active zone for each unique signature.
      int existing = FindZoneBySignature(z.signature);
      if(existing >= 0)
      {
         if(z.end_time > zones[existing].end_time)
            zones[existing] = z;
         continue;
      }

      int n = ArraySize(zones);
      ArrayResize(zones, n + 1);
      zones[n] = z;
   }

   if(UseOverlapDirectionFix)
      ApplyOverlapDirectionFix();

   for(int i = 0; i < ArraySize(zones); i++)
   {
      if(zones[i].direction != "BULLISH" && zones[i].direction != "BEARISH")
         continue;

      int state_idx = FindDetectionState(zones[i].signature);

      // Already conclusively resolved as tested on a previous scan -- reuse
      // it instead of re-walking this zone's entire bar history again.
      // Virgin only ever transitions one way (true -> false), so a
      // resolved zone can never need to be checked again.
      if(state_idx >= 0 && detection_states[state_idx].resolved)
      {
         zones[i].virgin          = false;
         zones[i].visit_time      = detection_states[state_idx].resolved_visit_time;
         zones[i].validation_time = detection_states[state_idx].resolved_validation_time;
         continue;
      }

      datetime visit_time = 0;
      bool visited = HasZoneBeenRetested(zones[i], visit_time, symbol, period);

      // Historical validation/retest reconstruction and live price monitoring
      // are intentionally independent. A missing historical validation must
      // never prevent a real-time touch from changing Virgin to false.
      if(!visited)
         visited = ApplyIndependentLiveTouch(zones[i], visit_time, symbol);

      zones[i].virgin = !visited;
      zones[i].visit_time = visit_time;

      if(visited && state_idx >= 0)
      {
         detection_states[state_idx].resolved                 = true;
         detection_states[state_idx].resolved_visit_time      = visit_time;
         detection_states[state_idx].resolved_validation_time = zones[i].validation_time;
         g_detection_states_dirty = true;
      }
   }

   // Keep baseline mode active until this timeframe has actually shown at
   // least one OB rectangle. This prevents all startup objects from being
   // stamped later with the same live detection time.
   if(ArraySize(zones) > 0)
      g_first_scan[tf_idx] = false;
}

//+------------------------------------------------------------------+
double DetectionMarketPrice(const string symbol)
{
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

   if(UseMidPriceForDetection && bid > 0.0 && ask > 0.0)
      return (bid + ask) * 0.5;

   return (bid > 0.0 ? bid : ask);
}

//+------------------------------------------------------------------+
int FindDetectionState(string signature)
{
   for(int i = 0; i < ArraySize(detection_states); i++)
      if(detection_states[i].signature == signature)
         return i;

   return -1;
}

//+------------------------------------------------------------------+
void AssignDetectionState(OBZone &z, const string symbol, const int tf_idx)
{
   int index = FindDetectionState(z.signature);

   if(index < 0)
   {
      OBDetectionState state;
      state.signature      = z.signature;
      state.baseline       = (g_first_scan[tf_idx] && TreatExistingObjectsAsBaseline);
      state.detected_time  = (state.baseline ? 0 : TimeCurrent());
      state.detected_price = (state.baseline ? 0.0 : DetectionMarketPrice(symbol));
      state.live_visited   = false;
      state.live_visit_time= 0;
      state.resolved                 = false;
      state.resolved_visit_time      = 0;
      state.resolved_validation_time = 0;

      int size = ArraySize(detection_states);
      ArrayResize(detection_states, size + 1);
      detection_states[size] = state;
      index = size;
      g_detection_states_dirty = true;
   }

   z.detected_time  = detection_states[index].detected_time;
   z.detected_price = detection_states[index].detected_price;
   z.baseline       = detection_states[index].baseline;
}

//+------------------------------------------------------------------+
string DetectDirection(string name, color c)
{
   string lower = StringToLowerCopy(name);

   if(StringFind(lower, "bull") >= 0 ||
      StringFind(lower, "buy") >= 0 ||
      StringFind(lower, "demand") >= 0)
      return "BULLISH";

   if(StringFind(lower, "bear") >= 0 ||
      StringFind(lower, "sell") >= 0 ||
      StringFind(lower, "supply") >= 0)
      return "BEARISH";

   int r = (int)c & 0xFF;
   int g = ((int)c >> 8) & 0xFF;
   int b = ((int)c >> 16) & 0xFF;

   if(g >= DirectionColorMinChannel && g >= r + DirectionColorGap && g >= b + DirectionColorGap)
      return "BULLISH";

   if(r >= DirectionColorMinChannel && r >= g + DirectionColorGap && r >= b + DirectionColorGap)
      return "BEARISH";

   return "UNKNOWN";
}

//+------------------------------------------------------------------+
void ApplyOverlapDirectionFix()
{
   for(int i = 0; i < ArraySize(zones); i++)
   {
      if(zones[i].direction != "UNKNOWN")
         continue;

      double best_bull = 0.0;
      double best_bear = 0.0;

      for(int j = 0; j < ArraySize(zones); j++)
      {
         if(i == j)
            continue;

         if(zones[j].direction != "BULLISH" && zones[j].direction != "BEARISH")
            continue;

         double overlap = OverlapPercent(zones[i].high, zones[i].low, zones[j].high, zones[j].low);

         if(zones[j].direction == "BULLISH")
            best_bull = MathMax(best_bull, overlap);
         else if(zones[j].direction == "BEARISH")
            best_bear = MathMax(best_bear, overlap);
      }

      if(best_bull < OverlapDirectionMinPercent && best_bear < OverlapDirectionMinPercent)
         continue;

      if(best_bull >= OverlapDirectionMinPercent &&
         best_bull >= best_bear + OverlapDirectionTieGapPercent)
         zones[i].direction = "BULLISH";
      else if(best_bear >= OverlapDirectionMinPercent &&
              best_bear >= best_bull + OverlapDirectionTieGapPercent)
         zones[i].direction = "BEARISH";
   }
}

//+------------------------------------------------------------------+
double OverlapPercent(double high1, double low1, double high2, double low2)
{
   double top = MathMin(high1, high2);
   double bottom = MathMax(low1, low2);
   double overlap = top - bottom;

   if(overlap <= 0.0)
      return 0.0;

   double height = MathMax(0.0000001, high1 - low1);
   return (overlap / height) * 100.0;
}

//+------------------------------------------------------------------+
bool IsCurrentMarketTouchingZone(const OBZone &z, const string symbol)
{
   const double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   const double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

   if(bid <= 0.0 || ask <= 0.0 || z.high <= z.low)
      return false;

   // The live market/spread overlaps the rectangle's price interval.
   return (bid <= z.high && ask >= z.low);
}

//+------------------------------------------------------------------+
bool ApplyIndependentLiveTouch(OBZone &z, datetime &visit_time, const string symbol)
{
   const int index = FindDetectionState(z.signature);
   if(index < 0)
      return false;

   // Once visited live, keep the state permanently for this indicator run,
   // even after price moves away from the zone.
   if(detection_states[index].live_visited)
   {
      visit_time = detection_states[index].live_visit_time;
      return true;
   }

   // Existing/baseline zones are monitored immediately. For a newly detected
   // live zone, do not count the exact creation tick itself as a retest.
   if(!z.baseline && z.detected_time > 0 && TimeCurrent() <= z.detected_time)
      return false;

   if(!IsCurrentMarketTouchingZone(z, symbol))
      return false;

   detection_states[index].live_visited    = true;
   detection_states[index].live_visit_time = TimeCurrent();
   visit_time = detection_states[index].live_visit_time;
   g_detection_states_dirty = true;

   Print("LIVE OB VISIT: ", z.signature,
         " | ", z.direction,
         " | zone=", DoubleToString(z.low, 5),
         "-", DoubleToString(z.high, 5),
         " | time=", TimeToString(visit_time, TIME_DATE|TIME_SECONDS));

   return true;
}

//+------------------------------------------------------------------+
bool HasHistoricalZoneBeenRetested(OBZone &z, datetime &visit_time, datetime &validation_time,
                                    const string symbol, const ENUM_TIMEFRAMES period)
{
   visit_time = 0;
   validation_time = 0;

   const int origin_shift = iBarShift(symbol, period, z.start_time, false);
   if(origin_shift < 0)
      return false;

   const int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   const double origin_close = iClose(symbol, period, origin_shift);
   if(origin_close <= 0.0)
      return false;

   int validation_shift = -1;

   // Reconstruct validation using the same close-based rule used by the source OB indicator.
   // Bullish: first later candle closing above the OB origin candle close.
   // Bearish: first later candle closing below the OB origin candle close.
   for(int shift = origin_shift - 1; shift >= check_to; shift--)
   {
      const double candle_close = iClose(symbol, period, shift);

      if(z.direction == "BULLISH" && candle_close > origin_close)
      {
         validation_shift = shift;
         break;
      }

      if(z.direction == "BEARISH" && candle_close < origin_close)
      {
         validation_shift = shift;
         break;
      }
   }

   if(validation_shift < 0)
      return false;

   validation_time = iTime(symbol, period, validation_shift);

   // Only candles AFTER validation may count as a retest.
   for(int shift = validation_shift - 1; shift >= check_to; shift--)
   {
      const double candle_high = iHigh(symbol, period, shift);
      const double candle_low  = iLow(symbol, period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(symbol, period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasLiveZoneBeenRetested(OBZone &z, datetime &visit_time, const string symbol, const ENUM_TIMEFRAMES period)
{
   visit_time = 0;

   if(z.detected_time <= 0)
      return false;

   const int detection_shift = iBarShift(symbol, period, z.detected_time, false);
   if(detection_shift < 0)
      return false;

   const int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   const int skip_bars = MathMax(1, RetestSkipBarsFromDetection);
   const int first_check_shift = detection_shift - skip_bars;

   if(first_check_shift < check_to)
      return false;

   // A live rectangle is already a validated OB. Ignore everything before
   // detection and the detection candle itself. Any later wick overlap is a retest.
   for(int shift = first_check_shift; shift >= check_to; shift--)
   {
      const double candle_high = iHigh(symbol, period, shift);
      const double candle_low  = iLow(symbol, period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(symbol, period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasZoneBeenRetested(OBZone &z, datetime &visit_time, const string symbol, const ENUM_TIMEFRAMES period)
{
   visit_time = 0;
   z.validation_time = 0;

   // Existing rectangles: reconstruct validation from the origin candle close,
   // then count only a later zone touch as the retest.
   if(z.baseline || z.detected_time <= 0)
      return HasHistoricalZoneBeenRetested(z, visit_time, z.validation_time, symbol, period);

   // Fresh live rectangles: appearance itself is validation.
   z.validation_time = z.detected_time;
   return HasLiveZoneBeenRetested(z, visit_time, symbol, period);
}

//+------------------------------------------------------------------+
bool GetLatestZone(string direction_filter, OBZone &z)
{
   bool found = false;
   datetime latest_time = 0;

   for(int i = 0; i < ArraySize(zones); i++)
   {
      if(zones[i].direction != "BULLISH" && zones[i].direction != "BEARISH")
         continue;

      if(direction_filter != "" && zones[i].direction != direction_filter)
         continue;

      if(!found || zones[i].start_time > latest_time)
      {
         z = zones[i];
         latest_time = zones[i].start_time;
         found = true;
      }
   }

   return found;
}

//+------------------------------------------------------------------+
// Newest-first list of up to max_count zones for one direction, from the
// zones[] scratch array already scanned for the current timeframe.
void CollectRecentZones(const string direction_filter, const int max_count, OBZone &out[])
{
   ArrayResize(out, 0);

   int n = ArraySize(zones);
   bool used[];
   ArrayResize(used, n);
   ArrayInitialize(used, false);

   while(ArraySize(out) < max_count)
   {
      int best = -1;
      datetime best_time = 0;

      for(int i = 0; i < n; i++)
      {
         if(used[i] || zones[i].direction != direction_filter)
            continue;

         if(best < 0 || zones[i].start_time > best_time)
         {
            best = i;
            best_time = zones[i].start_time;
         }
      }

      if(best < 0)
         break;

      used[best] = true;
      int idx = ArraySize(out);
      ArrayResize(out, idx + 1);
      out[idx] = zones[best];
   }
}

//+------------------------------------------------------------------+
string GVBaseFor(const string symbol, const int minutes)
{
   return GlobalVariablePrefix + "_" + symbol + "_" + IntegerToString(minutes);
}

//+------------------------------------------------------------------+
void PublishGVFor(const TFState &st, const string symbol, const int minutes)
{
   string base = GVBaseFor(symbol, minutes);

   GlobalVariableSet(base + "_BIAS", st.bias);
   GlobalVariableSet(base + "_LATEST_HIGH", st.latest_high);
   GlobalVariableSet(base + "_LATEST_LOW", st.latest_low);
   GlobalVariableSet(base + "_LATEST_VIRGIN", st.latest_virgin);
   GlobalVariableSet(base + "_LATEST_TIME", st.latest_time);
   GlobalVariableSet(base + "_LATEST_DETECTED_TIME", st.latest_detected_time);
   GlobalVariableSet(base + "_LATEST_DETECTED_PRICE", st.latest_detected_price);
   GlobalVariableSet(base + "_LATEST_VISIT_TIME", st.latest_visit_time);
   GlobalVariableSet(base + "_LATEST_VALIDATION_TIME", st.latest_validation_time);

   GlobalVariableSet(base + "_BULL_HIGH", st.bull_high);
   GlobalVariableSet(base + "_BULL_LOW", st.bull_low);
   GlobalVariableSet(base + "_BULL_VIRGIN", st.bull_virgin);

   GlobalVariableSet(base + "_BEAR_HIGH", st.bear_high);
   GlobalVariableSet(base + "_BEAR_LOW", st.bear_low);
   GlobalVariableSet(base + "_BEAR_VIRGIN", st.bear_virgin);

   GlobalVariableSet(base + "_UPDATED", (double)TimeCurrent());
}

//+------------------------------------------------------------------+
string JsonNumber(const double v){ return DoubleToString(v, 8); }

//+------------------------------------------------------------------+
string JsonZoneArray(OBZone &arr[])
{
   string j = "[";
   for(int i = 0; i < ArraySize(arr); i++)
   {
      if(i > 0) j += ",";
      j += "{";
      j += "\"high\":" + JsonNumber(arr[i].high) + ",";
      j += "\"low\":" + JsonNumber(arr[i].low) + ",";
      j += "\"virgin\":" + (arr[i].virgin ? "true" : "false") + ",";
      j += "\"start_time\":" + IntegerToString((long)arr[i].start_time) + ",";
      j += "\"detected_time\":" + IntegerToString((long)arr[i].detected_time) + ",";
      j += "\"detected_price\":" + JsonNumber(arr[i].detected_price);
      j += "}";
   }
   j += "]";
   return j;
}

//+------------------------------------------------------------------+
string BuildStateJsonFor(const TFState &st, const string symbol, const int minutes,
                         OBZone &bull_hist[], OBZone &bear_hist[])
{
   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"bias\":" + IntegerToString((int)st.bias) + ",";
   j += "\"latest\":{";
   j += "\"high\":" + JsonNumber(st.latest_high) + ",";
   j += "\"low\":" + JsonNumber(st.latest_low) + ",";
   j += "\"virgin\":" + (st.latest_virgin > 0.5 ? "true" : "false") + ",";
   j += "\"time\":" + IntegerToString((long)st.latest_time) + ",";
   j += "\"detected_time\":" + IntegerToString((long)st.latest_detected_time) + ",";
   j += "\"detected_price\":" + JsonNumber(st.latest_detected_price) + ",";
   j += "\"visit_time\":" + IntegerToString((long)st.latest_visit_time) + ",";
   j += "\"validation_time\":" + IntegerToString((long)st.latest_validation_time);
   j += "},";
   j += "\"bull\":" + JsonZoneArray(bull_hist) + ",";
   j += "\"bear\":" + JsonZoneArray(bear_hist);
   j += "}";
   return j;
}

//+------------------------------------------------------------------+
void PublishFileFor(const TFState &st, const string symbol, const int minutes,
                    OBZone &bull_hist[], OBZone &bear_hist[])
{
   const string final_name = FileBridgeFolder + "\\" + GVBaseFor(symbol, minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("OB bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
   }

   FileWriteString(handle, BuildStateJsonFor(st, symbol, minutes, bull_hist, bear_hist));
   FileClose(handle);

   // Write-then-rename so an external reader (e.g. Python) can never observe
   // a half-written file mid-scan.
   if(!FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE))
      Print("OB bridge file publish failed to finalize: ", final_name, " | error=", GetLastError());
}

//+------------------------------------------------------------------+
string BiasText(const double bias)
{
   if(bias > 0.5)  return "BULLISH";
   if(bias < -0.5) return "BEARISH";
   return "NONE";
}

//+------------------------------------------------------------------+
color BiasColorFor(const double bias)
{
   if(bias > 0.5)  return BullishBiasColor;
   if(bias < -0.5) return BearishBiasColor;
   return NeutralBiasColor;
}

//+------------------------------------------------------------------+
string BiasLabelName(const int idx)
{
   return "OBSP_BIAS_" + g_targets[idx].label;
}

//+------------------------------------------------------------------+
void UpdateBiasLabels()
{
   if(!ShowBiasLabels)
   {
      for(int i = 0; i < ArraySize(g_targets); i++)
         ObjectDelete(0, BiasLabelName(i));
      return;
   }

   for(int i = 0; i < ArraySize(g_targets); i++)
   {
      const string name = BiasLabelName(i);
      const string txt   = g_targets[i].label + ": " + BiasText(g_state[i].bias);
      const color  clr   = BiasColorFor(g_state[i].bias);

      if(ObjectFind(0, name) < 0)
      {
         ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, name, OBJPROP_BACK, false);
      }

      ObjectSetInteger(0, name, OBJPROP_CORNER, BiasLabelCorner);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, BiasLabelX);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, BiasLabelYStart + i * BiasLabelRowHeight);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, BiasLabelFontSize);
      ObjectSetString(0, name, OBJPROP_FONT, BiasLabelFont);
      ObjectSetString(0, name, OBJPROP_TEXT, txt);
   }
}

//+------------------------------------------------------------------+
void SetPanelLabel(const string name, const string text, const color clr, const int x, const int y,
                   const int font_size, const string font)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
   }
   ObjectSetInteger(0, name, OBJPROP_CORNER, PanelCorner);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetString(0, name, OBJPROP_TEXT, text);

   // Every panel label funnels through here, so this is the one place that
   // can track the panel's true rightmost edge as it's actually drawn --
   // used by ScanAndPublishAll to keep the reset button/status column
   // clear of it, without hardcoding a width guess that a long zone or
   // detection string could someday outgrow.
   TextSetFont(font, -(font_size * 10), 0, 0);
   uint tw, th;
   TextGetSize(text, tw, th);
   g_panel_max_x = (int)MathMax(g_panel_max_x, x + (int)tw);
}

//+------------------------------------------------------------------+
string VirginText(const bool v) { return v ? "Virgin" : "Tested"; }

//+------------------------------------------------------------------+
// Drops the leading "yyyy." from TimeToString's fixed "yyyy.mm.dd hh:mi"
// output -- shaves 5 characters off every real timestamp shown, which
// matters given how little margin these single-line panel entries have
// before hitting MT5's apparent per-label length limit.
string ShortTime(datetime t)
{
   string full = TimeToString(t, TIME_DATE | TIME_MINUTES);
   return StringSubstr(full, 5);
}

//+------------------------------------------------------------------+
string DetectionText(OBZone &z)
{
   if(z.baseline || z.detected_time <= 0)
      return "baseline";
   return ShortTime(z.detected_time);
}

//+------------------------------------------------------------------+
string RetestText(OBZone &z)
{
   if(z.virgin || z.visit_time <= 0)
      return "--";
   return ShortTime(z.visit_time);
}

//+------------------------------------------------------------------+
// A timeframe is in the No Long/No Short classification set (default
// H4,H2,H1,M30,M15 -- the higher timeframes; M5/M3/M1 are entry
// timeframes and deliberately excluded) if its label appears in
// NoTradeZoneTimeframes.
bool IsNoTradeZoneTimeframe(const string label)
{
   string parts[];
   ushort sep = (ushort)StringGetCharacter(",", 0);
   int n = StringSplit(NoTradeZoneTimeframes, sep, parts);
   for(int i = 0; i < n; i++)
      if(TrimStr(parts[i]) == label)
         return true;
   return false;
}

//+------------------------------------------------------------------+
// Header is built from up to 4 side-by-side label objects instead of one,
// since a single OBJ_LABEL can only carry one color for its whole text:
// "H4, Recent: " (neutral) | "BULLISH OB"/"BEARISH OB" (bull/bear color) |
// ", ATR Trail: " (neutral) | the value itself (bull/bear color by
// whether price is currently above/below that trail). TextGetSize gives
// each segment's rendered pixel width so the next segment lines up
// immediately after it with no gap or overlap.
void DrawHeaderSegments(const string label, const int y, const bool available,
                        const bool has_ob, const double bias,
                        const bool atr_ok, const double atr_value, const int atr_trend,
                        const int digits)
{
   string texts[4];
   color  colors[4];
   int    count = 0;

   if(!available)
   {
      texts[0] = label + ": chart not open"; colors[0] = NeutralBiasColor;
      count = 1;
   }
   else
   {
      texts[0] = label + ", Recent: ";                       colors[0] = NeutralBiasColor;
      texts[1] = (has_ob ? BiasText(bias) : "NONE") + " OB"; colors[1] = has_ob ? BiasColorFor(bias) : NeutralBiasColor;
      count = 2;

      if(atr_ok)
      {
         texts[2] = ", ATR Trail: ";                    colors[2] = NeutralBiasColor;
         texts[3] = DoubleToString(atr_value, digits);
         colors[3] = (atr_trend > 0) ? BullishBiasColor : (atr_trend < 0 ? BearishBiasColor : NeutralBiasColor);
         count = 4;
      }
   }

   TextSetFont(PanelHeaderFont, -(PanelHeaderFontSize * 10), 0, 0);

   int x = PanelX;
   for(int i = 0; i < 4; i++)
   {
      string name = "OBP_HDR_" + label + "_" + IntegerToString(i);
      if(i < count)
      {
         SetPanelLabel(name, texts[i], colors[i], x, y, PanelHeaderFontSize, PanelHeaderFont);
         uint w, h;
         TextGetSize(texts[i], w, h);
         x += (int)w;
      }
      else
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
// Draws one timeframe's block (bold header + small Supply/Demand lines +
// small "Additional Zones" history) starting at y, and returns the y for
// the next block. bull_hist/bear_hist are newest-first (index 0 is the
// same zone as latest_bull/latest_bear); "Additional Zones" shows index 1
// onward, i.e. everything beyond what the header/Supply-Demand lines
// already cover.
int RenderTimeframeBlock(const int idx, const string symbol, int y, const bool available,
                         const bool has_bull, OBZone &latest_bull,
                         const bool has_bear, OBZone &latest_bear,
                         OBZone &bull_hist[], OBZone &bear_hist[],
                         const bool atr_ok, const double atr_value, const int atr_trend)
{
   string label = g_targets[idx].label;
   int    digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = 5;

   double bias = 0.0;
   bool   has_ob = has_bull || has_bear;
   if(has_ob)
      bias = (has_bull && (!has_bear || latest_bull.start_time >= latest_bear.start_time)) ? 1.0 : -1.0;

   DrawHeaderSegments(label, y, available, has_ob, bias, atr_ok, atr_value, atr_trend, digits);
   y += PanelHeaderLineHeight;

   // Two independent columns: Supply (bold, colored) and its own older
   // history stack on the LEFT; Demand (bold, colored) and its own older
   // history stack on the RIGHT. Each column advances its own y
   // independently since one side can have more history than the other;
   // the block as a whole advances by whichever column ended up taller.
   int extra_bull = available ? MathMax(0, ArraySize(bull_hist) - 1) : 0;
   int extra_bear = available ? MathMax(0, ArraySize(bear_hist) - 1) : 0;
   int shown_bull = MathMin(extra_bull, MaxAdditionalZonesShown);
   int shown_bear = MathMin(extra_bear, MaxAdditionalZonesShown);

   int y_body   = y;
   int y_supply = y_body;
   int y_demand = y_body;
   int y_zone   = y_body;   // No Long/No Short column -- tracked independently, only merged in at the very end so it never pads out Supply/Demand's own Additional Zones
   int demand_x = PanelX + PanelRightColumnX;

   if(available)
   {
      TextSetFont(PanelBoldContentFont, -(PanelContentFontSize * 10), 0, 0);

      string supply_text = "Supply: " + (has_bear ? PriceText(latest_bear.high, latest_bear.low, digits) + " " + VirginText(latest_bear.virgin) +
                           " D:" + DetectionText(latest_bear) + " R:" + RetestText(latest_bear) : "none");
      // Orange while live price sits inside this zone right now -- reverts
      // to plain Bearish/BullishBiasColor the moment price moves back out.
      color  supply_color = has_bear ? (IsCurrentMarketTouchingZone(latest_bear, symbol) ? ActiveTouchColor : BearishBiasColor) : NeutralBiasColor;
      SetPanelLabel("OBP_SUP_" + label, supply_text, supply_color, PanelX, y_supply, PanelContentFontSize, PanelBoldContentFont);
      y_supply += PanelLineHeight;

      uint sw, sh;
      TextGetSize(supply_text, sw, sh);
      demand_x = PanelX + MathMax(PanelRightColumnX, (int)sw + PanelColumnPadding);

      string demand_text = "Demand: " + (has_bull ? PriceText(latest_bull.high, latest_bull.low, digits) + " " + VirginText(latest_bull.virgin) +
                           " D:" + DetectionText(latest_bull) + " R:" + RetestText(latest_bull) : "none");
      color  demand_color = has_bull ? (IsCurrentMarketTouchingZone(latest_bull, symbol) ? ActiveTouchColor : BullishBiasColor) : NeutralBiasColor;
      SetPanelLabel("OBP_DEM_" + label, demand_text, demand_color, demand_x, y_demand, PanelContentFontSize, PanelBoldContentFont);
      y_demand += PanelLineHeight;

      // Fixed alignment (PanelThirdColumnX) -- only shifts further right if
      // this column's actual widest text would otherwise overrun it, so
      // every row's Reversal Zone entry lines up in one straight column.
      // Must measure the D2/D3/D4 Additional Zones rows too, not just the
      // primary Demand line -- they sit in the same column (demand_x) and
      // are often just as long.
      uint dw, dh;
      TextGetSize(demand_text, dw, dh);
      int demand_col_w = (int)dw;

      TextSetFont(PanelContentFont, -(PanelContentFontSize * 10), 0, 0);
      for(int i = 0; i < shown_bull; i++)
      {
         OBZone z = bull_hist[i + 1];
         string dtext_probe = "D" + IntegerToString(i + 2) + ") " + PriceText(z.high, z.low, digits) + " " + VirginText(z.virgin) +
                              " D:" + DetectionText(z) + " R:" + RetestText(z);
         uint pw, ph;
         TextGetSize(dtext_probe, pw, ph);
         demand_col_w = (int)MathMax(demand_col_w, (int)pw);
      }
      TextSetFont(PanelBoldContentFont, -(PanelContentFontSize * 10), 0, 0);

      int zone_x = PanelX + MathMax(PanelThirdColumnX, (demand_x - PanelX) + demand_col_w + PanelColumnPadding);

      // Third column: virgin zones only, from the classified timeframes
      // (NoTradeZoneTimeframes). A virgin Supply zone is a Bearish
      // Reversal Zone; a virgin Demand zone is a Bullish Reversal Zone.
      bool classified = IsNoTradeZoneTimeframe(label);

      if(classified)
      {
         int bear_count = MathMin(ArraySize(bear_hist), MaxAdditionalZonesShown + 1);
         for(int i = 0; i < bear_count; i++)
         {
            string name = "OBP_NOLONG_" + label + "_" + IntegerToString(i);
            if(bear_hist[i].virgin)
            {
               string text = label + " Bearish Reversal Zone: " + PriceText(bear_hist[i].high, bear_hist[i].low, digits);
               SetPanelLabel(name, text, BearishBiasColor, zone_x, y_zone, PanelContentFontSize, PanelBoldContentFont);
               y_zone += PanelLineHeight;
            }
            else
               ObjectDelete(0, name);
         }
         for(int i = bear_count; i < MaxAdditionalZonesShown + 1; i++)
            ObjectDelete(0, "OBP_NOLONG_" + label + "_" + IntegerToString(i));

         int bull_count = MathMin(ArraySize(bull_hist), MaxAdditionalZonesShown + 1);
         for(int i = 0; i < bull_count; i++)
         {
            string name = "OBP_NOSHORT_" + label + "_" + IntegerToString(i);
            if(bull_hist[i].virgin)
            {
               string text = label + " Bullish Reversal Zone: " + PriceText(bull_hist[i].high, bull_hist[i].low, digits);
               SetPanelLabel(name, text, BullishBiasColor, zone_x, y_zone, PanelContentFontSize, PanelBoldContentFont);
               y_zone += PanelLineHeight;
            }
            else
               ObjectDelete(0, name);
         }
         for(int i = bull_count; i < MaxAdditionalZonesShown + 1; i++)
            ObjectDelete(0, "OBP_NOSHORT_" + label + "_" + IntegerToString(i));
      }
      else
      {
         for(int i = 0; i < MaxAdditionalZonesShown + 1; i++)
         {
            ObjectDelete(0, "OBP_NOLONG_" + label + "_" + IntegerToString(i));
            ObjectDelete(0, "OBP_NOSHORT_" + label + "_" + IntegerToString(i));
         }
      }
   }
   else
   {
      ObjectDelete(0, "OBP_SUP_" + label);
      ObjectDelete(0, "OBP_DEM_" + label);
      for(int i = 0; i < MaxAdditionalZonesShown + 1; i++)
      {
         ObjectDelete(0, "OBP_NOLONG_" + label + "_" + IntegerToString(i));
         ObjectDelete(0, "OBP_NOSHORT_" + label + "_" + IntegerToString(i));
      }
   }

   TextSetFont(PanelContentFont, -(PanelContentFontSize * 10), 0, 0);
   for(int i = 0; i < MaxAdditionalZonesShown; i++)
   {
      string sname = "OBP_ADD_" + label + "_S" + IntegerToString(i);
      if(i < shown_bear)
      {
         OBZone z = bear_hist[i + 1];
         string stext = "S" + IntegerToString(i + 2) + ") " + PriceText(z.high, z.low, digits) + " " + VirginText(z.virgin) +
                        " D:" + DetectionText(z) + " R:" + RetestText(z);
         SetPanelLabel(sname, stext, NeutralBiasColor, PanelX, y_supply, PanelContentFontSize, PanelContentFont);
         y_supply += PanelLineHeight;
      }
      else
         ObjectDelete(0, sname);

      string dname = "OBP_ADD_" + label + "_D" + IntegerToString(i);
      if(i < shown_bull)
      {
         OBZone z = bull_hist[i + 1];
         string dtext = "D" + IntegerToString(i + 2) + ") " + PriceText(z.high, z.low, digits) + " " + VirginText(z.virgin) +
                        " D:" + DetectionText(z) + " R:" + RetestText(z);
         SetPanelLabel(dname, dtext, NeutralBiasColor, demand_x, y_demand, PanelContentFontSize, PanelContentFont);
         y_demand += PanelLineHeight;
      }
      else
         ObjectDelete(0, dname);
   }

   // y_zone (No Long/No Short column) deliberately left out here -- the
   // next block's position follows Supply/Demand only, so those two never
   // get an artificial gap.
   y = MathMax(y_supply, y_demand) + PanelBlockGap;
   return y;
}

//+------------------------------------------------------------------+
string PriceText(double high, double low, int digits = 5)
{
   return DoubleToString(high, digits) + "-" + DoubleToString(low, digits);
}

//+------------------------------------------------------------------+
string BuildSignature(OBZone &z, const int minutes)
{
   return IntegerToString(minutes) + "|" +
          IntegerToString((int)z.start_time) + "|" +
          DoubleToString(z.high, 8) + "|" +
          DoubleToString(z.low, 8);
}

//+------------------------------------------------------------------+
string StringToLowerCopy(string s)
{
   string out = s;
   StringToLower(out);
   return out;
}
//+------------------------------------------------------------------+
