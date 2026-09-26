//+------------------------------------------------------------------+
//| Dynamic Zones.mq5                                                 |
//| Port of Pine "Dynamic Zone - Suraj" (v5, fixed alignment).        |
//|                                                                   |
//| Per zone-timeframe bar (default D1), from that bar's OPEN:        |
//|   Z1 = open + SMA(high-low, 5)/2   of the previous COMPLETED bars |
//|   Z2 = open + SMA(high-low, 10)/2                                 |
//|   Z3 = open - SMA(high-low, 5)/2                                  |
//|   Z4 = open - SMA(high-low, 10)/2                                 |
//| Values are computed once per zone bar and cached (lightweight).   |
//| Alerts (optional) fire on CLOSED chart bars only:                 |
//|   close crosses above Z2 / crosses below Z4.                      |
//+------------------------------------------------------------------+
#property copyright "Suraj_ARK"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 8
#property indicator_plots   6

#property indicator_type1   DRAW_FILLING
#property indicator_label1  "Z1;Z2"
#property indicator_type2   DRAW_FILLING
#property indicator_label2  "Z3;Z4"
#property indicator_type3   DRAW_LINE
#property indicator_label3  "Z1"
#property indicator_type4   DRAW_LINE
#property indicator_label4  "Z2"
#property indicator_type5   DRAW_LINE
#property indicator_label5  "Z3"
#property indicator_type6   DRAW_LINE
#property indicator_label6  "Z4"

input ENUM_TIMEFRAMES InpZoneTF    = PERIOD_D1;     // Zone timeframe
input int             InpShortLen  = 5;             // Short range average (Z1/Z3)
input int             InpLongLen   = 10;            // Long range average (Z2/Z4)
input bool            InpShowFill  = true;          // Fill zones
input color           InpFillColor = C'0,0,60';     // Fill color
input color           InpLineColor = clrBlue;      // Line color
input bool            InpAlerts    = false;         // Alert on Z2 / Z4 cross (closed bars)

double FillUp1[], FillUp2[], FillDn1[], FillDn2[];
double Z1[], Z2[], Z3[], Z4[];

datetime g_day = 0;          // zone bar currently cached
double   g_open, g_half5, g_half10;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, FillUp1, INDICATOR_DATA);
   SetIndexBuffer(1, FillUp2, INDICATOR_DATA);
   SetIndexBuffer(2, FillDn1, INDICATOR_DATA);
   SetIndexBuffer(3, FillDn2, INDICATOR_DATA);
   SetIndexBuffer(4, Z1, INDICATOR_DATA);
   SetIndexBuffer(5, Z2, INDICATOR_DATA);
   SetIndexBuffer(6, Z3, INDICATOR_DATA);
   SetIndexBuffer(7, Z4, INDICATOR_DATA);

   for(int p = 0; p < 6; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   for(int p = 0; p < 2; p++)
   {
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, 0, InpFillColor);
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, 1, InpFillColor);
      if(!InpShowFill)
         PlotIndexSetInteger(p, PLOT_DRAW_TYPE, DRAW_NONE);
   }
   for(int p = 2; p < 6; p++)
   {
      PlotIndexSetInteger(p, PLOT_LINE_COLOR, InpLineColor);
      PlotIndexSetInteger(p, PLOT_LINE_WIDTH, 1);
   }

   IndicatorSetString(INDICATOR_SHORTNAME, "Dynamic Zones");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Zone values for the zone-TF bar at `shift`, using only the        |
//| completed bars before it (shift+1 .. shift+N).                    |
//+------------------------------------------------------------------+
bool CalcZone(const int shift)
{
   int maxLen = MathMax(InpShortLen, InpLongLen);
   if(shift + maxLen >= iBars(_Symbol, InpZoneTF))
      return(false);

   double o = iOpen(_Symbol, InpZoneTF, shift);
   if(o <= 0.0)
      return(false);

   double sumShort = 0.0, sumLong = 0.0;
   for(int k = 1; k <= maxLen; k++)
   {
      double h = iHigh(_Symbol, InpZoneTF, shift + k);
      double l = iLow(_Symbol, InpZoneTF, shift + k);
      if(h <= 0.0 || l <= 0.0)
         return(false);
      double r = h - l;
      if(k <= InpShortLen) sumShort += r;
      if(k <= InpLongLen)  sumLong  += r;
   }

   g_open   = o;
   g_half5  = sumShort / InpShortLen / 2.0;
   g_half10 = sumLong  / InpLongLen  / 2.0;
   return(true);
}

//+------------------------------------------------------------------+
void SetEmpty(const int i)
{
   FillUp1[i] = FillUp2[i] = FillDn1[i] = FillDn2[i] = EMPTY_VALUE;
   Z1[i] = Z2[i] = Z3[i] = Z4[i] = EMPTY_VALUE;
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
   if(prev_calculated == 0)
   {
      // Zone-TF history not synced yet -> retry on next tick
      if(iBars(_Symbol, InpZoneTF) < MathMax(InpShortLen, InpLongLen) + 2)
         return(0);
      g_day = 0;
   }

   int start = (prev_calculated > 0) ? prev_calculated - 1 : 0;

   for(int i = start; i < rates_total; i++)
   {
      int shift = iBarShift(_Symbol, InpZoneTF, time[i], false);
      if(shift < 0) { SetEmpty(i); continue; }

      datetime zt = iTime(_Symbol, InpZoneTF, shift);
      if(zt != g_day)
      {
         if(!CalcZone(shift)) { SetEmpty(i); g_day = 0; continue; }
         g_day = zt;
      }

      Z1[i] = g_open + g_half5;
      Z2[i] = g_open + g_half10;
      Z3[i] = g_open - g_half5;
      Z4[i] = g_open - g_half10;
      FillUp1[i] = Z1[i]; FillUp2[i] = Z2[i];
      FillDn1[i] = Z3[i]; FillDn2[i] = Z4[i];
   }

   // Alerts: only once per new chart bar, on the bar that just closed
   if(InpAlerts && prev_calculated > 0 && rates_total > prev_calculated && rates_total >= 3)
   {
      int c = rates_total - 2, p = rates_total - 3;
      if(Z2[c] != EMPTY_VALUE && Z2[p] != EMPTY_VALUE &&
         close[c] > Z2[c] && close[p] <= Z2[p])
         Alert("Dynamic Zones BUY: ", _Symbol, " closed above Z2, price = ", DoubleToString(close[c], _Digits));
      if(Z4[c] != EMPTY_VALUE && Z4[p] != EMPTY_VALUE &&
         close[c] < Z4[c] && close[p] >= Z4[p])
         Alert("Dynamic Zones SELL: ", _Symbol, " closed below Z4, price = ", DoubleToString(close[c], _Digits));
   }

   return(rates_total);
}
//+------------------------------------------------------------------+
