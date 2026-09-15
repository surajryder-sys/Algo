//+------------------------------------------------------------------+
//|                         CISD_AlgoAlpha.mq5                        |
//| Direct MQL5 port of AlgoAlpha's Pine v6 script "Change in State   |
//| of Delivery CISD [AlgoAlpha]". Original logic/attribution:        |
//| AlgoAlpha (TradingView) -- ported here unmodified in behavior,    |
//| only the drawing mechanics differ where MQL5 has no equivalent    |
//| (see notes below). Not a redesign -- every threshold, every       |
//| array-ordering/tie-break quirk in the source is preserved on      |
//| purpose, even the odd ones (see HS_/CISD confirmation loop below).|
//|                                                                     |
//| WHAT IT DOES (same as the Pine): tracks swing high/low pivots as   |
//| "liquidity" lines; watches for a single reversal candle (red->green|
//| or green->red) and records its OPEN as a pending CISD origin;      |
//| confirms that origin as a real "Change in State of Delivery" once  |
//| price closes back through it AND the retracement depth (vs. the    |
//| swing that preceded the reversal candle) clears the Noise Filter    |
//| tolerance; flags a "liquidity sweep" variant when that confirmation |
//| follows a recent old-high/old-low wick mitigation.                 |
//|                                                                     |
//| CLOSED-BAR ONLY, same convention as HammerShootingStar.mq5: every   |
//| bit of this is stateful/cumulative (arrays that grow, shrink, get   |
//| cleared) so a bar's processing must run EXACTLY ONCE, ever -- never |
//| on the live/forming bar, never replayed. Uses the same watermark    |
//| pattern ATRTrailDual_MajorMinor.mq5's gc_ConfirmedUpTo already      |
//| uses for exactly this reason (Major/Minor's pivot state is just as  |
//| cumulative). On a fresh attach (prev_calculated<=0) all state       |
//| resets and the whole loaded history replays once, same as Major/   |
//| Minor's own ResetAllConfirmed() -- Pine would do the same thing on  |
//| a fresh load since its `var` state starts over too.                |
//|                                                                     |
//| DROPPED, cosmetic-only, no MQL5 equivalent:                        |
//|  - plotcandle's color.from_gradient recoloring of the actual price  |
//|    candles by transparency (t1/t2 inputs). MQL5 indicator buffer    |
//|    colors are opaque, fixed-palette slots -- there's no dynamic     |
//|    alpha blend against chart.bg_color the way Pine's gradient does. |
//|    The `trend` value this would have colored by is still tracked    |
//|    and bridge-published; only the recolored-candle overlay itself   |
//|    is left out, same call made for Supertrend.mq5's fill() drop.    |
//|  - alertcondition() has no Alert()/SendNotification() wired here,   |
//|    same call made for Supertrend.mq5 and HammerShootingStar.mq5.    |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots   2

//--- Plot 1: swing-high marker (Pine's red "*" plotchar, offset -len)
#property indicator_label1  "Swing High"
#property indicator_type1   DRAW_ARROW
#property indicator_width1  1

//--- Plot 2: swing-low marker (Pine's green "*" plotchar, offset -len)
#property indicator_label2  "Swing Low"
#property indicator_type2   DRAW_ARROW
#property indicator_width2  1

//===================== Inputs -- names/defaults match the Pine script =====================
input double Tolerance          = 0.7;   // "Noise Filter" -- larger = less noise
input int    SwingPeriod        = 12;     // "Swing Period" -- pivot left/right bars
input int    ExpiryBars         = 100;    // "Expiry Bars" -- liquidity lines stop updating past this age
input int    LiquidityLookback  = 10;     // "Liquidity Lookback" -- how recent a wick mitigation must be to count as a sweep

input color  BullColor = C'0,255,187';    // Pine default #00ffbb
input color  BearColor = C'255,17,0';     // Pine default #ff1100
input bool   HideExpiredLevels   = true;
input bool   HideMitigatedLevels = false;
input int    MaxSwingLines       = 100;   // Pine hardcodes this cap (while size()>100: pop)

input bool   PublishToFile       = true;
input string FileBridgeFolder    = "OBBridge";
input string BridgeSymbol        = "";
input int    PublishEverySeconds = 2;

#define DOT_MARKER_CODE 159   // wingdings solid filled circle, same as Supertrend's flip dots

#define PREFIX_SWING_HIGH  "CISD_SH_"
#define PREFIX_SWING_LOW   "CISD_SL_"
#define PREFIX_ORIGIN_LINE "CISD_ORIGIN_"
#define PREFIX_SWEEP       "CISD_SWEEP_"

//===================== Plot buffers =====================
double SwingHighMarker[];
double SwingLowMarker[];

//===================== Persistent CISD state (must survive across calls) =====================
// Swing-high / swing-low "liquidity" lines. Index 0 = newest (matches
// Pine's unshift-at-front); trimmed from the tail (oldest) past
// MaxSwingLines, same as Pine's own while-pop cap.
double sh_level[]; int sh_startIdx[];
double sl_level[]; int sl_startIdx[];

datetime g_last_wicked_high_time = 0;  double g_last_wicked_high_level = 0.0;  bool g_have_wicked_high = false;
datetime g_last_wicked_low_time  = 0;  double g_last_wicked_low_level  = 0.0;  bool g_have_wicked_low  = false;
int      g_last_wicked_high_bar  = -1;
int      g_last_wicked_low_bar   = -1;

// Pending CISD origin candidates. Index 0 = newest (front) -- the
// confirm/discard loop below structurally depends on front-only checks,
// not just a tie-break nicety (see ProcessCISDCandidates's own comment).
double bear_open[]; int bear_idx[];
double bull_open[]; int bull_idx[];

int      g_trend = 0;   // Pine's `var trend`

int      g_last_cisd_type = 0;   // 0=none, 1=bearish, 2=bullish (most recent ever confirmed)
double   g_last_cisd_level = 0.0;
datetime g_last_cisd_time  = 0;
bool     g_last_sweep = false;   // was the most recent confirmed CISD a liquidity-sweep variant

int      g_confirmed_upto = -1;  // watermark -- highest closed bar index already processed, ever
datetime g_last_publish_time = 0;

//+------------------------------------------------------------------+
//| tiny front-insert / arbitrary-remove helpers for the parallel     |
//| double/int arrays above -- same spirit as ATRTrailDual_MajorMinor |
//| .mq5's own PushD/InsertAt_D helpers, just front-biased since that's|
//| what Pine's array.unshift/array.shift/array.first need here.      |
//+------------------------------------------------------------------+
void InsertFrontD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void InsertFrontI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void RemoveFrontD(double &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveFrontI(int    &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveAtD(double &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveAtI(int    &a[], int idx){ int n=ArraySize(a); if(idx<0 || idx>=n) return; for(int k=idx;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

string EffectiveSymbol(){ return (BridgeSymbol == "") ? _Symbol : BridgeSymbol; }

//+------------------------------------------------------------------+
//| Strict local extreme over a 2*len+1 window centered on c -- same  |
//| math as ATRTrailDual_MajorMinor.mq5's IsPivotHigh/IsPivotLow       |
//| (itself already a verified-faithful match for Pine's ta.pivothigh/ |
//| ta.pivotlow strictness), just renamed to avoid clashing if this    |
//| ever gets merged into that file later.                             |
//+------------------------------------------------------------------+
bool CISD_IsPivotHigh(const double &high[], int c, int len, int total)
{
   if(c-len<0 || c+len>=total) return false;
   double v=high[c];
   for(int k=c-len;k<=c+len;k++)
      if(k!=c && high[k]>=v) return false;
   return true;
}
bool CISD_IsPivotLow(const double &low[], int c, int len, int total)
{
   if(c-len<0 || c+len>=total) return false;
   double v=low[c];
   for(int k=c-len;k<=c+len;k++)
      if(k!=c && low[k]<=v) return false;
   return true;
}

//+------------------------------------------------------------------+
//| MT5 chart objects show on every timeframe by default -- restrict  |
//| each drawn object to the timeframe it was actually created on,    |
//| same fix (and same reason: confirmed live 2026-09-10) applied to   |
//| HammerShootingStar.mq5's labels.                                   |
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
//| One swing-liquidity line, named deterministically by its start bar|
//| so it's naturally idempotent to (re)create/move.                  |
//+------------------------------------------------------------------+
void DrawOrMoveSwingLine(const string prefix, const int startIdx, const double level,
                          const datetime &time[], const int endIdx, const color clr)
{
   string name = prefix + IntegerToString(startIdx);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, time[startIdx], level, time[endIdx], level);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
   }
   else
      ObjectMove(0, name, 1, time[endIdx], level);
}
void DeleteSwingLine(const string prefix, const int startIdx)
{
   ObjectDelete(0, prefix + IntegerToString(startIdx));
}

//+------------------------------------------------------------------+
//| Insert a freshly-confirmed pivot at the FRONT (index 0), matching |
//| Pine's unshift -- required for the wick-scan's tie-break (whoever |
//| gets processed LAST when multiple lines are hit the same bar wins,|
//| and Pine's oldest-to-newest traversal below means newest wins).   |
//+------------------------------------------------------------------+
void AddSwingHigh(const int startIdx, const double level){ InsertFrontI(sh_startIdx, startIdx); InsertFrontD(sh_level, level); }
void AddSwingLow (const int startIdx, const double level){ InsertFrontI(sl_startIdx, startIdx); InsertFrontD(sl_level,  level); }

//+------------------------------------------------------------------+
//| Extend every still-active swing-high line to bar i, delete/expire |
//| or delete/mitigate as needed, exactly mirroring the Pine loop's   |
//| oldest-index-to-newest-index traversal (safe removal direction --  |
//| removing at index j only shifts already-visited higher indices).  |
//+------------------------------------------------------------------+
void ScanSwingHighs(const int i, const datetime &time[], const double &high[],
                     bool &wicked_high, double &wicked_level)
{
   wicked_high = false;
   for(int j = ArraySize(sh_startIdx) - 1; j >= 0; j--)
   {
      int    startIdx = sh_startIdx[j];
      double level     = sh_level[j];

      if(i - startIdx < ExpiryBars)
      {
         DrawOrMoveSwingLine(PREFIX_SWING_HIGH, startIdx, level, time, i, (color)ChartGetInteger(0, CHART_COLOR_FOREGROUND));
         if(high[i] >= level)
         {
            if(HideMitigatedLevels) DeleteSwingLine(PREFIX_SWING_HIGH, startIdx);
            RemoveAtI(sh_startIdx, j); RemoveAtD(sh_level, j);
            wicked_high = true;
            wicked_level = level;
         }
      }
      else
      {
         if(HideExpiredLevels) DeleteSwingLine(PREFIX_SWING_HIGH, startIdx);
         RemoveAtI(sh_startIdx, j); RemoveAtD(sh_level, j);
      }
   }

   while(ArraySize(sh_startIdx) > MaxSwingLines)
   {
      int last = ArraySize(sh_startIdx) - 1;
      DeleteSwingLine(PREFIX_SWING_HIGH, sh_startIdx[last]);
      ArrayResize(sh_startIdx, last); ArrayResize(sh_level, last);
   }
}
void ScanSwingLows(const int i, const datetime &time[], const double &low[],
                    bool &wicked_low, double &wicked_level)
{
   wicked_low = false;
   for(int j = ArraySize(sl_startIdx) - 1; j >= 0; j--)
   {
      int    startIdx = sl_startIdx[j];
      double level     = sl_level[j];

      if(i - startIdx < ExpiryBars)
      {
         DrawOrMoveSwingLine(PREFIX_SWING_LOW, startIdx, level, time, i, (color)ChartGetInteger(0, CHART_COLOR_FOREGROUND));
         if(low[i] <= level)
         {
            if(HideMitigatedLevels) DeleteSwingLine(PREFIX_SWING_LOW, startIdx);
            RemoveAtI(sl_startIdx, j); RemoveAtD(sl_level, j);
            wicked_low = true;
            wicked_level = level;
         }
      }
      else
      {
         if(HideExpiredLevels) DeleteSwingLine(PREFIX_SWING_LOW, startIdx);
         RemoveAtI(sl_startIdx, j); RemoveAtD(sl_level, j);
      }
   }

   while(ArraySize(sl_startIdx) > MaxSwingLines)
   {
      int last = ArraySize(sl_startIdx) - 1;
      DeleteSwingLine(PREFIX_SWING_LOW, sl_startIdx[last]);
      ArrayResize(sl_startIdx, last); ArrayResize(sl_level, last);
   }
}

//+------------------------------------------------------------------+
//| Bear-side (tracks bullish reversal-candle opens, confirms a       |
//| BEARISH CISD once price closes back below one) confirm/discard    |
//| loop -- direct port of the Pine while-loop. Checks the FRONT      |
//| (newest pending) candidate only; if it fails the retracement       |
//| tolerance it's discarded and the next-older one is checked against|
//| the SAME bar's close; if the front candidate's condition doesn't   |
//| even hold, the loop stops WITHOUT checking older ones -- that's    |
//| the Pine source's own behavior, preserved as-is, not a bug to fix. |
//+------------------------------------------------------------------+
bool ConfirmBearCISD(const int i, const double &open[], const double &close[],
                      double &origin_lvl, int &origin_idx)
{
   while(ArraySize(bear_open) > 0)
   {
      double candOpen = bear_open[0];
      int    candIdx  = bear_idx[0];

      if(close[i] < candOpen)
      {
         double highest = 0.0;
         for(int k = candIdx; k <= i; k++)
            if(close[k] > highest) highest = close[k];

         double top = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && close[k] < open[k])
         {
            top = open[k];
            k--;
         }

         double denom = top - candOpen;
         if(denom != 0.0 && (highest - candOpen) / denom > Tolerance)
         {
            origin_lvl = candOpen;
            origin_idx = candIdx;
            ArrayResize(bear_open, 0); ArrayResize(bear_idx, 0);
            return true;
         }
         else
         {
            RemoveFrontD(bear_open); RemoveFrontI(bear_idx);
         }
      }
      else
         break;
   }
   return false;
}
//+------------------------------------------------------------------+
//| Bull-side mirror of ConfirmBearCISD -- see its comment above.     |
//+------------------------------------------------------------------+
bool ConfirmBullCISD(const int i, const double &open[], const double &close[],
                      double &origin_lvl, int &origin_idx)
{
   while(ArraySize(bull_open) > 0)
   {
      double candOpen = bull_open[0];
      int    candIdx  = bull_idx[0];

      if(close[i] > candOpen)
      {
         double lowest = close[i];
         for(int k = candIdx; k <= i; k++)
            if(close[k] < lowest) lowest = close[k];

         double bottom = 0.0;
         int k = candIdx - 1;
         while(k >= 0 && close[k] > open[k])
         {
            bottom = open[k];
            k--;
         }

         double denom = candOpen - bottom;
         if(denom != 0.0 && (candOpen - lowest) / denom > Tolerance)
         {
            origin_lvl = candOpen;
            origin_idx = candIdx;
            ArrayResize(bull_open, 0); ArrayResize(bull_idx, 0);
            return true;
         }
         else
         {
            RemoveFrontD(bull_open); RemoveFrontI(bull_idx);
         }
      }
      else
         break;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Draw the static (never-updated-again) origin CISD line, exactly   |
//| like Pine's one-shot line.new(origin_idx, origin_lvl, bar_index,   |
//| origin_lvl, ...) -- unlike the swing lines above, this is drawn    |
//| once at confirmation and never touched again.                     |
//+------------------------------------------------------------------+
void DrawOriginLine(const int origin_idx, const double level, const int i, const datetime &time[], const color clr)
{
   string name = PREFIX_ORIGIN_LINE + IntegerToString(origin_idx) + "_" + IntegerToString(i);
   if(ObjectFind(0, name) >= 0) return;
   ObjectCreate(0, name, OBJ_TREND, 0, time[origin_idx], level, time[i], level);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 3);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
}

//+------------------------------------------------------------------+
//| One-shot sweep marker (Pine's plotshape labeldown/labelup with    |
//| the ▼/▲ text) -- same permanent-object-per-event approach as      |
//| HammerShootingStar.mq5's "H"/"S" labels.                           |
//+------------------------------------------------------------------+
void DrawSweepMarker(const int i, const datetime &time[], const double price,
                      const string text, const color clr, const int anchor)
{
   string name = PREFIX_SWEEP + IntegerToString(i);
   if(ObjectFind(0, name) >= 0) return;
   ObjectCreate(0, name, OBJ_TEXT, 0, time[i], price);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, CurrentPeriodObjectMask());
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

#define STATUS_LABEL_NAME  "CISD_StatusLabel"
#define STATUS_LABEL_NAME2 "CISD_StatusLabel2"

//+------------------------------------------------------------------+
//| One line of the status label -- pulled out so both lines share    |
//| identical object setup, only name/yDistance/text differ.          |
//+------------------------------------------------------------------+
void SetStatusLine(const string name, const int yDistance, const string text, const color clr)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, yDistance);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 11);
      ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
//| Unambiguous on-chart proof-of-life -- same corner-label style as  |
//| ATRTrailDual_MajorMinor.mq5's DisplayTrailValue, placed lower      |
//| (yDistance 70/90) to sit below that indicator's own ATR2/ATR300    |
//| labels rather than overlapping them. Added 2026-09-15: on a chart  |
//| with 5+ overlaid indicators, colored dots/lines can be genuinely   |
//| impossible to tell apart by eye -- this settles "is it actually    |
//| generating anything" without hunting through the Object List.      |
//|                                                                     |
//| TWO short lines instead of one long one (2026-09-15, confirmed     |
//| live): a single line long enough to spell out every field ran past |
//| the visible edge of a narrower chart pane -- this user moves chart |
//| windows across 4 differently-sized monitors (see project notes),   |
//| so a fixed-width single line was never going to reliably fit.      |
//+------------------------------------------------------------------+
void DisplayStatus()
{
   string trend_text = (g_trend > 0) ? "BULLISH" : (g_trend < 0) ? "BEARISH" : "NONE";
   string cisd_text  = (g_last_cisd_type == 1) ? "Bearish" : (g_last_cisd_type == 2) ? "Bullish" : "none yet";
   color  clr = (g_trend > 0) ? BullColor : (g_trend < 0) ? BearColor : clrSilver;

   string line1 = StringFormat("CISD  SH:%d SL:%d  Trend:%s", ArraySize(sh_startIdx), ArraySize(sl_startIdx), trend_text);
   string line2 = StringFormat("Last CISD: %s%s", cisd_text, g_last_sweep ? " (sweep)" : "");

   SetStatusLine(STATUS_LABEL_NAME,  70, line1, clr);
   SetStatusLine(STATUS_LABEL_NAME2, 90, line2, clr);
}

//+------------------------------------------------------------------+
//| Full per-bar orchestration for ONE closed bar i -- steps run in   |
//| the exact same order as the Pine script's top-to-bottom execution |
//| (pivot insert -> wick scan/extend -> reversal-candle detection ->  |
//| CISD confirm -> trend/origin-line/sweep), since later steps in a   |
//| single Pine bar can depend on earlier steps' SAME-bar side effects |
//| (e.g. a freshly-inserted swing line is immediately eligible for    |
//| this same bar's own wick scan).                                    |
//+------------------------------------------------------------------+
void ProcessCISDBar(const int i, const int rates_total, const datetime &time[],
                     const double &open[], const double &high[], const double &low[], const double &close[])
{
   // 1. Pivot detection -- candidate center is c = i-SwingPeriod, confirmed
   // exactly SwingPeriod bars later (i.e. now, at bar i).
   int c = i - SwingPeriod;
   if(c >= 0)
   {
      if(CISD_IsPivotHigh(high, c, SwingPeriod, rates_total))
      {
         AddSwingHigh(c, high[c]);
         SwingHighMarker[c] = high[c];
      }
      if(CISD_IsPivotLow(low, c, SwingPeriod, rates_total))
      {
         AddSwingLow(c, low[c]);
         SwingLowMarker[c] = low[c];
      }
   }

   // 2. Wick/mitigation scan + line extension -- unconditional every bar,
   // includes whatever was just inserted in step 1 above.
   bool   wicked_high, wicked_low;
   double wicked_high_level, wicked_low_level;
   ScanSwingHighs(i, time, high, wicked_high, wicked_high_level);
   ScanSwingLows(i, time, low, wicked_low, wicked_low_level);

   if(wicked_high) { g_have_wicked_high = true; g_last_wicked_high_level = wicked_high_level; g_last_wicked_high_time = time[i]; g_last_wicked_high_bar = i; }
   if(wicked_low)  { g_have_wicked_low  = true; g_last_wicked_low_level  = wicked_low_level;  g_last_wicked_low_time  = time[i]; g_last_wicked_low_bar  = i; }

   // 3. Reversal-candle detection -> pending CISD origin candidates.
   if(i >= 1)
   {
      if(close[i-1] < open[i-1] && close[i] > open[i])
      {
         InsertFrontI(bear_idx, i);
         InsertFrontD(bear_open, open[i]);
      }
      if(close[i-1] > open[i-1] && close[i] < open[i])
      {
         InsertFrontI(bull_idx, i);
         InsertFrontD(bull_open, open[i]);
      }
   }

   // 4. CISD confirmation (bear checked first, then bull -- matches Pine's
   // own top-to-bottom order; both could in principle fire on the same
   // bar, in which case bull's assignment to `trend`/origin below wins,
   // same as Pine since its bull `if` block runs after its bear one).
   double origin_lvl = 0.0; int origin_idx = 0;
   int cisd = 0;
   if(ConfirmBearCISD(i, open, close, origin_lvl, origin_idx)) cisd = 1;
   double origin_lvl2 = 0.0; int origin_idx2 = 0;
   if(ConfirmBullCISD(i, open, close, origin_lvl2, origin_idx2))
   {
      cisd = 2;
      origin_lvl = origin_lvl2;
      origin_idx = origin_idx2;
   }

   // 5. Trend update + origin line + liquidity-sweep check.
   if(cisd == 1)
   {
      g_trend = -1;
      DrawOriginLine(origin_idx, origin_lvl, i, time, BearColor);
      g_last_cisd_type = 1; g_last_cisd_level = origin_lvl; g_last_cisd_time = time[i];
      g_last_sweep = false;

      if(g_have_wicked_high && (i - g_last_wicked_high_bar) <= LiquidityLookback && close[i] < g_last_wicked_high_level)
      {
         g_last_sweep = true;
         DrawSweepMarker(i, time, high[i], "\x25BC", BearColor, ANCHOR_LOWER); // "▼" above bar
      }
   }
   if(cisd == 2)
   {
      g_trend = 1;
      DrawOriginLine(origin_idx, origin_lvl, i, time, BullColor);
      g_last_cisd_type = 2; g_last_cisd_level = origin_lvl; g_last_cisd_time = time[i];
      g_last_sweep = false;

      if(g_have_wicked_low && (i - g_last_wicked_low_bar) <= LiquidityLookback && close[i] > g_last_wicked_low_level)
      {
         g_last_sweep = true;
         DrawSweepMarker(i, time, low[i], "\x25B2", BullColor, ANCHOR_UPPER); // "▲" below bar
      }
   }
}

//+------------------------------------------------------------------+
//| Wipes every bit of persistent state + every object this indicator |
//| has ever drawn -- used on a fresh attach (prev_calculated<=0),     |
//| same role as ATRTrailDual_MajorMinor.mq5's ResetAllConfirmed().    |
//+------------------------------------------------------------------+
void ResetAllCISDState()
{
   ArrayResize(sh_level,0); ArrayResize(sh_startIdx,0);
   ArrayResize(sl_level,0); ArrayResize(sl_startIdx,0);
   ArrayResize(bear_open,0); ArrayResize(bear_idx,0);
   ArrayResize(bull_open,0); ArrayResize(bull_idx,0);

   g_have_wicked_high = false; g_last_wicked_high_bar = -1;
   g_have_wicked_low  = false; g_last_wicked_low_bar  = -1;
   g_trend = 0;
   g_last_cisd_type = 0; g_last_cisd_level = 0.0; g_last_cisd_time = 0; g_last_sweep = false;
   g_confirmed_upto = -1;

   ObjectsDeleteAll(0, PREFIX_SWING_HIGH);
   ObjectsDeleteAll(0, PREFIX_SWING_LOW);
   ObjectsDeleteAll(0, PREFIX_ORIGIN_LINE);
   ObjectsDeleteAll(0, PREFIX_SWEEP);
}

//+------------------------------------------------------------------+
void PublishBridgeFile(const int closed_idx, const datetime &time[], const double &close[])
{
   if(!PublishToFile) return;
   if(TimeCurrent() - g_last_publish_time < PublishEverySeconds) return;
   g_last_publish_time = TimeCurrent();

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0) tf_minutes = (int)_Period;

   string cisd_text = (g_last_cisd_type == 1) ? "bearish" : (g_last_cisd_type == 2) ? "bullish" : "none";

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"bar_time\":" + IntegerToString((long)time[closed_idx]) + ",";
   j += "\"close\":" + DoubleToString(close[closed_idx], 3) + ",";
   j += "\"trend\":" + IntegerToString(g_trend) + ",";
   j += "\"last_cisd\":\"" + cisd_text + "\",";
   j += "\"last_cisd_level\":" + DoubleToString(g_last_cisd_level, 8) + ",";
   j += "\"last_cisd_time\":" + IntegerToString((long)g_last_cisd_time) + ",";
   j += "\"last_cisd_sweep\":" + (g_last_sweep ? "true" : "false");
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);
   const string final_name = FileBridgeFolder + "\\CISD_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("CISD bridge file write failed: ", tmp_name, " | error=", GetLastError());
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
      Print("CISD bridge file publish failed to finalize after retries: ", final_name, " | error=", last_error);
}

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, SwingHighMarker, INDICATOR_DATA);
   SetIndexBuffer(1, SwingLowMarker,  INDICATOR_DATA);

   PlotIndexSetInteger(0, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetInteger(1, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, BearColor); // Pine: swing HIGH marked in the bear/red colour
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, BullColor); // Pine: swing LOW marked in the bull/green colour
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   IndicatorSetString(INDICATOR_SHORTNAME, "CISD [Ported from AlgoAlpha]");

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
   if(rates_total < 2*SwingPeriod + 2)
      return(0);

   if(prev_calculated <= 0)
      ResetAllCISDState();

   int last_closed = rates_total - 2;
   int watermark_before = g_confirmed_upto;

   // Never reprocess a bar twice, ever -- see this file's header for why.
   for(int i = g_confirmed_upto + 1; i <= last_closed; i++)
   {
      ProcessCISDBar(i, rates_total, time, open, high, low, close);
      g_confirmed_upto = i;
   }

   PublishBridgeFile(last_closed, time, close);

   // DisplayStatus's own text only ever changes as a RESULT of the loop
   // above (swing counts / trend / last-cisd are all bar-close-driven,
   // never tick-reactive) -- so only touch the label object when that
   // loop actually processed at least one bar. Calling it unconditionally
   // every tick was pure per-tick object-property churn for a label whose
   // content couldn't have changed, exactly what this project's own
   // lightweight-indicator convention warns against.
   if(g_confirmed_upto != watermark_before)
      DisplayStatus();

   return(rates_total);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectDelete(0, STATUS_LABEL_NAME);
   ObjectDelete(0, STATUS_LABEL_NAME2);

   if(reason == REASON_REMOVE)
   {
      ObjectsDeleteAll(0, PREFIX_SWING_HIGH);
      ObjectsDeleteAll(0, PREFIX_SWING_LOW);
      ObjectsDeleteAll(0, PREFIX_ORIGIN_LINE);
      ObjectsDeleteAll(0, PREFIX_SWEEP);
   }
}
//+------------------------------------------------------------------+
