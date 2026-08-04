//+------------------------------------------------------------------+
//|                 OB_StatePublisher_Indicator.mq5                  |
//| Reads OB rectangles from chart objects, classifies direction,     |
//| calculates virgin status, and publishes latest OB bias/levels.    |
//| Single-instance multi-chart bridge: attach to ONE chart; it scans |
//| every other open chart (same symbol) for each configured          |
//| timeframe by chart ID, so the publisher does not need to be       |
//| attached per timeframe. The LuxAlgo OB detector must still run on |
//| each of those timeframe charts (it draws the pineBox rectangles), |
//| but those charts can stay open/minimized without our indicator.   |
//|                                                                     |
//| Built for XAUUSD first (BridgeSymbol="" -> uses the attached       |
//| chart's symbol, TargetTimeframes below). Extending to another      |
//| symbol later is just attaching this same file on that symbol's     |
//| chart -- BridgeSymbol/TargetTimeframes already make it generic,    |
//| and OBSTATE_<symbol>_<minutes>.json output is already namespaced   |
//| by symbol so multiple instances never collide.                     |
//|                                                                     |
//| No RESET buttons / BLOCK_STATUS display in this build -- manual-   |
//| intervention blocking isn't wired up yet. Add back later if/when   |
//| that's needed.                                                     |
//|                                                                     |
//| On-chart display: one stacked block per timeframe, left side,      |
//| ordered as listed in TargetTimeframes. Each block is a bold header |
//| line ("H4, Recent: Bullish OB, ATR Trail: 4023.10"), then a Supply  |
//| column (left) and Demand column (right) that each stack their own  |
//| latest zone followed by their own older-zone history. ATR Trail is |
//| computed inline per timeframe (ComputeATRTrailInline) using the    |
//| same math as ATR_Trail.mq5 -- deliberately NOT iCustom: calling    |
//| iCustom("ATR_Trail",...) for ANY of the 8 timeframes was found to  |
//| draw that indicator's own top-left label onto this chart regardless|
//| of which period was requested, so it's never invoked at all now.   |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

input string OB_ObjectKeyword               = "pineBox";
input string BridgeSymbol                   = "";      // empty = use the attached chart's symbol
input string TargetTimeframes               = "H4,H2,H1,M30,M15,M5,M3,M1";
input string NoTradeZoneTimeframes          = "H4,H2,H1,M30,M15";  // which of the above tag [NO LONG]/[NO SHORT] on their header when their latest Supply/Demand zone is still virgin
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

input double ATRTrailKeyValue               = 2.0;
input int    ATRTrailPeriod                 = 2;
input int    ATRTrailInlineBars             = 300;   // warm-up window for the one timeframe computed inline (matches this chart's own period)

input color  BullishColor                   = clrLime;
input color  BearishColor                   = clrRed;
input color  NeutralColor                   = clrSilver;
input color  ActiveTouchColor               = clrOrange;  // primary Supply/Demand line turns this color while live price sits inside that zone right now

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
input int    PanelRightColumnX              = 400;    // fixed x offset (from PanelX) for BOTH the Demand column and the S column -- shared so they line up on one straight column
input int    PanelThirdColumnX              = 780;    // fixed x offset (from PanelX) for the Reversal Zone column -- mostly a safety floor now, since the per-row guard (widest of Demand's own lines + PanelColumnPadding) does the real work, matching how Supply->Demand's gap is computed
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
int      g_iatr_handles[];   // one built-in iATR handle per target timeframe, created once (see CreateATRHandles)
int      g_panel_y = 0;

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
// for the indicator's lifetime -- creating/releasing a fresh handle on
// every scan (the previous version of this function did exactly that,
// 8 times per tick) eventually exhausts MT5's indicator handle pool,
// which is exactly why ATR Trail worked right after a reload and then
// silently stopped showing on every block after the terminal had been
// running a while.
void CreateATRHandles(const string symbol)
{
   for(int i = 0; i < ArraySize(g_iatr_handles); i++)
      if(g_iatr_handles[i] != INVALID_HANDLE)
         IndicatorRelease(g_iatr_handles[i]);

   ArrayResize(g_iatr_handles, ArraySize(g_targets));
   for(int i = 0; i < ArraySize(g_targets); i++)
   {
      g_iatr_handles[i] = iATR(symbol, g_targets[i].period, ATRTrailPeriod);
      if(g_iatr_handles[i] == INVALID_HANDLE)
         Print("ATR handle failed for ", g_targets[i].label, " | error=", GetLastError());
   }
}

//+------------------------------------------------------------------+
// Same recursive math as ATR_Trail.mq5's OnCalculate, run here directly
// over each timeframe's own recent history -- deliberately NOT iCustom.
// iCustom("ATR_Trail",...) for a timeframe other than this chart's own
// still ended up drawing that indicator's own top-left "ATR Trail:
// <value>" label on THIS chart regardless of the requested period, so
// invoking it at all -- for any of the 8 timeframes -- was the actual
// bug. Only the built-in iATR() is used below (via the cached handle from
// CreateATRHandles); nothing here can ever create a chart object.
// Warm-starts from ATRTrailInlineBars back rather than true bar zero,
// which converges to the same value within a handful of bars for this
// KeyValue/ATRPeriod combination -- fine for a display value.
bool ComputeATRTrailInline(const int atr_handle, const string symbol, const ENUM_TIMEFRAMES period, double &value, int &trend)
{
   value = 0.0;
   trend = 0;

   if(atr_handle == INVALID_HANDLE)
      return false;

   int available = iBars(symbol, period);
   int bars = MathMin(ATRTrailInlineBars, available);
   if(bars < ATRTrailPeriod + 2)
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

      double nLoss = ATRTrailKeyValue * atr_buf[i];

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
int OnInit()
{
   ParseTargets();
   CreateATRHandles(EffectiveSymbol());

   if(PublishToFile)
      FolderCreate(FileBridgeFolder, FILE_COMMON);

   if(ScanEverySeconds > 0)
      EventSetTimer(ScanEverySeconds);

   ScanAndPublishAll();
   return INIT_SUCCEEDED;
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
void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i = 0; i < ArraySize(g_iatr_handles); i++)
      if(g_iatr_handles[i] != INVALID_HANDLE)
         IndicatorRelease(g_iatr_handles[i]);
   DeleteObjectsByPrefix("OBP_");
   Comment("");
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

   g_panel_y = PanelYStart;
   for(int i = 0; i < ArraySize(g_targets); i++)
      ProcessTimeframe(i, symbol);

   // Defensive only -- GetATRTrail no longer calls iCustom at all (see
   // ComputeATRTrailInline), so this object should never originate from
   // this indicator. Deleted here anyway in case a separately-attached
   // ATR_Trail instance on this same chart writes it (that one has to be
   // removed from the chart's Indicators List directly; this can't reach it).
   ObjectDelete(0, "ATR_Trail_Label");

   ChartRedraw(0);
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
}

//+------------------------------------------------------------------+
string VirginText(const bool v) { return v ? "Virgin" : "Tested"; }

//+------------------------------------------------------------------+
// Drops the leading "yyyy." from TimeToString's fixed "yyyy.mm.dd hh:mi"
// output -- shaves 5 characters off every real timestamp shown, which
// matters given how little margin these single-line entries have before
// hitting MT5's apparent per-label length limit.
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
// A timeframe is in the No Long/No Short classification set (currently
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
// "H4, Recent: " (neutral) | "Bullish OB"/"Bearish OB" (bull/bear color) |
// ", ATR Trail: " (neutral) | the value itself (bull/bear color by
// whether price is currently above/below that trail). No Long/No Short
// status has its own dedicated column now (see RenderTimeframeBlock)
// instead of a header tag, so the actual price range is visible without
// cross-referencing the Supply/Demand lines. TextGetSize gives each
// segment's rendered pixel width so the next segment lines up
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
      texts[0] = label + ": chart not open"; colors[0] = NeutralColor;
      count = 1;
   }
   else
   {
      texts[0] = label + ", Recent: ";                       colors[0] = NeutralColor;
      texts[1] = (has_ob ? BiasText(bias) : "None") + " OB"; colors[1] = has_ob ? BiasColorFor(bias) : NeutralColor;
      count = 2;

      if(atr_ok)
      {
         texts[2] = ", ATR Trail: ";                    colors[2] = NeutralColor;
         texts[3] = DoubleToString(atr_value, digits);
         colors[3] = (atr_trend > 0) ? BullishColor : (atr_trend < 0 ? BearishColor : NeutralColor);
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

   // Two independent columns now instead of interleaved D/S rows: Supply
   // (bold, colored) and its own older history stack on the LEFT; Demand
   // (bold, colored) and its own older history stack on the RIGHT. Each
   // column advances its own y independently since one side can have more
   // history than the other; the block as a whole advances by whichever
   // column ended up taller.
   int extra_bull = available ? MathMax(0, ArraySize(bull_hist) - 1) : 0;
   int extra_bear = available ? MathMax(0, ArraySize(bear_hist) - 1) : 0;
   int shown_bull = MathMin(extra_bull, MaxAdditionalZonesShown);
   int shown_bear = MathMin(extra_bear, MaxAdditionalZonesShown);

   // Single line per entry, as tight as the content can reasonably get:
   // single-letter D:/R: labels, no " | " separators, no spaces around the
   // price dash. Worst case (a zone with a real, non-baseline Detected AND
   // a real Retested time) comes to ~60 characters, vs. the ~63 where the
   // earlier "| Det: x | Ret: y" version (with full-width labels) was
   // measured cutting off live -- some margin this time, but still not a
   // guarantee given the limit itself was never pinned down exactly. Flag
   // it immediately if any row still clips.
   int y_body   = y;
   int y_supply = y_body;
   int y_demand = y_body;
   int y_zone   = y_body;   // No Long/No Short column -- tracked fully independently, only maxed in at the very end (see bottom of this function) so it never pads out Supply/Demand's own Additional Zones
   int demand_x = PanelX + PanelRightColumnX;

   if(available)
   {
      TextSetFont(PanelBoldContentFont, -(PanelContentFontSize * 10), 0, 0);

      string supply_text = "Supply: " + (has_bear ? PriceText(latest_bear.high, latest_bear.low, digits) + " " + VirginText(latest_bear.virgin) +
                           " D:" + DetectionText(latest_bear) + " R:" + RetestText(latest_bear) : "none");
      // Orange while live price sits inside this zone right now -- reverts
      // to plain Bearish/BullishColor the moment price moves back out,
      // whether that's because the zone got respected (still on the chart,
      // now Tested) or it's just between visits. A fully invalidated zone
      // never reaches this code at all -- LuxAlgo deletes its rectangle,
      // so the next scan simply no longer has it in bear_hist/bull_hist.
      color  supply_color = has_bear ? (IsCurrentMarketTouchingZone(latest_bear, symbol) ? ActiveTouchColor : BearishColor) : NeutralColor;
      SetPanelLabel("OBP_SUP_" + label, supply_text, supply_color, PanelX, y_supply, PanelContentFontSize, PanelBoldContentFont);
      y_supply += PanelLineHeight;

      uint sw, sh;
      TextGetSize(supply_text, sw, sh);
      demand_x = PanelX + MathMax(PanelRightColumnX, (int)sw + PanelColumnPadding);

      string demand_text = "Demand: " + (has_bull ? PriceText(latest_bull.high, latest_bull.low, digits) + " " + VirginText(latest_bull.virgin) +
                           " D:" + DetectionText(latest_bull) + " R:" + RetestText(latest_bull) : "none");
      color  demand_color = has_bull ? (IsCurrentMarketTouchingZone(latest_bull, symbol) ? ActiveTouchColor : BullishColor) : NeutralColor;
      SetPanelLabel("OBP_DEM_" + label, demand_text, demand_color, demand_x, y_demand, PanelContentFontSize, PanelBoldContentFont);
      y_demand += PanelLineHeight;

      // Fixed alignment (PanelThirdColumnX) same as Demand's own guard
      // against Supply -- only shifts further right if THIS COLUMN's actual
      // widest text would otherwise overrun it, so every row's Reversal
      // Zone entry lines up in one straight column instead of drifting.
      // Must measure the D2/D3/D4 Additional Zones rows too, not just the
      // primary Demand line -- they sit in the same column (demand_x) and
      // are often just as long, so guarding on the primary line alone let
      // those specific rows bleed into column 3.
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

      // Third column, own dedicated listing (not just a header flag) so the
      // actual price range is visible without cross-referencing Supply/
      // Demand above. Only the classified timeframes (NoTradeZoneTimeframes)
      // get entries here; H4/H2/H1/M30/M15 by default -- M5/M3/M1 leave this
      // column empty.
      //
      // Virgin zones ONLY -- every zone (tested or not) is already listed
      // in the Supply/Demand columns to the left, so repeating all of them
      // here was pure duplication. This column exists specifically to call
      // out the untested ones: a virgin Supply zone is a Bearish Reversal
      // Zone (a live trigger for price to reverse down when it's first
      // reached), a virgin Demand zone is a Bullish Reversal Zone.
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
               SetPanelLabel(name, text, BearishColor, zone_x, y_zone, PanelContentFontSize, PanelBoldContentFont);
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
               SetPanelLabel(name, text, BullishColor, zone_x, y_zone, PanelContentFontSize, PanelBoldContentFont);
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
         SetPanelLabel(sname, stext, NeutralColor, PanelX, y_supply, PanelContentFontSize, PanelContentFont);
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
         SetPanelLabel(dname, dtext, NeutralColor, demand_x, y_demand, PanelContentFontSize, PanelContentFont);
         y_demand += PanelLineHeight;
      }
      else
         ObjectDelete(0, dname);
   }

   // Stale objects from the old 2-line layout (OBP_SUPD_/OBP_DEMD_/the "D"
   // suffix on Additional Zone names) linger otherwise -- OnDeinit's
   // prefix cleanup only runs on a full remove/reattach, not every scan.
   ObjectDelete(0, "OBP_SUPD_" + label);
   ObjectDelete(0, "OBP_DEMD_" + label);
   for(int i = 0; i < MaxAdditionalZonesShown; i++)
   {
      ObjectDelete(0, "OBP_ADD_" + label + "_S" + IntegerToString(i) + "D");
      ObjectDelete(0, "OBP_ADD_" + label + "_D" + IntegerToString(i) + "D");
   }

   // y_zone (No Long/No Short column) deliberately left out here -- the
   // next block's position follows Supply/Demand only, so those two never
   // get an artificial gap. If a timeframe's zone list runs longer than
   // Supply/Demand's own content, it's allowed to extend into the next
   // block's row-space rather than reserving room for itself -- it's off
   // in its own fixed column (PanelThirdColumnX) with each line labeled by
   // timeframe, so it stays legible even then.
   y = MathMax(y_supply, y_demand) + PanelBlockGap;
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
      OBZone empty_zone;
      OBZone empty_hist[];
      g_panel_y = RenderTimeframeBlock(idx, symbol, g_panel_y, false,
                                       false, empty_zone, false, empty_zone,
                                       empty_hist, empty_hist, false, 0.0, 0);
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

   double atr_value; int atr_trend;
   bool atr_ok = GetATRTrail(idx, symbol, atr_value, atr_trend);

   g_panel_y = RenderTimeframeBlock(idx, symbol, g_panel_y, true,
                                    has_bull, latest_bull, has_bear, latest_bear,
                                    bull_history, bear_history, atr_ok, atr_value, atr_trend);
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
   if(bias > 0.5)  return "Bullish";
   if(bias < -0.5) return "Bearish";
   return "None";
}

//+------------------------------------------------------------------+
color BiasColorFor(const double bias)
{
   if(bias > 0.5)  return BullishColor;
   if(bias < -0.5) return BearishColor;
   return NeutralColor;
}

//+------------------------------------------------------------------+
string PriceText(double high, double low, int digits)
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
