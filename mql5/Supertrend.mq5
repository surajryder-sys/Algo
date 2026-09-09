//+------------------------------------------------------------------+
//|                        Supertrend.mq5                             |
//| Direct MQL5 port of the TradingView Pine v4 script "Supertrend"   |
//| (KivancOzbilgic-style ATR bands: src=hl2, up/dn bands trail        |
//| toward price, trend flips when close crosses the opposite band's  |
//| prior-bar value). Logic is a 1:1 translation of the Pine recurrence|
//| -- nothing added, nothing simplified away from the original math. |
//|                                                                     |
//| Kept light on purpose (see project convention: MT5 indicator work |
//| must stay closed-bar/cheap, no per-tick heavy chart-object churn --|
//| the same lesson ATRTrailDual_MajorMinor.mq5 already applies):      |
//|  - up/dn/trend are real indicator buffers, so MT5 persists every   |
//|    already-computed bar between calls -- each OnCalculate only     |
//|    reprocesses from prev_calculated-1 (plus a small safety margin, |
//|    same SAFETY_REPROCESS_BARS pattern as the Dual indicator, to    |
//|    self-correct any iATR settling-lag drift on reattach).          |
//|  - Buy/Sell flip markers are plotted via DRAW_ARROW buffers (a     |
//|    thick solid dot, not an arrow glyph), not ObjectCreate calls --  |
//|    MT5 draws those natively, no manual chart object management     |
//|    needed.                                                          |
//|  - The Pine version's fill() highlighter between the plot and      |
//|    ohlc4 is a pure cosmetic (chart-shading) touch with no signal    |
//|    value and no direct MT5 equivalent worth the extra per-bar       |
//|    object work -- deliberately dropped. Everything that actually   |
//|    carries information (both bands, trend, flip signals) is kept.  |
//|                                                                     |
//| Bridge publish (optional, PublishToFile) follows the same JSON     |
//| write-tmp-then-FileMove pattern as OB_ATR_Bridge_Indicator.mq5 /    |
//| ATRTrailDual_MajorMinor.mq5, so a Python bot can read this exactly |
//| like the existing ATRSTATE_*/OBSTATE_* bridge files, once one is   |
//| wired up to consume it. Not wired to any bot yet.                  |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 7
#property indicator_plots   3

//--- Plot 1: the trend line itself (up while bullish, dn while bearish)
#property indicator_label1  "Supertrend"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrLimeGreen, clrRed
#property indicator_width1  2

//--- Plot 2: bullish flip marker (Pine's "Up" label / green circle) -- thick dot
#property indicator_label2  "Supertrend Buy"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrLimeGreen
#property indicator_width2  4

//--- Plot 3: bearish flip marker (Pine's "Down" label / red circle) -- thick dot
#property indicator_label3  "Supertrend Sell"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrRed
#property indicator_width3  4

//===================== Inputs -- names/defaults match the Pine script =====================
input int                ATRPeriodInp   = 10;                 // "ATR Period"
input ENUM_APPLIED_PRICE SourcePrice    = PRICE_MEDIAN;        // "Source" -- hl2 in Pine == PRICE_MEDIAN here
input double             Multiplier     = 3.0;                 // "ATR Multiplier"
input bool                ChangeATR      = true;                // true = built-in ATR (Wilder/RMA, same as Pine's atr()); false = SMA(TrueRange)
input bool                ShowSignals    = true;                // draw the Buy/Sell dot markers
input bool                PublishToFile  = true;
input string              FileBridgeFolder    = "OBBridge";     // same Common Files folder as the other bridge indicators
input string              BridgeSymbol        = "";             // empty = use the attached chart's symbol
input int                 PublishEverySeconds = 2;

#define DOT_MARKER_CODE 159   // wingdings solid filled circle -- same glyph for both, color tells buy/sell apart

// Same reasoning as ATRTrailDual_MajorMinor.mq5's SAFETY_REPROCESS_BARS:
// always reprocess at least this many trailing bars regardless of
// prev_calculated, so an iATR() settling-lag drift on reattach
// self-corrects instead of staying wrong forever.
#define SAFETY_REPROCESS_BARS 50

//===================== Buffers =====================
double SupertrendLine[];   // plotted (data)
double LineColorIdx[];     // plotted (color index: 0=green/up, 1=red/dn)
double BuyArrow[];         // plotted (data)
double SellArrow[];        // plotted (data)

double UpBuffer[];         // calc-only, must persist bar-to-bar (up[1] in Pine)
double DnBuffer[];         // calc-only, must persist bar-to-bar (dn[1] in Pine)
double TrendBuffer[];      // calc-only, must persist bar-to-bar (trend[1] in Pine)

int    ATRHandle = INVALID_HANDLE;
bool   loggedInsufficientBars = false;
bool   loggedCopyFail = false;
datetime g_last_publish_time = 0;

// Cached last-flip lookup -- same gap-scanning pattern as
// ATRTrailDual_MajorMinor.mq5's FindEventTime, so a long stretch without a
// flip doesn't turn into a full backward history scan every publish cycle.
datetime g_cachedEventTime = 0;
int      g_cachedTrend = 0;
int      g_cachedIdx   = -1;

//+------------------------------------------------------------------+
//| Pine's src input (hl2 by default) -- generalized to any applied  |
//| price via MT5's own enum, computed per-bar from OHLC directly.   |
//+------------------------------------------------------------------+
double SourceValue(const ENUM_APPLIED_PRICE mode, const double o, const double h, const double l, const double c)
{
   switch(mode)
   {
      case PRICE_OPEN:     return o;
      case PRICE_HIGH:     return h;
      case PRICE_LOW:      return l;
      case PRICE_CLOSE:    return c;
      case PRICE_MEDIAN:   return (h + l) / 2.0;
      case PRICE_TYPICAL:  return (h + l + c) / 3.0;
      case PRICE_WEIGHTED: return (h + l + 2.0 * c) / 4.0;
   }
   return (h + l) / 2.0;
}

//+------------------------------------------------------------------+
//| SMA(tr, Periods) -- Pine's atr2 fallback when ChangeATR=false.   |
//| Bounded to ATRPeriodInp samples per call; the outer loop is      |
//| already bounded by SAFETY_REPROCESS_BARS, so this stays cheap.   |
//+------------------------------------------------------------------+
double SmaTrueRange(const int i, const int period,
                     const double &high[], const double &low[], const double &close[])
{
   double sum = 0.0;
   int n = 0;
   for(int k = i; k > i - period && k >= 0; k--)
   {
      double prevClose = (k > 0) ? close[k - 1] : close[k];
      double tr = MathMax(high[k] - low[k], MathMax(MathAbs(high[k] - prevClose), MathAbs(low[k] - prevClose)));
      sum += tr;
      n++;
   }
   return (n > 0) ? sum / n : 0.0;
}

//+------------------------------------------------------------------+
//| Chart symbol, or BridgeSymbol override if one is set              |
//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "") ? _Symbol : BridgeSymbol;
}

//+------------------------------------------------------------------+
//| Direct port of the Pine recurrence, one bar at a time:            |
//|   up = src - mult*atr ; up1 = nz(up[1], up) ; up := close[1]>up1 ? max(up,up1) : up   |
//|   dn = src + mult*atr ; dn1 = nz(dn[1], dn) ; dn := close[1]<dn1 ? min(dn,dn1) : dn   |
//|   trend := trend==-1 and close>dn1 ? 1 : trend==1 and close<up1 ? -1 : trend         |
//| Note up1/dn1 are the PRIOR bar's finalized bands (read before this |
//| bar's up/dn are reassigned) -- the flip test and the close[1]      |
//| reassignment test both compare against those prior values, exactly|
//| as in the Pine source. Reassignment uses close[1] (previous bar's  |
//| close); the flip test uses this bar's close. Both preserved as-is.|
//+------------------------------------------------------------------+
void CalcSupertrend(const int rates_total, const int prev_calculated,
                     const double &atrBuf[],
                     const double &open[], const double &high[], const double &low[], const double &close[])
{
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;

   int safety_start = rates_total - SAFETY_REPROCESS_BARS;
   if(safety_start < 0) safety_start = 0;
   if(safety_start < start) start = safety_start;
   if(start < 0) start = 0;

   for(int i = start; i < rates_total; i++)
   {
      double src = SourceValue(SourcePrice, open[i], high[i], low[i], close[i]);
      double atrVal = atrBuf[i];

      double upRaw = src - Multiplier * atrVal;
      double up1   = (i > 0) ? UpBuffer[i - 1] : upRaw;              // nz(up[1], up)
      double closePrev = (i > 0) ? close[i - 1] : close[i];          // close[1]
      double upVal = (closePrev > up1) ? MathMax(upRaw, up1) : upRaw;

      double dnRaw = src + Multiplier * atrVal;
      double dn1   = (i > 0) ? DnBuffer[i - 1] : dnRaw;               // nz(dn[1], dn)
      double dnVal = (closePrev < dn1) ? MathMin(dnRaw, dn1) : dnRaw;

      int trendPrev = (i > 0) ? (int)TrendBuffer[i - 1] : 1;          // nz(trend[1], 1)
      int trend = trendPrev;
      if(trendPrev == -1 && close[i] > dn1)
         trend = 1;
      else if(trendPrev == 1 && close[i] < up1)
         trend = -1;

      UpBuffer[i]    = upVal;
      DnBuffer[i]    = dnVal;
      TrendBuffer[i] = trend;

      SupertrendLine[i] = (trend == 1) ? upVal : dnVal;
      LineColorIdx[i]   = (trend == 1) ? 0 : 1;

      bool buySignal  = (trend == 1  && trendPrev == -1);
      bool sellSignal = (trend == -1 && trendPrev == 1);

      BuyArrow[i]  = (ShowSignals && buySignal)  ? upVal : EMPTY_VALUE;
      SellArrow[i] = (ShowSignals && sellSignal) ? dnVal : EMPTY_VALUE;
   }
}

//+------------------------------------------------------------------+
//| Bar time of the most recent trend flip -- cached the same         |
//| gap-scanning way as ATRTrailDual_MajorMinor.mq5's FindEventTime.  |
//+------------------------------------------------------------------+
datetime FindEventTime(const int reference_idx, const datetime &time[])
{
   int current_trend = (int)TrendBuffer[reference_idx];

   if(g_cachedIdx >= 0 && g_cachedIdx <= reference_idx)
   {
      int scan_from = MathMin(g_cachedIdx + 1, reference_idx - SAFETY_REPROCESS_BARS + 1);
      if(scan_from < 1) scan_from = 1;

      int flip_at = -1;
      for(int i = scan_from; i <= reference_idx; i++)
         if((int)TrendBuffer[i] != (int)TrendBuffer[i - 1])
            flip_at = i;

      if(flip_at < 0 && current_trend == g_cachedTrend)
      {
         g_cachedIdx = reference_idx;
         return g_cachedEventTime;
      }
      if(flip_at >= 0)
      {
         g_cachedEventTime = time[flip_at];
         g_cachedTrend = current_trend;
         g_cachedIdx = reference_idx;
         return g_cachedEventTime;
      }
   }

   datetime result = time[0];
   for(int i = reference_idx - 1; i >= 0; i--)
   {
      if((int)TrendBuffer[i] != current_trend)
      {
         result = time[i + 1];
         break;
      }
   }
   g_cachedEventTime = result;
   g_cachedTrend = current_trend;
   g_cachedIdx = reference_idx;
   return result;
}

//+------------------------------------------------------------------+
//| Publish the last CLOSED bar's state to the bridge -- never the    |
//| still-forming bar, same reasoning as the other bridge indicators  |
//| (a live bar's trend/value can wobble tick-to-tick right at the    |
//| band; a closed bar's close[] never changes once it closes).       |
//+------------------------------------------------------------------+
void PublishBridgeFile(const int rates_total, const datetime &time[], const double &close[])
{
   if(!PublishToFile) return;
   if(TimeCurrent() - g_last_publish_time < PublishEverySeconds) return;
   g_last_publish_time = TimeCurrent();

   if(rates_total < 2) return;
   int closed_idx = rates_total - 2;

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0) tf_minutes = (int)_Period;

   int trend = (int)TrendBuffer[closed_idx];
   datetime event_time = FindEventTime(closed_idx, time);

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"close\":" + DoubleToString(close[closed_idx], 3) + ",";
   j += "\"bar_time\":" + IntegerToString((long)time[closed_idx]) + ",";
   j += "\"supertrend\":" + DoubleToString(SupertrendLine[closed_idx], 8) + ",";
   j += "\"up\":" + DoubleToString(UpBuffer[closed_idx], 8) + ",";
   j += "\"dn\":" + DoubleToString(DnBuffer[closed_idx], 8) + ",";
   j += "\"trend\":" + IntegerToString(trend) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time);
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\SUPERTREND_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("Supertrend bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
   }
   FileWriteString(handle, j);
   FileClose(handle);

   // Same immediate-retry pattern as ATRTrailDual_MajorMinor.mq5's
   // PublishATRBridgeFile -- a Python reader holding final_name open for
   // read at the exact rename instant can throw a sharing violation; a
   // handful of short retries clears the vast majority of those.
   bool moved = false;
   int last_error = 0;
   for(int attempt = 0; attempt < 5 && !moved; attempt++)
   {
      if(attempt > 0) Sleep(10);
      moved = FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE);
      if(!moved) last_error = GetLastError();
   }
   if(!moved)
      Print("Supertrend bridge file publish failed to finalize after retries: ", final_name, " | error=", last_error);
}

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, SupertrendLine, INDICATOR_DATA);
   SetIndexBuffer(1, LineColorIdx,   INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, BuyArrow,       INDICATOR_DATA);
   SetIndexBuffer(3, SellArrow,      INDICATOR_DATA);
   SetIndexBuffer(4, UpBuffer,       INDICATOR_CALCULATIONS);
   SetIndexBuffer(5, DnBuffer,       INDICATOR_CALCULATIONS);
   SetIndexBuffer(6, TrendBuffer,    INDICATOR_CALCULATIONS);

   // Plotted directly at the up/dn band value (location.absolute in Pine's
   // plotshape) -- no extra PLOT_ARROW_SHIFT, so the marker sits exactly on
   // the level the flip actually happened at, same as the source script.
   PlotIndexSetInteger(1, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetInteger(2, PLOT_ARROW, DOT_MARKER_CODE);
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   ATRHandle = iATR(NULL, 0, ATRPeriodInp);
   if(ATRHandle == INVALID_HANDLE)
   {
      Print("Supertrend: ATR handle error (period ", ATRPeriodInp, ")");
      return(INIT_FAILED);
   }

   IndicatorSetString(INDICATOR_SHORTNAME, "Supertrend(" + IntegerToString(ATRPeriodInp) + "," + DoubleToString(Multiplier, 1) + ")");

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
   if(rates_total < ATRPeriodInp + 2)
   {
      if(!loggedInsufficientBars)
      {
         Print("Supertrend: waiting for history -- have ", rates_total, " bars, need ", ATRPeriodInp + 2);
         loggedInsufficientBars = true;
      }
      return(0);
   }

   double atrBuf[];
   if(ChangeATR)
   {
      int copied = CopyBuffer(ATRHandle, 0, 0, rates_total, atrBuf);
      if(copied <= 0)
      {
         if(!loggedCopyFail)
         {
            Print("Supertrend: ATR CopyBuffer failed, returned ", copied, ", error ", GetLastError());
            loggedCopyFail = true;
         }
         return(0);
      }
   }
   else
   {
      // Pine's atr2 = sma(tr, Periods) fallback -- only the reprocessed
      // window actually needs values; the rest of atrBuf is unused by
      // CalcSupertrend's own start-bounded loop.
      ArrayResize(atrBuf, rates_total);
      int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;
      int safety_start = rates_total - SAFETY_REPROCESS_BARS;
      if(safety_start < 0) safety_start = 0;
      if(safety_start < start) start = safety_start;
      for(int i = start; i < rates_total; i++)
         atrBuf[i] = SmaTrueRange(i, ATRPeriodInp, high, low, close);
   }

   CalcSupertrend(rates_total, prev_calculated, atrBuf, open, high, low, close);

   if(PublishToFile)
      PublishBridgeFile(rates_total, time, close);

   return(rates_total);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // No chart objects created outside the indicator buffers themselves --
   // nothing to clean up here.
}
//+------------------------------------------------------------------+
