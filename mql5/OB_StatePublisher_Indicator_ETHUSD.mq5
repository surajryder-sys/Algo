//+------------------------------------------------------------------+
//|            OB_StatePublisher_Indicator_ETHUSD.mq5                |
//| ETHUSD-specific copy of OB_StatePublisher_Indicator_v2.00.mq5,   |
//| for the eth_smc Python bot. Same single-instance multi-chart     |
//| bridge engine, retargeted to the M5/M15/M30 timeframe set (no    |
//| M1/M3/H*) and with its RESET buttons/flag files namespaced by    |
//| symbol (RESET_<symbol>_<tf>.flag) instead of the bare RESET_<tf> |
//| names the original file uses.                                    |
//|                                                                    |
//| Why a separate file instead of editing v2.00 in place: the        |
//| MT5 Common Files bridge folder (where both indicators publish/    |
//| read RESET flags) is shared across every terminal install for     |
//| this Windows user, so the ETHUSD instance's flag files would      |
//| collide with the already-running XAUUSD instance's (both use M5)  |
//| unless namespaced. Editing v2.00 in place would require           |
//| recompiling and re-attaching it on the live XAUUSD terminal --    |
//| this file avoids touching that terminal at all.                   |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

input string OB_ObjectKeyword               = "pineBox";
input string BridgeSymbol                   = "";      // empty = use the attached chart's symbol
input string TargetTimeframes               = "H4,H2,H1,M30,M15,M5";
input bool   ShowPanel                      = true;
input int    ScanEverySeconds               = 1;
input int    MaxZonesToShow                 = 4;
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

input bool   ShowBiasLabels                 = true;
input int    BiasLabelCorner                = 0;    // 0=left top, 1=right top, 2=left bottom, 3=right bottom
input int    BiasLabelX                     = 480;
input int    BiasLabelYStart                = 20;
input int    BiasLabelRowHeight             = 20;
input int    BiasLabelFontSize              = 11;
input string BiasLabelFont                  = "Arial Bold";
input color  BullishBiasColor               = clrLime;
input color  BearishBiasColor               = clrRed;
input color  NeutralBiasColor               = clrSilver;

input bool   ShowVirginObList               = true;   // untested-only OB list per timeframe, under the bias labels
input int    VirginObListYGap               = 10;

input bool   ShowResetButtons               = true;   // RESET M5/M15/M30 buttons -> write a symbol-scoped flag file the Python bot polls
input int    ResetButtonCorner              = 0;      // 0=left top, 1=right top, 2=left bottom, 3=right bottom -- matches BiasLabelCorner by default so they sit in the same column
input int    ResetButtonX                   = 480;    // matches BiasLabelX by default
input int    ResetButtonYGap                = 15;     // gap below the virgin-OB list's current bottom row

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

TFTarget g_targets[];
TFState  g_state[];
bool     g_first_scan[];
string   g_panel_section[];
int      g_vob_slot_count = 0;

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
   ArrayResize(g_panel_section, 0);

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
      ArrayResize(g_panel_section, idx + 1);

      g_targets[idx].period  = period;
      g_targets[idx].minutes = (int)(PeriodSeconds(period) / 60);
      g_targets[idx].label   = PeriodLabel(g_targets[idx].minutes);
      g_first_scan[idx]      = true;
      g_panel_section[idx]   = "";
      ZeroMemory(g_state[idx]);
   }
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
int OnInit()
{
   ParseTargets();

   if(PublishToFile)
      FolderCreate(FileBridgeFolder, FILE_COMMON);

   CreateResetButtons();

   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);

   ScanAndPublishAll();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i = 0; i < ArraySize(g_targets); i++)
      ObjectDelete(0, BiasLabelName(i));
   for(int i = 0; i < g_vob_slot_count; i++)
      ObjectDelete(0, "OBSP_VOB_" + IntegerToString(i));
   DeleteResetButtons();
   Comment("");
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK)
      return;

   string tf = "";
   if(sparam == "OBSP_RESET_M5")       tf = "M5";
   else if(sparam == "OBSP_RESET_M15") tf = "M15";
   else if(sparam == "OBSP_RESET_M30") tf = "M30";
   else return;

   ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
   WriteResetRequest(tf);
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
   ScanAndPublishAll();
   return rates_total;
}

//+------------------------------------------------------------------+
void ScanAndPublishAll()
{
   const string symbol = EffectiveSymbol();

   for(int i = 0; i < ArraySize(g_targets); i++)
      ProcessTimeframe(i, symbol);

   UpdateBiasLabels();

   int vob_bottom_y = BiasLabelYStart + ArraySize(g_targets) * BiasLabelRowHeight + VirginObListYGap;
   if(ShowVirginObList)
      vob_bottom_y = UpdateVirginObLabels();

   RepositionResetButtons(vob_bottom_y + ResetButtonYGap);

   if(ShowPanel)
      DrawCombinedPanel(symbol);
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
void CreateResetButtons()
{
   if(!ShowResetButtons)
      return;
   // Placed at a default Y here; RepositionResetButtons() moves them under
   // the virgin-OB list every poll, once that list's height is known.
   CreateResetButton("OBSP_RESET_M5",  "RESET M5",  ResetButtonX, BiasLabelYStart);
   CreateResetButton("OBSP_RESET_M15", "RESET M15", ResetButtonX + 87, BiasLabelYStart);
   CreateResetButton("OBSP_RESET_M30", "RESET M30", ResetButtonX + 174, BiasLabelYStart);
}

//+------------------------------------------------------------------+
void RepositionResetButtons(const int y)
{
   if(!ShowResetButtons)
      return;
   ObjectSetInteger(0, "OBSP_RESET_M5", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "OBSP_RESET_M15", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "OBSP_RESET_M30", OBJPROP_YDISTANCE, y);
}

//+------------------------------------------------------------------+
void DeleteResetButtons()
{
   ObjectDelete(0, "OBSP_RESET_M5");
   ObjectDelete(0, "OBSP_RESET_M15");
   ObjectDelete(0, "OBSP_RESET_M30");
}

//+------------------------------------------------------------------+
void WriteResetRequest(const string tf)
{
   // The block state this releases lives in the Python bot's own store, not
   // here -- this just drops a flag file for it to pick up on its next poll
   // and delete, same direction as the JSON bridge but reversed. Namespaced
   // by symbol so it can never collide with the XAUUSD indicator's
   // unscoped RESET_<tf>.flag in the same shared Common Files folder.
   FolderCreate(FileBridgeFolder, FILE_COMMON);
   string name = FileBridgeFolder + "\\RESET_" + EffectiveSymbol() + "_" + tf + ".flag";
   int handle = FileOpen(name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("Reset request write failed: ", name, " | error=", GetLastError());
      return;
   }
   FileWriteString(handle, IntegerToString((long)TimeCurrent()));
   FileClose(handle);
   Print("Reset requested for ", tf, " -- flag written: ", name);
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
void ProcessTimeframe(const int idx, const string symbol)
{
   const ENUM_TIMEFRAMES period  = g_targets[idx].period;
   const int              minutes = g_targets[idx].minutes;

   long chart_id = FindChartForSymbolPeriod(symbol, period);
   if(chart_id < 0)
   {
      // Chart not open for this timeframe. Leave the last published state
      // untouched rather than overwriting it with zeros on a transient miss.
      g_state[idx].chart_found = false;
      g_panel_section[idx] = g_targets[idx].label + ": chart not open (attach LuxAlgo + open a " +
                             g_targets[idx].label + " chart for " + symbol + ")\n";
      return;
   }
   g_state[idx].chart_found = true;

   ScanObjectsFor(chart_id, period, symbol, minutes, idx);

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
      PublishGVFor(st, symbol, minutes);

   if(PublishToFile)
      PublishFileFor(st, symbol, minutes, bull_history, bear_history);

   BuildPanelSection(idx, symbol, has_bull, latest_bull, has_bear, latest_bear, bull_history, bear_history);
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

      datetime visit_time = 0;
      bool visited = HasZoneBeenRetested(zones[i], visit_time, symbol, period);

      // Historical validation/retest reconstruction and live price monitoring
      // are intentionally independent. A missing historical validation must
      // never prevent a real-time touch from changing Virgin to false.
      if(!visited)
         visited = ApplyIndependentLiveTouch(zones[i], visit_time, symbol);

      zones[i].virgin = !visited;
      zones[i].visit_time = visit_time;
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
void BuildPanelSection(const int idx, const string symbol,
                       bool has_bull, OBZone &latest_bull,
                       bool has_bear, OBZone &latest_bear,
                       OBZone &bull_hist[], OBZone &bear_hist[])
{
   string text = "";
   text += "-- " + g_targets[idx].label + " --\n";

   if(has_bull)
      text += "Demand: " + PriceText(latest_bull.high, latest_bull.low) +
              " | Virgin: " + BoolText(latest_bull.virgin) +
              " | Detected: " + DetectionText(latest_bull) + "\n";
   else
      text += "Demand: none\n";

   if(has_bear)
      text += "Supply: " + PriceText(latest_bear.high, latest_bear.low) +
              " | Virgin: " + BoolText(latest_bear.virgin) +
              " | Detected: " + DetectionText(latest_bear) + "\n";
   else
      text += "Supply: none\n";

   int shown_demand = MathMin(ArraySize(bull_hist), MaxZonesToShow);
   for(int i = 0; i < shown_demand; i++)
      text += "  D" + IntegerToString(i + 1) + ") " +
              PriceText(bull_hist[i].high, bull_hist[i].low) +
              " | Virgin: " + BoolText(bull_hist[i].virgin) +
              " | Detected: " + DetectionText(bull_hist[i]) + "\n";

   int shown_supply = MathMin(ArraySize(bear_hist), MaxZonesToShow);
   for(int i = 0; i < shown_supply; i++)
      text += "  S" + IntegerToString(i + 1) + ") " +
              PriceText(bear_hist[i].high, bear_hist[i].low) +
              " | Virgin: " + BoolText(bear_hist[i].virgin) +
              " | Detected: " + DetectionText(bear_hist[i]) + "\n";

   g_panel_section[idx] = text;
}

//+------------------------------------------------------------------+
void DrawCombinedPanel(const string symbol)
{
   string text = "OB STATE BRIDGE (ETHUSD) | " + symbol + " | " + IntegerToString(ArraySize(g_targets)) + " timeframes\n\n";

   for(int i = 0; i < ArraySize(g_targets); i++)
      text += g_panel_section[i] + "\n";

   if(PublishToFile)
      text += "Bridge folder: " + FileBridgeFolder + "\\ (OBSTATE_" + symbol + "_<minutes>.json)\n";

   Comment(text);
}

//+------------------------------------------------------------------+
string DetectionText(OBZone &z)
{
   if(z.baseline || z.detected_time <= 0)
      return "baseline";

   return TimeToString(z.detected_time, TIME_DATE | TIME_SECONDS) +
          " @ " + DoubleToString(z.detected_price, 5);
}

//+------------------------------------------------------------------+
string PriceText(double high, double low)
{
   return DoubleToString(high, 5) + " - " + DoubleToString(low, 5);
}

//+------------------------------------------------------------------+
string BoolText(bool v)
{
   return (v ? "true" : "false");
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
