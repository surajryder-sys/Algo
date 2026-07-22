//+------------------------------------------------------------------+
//| OB_Bridge_Aggregator.mq5                                          |
//| Single-instance aggregator: walks every open chart for the target |
//| symbol, reads OB ("pineBox") rectangles per timeframe, reads FVG  |
//| rectangles (BullFVG_/BearFVG_, timeframe embedded in the name) on |
//| any chart, recomputes Dynamic Zones directly from D1 bars, and    |
//| writes everything to one shared JSON file in Common\Files so an   |
//| external process (Python) can read it without touching the        |
//| terminal at all.                                                   |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_buffers 1
#property indicator_plots   0
#property version "1.00"

input string TargetSymbol                   = "";          // empty = use chart's own symbol
input string OB_ObjectKeyword                = "pineBox";
input string TargetTimeframesCSV             = "H4,H2,H1,M30,M15,M5";
input int    ScanEverySeconds                = 2;
input bool   UseClosedCandlesOnlyForRetest   = false;
input int    RetestSkipBarsFromDetection     = 1;
input bool   TreatExistingObjectsAsBaseline  = true;
input string OutputFileName                  = "ob_bridge_state.json";
input int    MaxFVGsPerSidePerTF             = 6;
input bool   ShowPanel                       = true;

double DummyBuffer[];

//+------------------------------------------------------------------+
struct OBZone
{
   string   tf;
   string   direction;
   string   signature;
   double   high;
   double   low;
   datetime start_time;
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

struct FVGZone
{
   string   name;
   string   tf;
   string   direction;
   double   high;
   double   low;
   datetime created_time;
   bool     retested;
   datetime retest_time;
};

struct FVGDetectionState
{
   string   name;
   bool     retested;
   datetime retest_time;
};

struct DynZones
{
   bool     valid;
   double   day_open;
   double   zone1_upper_5d;
   double   zone2_upper_10d;
   double   zone3_lower_5d;
   double   zone4_lower_10d;
   datetime computed_at;
};

OBZone            g_ob_zones[];
OBDetectionState  g_ob_states[];
FVGZone           g_fvg_zones[];
FVGDetectionState g_fvg_states[];
bool              g_first_scan = true;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, DummyBuffer, INDICATOR_DATA);
   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);
   ScanAndPublish();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

void OnTimer() { ScanAndPublish(); }

int OnCalculate(const int rates_total, const int prev_calculated,
                 const datetime &time[], const double &open[], const double &high[],
                 const double &low[], const double &close[], const long &tick_volume[],
                 const long &volume[], const int &spread[])
{
   ScanAndPublish();
   int fill = MathMin(rates_total, 2);
   for(int i = 0; i < fill; i++)
      DummyBuffer[i] = 0.0;
   return rates_total;
}

//+------------------------------------------------------------------+
string TargetSym() { return (TargetSymbol == "" ? _Symbol : TargetSymbol); }

string TagFromPeriod(ENUM_TIMEFRAMES per)
{
   string s = EnumToString(per);
   return StringSubstr(s, 7); // strip "PERIOD_"
}

bool TagInList(string tag, string &list[])
{
   for(int i = 0; i < ArraySize(list); i++)
   {
      string t = list[i];
      StringTrimLeft(t);
      StringTrimRight(t);
      if(t == tag)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void ScanAndPublish()
{
   ScanOBZonesAllCharts();
   ScanFVGZonesAllCharts();
   TrimFVGZones();
   WriteJson();
   if(ShowPanel)
      DrawPanel();
}

//+------------------------------------------------------------------+
// Keeps only the MaxFVGsPerSidePerTF most recent zones per (tf, direction),
// independent of how many raw objects exist on the source chart - this is
// decided entirely on the bridge side, not by each chart's own indicator
// input, so a misconfigured chart can never flood the output file.
void TrimFVGZones()
{
   string tfs_seen[];
   for(int i = 0; i < ArraySize(g_fvg_zones); i++)
   {
      string tf = g_fvg_zones[i].tf;
      bool found = false;
      for(int j = 0; j < ArraySize(tfs_seen); j++)
         if(tfs_seen[j] == tf) { found = true; break; }
      if(!found)
      {
         int n = ArraySize(tfs_seen);
         ArrayResize(tfs_seen, n + 1);
         tfs_seen[n] = tf;
      }
   }

   FVGZone kept[];
   string dirs[2] = {"BULLISH", "BEARISH"};

   for(int t = 0; t < ArraySize(tfs_seen); t++)
   {
      string tf = tfs_seen[t];
      for(int d = 0; d < 2; d++)
      {
         string dir = dirs[d];

         int idxs[];
         for(int i = 0; i < ArraySize(g_fvg_zones); i++)
         {
            if(g_fvg_zones[i].tf == tf && g_fvg_zones[i].direction == dir)
            {
               int n = ArraySize(idxs);
               ArrayResize(idxs, n + 1);
               idxs[n] = i;
            }
         }

         // Insertion sort by created_time descending (lists here are tiny).
         int m = ArraySize(idxs);
         for(int a = 1; a < m; a++)
         {
            int key = idxs[a];
            datetime key_time = g_fvg_zones[key].created_time;
            int b = a - 1;
            while(b >= 0 && g_fvg_zones[idxs[b]].created_time < key_time)
            {
               idxs[b + 1] = idxs[b];
               b--;
            }
            idxs[b + 1] = key;
         }

         int keep_n = MathMin(MaxFVGsPerSidePerTF, m);
         for(int k = 0; k < keep_n; k++)
         {
            int n = ArraySize(kept);
            ArrayResize(kept, n + 1);
            kept[n] = g_fvg_zones[idxs[k]];
         }
      }
   }

   ArrayResize(g_fvg_zones, ArraySize(kept));
   for(int i = 0; i < ArraySize(kept); i++)
      g_fvg_zones[i] = kept[i];
}

//+------------------------------------------------------------------+
long PeriodObjectMask(ENUM_TIMEFRAMES per)
{
   switch(per)
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

bool IsObjectVisibleOnPeriod(long chart_id, string name, ENUM_TIMEFRAMES per)
{
   long visibility = ObjectGetInteger(chart_id, name, OBJPROP_TIMEFRAMES);
   if(visibility == OBJ_ALL_PERIODS)
      return true;
   if(visibility == OBJ_NO_PERIODS)
      return false;
   long mask = PeriodObjectMask(per);
   return ((visibility & mask) != 0);
}

//+------------------------------------------------------------------+
string DetectDirection(string name, color c)
{
   string lower = name;
   StringToLower(lower);

   if(StringFind(lower, "bull") >= 0 || StringFind(lower, "buy") >= 0 || StringFind(lower, "demand") >= 0)
      return "BULLISH";
   if(StringFind(lower, "bear") >= 0 || StringFind(lower, "sell") >= 0 || StringFind(lower, "supply") >= 0)
      return "BEARISH";

   int r = (int)c & 0xFF;
   int g = ((int)c >> 8) & 0xFF;
   int b = ((int)c >> 16) & 0xFF;

   if(g >= 20 && g >= r + 8 && g >= b + 8)
      return "BULLISH";
   if(r >= 20 && r >= g + 8 && r >= b + 8)
      return "BEARISH";
   return "UNKNOWN";
}

//+------------------------------------------------------------------+
int FindOBZoneIndexBySignature(string signature)
{
   for(int i = 0; i < ArraySize(g_ob_zones); i++)
      if(g_ob_zones[i].signature == signature)
         return i;
   return -1;
}

int FindOBStateIndex(string signature)
{
   for(int i = 0; i < ArraySize(g_ob_states); i++)
      if(g_ob_states[i].signature == signature)
         return i;
   return -1;
}

void AssignOBDetectionState(OBZone &z, double market_price)
{
   int idx = FindOBStateIndex(z.signature);
   if(idx < 0)
   {
      OBDetectionState st;
      st.signature = z.signature;
      st.baseline = (g_first_scan && TreatExistingObjectsAsBaseline);
      st.detected_time = (st.baseline ? 0 : TimeCurrent());
      st.detected_price = (st.baseline ? 0.0 : market_price);
      st.live_visited = false;
      st.live_visit_time = 0;
      int n = ArraySize(g_ob_states);
      ArrayResize(g_ob_states, n + 1);
      g_ob_states[n] = st;
      idx = n;
   }
   z.detected_time  = g_ob_states[idx].detected_time;
   z.detected_price = g_ob_states[idx].detected_price;
   z.baseline       = g_ob_states[idx].baseline;
}

//+------------------------------------------------------------------+
bool IsCurrentMarketTouchingZone(OBZone &z, string symbol)
{
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0 || z.high <= z.low)
      return false;
   return (bid <= z.high && ask >= z.low);
}

bool ApplyIndependentLiveTouch(OBZone &z, string symbol)
{
   int idx = FindOBStateIndex(z.signature);
   if(idx < 0)
      return false;

   if(g_ob_states[idx].live_visited)
   {
      z.visit_time = g_ob_states[idx].live_visit_time;
      return true;
   }

   if(!z.baseline && z.detected_time > 0 && TimeCurrent() <= z.detected_time)
      return false;

   if(!IsCurrentMarketTouchingZone(z, symbol))
      return false;

   g_ob_states[idx].live_visited = true;
   g_ob_states[idx].live_visit_time = TimeCurrent();
   z.visit_time = g_ob_states[idx].live_visit_time;
   return true;
}

bool HasHistoricalZoneBeenRetested(OBZone &z, string symbol, ENUM_TIMEFRAMES per)
{
   z.visit_time = 0;
   z.validation_time = 0;

   int origin_shift = iBarShift(symbol, per, z.start_time, false);
   if(origin_shift < 0)
      return false;

   int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   double origin_close = iClose(symbol, per, origin_shift);
   if(origin_close <= 0.0)
      return false;

   int validation_shift = -1;
   for(int shift = origin_shift - 1; shift >= check_to; shift--)
   {
      double c = iClose(symbol, per, shift);
      if(z.direction == "BULLISH" && c > origin_close) { validation_shift = shift; break; }
      if(z.direction == "BEARISH" && c < origin_close) { validation_shift = shift; break; }
   }
   if(validation_shift < 0)
      return false;

   z.validation_time = iTime(symbol, per, validation_shift);

   for(int shift = validation_shift - 1; shift >= check_to; shift--)
   {
      double h = iHigh(symbol, per, shift);
      double l = iLow(symbol, per, shift);
      if(h >= z.low && l <= z.high)
      {
         z.visit_time = iTime(symbol, per, shift);
         return true;
      }
   }
   return false;
}

bool HasLiveZoneBeenRetested(OBZone &z, string symbol, ENUM_TIMEFRAMES per)
{
   z.visit_time = 0;
   if(z.detected_time <= 0)
      return false;

   int detection_shift = iBarShift(symbol, per, z.detected_time, false);
   if(detection_shift < 0)
      return false;

   int check_to = (UseClosedCandlesOnlyForRetest ? 1 : 0);
   int skip_bars = MathMax(1, RetestSkipBarsFromDetection);
   int first_check_shift = detection_shift - skip_bars;
   if(first_check_shift < check_to)
      return false;

   for(int shift = first_check_shift; shift >= check_to; shift--)
   {
      double h = iHigh(symbol, per, shift);
      double l = iLow(symbol, per, shift);
      if(h >= z.low && l <= z.high)
      {
         z.visit_time = iTime(symbol, per, shift);
         return true;
      }
   }
   return false;
}

bool HasZoneBeenRetested(OBZone &z, string symbol, ENUM_TIMEFRAMES per)
{
   z.visit_time = 0;
   z.validation_time = 0;

   if(z.baseline || z.detected_time <= 0)
      return HasHistoricalZoneBeenRetested(z, symbol, per);

   z.validation_time = z.detected_time;
   return HasLiveZoneBeenRetested(z, symbol, per);
}

//+------------------------------------------------------------------+
void ScanOBZonesOnChart(long chart_id, string symbol, ENUM_TIMEFRAMES per, string tag)
{
   int total = ObjectsTotal(chart_id, 0, OBJ_RECTANGLE);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double market_price = (bid > 0 && ask > 0) ? (bid + ask) / 2.0 : 0.0;

   for(int i = 0; i < total; i++)
   {
      string name = ObjectName(chart_id, i, 0, OBJ_RECTANGLE);
      if(name == "")
         continue;
      if(OB_ObjectKeyword != "" && StringFind(name, OB_ObjectKeyword) < 0)
         continue;
      if(!IsObjectVisibleOnPeriod(chart_id, name, per))
         continue;

      double p1 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 0);
      double p2 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 1);
      if(p1 <= 0.0 || p2 <= 0.0)
         continue;

      datetime t1 = (datetime)ObjectGetInteger(chart_id, name, OBJPROP_TIME, 0);
      datetime t2 = (datetime)ObjectGetInteger(chart_id, name, OBJPROP_TIME, 1);
      color c = (color)ObjectGetInteger(chart_id, name, OBJPROP_COLOR);

      OBZone z;
      z.tf = tag;
      z.high = MathMax(p1, p2);
      z.low  = MathMin(p1, p2);
      z.start_time = (t1 < t2 ? t1 : t2);
      z.direction = DetectDirection(name, c);
      if(z.direction != "BULLISH" && z.direction != "BEARISH")
         continue;

      z.signature = tag + "|" + IntegerToString((int)z.start_time) + "|" +
                    DoubleToString(z.high, _Digits) + "|" + DoubleToString(z.low, _Digits);
      z.virgin = true;
      z.visit_time = 0;
      z.validation_time = 0;

      AssignOBDetectionState(z, market_price);

      if(FindOBZoneIndexBySignature(z.signature) >= 0)
         continue;

      int n = ArraySize(g_ob_zones);
      ArrayResize(g_ob_zones, n + 1);
      g_ob_zones[n] = z;
   }

   for(int i = 0; i < ArraySize(g_ob_zones); i++)
   {
      if(g_ob_zones[i].tf != tag)
         continue;
      bool visited = HasZoneBeenRetested(g_ob_zones[i], symbol, per);
      if(!visited)
         visited = ApplyIndependentLiveTouch(g_ob_zones[i], symbol);
      g_ob_zones[i].virgin = !visited;
   }
}

void ScanOBZonesAllCharts()
{
   ArrayResize(g_ob_zones, 0);
   string target_tags[];
   StringSplit(TargetTimeframesCSV, ',', target_tags);

   long cid = ChartFirst();
   while(cid >= 0)
   {
      if(ChartSymbol(cid) == TargetSym())
      {
         ENUM_TIMEFRAMES per = ChartPeriod(cid);
         string tag = TagFromPeriod(per);
         if(TagInList(tag, target_tags))
            ScanOBZonesOnChart(cid, TargetSym(), per, tag);
      }
      cid = ChartNext(cid);
   }

   if(ArraySize(g_ob_zones) > 0)
      g_first_scan = false;
}

//+------------------------------------------------------------------+
string ExtractTFTagFromFVGName(string name)
{
   int idx = StringFind(name, "PERIOD_");
   if(idx < 0)
      return "UNKNOWN";
   int start = idx + 7;
   string rest = StringSubstr(name, start, StringLen(name) - start);
   int us = StringFind(rest, "_");
   if(us < 0)
      return rest;
   return StringSubstr(rest, 0, us);
}

int FindFVGZoneIndexByName(string name)
{
   for(int i = 0; i < ArraySize(g_fvg_zones); i++)
      if(g_fvg_zones[i].name == name)
         return i;
   return -1;
}

int FindFVGStateIndex(string name)
{
   for(int i = 0; i < ArraySize(g_fvg_states); i++)
      if(g_fvg_states[i].name == name)
         return i;
   return -1;
}

void ApplyFVGLiveTouch(FVGZone &z, string symbol)
{
   int idx = FindFVGStateIndex(z.name);
   if(idx < 0)
   {
      FVGDetectionState st;
      st.name = z.name;
      st.retested = false;
      st.retest_time = 0;
      int n = ArraySize(g_fvg_states);
      ArrayResize(g_fvg_states, n + 1);
      g_fvg_states[n] = st;
      idx = n;
   }

   if(!g_fvg_states[idx].retested)
   {
      double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      if(bid > 0 && ask > 0 && z.high > z.low && bid <= z.high && ask >= z.low)
      {
         g_fvg_states[idx].retested = true;
         g_fvg_states[idx].retest_time = TimeCurrent();
      }
   }

   z.retested = g_fvg_states[idx].retested;
   z.retest_time = g_fvg_states[idx].retest_time;
}

// Two FVG indicators may be in play, with different object-naming schemes:
//  - FVG_V5_Dual_MTF: "BullFVG_PERIOD_M15_..." / "BearFVG_..." - TF embedded
//    in the name (one chart instance can cover two timeframes at once).
//    No built-in retest marker, so retest is tracked via live touch.
//  - FVG_Retest_V2:   "FVG_ONLY_Bull_<id>" / "FVG_ONLY_Bear_<id>" - single
//    timeframe per chart instance (TF = whatever chart it's attached to),
//    but it draws a companion "FVG_ONLY_BullRT_<id>"/"...BearRT_<id>"
//    circle object the moment a zone is retested, so retest status/time
//    is read directly instead of inferred.
void ScanFVGZonesOnChart(long chart_id, string &target_tags[])
{
   ENUM_TIMEFRAMES chart_per = ChartPeriod(chart_id);
   string chart_tag = TagFromPeriod(chart_per);
   bool chart_is_target = TagInList(chart_tag, target_tags);

   int total = ObjectsTotal(chart_id, 0, OBJ_RECTANGLE);
   for(int i = 0; i < total; i++)
   {
      string name = ObjectName(chart_id, i, 0, OBJ_RECTANGLE);
      if(name == "")
         continue;
      if(FindFVGZoneIndexByName(name) >= 0)
         continue;

      bool v5_bull = (StringFind(name, "BullFVG_") == 0);
      bool v5_bear = (StringFind(name, "BearFVG_") == 0);
      bool v2_bull = (StringFind(name, "FVG_ONLY_Bull_") == 0);
      bool v2_bear = (StringFind(name, "FVG_ONLY_Bear_") == 0);

      if(!v5_bull && !v5_bear && !v2_bull && !v2_bear)
         continue;
      if((v2_bull || v2_bear) && !chart_is_target)
         continue;

      double p1 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 0);
      double p2 = ObjectGetDouble(chart_id, name, OBJPROP_PRICE, 1);
      if(p1 <= 0.0 || p2 <= 0.0)
         continue;
      datetime t1 = (datetime)ObjectGetInteger(chart_id, name, OBJPROP_TIME, 0);

      FVGZone z;
      z.name = name;
      z.high = MathMax(p1, p2);
      z.low  = MathMin(p1, p2);
      z.created_time = t1;
      z.retested = false;
      z.retest_time = 0;

      if(v5_bull || v5_bear)
      {
         z.tf = ExtractTFTagFromFVGName(name);
         z.direction = (v5_bull ? "BULLISH" : "BEARISH");
         ApplyFVGLiveTouch(z, TargetSym());
      }
      else
      {
         z.tf = chart_tag;
         z.direction = (v2_bull ? "BULLISH" : "BEARISH");

         string prefix   = v2_bull ? "FVG_ONLY_Bull_" : "FVG_ONLY_Bear_";
         string rt_prefix= v2_bull ? "FVG_ONLY_BullRT_" : "FVG_ONLY_BearRT_";
         string id       = StringSubstr(name, StringLen(prefix));
         string rt_name  = rt_prefix + id;

         if(ObjectFind(chart_id, rt_name) >= 0)
         {
            z.retested = true;
            z.retest_time = (datetime)ObjectGetInteger(chart_id, rt_name, OBJPROP_TIME, 0);
         }
      }

      int n = ArraySize(g_fvg_zones);
      ArrayResize(g_fvg_zones, n + 1);
      g_fvg_zones[n] = z;
   }
}

void ScanFVGZonesAllCharts()
{
   ArrayResize(g_fvg_zones, 0);
   string target_tags[];
   StringSplit(TargetTimeframesCSV, ',', target_tags);

   long cid = ChartFirst();
   while(cid >= 0)
   {
      if(ChartSymbol(cid) == TargetSym())
         ScanFVGZonesOnChart(cid, target_tags);
      cid = ChartNext(cid);
   }
}

//+------------------------------------------------------------------+
DynZones ComputeDynamicZones(string symbol)
{
   DynZones dz;
   dz.valid = false;
   dz.day_open = 0; dz.zone1_upper_5d = 0; dz.zone2_upper_10d = 0;
   dz.zone3_lower_5d = 0; dz.zone4_lower_10d = 0; dz.computed_at = 0;

   double yOpen = iOpen(symbol, PERIOD_D1, 1);
   if(yOpen <= 0)
      return dz;

   double sum5 = 0, sum10 = 0;
   for(int k = 1; k <= 5; k++)
      sum5 += iHigh(symbol, PERIOD_D1, k) - iLow(symbol, PERIOD_D1, k);
   for(int k = 1; k <= 10; k++)
      sum10 += iHigh(symbol, PERIOD_D1, k) - iLow(symbol, PERIOD_D1, k);

   double avg5 = sum5 / 5.0;
   double avg10 = sum10 / 10.0;

   dz.day_open = yOpen;
   dz.zone1_upper_5d  = yOpen + avg5 / 2.0;
   dz.zone2_upper_10d = yOpen + avg10 / 2.0;
   dz.zone3_lower_5d  = yOpen - avg5 / 2.0;
   dz.zone4_lower_10d = yOpen - avg10 / 2.0;
   dz.computed_at = TimeCurrent();
   dz.valid = true;
   return dz;
}

//+------------------------------------------------------------------+
string JsonEscape(string s)
{
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   return out;
}

void WriteJson()
{
   DynZones dz = ComputeDynamicZones(TargetSym());

   string json = "{\n";
   json += "  \"generated_at\": " + IntegerToString((long)TimeCurrent()) + ",\n";
   json += "  \"generated_at_str\": \"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\",\n";
   json += "  \"symbol\": \"" + JsonEscape(TargetSym()) + "\",\n";

   json += "  \"dynamic_zones\": ";
   if(dz.valid)
   {
      json += "{\n";
      json += "    \"day_open\": " + DoubleToString(dz.day_open, _Digits) + ",\n";
      json += "    \"zone1_upper_5d\": " + DoubleToString(dz.zone1_upper_5d, _Digits) + ",\n";
      json += "    \"zone2_upper_10d\": " + DoubleToString(dz.zone2_upper_10d, _Digits) + ",\n";
      json += "    \"zone3_lower_5d\": " + DoubleToString(dz.zone3_lower_5d, _Digits) + ",\n";
      json += "    \"zone4_lower_10d\": " + DoubleToString(dz.zone4_lower_10d, _Digits) + ",\n";
      json += "    \"computed_at\": " + IntegerToString((long)dz.computed_at) + "\n";
      json += "  },\n";
   }
   else
      json += "null,\n";

   json += "  \"order_blocks\": [\n";
   for(int i = 0; i < ArraySize(g_ob_zones); i++)
   {
      OBZone z = g_ob_zones[i];
      json += "    {";
      json += "\"tf\": \"" + z.tf + "\", ";
      json += "\"direction\": \"" + z.direction + "\", ";
      json += "\"high\": " + DoubleToString(z.high, _Digits) + ", ";
      json += "\"low\": " + DoubleToString(z.low, _Digits) + ", ";
      json += "\"start_time\": " + IntegerToString((long)z.start_time) + ", ";
      json += "\"start_time_str\": \"" + TimeToString(z.start_time, TIME_DATE | TIME_SECONDS) + "\", ";
      json += "\"virgin\": " + (z.virgin ? "true" : "false") + ", ";
      json += "\"visit_time\": " + IntegerToString((long)z.visit_time) + ", ";
      json += "\"validation_time\": " + IntegerToString((long)z.validation_time) + ", ";
      json += "\"detected_time\": " + IntegerToString((long)z.detected_time) + ", ";
      json += "\"detected_price\": " + DoubleToString(z.detected_price, _Digits) + ", ";
      json += "\"baseline\": " + (z.baseline ? "true" : "false") + ", ";
      json += "\"signature\": \"" + JsonEscape(z.signature) + "\"";
      json += "}";
      if(i < ArraySize(g_ob_zones) - 1) json += ",";
      json += "\n";
   }
   json += "  ],\n";

   json += "  \"fvgs\": [\n";
   for(int i = 0; i < ArraySize(g_fvg_zones); i++)
   {
      FVGZone z = g_fvg_zones[i];
      json += "    {";
      json += "\"tf\": \"" + z.tf + "\", ";
      json += "\"direction\": \"" + z.direction + "\", ";
      json += "\"high\": " + DoubleToString(z.high, _Digits) + ", ";
      json += "\"low\": " + DoubleToString(z.low, _Digits) + ", ";
      json += "\"created_time\": " + IntegerToString((long)z.created_time) + ", ";
      json += "\"created_time_str\": \"" + TimeToString(z.created_time, TIME_DATE | TIME_SECONDS) + "\", ";
      json += "\"active\": true, ";
      json += "\"retested\": " + (z.retested ? "true" : "false") + ", ";
      json += "\"retest_time\": " + IntegerToString((long)z.retest_time) + ", ";
      json += "\"name\": \"" + JsonEscape(z.name) + "\"";
      json += "}";
      if(i < ArraySize(g_fvg_zones) - 1) json += ",";
      json += "\n";
   }
   json += "  ]\n";
   json += "}\n";

   int handle = FileOpen(OutputFileName, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("OB_Bridge_Aggregator: failed to open output file, error=", GetLastError());
      return;
   }
   FileWriteString(handle, json);
   FileClose(handle);
}

//+------------------------------------------------------------------+
void DrawPanel()
{
   int bull_ob = 0, bear_ob = 0, bull_fvg = 0, bear_fvg = 0;
   for(int i = 0; i < ArraySize(g_ob_zones); i++)
      (g_ob_zones[i].direction == "BULLISH") ? bull_ob++ : bear_ob++;
   for(int i = 0; i < ArraySize(g_fvg_zones); i++)
      (g_fvg_zones[i].direction == "BULLISH") ? bull_fvg++ : bear_fvg++;

   string text = "\n\nOB BRIDGE AGGREGATOR | " + TargetSym() + "\n";
   text += "OB zones: " + IntegerToString(bull_ob) + " bull / " + IntegerToString(bear_ob) + " bear\n";
   text += "FVG zones: " + IntegerToString(bull_fvg) + " bull / " + IntegerToString(bear_fvg) + " bear\n";
   text += "Output: " + OutputFileName + " (Common\\Files)\n";
   Comment(text);
}
//+------------------------------------------------------------------+
