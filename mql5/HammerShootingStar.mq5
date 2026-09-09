//+------------------------------------------------------------------+
//|                   HammerShootingStar.mq5                          |
//| Direct MQL5 port of the TradingView Pine v4 script                |
//| "Hammer & ShootingStar Candle Detector". Same four conditions,     |
//| same thresholds (body*2, 10-bar lowest/highest, shadow*2, prior-   |
//| bar open/close gate) -- nothing added, nothing simplified out of   |
//| the pattern math itself.                                           |
//|                                                                     |
//| ONE deliberate behavior change from the Pine source, per explicit  |
//| request: Pine re-evaluates every condition on the still-forming    |
//| live bar on every tick (classic repaint -- the "H"/"S" label can   |
//| appear, then vanish, then reappear as that bar's OHLC keeps moving |
//| before it actually closes). This port only ever reads/evaluates    |
//| the last CLOSED bar (rates_total-2) -- the live/forming bar        |
//| (rates_total-1) is never touched, so a label appears exactly once, |
//| only after the candle that triggered it has actually finished.     |
//|                                                                     |
//| Drawing approach: unlike Supertrend/ATR Dual, this indicator has no|
//| continuous per-bar SERIES to plot -- a Hammer/Star is a discrete,  |
//| rare event on specific bars. So (same precedent as this file's own |
//| Major/Minor S/R-ray block) it draws via OBJ_TEXT labels, one object |
//| created ONCE per signal bar (named by bar time, never touched again|
//| after creation since a closed bar's classification can never       |
//| change) -- not per-tick, not per-bar-in-history, only per actual   |
//| signal. Cheap regardless of how much history is loaded.            |
//|                                                                     |
//| Simplification vs the Pine source: Hammer/GreenHammer (and         |
//| Star/RedStar) are drawn as ONE label, not two -- both conditions    |
//| are still evaluated exactly as written below, but when a bar       |
//| satisfies both (HamStar on AND ColorHamStar on AND the candle is   |
//| actually green), Pine's two plotshape() calls draw two fully        |
//| identical, perfectly overlapping "H" labels at the same spot; that |
//| second draw is visually a no-op, so this port draws once. Nothing  |
//| about the pattern LOGIC is changed by this -- only the redundant   |
//| duplicate draw call is dropped.                                    |
//|                                                                     |
//| alertcondition() in the Pine source has no MT5 Alert()/            |
//| SendNotification() equivalent wired here, same call made for       |
//| Supertrend.mq5 -- not asked for, easy to add later if wanted.      |
//|                                                                     |
//| Optional bridge publish (PublishToFile, default on) follows the    |
//| same JSON write-tmp-then-FileMove pattern as the other indicators  |
//| in this folder, so a Python bot can read the latest closed-bar     |
//| pattern state later. Not wired to any bot yet.                     |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//===================== Inputs -- names/defaults match the Pine script =====================
input bool   HamStar       = true;   // "Hammer and ShootingStar candles (color is not important)"
input bool   ColorHamStar  = false;  // "Only Green Hammer and Red Star"

input bool   PublishToFile       = true;
input string FileBridgeFolder    = "OBBridge";  // same Common Files folder as the other bridge indicators
input string BridgeSymbol        = "";          // empty = use the attached chart's symbol
input int    PublishEverySeconds = 2;

#define LOOKBACK 10   // Pine's lowest(low,10) / highest(high,10) -- a 10-bar window INCLUDING the current bar

#define PREFIX_HAMMER "HamStarDet_H_"
#define PREFIX_STAR   "HamStarDet_S_"

datetime g_last_publish_time = 0;
datetime g_last_hammer_time  = 0;
datetime g_last_star_time    = 0;
bool     g_have_last_hammer  = false;
bool     g_have_last_star    = false;

//+------------------------------------------------------------------+
//| Chart symbol, or BridgeSymbol override if one is set              |
//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "") ? _Symbol : BridgeSymbol;
}

//+------------------------------------------------------------------+
//| Pine's lowest(low,10) / highest(high,10) at bar i -- window is    |
//| [i-LOOKBACK+1 .. i] inclusive. Returns false via *ok if the chart  |
//| doesn't have enough history yet (mirrors Pine's na-propagation --  |
//| the condition simply can't be true without a full window).        |
//+------------------------------------------------------------------+
double LowestLow(const int i, const double &low[], bool &ok)
{
   ok = (i - LOOKBACK + 1 >= 0);
   if(!ok) return 0.0;
   double v = low[i];
   for(int k = i - LOOKBACK + 1; k <= i; k++)
      if(low[k] < v) v = low[k];
   return v;
}
double HighestHigh(const int i, const double &high[], bool &ok)
{
   ok = (i - LOOKBACK + 1 >= 0);
   if(!ok) return 0.0;
   double v = high[i];
   for(int k = i - LOOKBACK + 1; k <= i; k++)
      if(high[k] > v) v = high[k];
   return v;
}

//+------------------------------------------------------------------+
//| One label object, created once, never moved/deleted afterward --  |
//| a closed bar's OHLC (and therefore its classification) is final   |
//| the moment it closes, so there's nothing to ever update.          |
//+------------------------------------------------------------------+
void CreateSignalLabel(const string name, const datetime bar_time, const double price,
                        const string text, const color clr, const int anchor)
{
   if(ObjectFind(0, name) >= 0)
      return; // already drawn for this bar -- nothing to do

   ObjectCreate(0, name, OBJ_TEXT, 0, bar_time, price);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
}

//+------------------------------------------------------------------+
//| Evaluate + (if triggered) draw the pattern for ONE closed bar i.  |
//| Direct port of the Pine boolean expressions -- see header for the |
//| one drawing-side simplification (Hammer/GreenHammer share a label,|
//| Star/RedStar share a label).                                      |
//+------------------------------------------------------------------+
void EvaluateBar(const int i, const datetime &time[],
                  const double &open[], const double &high[], const double &low[], const double &close[],
                  bool &hammer_out, bool &star_out)
{
   hammer_out = false;
   star_out   = false;

   if(i < 1) return; // needs open[i-1]/close[i-1] -- Pine's [1] history reference

   double bodySize      = MathAbs(open[i] - close[i]);
   double HlowerShadow  = MathAbs(low[i]  - open[i]);
   double HupperShadow  = MathAbs(high[i] - close[i]);
   double SlowerShadow  = MathAbs(low[i]  - close[i]);
   double SupperShadow  = MathAbs(high[i] - open[i]);

   bool lowOk, highOk;
   double lowest10  = LowestLow(i, low, lowOk);
   double highest10 = HighestHigh(i, high, highOk);

   bool hammerShape = lowOk  && (HlowerShadow > bodySize * 2.0) && (low[i]  <= lowest10)
                       && (HlowerShadow > HupperShadow * 2.0) && (close[i] <= open[i - 1]);
   bool starShape   = highOk && (SupperShadow > bodySize * 2.0) && (high[i] >= highest10)
                       && (SupperShadow > SlowerShadow * 2.0) && (open[i] <= close[i - 1]);

   bool Hammer      = HamStar      && hammerShape;
   bool Star        = HamStar      && starShape;
   bool GreenHammer = ColorHamStar && hammerShape && (close[i] > open[i]);
   bool RedStar     = ColorHamStar && starShape   && (close[i] < open[i]);

   hammer_out = Hammer || GreenHammer;
   star_out   = Star   || RedStar;

   if(hammer_out)
   {
      string name = PREFIX_HAMMER + IntegerToString((long)time[i]);
      // ANCHOR_UPPER: the anchor point sits at the TOP-center of the text
      // box, so the text itself hangs downward from low[i] -- Pine's
      // location.belowbar.
      CreateSignalLabel(name, time[i], low[i], "H", clrLime, ANCHOR_UPPER);
   }
   if(star_out)
   {
      string name = PREFIX_STAR + IntegerToString((long)time[i]);
      // ANCHOR_LOWER: the anchor point sits at the BOTTOM-center of the
      // text box, so the text rises upward from high[i] -- Pine's
      // location.abovebar.
      CreateSignalLabel(name, time[i], high[i], "S", clrRed, ANCHOR_LOWER);
   }
}

//+------------------------------------------------------------------+
//| Publish the last CLOSED bar's pattern state to the bridge.        |
//+------------------------------------------------------------------+
void PublishBridgeFile(const int closed_idx, const datetime &time[], const double &close[],
                        const bool hammer_now, const bool star_now)
{
   if(!PublishToFile) return;
   if(TimeCurrent() - g_last_publish_time < PublishEverySeconds) return;
   g_last_publish_time = TimeCurrent();

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0) tf_minutes = (int)_Period;

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"bar_time\":" + IntegerToString((long)time[closed_idx]) + ",";
   j += "\"close\":" + DoubleToString(close[closed_idx], 3) + ",";
   j += "\"hammer\":" + (hammer_now ? "true" : "false") + ",";
   j += "\"star\":" + (star_now ? "true" : "false") + ",";
   j += "\"last_hammer_time\":" + IntegerToString(g_have_last_hammer ? (long)g_last_hammer_time : 0) + ",";
   j += "\"last_star_time\":" + IntegerToString(g_have_last_star ? (long)g_last_star_time : 0);
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\HAMMERSTAR_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("HammerStar bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
   }
   FileWriteString(handle, j);
   FileClose(handle);

   bool moved = false;
   int last_error = 0;
   for(int attempt = 0; attempt < 5 && !moved; attempt++)
   {
      if(attempt > 0) Sleep(10);
      moved = FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE);
      if(!moved) last_error = GetLastError();
   }
   if(!moved)
      Print("HammerStar bridge file publish failed to finalize after retries: ", final_name, " | error=", last_error);
}

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "Hammer & ShootingStar Detector");

   if(PublishToFile)
      FolderCreate(FileBridgeFolder, FILE_COMMON);

   return(INIT_SUCCEEDED);
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
   if(rates_total < LOOKBACK + 2)
      return(0); // not enough history for even one full lowest/highest window plus a prior bar

   // Only ever walk CLOSED bars -- rates_total-1 (the still-forming bar) is
   // never evaluated, which is the whole point of this port (see header:
   // no repainting on the live bar). Incremental: only newly-closed bars
   // since the last call are (re)checked; a closed bar's own classification
   // can never retroactively change, so there's no need for a settling-lag
   // safety margin the way the ATR-based indicators need one.
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;
   int last_closed = rates_total - 2;

   bool hammer_now = false;
   bool star_now   = false;

   for(int i = start; i <= last_closed; i++)
   {
      bool hammer_i, star_i;
      EvaluateBar(i, time, open, high, low, close, hammer_i, star_i);

      if(hammer_i) { g_last_hammer_time = time[i]; g_have_last_hammer = true; }
      if(star_i)   { g_last_star_time   = time[i]; g_have_last_star   = true; }

      if(i == last_closed) { hammer_now = hammer_i; star_now = star_i; }
   }

   PublishBridgeFile(last_closed, time, close, hammer_now, star_now);

   return(rates_total);
}

//+------------------------------------------------------------------+
// Only deletes the H/S label objects when the indicator is actually
// being REMOVED from the chart (reason == REASON_REMOVE) -- a
// recompile, timeframe/symbol change, input-parameter change, etc.
// leave them alone, since those cases just re-run OnInit/OnCalculate
// on the same chart and the "created once, permanent" design (see
// header) means picking up where it left off, not forcing a full-
// history rebuild. Only an actual removal should make them vanish.
void OnDeinit(const int reason)
{
   if(reason == REASON_REMOVE)
   {
      ObjectsDeleteAll(0, PREFIX_HAMMER);
      ObjectsDeleteAll(0, PREFIX_STAR);
   }
}
//+------------------------------------------------------------------+
