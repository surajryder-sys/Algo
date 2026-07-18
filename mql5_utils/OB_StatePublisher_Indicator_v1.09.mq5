//+------------------------------------------------------------------+
//|                 OB_StatePublisher_Indicator.mq5                  |
//| Reads OB rectangles from chart objects, classifies direction,     |
//| calculates virgin status, and publishes latest OB bias/levels.    |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_buffers 11
#property indicator_plots   0
#property version "1.09"

input string OB_ObjectKeyword              = "pineBox";
input bool   ShowPanel                     = true;
input int    ScanEverySeconds              = 1;
input int    MaxZonesToShow                = 3;

input int    DirectionColorMinChannel      = 20;
input int    DirectionColorGap             = 8;

input bool   UseOverlapDirectionFix        = true;
input double OverlapDirectionMinPercent    = 60.0;
input double OverlapDirectionTieGapPercent = 20.0;

input bool   ShowVirginStatus              = true;
enum ENUM_OB_RETEST_MODE
{
   RETEST_BY_WICK  = 0,
   RETEST_BY_CLOSE = 1
};

input bool   UseClosedCandlesOnlyForRetest              = false;
input int    RetestSkipBarsFromDetection                = 1;
input bool   TreatExistingObjectsAsBaseline              = true;
input bool   UseMidPriceForDetection                     = true;

input bool   PublishGlobalVariables        = true;
input string GlobalVariablePrefix          = "OBSTATE";

// Colored bias label
input bool   ShowColoredBiasLabel          = true;
input int    BiasLabelCorner               = 0;        // 0=left top, 1=right top, 2=left bottom, 3=right bottom
input int    BiasLabelX                    = 10;
input int    BiasLabelY                    = 18;
input int    BiasLabelFontSize             = 13;
input string BiasLabelFont                 = "Arial Bold";
input color  BullishBiasColor              = clrLime;
input color  BearishBiasColor              = clrRed;
input color  NeutralBiasColor              = clrSilver;

// Buffer 0  = Bias: 1 bullish, -1 bearish, 0 none
// Buffer 1  = Latest OB high
// Buffer 2  = Latest OB low
// Buffer 3  = Latest OB virgin: 1 true, 0 false
// Buffer 4  = Latest OB start time
// Buffer 5  = Latest bullish OB high
// Buffer 6  = Latest bullish OB low
// Buffer 7  = Latest bullish OB virgin
// Buffer 8  = Latest bearish OB high
// Buffer 9  = Latest bearish OB low
// Buffer 10 = Latest bearish OB virgin
double BiasBuffer[], LatestHighBuffer[], LatestLowBuffer[], LatestVirginBuffer[], LatestTimeBuffer[];
double BullHighBuffer[], BullLowBuffer[], BullVirginBuffer[];
double BearHighBuffer[], BearLowBuffer[], BearVirginBuffer[];

struct OBZone
{
   string   name;
   string   direction;
   string   signature;
   double   high;
   double   low;
   datetime start_time;
   datetime end_time;
   color    zone_color;
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
};

OBZone zones[];
OBDetectionState detection_states[];
bool g_first_scan = true;

double g_bias = 0.0;
double g_latest_high = 0.0;
double g_latest_low = 0.0;
double g_latest_virgin = 0.0;
double g_latest_time = 0.0;
double g_bull_high = 0.0;
double g_bull_low = 0.0;
double g_bull_virgin = 0.0;
double g_bear_high = 0.0;
double g_bear_low = 0.0;
double g_bear_virgin = 0.0;
double g_latest_detected_time = 0.0;
double g_latest_detected_price = 0.0;
double g_latest_visit_time = 0.0;
double g_latest_validation_time = 0.0;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BiasBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, LatestHighBuffer, INDICATOR_DATA);
   SetIndexBuffer(2, LatestLowBuffer, INDICATOR_DATA);
   SetIndexBuffer(3, LatestVirginBuffer, INDICATOR_DATA);
   SetIndexBuffer(4, LatestTimeBuffer, INDICATOR_DATA);
   SetIndexBuffer(5, BullHighBuffer, INDICATOR_DATA);
   SetIndexBuffer(6, BullLowBuffer, INDICATOR_DATA);
   SetIndexBuffer(7, BullVirginBuffer, INDICATOR_DATA);
   SetIndexBuffer(8, BearHighBuffer, INDICATOR_DATA);
   SetIndexBuffer(9, BearLowBuffer, INDICATOR_DATA);
   SetIndexBuffer(10, BearVirginBuffer, INDICATOR_DATA);

   ArraySetAsSeries(BiasBuffer, true);
   ArraySetAsSeries(LatestHighBuffer, true);
   ArraySetAsSeries(LatestLowBuffer, true);
   ArraySetAsSeries(LatestVirginBuffer, true);
   ArraySetAsSeries(LatestTimeBuffer, true);
   ArraySetAsSeries(BullHighBuffer, true);
   ArraySetAsSeries(BullLowBuffer, true);
   ArraySetAsSeries(BullVirginBuffer, true);
   ArraySetAsSeries(BearHighBuffer, true);
   ArraySetAsSeries(BearLowBuffer, true);
   ArraySetAsSeries(BearVirginBuffer, true);

   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);

   ScanAndPublish();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectDelete(ChartID(), "OBSP_COLORED_BIAS_LABEL");
   Comment("");
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
   ScanAndPublish();

   int fill_count = MathMin(rates_total, 50);
   for(int i = 0; i < fill_count; i++)
   {
      BiasBuffer[i]         = g_bias;
      LatestHighBuffer[i]   = g_latest_high;
      LatestLowBuffer[i]    = g_latest_low;
      LatestVirginBuffer[i] = g_latest_virgin;
      LatestTimeBuffer[i]   = g_latest_time;
      BullHighBuffer[i]     = g_bull_high;
      BullLowBuffer[i]      = g_bull_low;
      BullVirginBuffer[i]   = g_bull_virgin;
      BearHighBuffer[i]     = g_bear_high;
      BearLowBuffer[i]      = g_bear_low;
      BearVirginBuffer[i]   = g_bear_virgin;
   }

   return rates_total;
}

//+------------------------------------------------------------------+
void ScanAndPublish()
{
   ScanObjects();

   OBZone latest, latest_bull, latest_bear;
   bool has_latest = GetLatestZone("", latest);
   bool has_bull   = GetLatestZone("BULLISH", latest_bull);
   bool has_bear   = GetLatestZone("BEARISH", latest_bear);

   g_bias = 0.0;
   g_latest_high = 0.0;
   g_latest_low = 0.0;
   g_latest_virgin = 0.0;
   g_latest_time = 0.0;
   g_bull_high = 0.0;
   g_bull_low = 0.0;
   g_bull_virgin = 0.0;
   g_bear_high = 0.0;
   g_bear_low = 0.0;
   g_bear_virgin = 0.0;
   g_latest_detected_time = 0.0;
   g_latest_detected_price = 0.0;
   g_latest_visit_time = 0.0;
   g_latest_validation_time = 0.0;

   if(has_latest)
   {
      if(latest.direction == "BULLISH")
         g_bias = 1.0;
      else if(latest.direction == "BEARISH")
         g_bias = -1.0;

      g_latest_high   = latest.high;
      g_latest_low    = latest.low;
      g_latest_virgin        = (latest.virgin ? 1.0 : 0.0);
      g_latest_time          = (double)latest.start_time;
      g_latest_detected_time = (double)latest.detected_time;
      g_latest_detected_price= latest.detected_price;
      g_latest_visit_time    = (double)latest.visit_time;
      g_latest_validation_time = (double)latest.validation_time;
   }

   if(has_bull)
   {
      g_bull_high   = latest_bull.high;
      g_bull_low    = latest_bull.low;
      g_bull_virgin = (latest_bull.virgin ? 1.0 : 0.0);
   }

   if(has_bear)
   {
      g_bear_high   = latest_bear.high;
      g_bear_low    = latest_bear.low;
      g_bear_virgin = (latest_bear.virgin ? 1.0 : 0.0);
   }

   if(PublishGlobalVariables)
      PublishGV();

   if(ShowPanel)
      DrawPanel(has_latest, latest, has_bull, latest_bull, has_bear, latest_bear);

   UpdateColoredBiasLabel(has_latest, latest);
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
bool IsObjectVisibleOnCurrentPeriod(const long chart_id, const string name)
{
   const long visibility = ObjectGetInteger(chart_id, name, OBJPROP_TIMEFRAMES);

   // OBJ_ALL_PERIODS means the object is visible everywhere.
   if(visibility == OBJ_ALL_PERIODS)
      return true;

   // OBJ_NO_PERIODS means it is deliberately hidden from every timeframe.
   if(visibility == OBJ_NO_PERIODS)
      return false;

   const long current_mask = CurrentPeriodObjectMask();
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
void ScanObjects()
{
   ArrayResize(zones, 0);

   long cid = ChartID();
   int total = ObjectsTotal(cid, 0, -1);

   for(int i = 0; i < total; i++)
   {
      string name = ObjectName(cid, i, 0, -1);
      if(name == "")
         continue;

      if(OB_ObjectKeyword != "" && StringFind(name, OB_ObjectKeyword) < 0)
         continue;

      ENUM_OBJECT type = (ENUM_OBJECT)ObjectGetInteger(cid, name, OBJPROP_TYPE);
      if(type != OBJ_RECTANGLE)
         continue;

      // Objects that exist on the chart but are hidden on this timeframe must
      // not be published as active zones. The source OB indicator may retain
      // such rectangles internally while showing only its configured maximum.
      if(!IsObjectVisibleOnCurrentPeriod(cid, name))
         continue;

      double p1 = ObjectGetDouble(cid, name, OBJPROP_PRICE, 0);
      double p2 = ObjectGetDouble(cid, name, OBJPROP_PRICE, 1);
      if(p1 <= 0.0 || p2 <= 0.0)
         continue;

      datetime t1 = (datetime)ObjectGetInteger(cid, name, OBJPROP_TIME, 0);
      datetime t2 = (datetime)ObjectGetInteger(cid, name, OBJPROP_TIME, 1);
      color c = (color)ObjectGetInteger(cid, name, OBJPROP_COLOR);

      OBZone z;
      z.name       = name;
      z.high       = MathMax(p1, p2);
      z.low        = MathMin(p1, p2);
      z.start_time = (t1 < t2 ? t1 : t2);
      z.end_time   = (t1 > t2 ? t1 : t2);
      z.zone_color = c;
      z.direction  = DetectDirection(name, c);
      z.signature  = BuildSignature(z);
      z.virgin         = true;
      z.visit_time      = 0;
      z.validation_time = 0;
      z.detected_time   = 0;
      z.detected_price  = 0.0;
      z.baseline        = false;

      AssignDetectionState(z);

      // During indicator startup the source may briefly expose duplicate
      // rectangle objects with different names but identical zone geometry.
      // Keep only one active zone for each unique signature.
      int existing = FindZoneBySignature(z.signature);
      if(existing >= 0)
      {
         // Prefer the object whose visible end extends furthest to the right.
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

      datetime visit_time = 0;
      bool visited = HasZoneBeenRetested(zones[i], visit_time);

      // Historical validation/retest reconstruction and live price monitoring
      // are intentionally independent. A missing historical validation must
      // never prevent a real-time touch from changing Virgin to false.
      if(!visited)
         visited = ApplyIndependentLiveTouch(zones[i], visit_time);

      zones[i].virgin = !visited;
      zones[i].visit_time = visit_time;
   }

   // Keep baseline mode active until the external indicator has actually
   // populated at least one OB rectangle. This prevents all startup objects
   // from being stamped later with the same live detection time.
   if(ArraySize(zones) > 0)
      g_first_scan = false;
}

//+------------------------------------------------------------------+
double DetectionMarketPrice()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

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
void AssignDetectionState(OBZone &z)
{
   int index = FindDetectionState(z.signature);

   if(index < 0)
   {
      OBDetectionState state;
      state.signature      = z.signature;
      state.baseline       = (g_first_scan && TreatExistingObjectsAsBaseline);
      state.detected_time  = (state.baseline ? 0 : TimeCurrent());
      state.detected_price = (state.baseline ? 0.0 : DetectionMarketPrice());
      state.live_visited   = false;
      state.live_visit_time= 0;

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
bool IsCurrentMarketTouchingZone(const OBZone &z)
{
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(bid <= 0.0 || ask <= 0.0 || z.high <= z.low)
      return false;

   // The live market/spread overlaps the rectangle's price interval.
   return (bid <= z.high && ask >= z.low);
}

//+------------------------------------------------------------------+
bool ApplyIndependentLiveTouch(OBZone &z, datetime &visit_time)
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

   if(!IsCurrentMarketTouchingZone(z))
      return false;

   detection_states[index].live_visited    = true;
   detection_states[index].live_visit_time = TimeCurrent();
   visit_time = detection_states[index].live_visit_time;

   Print("LIVE OB VISIT: ", z.signature,
         " | ", z.direction,
         " | zone=", DoubleToString(z.low, _Digits),
         "-", DoubleToString(z.high, _Digits),
         " | time=", TimeToString(visit_time, TIME_DATE|TIME_SECONDS));

   return true;
}

//+------------------------------------------------------------------+
bool HasHistoricalZoneBeenRetested(OBZone &z, datetime &visit_time, datetime &validation_time)
{
   visit_time = 0;
   validation_time = 0;

   const int origin_shift = iBarShift(_Symbol, _Period, z.start_time, false);
   if(origin_shift < 0)
      return false;

   const int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   const double origin_close = iClose(_Symbol, _Period, origin_shift);
   if(origin_close <= 0.0)
      return false;

   int validation_shift = -1;

   // Reconstruct validation using the same close-based rule used by the source OB indicator.
   // Bullish: first later candle closing above the OB origin candle close.
   // Bearish: first later candle closing below the OB origin candle close.
   for(int shift = origin_shift - 1; shift >= check_to; shift--)
   {
      const double candle_close = iClose(_Symbol, _Period, shift);

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

   validation_time = iTime(_Symbol, _Period, validation_shift);

   // Only candles AFTER validation may count as a retest.
   for(int shift = validation_shift - 1; shift >= check_to; shift--)
   {
      const double candle_high = iHigh(_Symbol, _Period, shift);
      const double candle_low  = iLow(_Symbol, _Period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(_Symbol, _Period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasLiveZoneBeenRetested(OBZone &z, datetime &visit_time)
{
   visit_time = 0;

   if(z.detected_time <= 0)
      return false;

   const int detection_shift = iBarShift(_Symbol, _Period, z.detected_time, false);
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
      const double candle_high = iHigh(_Symbol, _Period, shift);
      const double candle_low  = iLow(_Symbol, _Period, shift);
      const bool touches_zone  = (candle_high >= z.low && candle_low <= z.high);

      if(touches_zone)
      {
         visit_time = iTime(_Symbol, _Period, shift);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool HasZoneBeenRetested(OBZone &z, datetime &visit_time)
{
   visit_time = 0;
   z.validation_time = 0;

   // Existing rectangles: reconstruct validation from the origin candle close,
   // then count only a later zone touch as the retest.
   if(z.baseline || z.detected_time <= 0)
      return HasHistoricalZoneBeenRetested(z, visit_time, z.validation_time);

   // Fresh live rectangles: appearance itself is validation.
   z.validation_time = z.detected_time;
   return HasLiveZoneBeenRetested(z, visit_time);
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
void PublishGV()
{
   string base = GVBase();

   GlobalVariableSet(base + "_BIAS", g_bias);
   GlobalVariableSet(base + "_LATEST_HIGH", g_latest_high);
   GlobalVariableSet(base + "_LATEST_LOW", g_latest_low);
   GlobalVariableSet(base + "_LATEST_VIRGIN", g_latest_virgin);
   GlobalVariableSet(base + "_LATEST_TIME", g_latest_time);
   GlobalVariableSet(base + "_LATEST_DETECTED_TIME", g_latest_detected_time);
   GlobalVariableSet(base + "_LATEST_DETECTED_PRICE", g_latest_detected_price);
   GlobalVariableSet(base + "_LATEST_VISIT_TIME", g_latest_visit_time);
   GlobalVariableSet(base + "_LATEST_VALIDATION_TIME", g_latest_validation_time);

   GlobalVariableSet(base + "_BULL_HIGH", g_bull_high);
   GlobalVariableSet(base + "_BULL_LOW", g_bull_low);
   GlobalVariableSet(base + "_BULL_VIRGIN", g_bull_virgin);

   GlobalVariableSet(base + "_BEAR_HIGH", g_bear_high);
   GlobalVariableSet(base + "_BEAR_LOW", g_bear_low);
   GlobalVariableSet(base + "_BEAR_VIRGIN", g_bear_virgin);

   GlobalVariableSet(base + "_UPDATED", (double)TimeCurrent());
}

//+------------------------------------------------------------------+
string GVBase()
{
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0)
      tf_minutes = (int)_Period;
   return GlobalVariablePrefix + "_" + _Symbol + "_" + IntegerToString(tf_minutes);
}

//+------------------------------------------------------------------+
void DrawPanel(bool has_latest, OBZone &latest,
               bool has_bull, OBZone &latest_bull,
               bool has_bear, OBZone &latest_bear)
{
   string text = "";
   text += "\n\nOB STATE PUBLISHER | " + _Symbol + " | " + EnumToString(_Period) + "\n";
   text += "Objects tracked: " + IntegerToString(ArraySize(zones)) + "\n";
   if(has_bull)
      text += "Latest Demand: " + PriceText(latest_bull.high, latest_bull.low) +
              " | Virgin: " + BoolText(latest_bull.virgin) +
              " | Validated: " + ValidationText(latest_bull) +
              " | Detected: " + DetectionText(latest_bull) + "\n";

   if(has_bear)
      text += "Latest Supply: " + PriceText(latest_bear.high, latest_bear.low) +
              " | Virgin: " + BoolText(latest_bear.virgin) +
              " | Validated: " + ValidationText(latest_bear) +
              " | Detected: " + DetectionText(latest_bear) + "\n";

   int demand_count = 0;
   int supply_count = 0;
   int unknown_count = 0;

   for(int i = 0; i < ArraySize(zones); i++)
   {
      if(zones[i].direction == "BULLISH")
         demand_count++;
      else if(zones[i].direction == "BEARISH")
         supply_count++;
      else
         unknown_count++;
   }

   text += "Demand: " + IntegerToString(demand_count) +
           " | Supply: " + IntegerToString(supply_count) +
           " | Unknown: " + IntegerToString(unknown_count) + "\n";

   text += "\nACTIVE DEMAND ZONES\n";
   int shown_demand = 0;
   for(int i = ArraySize(zones) - 1; i >= 0 && shown_demand < MaxZonesToShow; i--)
   {
      if(zones[i].direction != "BULLISH")
         continue;

      text += IntegerToString(shown_demand + 1) + ") " +
              PriceText(zones[i].high, zones[i].low) +
              " | Virgin: " + BoolText(zones[i].virgin);

      text += " | Validated: " + ValidationText(zones[i]);
      text += " | Detected: " + DetectionText(zones[i]);

      if(!zones[i].virgin && zones[i].visit_time > 0)
         text += " | Visited: " + TimeToString(zones[i].visit_time, TIME_DATE | TIME_MINUTES);

      text += "\n";
      shown_demand++;
   }

   text += "\nACTIVE SUPPLY ZONES\n";
   int shown_supply = 0;
   for(int i = ArraySize(zones) - 1; i >= 0 && shown_supply < MaxZonesToShow; i--)
   {
      if(zones[i].direction != "BEARISH")
         continue;

      text += IntegerToString(shown_supply + 1) + ") " +
              PriceText(zones[i].high, zones[i].low) +
              " | Virgin: " + BoolText(zones[i].virgin);

      text += " | Validated: " + ValidationText(zones[i]);
      text += " | Detected: " + DetectionText(zones[i]);

      if(!zones[i].virgin && zones[i].visit_time > 0)
         text += " | Visited: " + TimeToString(zones[i].visit_time, TIME_DATE | TIME_MINUTES);

      text += "\n";
      shown_supply++;
   }

   if(PublishGlobalVariables)
      text += "\nGV Base: " + GVBase() + "\n";

   Comment(text);
}

//+------------------------------------------------------------------+
string DisplayDirection(string direction)
{
   if(direction == "BULLISH")
      return "DEMAND";

   if(direction == "BEARISH")
      return "SUPPLY";

   return direction;
}

//+------------------------------------------------------------------+
string BiasDirectionText(string direction)
{
   if(direction == "BULLISH")
      return "BULLISH";

   if(direction == "BEARISH")
      return "BEARISH";

   return "NONE";
}

//+------------------------------------------------------------------+
color BiasDirectionColor(string direction)
{
   if(direction == "BULLISH")
      return BullishBiasColor;

   if(direction == "BEARISH")
      return BearishBiasColor;

   return NeutralBiasColor;
}

//+------------------------------------------------------------------+
void UpdateColoredBiasLabel(bool has_latest, OBZone &latest)
{
   string name = "OBSP_COLORED_BIAS_LABEL";

   if(!ShowColoredBiasLabel)
   {
      ObjectDelete(ChartID(), name);
      return;
   }

   string txt = "BIAS: NONE";
   color txt_color = NeutralBiasColor;

   if(has_latest)
   {
      txt = "BIAS: " + BiasDirectionText(latest.direction) +
            " | " + DoubleToString(latest.high, _Digits) +
            " - " + DoubleToString(latest.low, _Digits) +
            " | Virgin: " + BoolText(latest.virgin) +
            " | Detected: " + DetectionText(latest);
      txt_color = BiasDirectionColor(latest.direction);
   }

   if(ObjectFind(ChartID(), name) < 0)
   {
      ObjectCreate(ChartID(), name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(ChartID(), name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(ChartID(), name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(ChartID(), name, OBJPROP_BACK, false);
   }

   ObjectSetInteger(ChartID(), name, OBJPROP_CORNER, BiasLabelCorner);
   ObjectSetInteger(ChartID(), name, OBJPROP_XDISTANCE, BiasLabelX);
   ObjectSetInteger(ChartID(), name, OBJPROP_YDISTANCE, BiasLabelY);
   ObjectSetInteger(ChartID(), name, OBJPROP_COLOR, txt_color);
   ObjectSetInteger(ChartID(), name, OBJPROP_FONTSIZE, BiasLabelFontSize);
   ObjectSetString(ChartID(), name, OBJPROP_FONT, BiasLabelFont);
   ObjectSetString(ChartID(), name, OBJPROP_TEXT, txt);
}

//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
string ValidationText(OBZone &z)
{
   if(z.validation_time <= 0)
      return "not found";

   return TimeToString(z.validation_time, TIME_DATE | TIME_SECONDS);
}

//+------------------------------------------------------------------+
string DetectionText(OBZone &z)
{
   if(z.baseline || z.detected_time <= 0)
      return "baseline";

   return TimeToString(z.detected_time, TIME_DATE | TIME_SECONDS) +
          " @ " + DoubleToString(z.detected_price, _Digits);
}

//+------------------------------------------------------------------+
string PriceText(double high, double low)
{
   return DoubleToString(high, _Digits) + " - " + DoubleToString(low, _Digits);
}

//+------------------------------------------------------------------+
string BoolText(bool v)
{
   return (v ? "true" : "false");
}

//+------------------------------------------------------------------+
string BuildSignature(OBZone &z)
{
   return IntegerToString((int)z.start_time) + "|" +
          DoubleToString(z.high, _Digits) + "|" +
          DoubleToString(z.low, _Digits);
}

//+------------------------------------------------------------------+
string StringToLowerCopy(string s)
{
   string out = s;
   StringToLower(out);
   return out;
}
//+------------------------------------------------------------------+
