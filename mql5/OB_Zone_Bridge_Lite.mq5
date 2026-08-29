//+------------------------------------------------------------------+
//|                        OB_Zone_Bridge_Lite.mq5                    |
//| V4's lightweight OB zone bridge -- reads OB rectangles drawn by   |
//| the LuxAlgo-style "Order Block Detector" on THIS chart only       |
//| (unlike OB_StatePublisher_Indicator_v2.00.mq5, which is one       |
//| instance scanning every other open chart by chart ID for a list  |
//| of configured timeframes). V4 only needs M5/M3/M1, so this is    |
//| attached individually to each of those three charts -- same      |
//| per-chart-instance idiom as SurajBot_ATRTrail_..._DUAL.mq5's ATR |
//| bridge, not the single-instance-scans-many-charts approach.      |
//|                                                                    |
//| Keeps the same core zone-detection/virgin/retest logic as         |
//| OB_StatePublisher_Indicator_v2.00.mq5 (signature-deduped          |
//| rectangle scan, color/name direction detection, overlap-based     |
//| UNKNOWN-direction resolution, historical-vs-live retest           |
//| reconstruction) verbatim where it doesn't depend on multi-chart   |
//| scanning -- dropped entirely: the panel/Comment() UI, bias        |
//| labels, virgin-OB on-chart list, RESET buttons, BLOCK_STATUS.json |
//| reader, and GlobalVariable publishing (all algo_v2-specific, none |
//| of which V4 uses). Publishes to its own OBSTATE_LITE_<symbol>_    |
//| <minutes>.json filename so it never collides with the (currently  |
//| removed) 8-timeframe OBSTATE_<symbol>_<minutes>.json files the    |
//| old publisher wrote.                                              |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

input string OB_ObjectKeyword               = "pineBox";
input string BridgeSymbol                   = "";      // empty = use the attached chart's symbol
input int    ScanEverySeconds               = 5;   // was 1 -- lowered per explicit request to reduce chart-thread load
input int    ZoneHistoryDepth               = 5;        // recent zones per direction published to the JSON bridge

input int    DirectionColorMinChannel       = 20;
input int    DirectionColorGap              = 8;

input bool   UseOverlapDirectionFix         = true;
input double OverlapDirectionMinPercent     = 60.0;
input double OverlapDirectionTieGapPercent  = 20.0;

input bool   UseClosedCandlesOnlyForRetest  = false;
input int    RetestSkipBarsFromDetection    = 1;
input bool   TreatExistingObjectsAsBaseline = true;
input bool   UseMidPriceForDetection        = true;

input bool   PublishToFile                  = true;
input string FileBridgeFolder               = "OBBridge";  // same Common Files folder as the other bridges

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
   // Once a zone's retest status resolves to non-virgin, it can never go
   // back to virgin -- cache that fact so later scans skip the
   // historical/live retest reconstruction (iBarShift/iClose/iHigh/iLow
   // loops) for this zone entirely instead of re-running it every single
   // scan forever. Still-virgin zones are NOT cached (their status can
   // genuinely still change) -- only resolved ones skip re-checking.
   bool     resolved_non_virgin;
   datetime resolved_visit_time;
};

OBZone zones[];
OBDetectionState detection_states[];
bool   g_first_scan = true;

//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "" ? _Symbol : BridgeSymbol);
}

//+------------------------------------------------------------------+
int TfMinutes()
{
   int m = (int)(PeriodSeconds(_Period) / 60);
   return (m > 0 ? m : (int)_Period);
}

//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
bool IsObjectVisibleOnThisPeriod(const string name)
{
   const long visibility = ObjectGetInteger(0, name, OBJPROP_TIMEFRAMES);
   if(visibility == OBJ_ALL_PERIODS)
      return true;
   if(visibility == OBJ_NO_PERIODS)
      return false;
   return ((visibility & CurrentPeriodObjectMask()) != 0);
}

//+------------------------------------------------------------------+
int OnInit()
{
   if(PublishToFile)
      FolderCreate(FileBridgeFolder, FILE_COMMON);

   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);

   ScanAndPublish();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void OnTimer()
{
   ScanAndPublish();
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
   // Deliberately does NOT call ScanAndPublish() -- OnCalculate fires on
   // EVERY incoming tick (which for XAUUSD can be many times a second),
   // so doing a full object scan + retest reconstruction here as well as
   // on the OnTimer interval below was running the whole scan far more
   // often than intended, on top of the timer. The timer alone (every
   // ScanEverySeconds) is the only scan trigger now -- zone/bias data
   // doesn't need tick-level freshness the way price itself does.
   return rates_total;
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
string BuildSignature(const OBZone &z, const int minutes)
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
void AssignDetectionState(OBZone &z, const string symbol)
{
   int index = FindDetectionState(z.signature);

   if(index < 0)
   {
      OBDetectionState state;
      state.signature      = z.signature;
      state.baseline       = (g_first_scan && TreatExistingObjectsAsBaseline);
      state.detected_time  = (state.baseline ? 0 : TimeCurrent());
      state.detected_price = (state.baseline ? 0.0 : DetectionMarketPrice(symbol));
      state.live_visited   = false;
      state.live_visit_time= 0;
      state.resolved_non_virgin = false;
      state.resolved_visit_time = 0;

      int size = ArraySize(detection_states);
      ArrayResize(detection_states, size + 1);
      detection_states[size] = state;
      index = size;
   }

   z.detected_time  = detection_states[index].detected_time;
   z.detected_price = detection_states[index].detected_price;
   z.baseline       = detection_states[index].baseline;
}

//+------------------------------------------------------------------+
bool IsCurrentMarketTouchingZone(const OBZone &z, const string symbol)
{
   const double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   const double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

   if(bid <= 0.0 || ask <= 0.0 || z.high <= z.low)
      return false;

   return (bid <= z.high && ask >= z.low);
}

//+------------------------------------------------------------------+
bool ApplyIndependentLiveTouch(OBZone &z, datetime &visit_time, const string symbol)
{
   const int index = FindDetectionState(z.signature);
   if(index < 0)
      return false;

   if(detection_states[index].live_visited)
   {
      visit_time = detection_states[index].live_visit_time;
      return true;
   }

   if(!z.baseline && z.detected_time > 0 && TimeCurrent() <= z.detected_time)
      return false;

   if(!IsCurrentMarketTouchingZone(z, symbol))
      return false;

   detection_states[index].live_visited    = true;
   detection_states[index].live_visit_time = TimeCurrent();
   visit_time = detection_states[index].live_visit_time;

   Print("LIVE OB VISIT: ", z.signature,
         " | ", z.direction,
         " | zone=", DoubleToString(z.low, 5),
         "-", DoubleToString(z.high, 5),
         " | time=", TimeToString(visit_time, TIME_DATE|TIME_SECONDS));

   return true;
}

//+------------------------------------------------------------------+
bool HasHistoricalZoneBeenRetested(OBZone &z, datetime &visit_time, datetime &validation_time, const string symbol)
{
   visit_time = 0;
   validation_time = 0;

   const int origin_shift = iBarShift(symbol, _Period, z.start_time, false);
   if(origin_shift < 0)
      return false;

   const int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   const double origin_close = iClose(symbol, _Period, origin_shift);
   if(origin_close <= 0.0)
      return false;

   int validation_shift = -1;

   for(int shift = origin_shift - 1; shift >= check_to; shift--)
   {
      const double candle_close = iClose(symbol, _Period, shift);

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

   validation_time = iTime(symbol, _Period, validation_shift);

   for(int shift = validation_shift - 1; shift >= check_to; shift--)
   {
      const double candle_high = iHigh(symbol, _Period, shift);
      const double candle_low  = iLow(symbol, _Period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(symbol, _Period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasLiveZoneBeenRetested(OBZone &z, datetime &visit_time, const string symbol)
{
   visit_time = 0;

   if(z.detected_time <= 0)
      return false;

   const int detection_shift = iBarShift(symbol, _Period, z.detected_time, false);
   if(detection_shift < 0)
      return false;

   const int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   const int skip_bars = MathMax(1, RetestSkipBarsFromDetection);
   const int first_check_shift = detection_shift - skip_bars;

   if(first_check_shift < check_to)
      return false;

   for(int shift = first_check_shift; shift >= check_to; shift--)
   {
      const double candle_high = iHigh(symbol, _Period, shift);
      const double candle_low  = iLow(symbol, _Period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(symbol, _Period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasZoneBeenRetested(OBZone &z, datetime &visit_time, const string symbol)
{
   visit_time = 0;
   z.validation_time = 0;

   if(z.baseline || z.detected_time <= 0)
      return HasHistoricalZoneBeenRetested(z, visit_time, z.validation_time, symbol);

   z.validation_time = z.detected_time;
   return HasLiveZoneBeenRetested(z, visit_time, symbol);
}

//+------------------------------------------------------------------+
void ScanObjects(const string symbol)
{
   ArrayResize(zones, 0);
   const int minutes = TfMinutes();

   int total = ObjectsTotal(0, 0, -1);

   for(int i = 0; i < total; i++)
   {
      string name = ObjectName(0, i, 0, -1);
      if(name == "")
         continue;

      if(OB_ObjectKeyword != "" && StringFind(name, OB_ObjectKeyword) < 0)
         continue;

      ENUM_OBJECT type = (ENUM_OBJECT)ObjectGetInteger(0, name, OBJPROP_TYPE);
      if(type != OBJ_RECTANGLE)
         continue;

      if(!IsObjectVisibleOnThisPeriod(name))
         continue;

      double p1 = ObjectGetDouble(0, name, OBJPROP_PRICE, 0);
      double p2 = ObjectGetDouble(0, name, OBJPROP_PRICE, 1);
      if(p1 <= 0.0 || p2 <= 0.0)
         continue;

      datetime t1 = (datetime)ObjectGetInteger(0, name, OBJPROP_TIME, 0);
      datetime t2 = (datetime)ObjectGetInteger(0, name, OBJPROP_TIME, 1);
      color c = (color)ObjectGetInteger(0, name, OBJPROP_COLOR);

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

      AssignDetectionState(z, symbol);

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
      if(state_idx >= 0 && detection_states[state_idx].resolved_non_virgin)
      {
         // Already resolved non-virgin on a previous scan -- a zone can
         // never go back to virgin, so reuse the cached result instead of
         // re-running the historical/live retest reconstruction (this is
         // the expensive part: iBarShift/iClose/iHigh/iLow loops back
         // through the zone's whole lifetime) on every single scan.
         zones[i].virgin = false;
         zones[i].visit_time = detection_states[state_idx].resolved_visit_time;
         continue;
      }

      datetime visit_time = 0;
      bool visited = HasZoneBeenRetested(zones[i], visit_time, symbol);

      if(!visited)
         visited = ApplyIndependentLiveTouch(zones[i], visit_time, symbol);

      zones[i].virgin = !visited;
      zones[i].visit_time = visit_time;

      if(visited && state_idx >= 0)
      {
         detection_states[state_idx].resolved_non_virgin = true;
         detection_states[state_idx].resolved_visit_time = visit_time;
      }
   }

   if(ArraySize(zones) > 0)
      g_first_scan = false;
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
// Newest-first list of up to max_count zones for one direction.
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
string BuildStateJson(const string symbol, const int minutes,
                      bool has_latest, OBZone &latest,
                      OBZone &bull_hist[], OBZone &bear_hist[])
{
   double bias = 0.0;
   if(has_latest)
   {
      if(latest.direction == "BULLISH") bias = 1.0;
      else if(latest.direction == "BEARISH") bias = -1.0;
   }

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"bias\":" + IntegerToString((int)bias) + ",";
   j += "\"latest\":{";
   j += "\"high\":" + JsonNumber(has_latest ? latest.high : 0.0) + ",";
   j += "\"low\":" + JsonNumber(has_latest ? latest.low : 0.0) + ",";
   j += "\"virgin\":" + (has_latest && latest.virgin ? "true" : "false") + ",";
   j += "\"time\":" + IntegerToString(has_latest ? (long)latest.start_time : 0) + ",";
   j += "\"detected_time\":" + IntegerToString(has_latest ? (long)latest.detected_time : 0) + ",";
   j += "\"detected_price\":" + JsonNumber(has_latest ? latest.detected_price : 0.0);
   j += "},";
   j += "\"bull\":" + JsonZoneArray(bull_hist) + ",";
   j += "\"bear\":" + JsonZoneArray(bear_hist);
   j += "}";
   return j;
}

//+------------------------------------------------------------------+
void PublishFile(const string symbol, const int minutes, const string json)
{
   if(!PublishToFile)
      return;

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\OBSTATE_LITE_" + symbol + "_" + IntegerToString(minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("OB lite bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
   }

   FileWriteString(handle, json);
   FileClose(handle);

   if(!FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE))
      Print("OB lite bridge file publish failed to finalize: ", final_name, " | error=", GetLastError());
}

//+------------------------------------------------------------------+
void ScanAndPublish()
{
   const string symbol = EffectiveSymbol();
   const int minutes = TfMinutes();

   ScanObjects(symbol);

   OBZone latest;
   bool has_latest = GetLatestZone("", latest);

   OBZone bull_history[];
   OBZone bear_history[];
   CollectRecentZones("BULLISH", ZoneHistoryDepth, bull_history);
   CollectRecentZones("BEARISH", ZoneHistoryDepth, bear_history);

   string json = BuildStateJson(symbol, minutes, has_latest, latest, bull_history, bear_history);
   PublishFile(symbol, minutes, json);
}
//+------------------------------------------------------------------+
