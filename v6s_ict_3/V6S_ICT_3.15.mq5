//+------------------------------------------------------------------+
//| V6S_ICT_3.15.mq5                                                 |
//| V6S_ICT_3 -- FRESH MAJOR -> MSS -> OB PULLBACK (new method,      |
//| simulated in v6s_ict/sim/s_mss.py on Exness Real7 real ticks,    |
//| Nov 2024 - Oct 2026: 333 trades, 55% win, +$1,129 on $5,000 at   |
//| 0.04 / 0.06 lots incl. $7/lot commission, max drop 3.6 %,        |
//| 4 losing months of 23). Separate EA -- V6S-ICT v2.x untouched.   |
//| Magic 26100501.                                                  |
//|                                                                  |
//| BUY (sell mirrored), decisions on closed candles, entry on tick: |
//|  1. M5 sets a FRESH Major support (V6S Major/Minor, pivot 5).    |
//|     The most recent M5 swing high at that moment (the newer of   |
//|     Major / Minor resistance) must be >= MinSwing (10) above it. |
//|     A newer qualifying Major replaces the setup.                 |
//|  2. MSS: a later M5 candle CLOSES above that swing high.         |
//|  3. Then the nearest active bullish OB on M3 or M5               |
//|     (OB_Detector_v1.07, 3 newest per side) below price with its  |
//|     bottom at/above the Major low; if none yet, keep looking     |
//|     every minute.                                                |
//|  4. ENTRY at the first tick that trades down to the OB top, not  |
//|     earlier than MinWaitMin (15) after the MSS and not between   |
//|     NoEntryFromHourIST and NoEntryToHourIST (03:00-07:59 IST).   |
//|  5. SL = OB bottom - SLBuffer (0.5); skipped if the SL distance  |
//|     is outside MinRisk-MaxRisk (3-15). TP = RR (1.0) x risk.     |
//|  6. Lots: BaseLots (0.04); x AlignedLotMult (1.5 -> 0.06) when   |
//|     the Major is within AlignTol (2.5) of an aligning level of   |
//|     its type (same price on >= 3 of H4 H2 H1 M30 M15 M10 M5,     |
//|     Major or Minor, M5 Majors only, M3 not used).                |
//|  Cancelled: an M5 close below the Major low, ValidBars (48) M5   |
//|  candles after the Major, or price below the OB bottom before    |
//|  entry. One position at a time (setup keeps waiting while a      |
//|  trade is open). Each OB trades once. Exit only at SL or TP.     |
//| Panel: setups, trade, aligning levels, Dynamic Zones (display    |
//| only). Times in IST.                                             |
//+------------------------------------------------------------------+
#property version   "3.15"
#property tester_indicator "OB_Detector_v1.07.ex5"
#include <Trade/Trade.mqh>

input group "Trade"
input bool   EnableTrading      = true;
input double BaseLots           = 0.04;
input double AlignedLotMult     = 1.5;     // lot x this when the Major sits at an aligning level
input long   MagicNumber        = 26100501;
input double RR                 = 1.0;     // TP = RR x risk
input double SLBuffer           = 0.5;
input double MinRisk            = 3.0;     // skip if the SL is closer than this
input double MaxRisk            = 15.0;    // skip if the SL is further than this

input group "Setup"
input double MinSwing           = 10.0;    // MSS level must be at least this far from the Major
input int    MinWaitMin         = 15;      // no entry sooner than this after the MSS
input int    NoEntryFromHourIST = 3;       // no entries from this IST hour ...
input int    NoEntryToHourIST   = 7;       // ... through this IST hour (inclusive); -1 = off
input int    ValidBars          = 48;      // M5 candles a setup lives after its Major
input double AlignTol           = 2.5;     // Major within this of an aligning level -> bigger lot
input int    MinAlignTFs        = 3;

input group "Levels / order blocks"
input int    PivotPeriod        = 5;
input int    WarmupBars         = 3000;
input int    OBLength           = 5;
input ENUM_APPLIED_VOLUME OBVolume = VOLUME_TICK;
input int    OBMitigation       = 0;
input bool   DrawChartTFZones   = true;

input group "Display"
input bool   ShowPanel          = true;
input int    PanelFontSize      = 8;
input int    PanelColWidth      = 450;
input int    PanelTop           = 20;
input int    ServerToISTMinutes = 330;
input int    DZShortLen         = 5;
input int    DZLongLen          = 10;

//===================== Small array helpers (verbatim from V6S-ICT v2.21) =====================
void PushS(string &a[], string v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void PushD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void PushI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); a[n]=v; }
void RemoveLastS(string &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void RemoveLastD(double &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void RemoveLastI(int    &a[]){ int n=ArraySize(a); if(n>0) ArrayResize(a,n-1); }
void InsertAt_S(string &a[], int idx, string v){ string t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void InsertAt_D(double &a[], int idx, double v){ double t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void InsertAt_I(int    &a[], int idx, int    v){ int t[1]; t[0]=v; ArrayInsert(a,t,idx,0,1); }
void ReplaceAtS(string &a[], int idx, string v){ if(idx>=0 && idx<ArraySize(a)) a[idx]=v; }
void InsertFrontD(double &a[], double v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void InsertFrontI(int    &a[], int    v){ int n=ArraySize(a); ArrayResize(a,n+1); for(int k=n;k>0;k--) a[k]=a[k-1]; a[0]=v; }
void RemoveFrontD(double &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }
void RemoveFrontI(int    &a[]){ int n=ArraySize(a); if(n<=0) return; for(int k=0;k<n-1;k++) a[k]=a[k+1]; ArrayResize(a,n-1); }

class CMajorMinor
{
public:
   ENUM_TIMEFRAMES tf;
   string   name;
   bool     ready;
   datetime lastBar;
   double   hi[], lo[], cl[];
   datetime tm[];
   int      n;

   string w_Type[], w_TypeAdv[];
   double w_Value[], w_ValueAdv[];
   int    w_Index[], w_IndexAdv[];
   double w_MajorHighLevel, w_MajorLowLevel;
   int    w_MajorHighIndex, w_MajorLowIndex;
   string w_MajorHighType, w_MajorLowType;
   bool   w_MajorLevelsSet, w_Lock0, w_Lock1;
   double w_LastHighValue, w_LastLowValue;
   int    w_LastHighIndex, w_LastLowIndex;
   int    w_MajSupX, w_MajResX, w_MinSupX, w_MinResX;
   double w_MajSupY, w_MajResY, w_MinSupY, w_MinResY;
   datetime sT[4];          // V6S_ICT_3: close time of the bar that set MajSup / MinSup / MajRes / MinRes
   int      pX[4];
   double   pY[4];

   void Init(const ENUM_TIMEFRAMES t, const string nm)
   {
      tf = t; name = nm; ready = false; lastBar = 0; n = 0;
      ArrayResize(hi, 0); ArrayResize(lo, 0); ArrayResize(cl, 0); ArrayResize(tm, 0);
      ArrayResize(w_Type, 0); ArrayResize(w_Value, 0); ArrayResize(w_Index, 0);
      ArrayResize(w_TypeAdv, 0); ArrayResize(w_ValueAdv, 0); ArrayResize(w_IndexAdv, 0);
      w_MajorHighLevel = 0; w_MajorLowLevel = 0; w_MajorHighIndex = -1; w_MajorLowIndex = -1;
      w_MajorHighType = ""; w_MajorLowType = ""; w_MajorLevelsSet = false; w_Lock0 = true; w_Lock1 = true;
      w_LastHighValue = 0; w_LastLowValue = 0; w_LastHighIndex = -1; w_LastLowIndex = -1;
      w_MajSupX = -1; w_MajResX = -1; w_MinSupX = -1; w_MinResX = -1;
      w_MajSupY = 0; w_MajResY = 0; w_MinSupY = 0; w_MinResY = 0;
      for(int j = 0; j < 4; j++) { sT[j] = 0; pX[j] = -1; pY[j] = 0; }
   }

   bool IsPivotHigh(int c, int PP)
   {
      if(c-PP<0 || c+PP>=n) return false;
      double v=hi[c];
      for(int k=c-PP;k<=c+PP;k++) if(k!=c && hi[k]>=v) return false;
      return true;
   }
   bool IsPivotLow(int c, int PP)
   {
      if(c-PP<0 || c+PP>=n) return false;
      double v=lo[c];
      for(int k=c-PP;k<=c+PP;k++) if(k!=c && lo[k]<=v) return false;
      return true;
   }

   void PushHighType()
   {
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastHighValue) ? "HH":"LH") : "H";
      PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
   }
   void PushLowType()
   {
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastLowValue) ? "HL":"LL") : "L";
      PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
   }
   void ReplaceLastWithHighType()
   {
      RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastHighValue) ? "HH":"LH") : "H";
      PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
   }
   void ReplaceLastWithLowType()
   {
      RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
      int N=ArraySize(w_Type);
      string t=(N>2) ? ((w_Value[N-2]<w_LastLowValue) ? "HL":"LL") : "L";
      PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
   }

   void ClassifyPivot(bool hasHigh, bool hasLow, double thisClose)
   {
      int N=ArraySize(w_Type);
      if(hasHigh && hasLow)
      {
         if(N==0) { }
         else
         {
            string last=w_Type[N-1];
            if(last=="L" || last=="LL")
            {
               if(w_LastLowValue<w_Value[N-1]) ReplaceLastWithLowType();
               else                            PushHighType();
            }
            else if(last=="H" || last=="HH")
            {
               if(w_LastHighValue>w_Value[N-1]) ReplaceLastWithHighType();
               else                             PushLowType();
            }
            else if(last=="LH")
            {
               if(w_LastHighValue<w_Value[N-1])
                  PushLowType();
               else if(w_LastHighValue>w_Value[N-1])
               {
                  if(thisClose<w_Value[N-1])      ReplaceLastWithHighType();
                  else if(thisClose>w_Value[N-1]) PushLowType();
               }
            }
            else if(last=="HL")
            {
               if(w_LastLowValue>w_Value[N-1])
                  PushHighType();
               else if(w_LastLowValue<w_Value[N-1])
               {
                  if(thisClose>w_Value[N-1])      ReplaceLastWithLowType();
                  else if(thisClose<w_Value[N-1]) PushHighType();
               }
            }
         }
      }
      else if(hasHigh)
      {
         if(N==0)
         {
            InsertAt_S(w_Type,0,"H"); InsertAt_D(w_Value,0,w_LastHighValue); InsertAt_I(w_Index,0,w_LastHighIndex);
         }
         else
         {
            string last=w_Type[N-1];
            if(last=="L" || last=="HL" || last=="LL")
            {
               if(w_LastHighValue>w_Value[N-1])      PushHighType();
               else if(w_LastHighValue<w_Value[N-1]) ReplaceLastWithLowType();
            }
            else if(last=="H" || last=="HH" || last=="LH")
            {
               if(w_Value[N-1]<w_LastHighValue) ReplaceLastWithHighType();
            }
         }
      }
      else if(hasLow)
      {
         if(N==0)
         {
            InsertAt_S(w_Type,0,"L"); InsertAt_D(w_Value,0,w_LastLowValue); InsertAt_I(w_Index,0,w_LastLowIndex);
         }
         else
         {
            string last=w_Type[N-1];
            if(last=="H" || last=="HH" || last=="LH")
            {
               if(w_LastLowValue<w_Value[N-1])      PushLowType();
               else if(w_LastLowValue>w_Value[N-1]) ReplaceLastWithHighType();
            }
            else if(last=="L" || last=="HL" || last=="LL")
            {
               if(w_Value[N-1]>w_LastLowValue) ReplaceLastWithLowType();
            }
         }
      }
   }

   void UpdateMajorLevels(double thisClose)
   {
      int nAdv=ArraySize(w_ValueAdv);
      if(nAdv<=1) return;
      int nBase=ArraySize(w_Type);
      if(nBase<1) return;

      if(thisClose>w_MajorHighLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"ML");
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL" || t=="mLL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH" || t=="MLH" || t=="MHH")
         {
            if(nAdv>=2 && nBase>=2)
            {
               string t2=w_TypeAdv[nAdv-2];
               if(t2=="mHL" || t2=="mLL")
               {
                  ReplaceAtS(w_TypeAdv,nAdv-2,"M"+w_Type[nBase-2]);
                  w_MajorLowLevel=w_ValueAdv[nAdv-2]; w_MajorLowIndex=w_IndexAdv[nAdv-2]; w_MajorLowType=w_TypeAdv[nAdv-2];
               }
            }
         }
      }

      if(w_ValueAdv[nAdv-1]>w_MajorHighLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"MH");
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH" || t=="MHH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
      }

      if(thisClose<w_MajorLowLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"MH");
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLH" || t=="mHH")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorHighLevel=w_ValueAdv[nAdv-1]; w_MajorHighIndex=w_IndexAdv[nAdv-1]; w_MajorHighType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL" || t=="mLL" || t=="MHL" || t=="MLL")
         {
            if(nAdv>=2 && nBase>=2)
            {
               string t2=w_TypeAdv[nAdv-2];
               if(t2=="mLH" || t2=="mHH")
               {
                  ReplaceAtS(w_TypeAdv,nAdv-2,"M"+w_Type[nBase-2]);
                  w_MajorHighLevel=w_ValueAdv[nAdv-2]; w_MajorHighIndex=w_IndexAdv[nAdv-2]; w_MajorHighType=w_TypeAdv[nAdv-2];
               }
            }
         }
      }

      if(w_ValueAdv[nAdv-1]<w_MajorLowLevel)
      {
         string t=w_TypeAdv[nAdv-1];
         if(t=="mL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"ML");
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mHL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
         else if(t=="mLL" || t=="MLL")
         {
            ReplaceAtS(w_TypeAdv,nAdv-1,"M"+w_Type[nBase-1]);
            w_MajorLowLevel=w_ValueAdv[nAdv-1]; w_MajorLowIndex=w_IndexAdv[nAdv-1]; w_MajorLowType=w_TypeAdv[nAdv-1];
         }
      }
   }

   void ProcessBar(int i, int PP)
   {
      int c=i-PP;
      bool hasHigh=false, hasLow=false;
      if(c>=PP && c+PP<n)
      {
         hasHigh=IsPivotHigh(c,PP);
         hasLow =IsPivotLow(c,PP);
      }
      if(hasHigh){ w_LastHighValue=hi[c]; w_LastHighIndex=c; }
      if(hasLow) { w_LastLowValue =lo[c]; w_LastLowIndex =c; }

      double thisClose=cl[i];
      int prevN=ArraySize(w_Value);
      double prevLastValue=(prevN>0) ? w_Value[prevN-1] : EMPTY_VALUE;
      string prevLastType =(prevN>0) ? w_Type[prevN-1]  : "";

      ClassifyPivot(hasHigh, hasLow, thisClose);

      int N=ArraySize(w_Type);
      if(N==2)
      {
         if(w_Type[0]=="H")
         {
            w_MajorHighLevel=w_Value[0]; w_MajorLowLevel=w_Value[1];
            w_MajorHighIndex=w_Index[0]; w_MajorLowIndex=w_Index[1];
            w_MajorHighType=w_Type[0];   w_MajorLowType=w_Type[1];
         }
         else if(w_Type[0]=="L")
         {
            w_MajorHighLevel=w_Value[1]; w_MajorLowLevel=w_Value[0];
            w_MajorHighIndex=w_Index[1]; w_MajorLowIndex=w_Index[0];
            w_MajorHighType=w_Type[1];   w_MajorLowType=w_Type[0];
         }
         w_MajorLevelsSet=true;
      }

      if(ArraySize(w_Value)==1 && w_Lock0)
      {
         InsertAt_S(w_TypeAdv,0,"M"+w_Type[0]); InsertAt_D(w_ValueAdv,0,w_Value[0]); InsertAt_I(w_IndexAdv,0,w_Index[0]);
         w_Lock0=false;
      }
      if(ArraySize(w_Value)==2 && w_Lock1)
      {
         InsertAt_S(w_TypeAdv,1,"M"+w_Type[1]); InsertAt_D(w_ValueAdv,1,w_Value[1]); InsertAt_I(w_IndexAdv,1,w_Index[1]);
         w_Lock1=false;
      }

      int m=ArraySize(w_Value);
      if(m>1)
      {
         double curLastValue=w_Value[m-1];
         string curLastType =w_Type[m-1];
         if(curLastValue!=prevLastValue)
         {
            string prevFamily=(StringLen(prevLastType)>0) ? StringSubstr(prevLastType,StringLen(prevLastType)-1,1) : "";
            string curFamily =StringSubstr(curLastType,StringLen(curLastType)-1,1);
            if(prevFamily!=curFamily)
            {
               PushS(w_TypeAdv,"m"+curLastType); PushD(w_ValueAdv,curLastValue); PushI(w_IndexAdv,w_Index[m-1]);
            }
            else
            {
               int nA=ArraySize(w_ValueAdv);
               if(nA>0){ w_ValueAdv[nA-1]=curLastValue; w_IndexAdv[nA-1]=w_Index[m-1]; }
            }
         }
      }

      if(w_MajorLevelsSet) UpdateMajorLevels(thisClose);
   }

   void TrackLatestPositions()
   {
      int nAdv=ArraySize(w_TypeAdv);
      if(nAdv<=2) return;
      int x=w_IndexAdv[nAdv-1];
      double y=w_ValueAdv[nAdv-1];
      string t=w_TypeAdv[nAdv-1];
      if(t=="MLL" || t=="MHL")      { w_MajSupX=x; w_MajSupY=y; }
      else if(t=="MHH" || t=="MLH") { w_MajResX=x; w_MajResY=y; }
      else if(t=="mLL" || t=="mHL") { w_MinSupX=x; w_MinSupY=y; }
      else if(t=="mHH" || t=="mLH") { w_MinResX=x; w_MinResY=y; }
   }

   void AddBar(const double h, const double l, const double c, const datetime t)
   {
      ArrayResize(hi, n+1, 20000); ArrayResize(lo, n+1, 20000); ArrayResize(cl, n+1, 20000); ArrayResize(tm, n+1, 20000);
      hi[n]=h; lo[n]=l; cl[n]=c; tm[n]=t; n++;
      ProcessBar(n-1, PivotPeriod);
      TrackLatestPositions();
      int    xs[4]; xs[0] = w_MajSupX; xs[1] = w_MinSupX; xs[2] = w_MajResX; xs[3] = w_MinResX;
      double ys[4]; ys[0] = w_MajSupY; ys[1] = w_MinSupY; ys[2] = w_MajResY; ys[3] = w_MinResY;
      for(int j = 0; j < 4; j++)
         if(xs[j] != pX[j] || ys[j] != pY[j]) { pX[j] = xs[j]; pY[j] = ys[j]; if(xs[j] >= 0) sT[j] = t + PeriodSeconds(tf); }
   }

   // Replay WarmupBars closed bars once.
   bool Warmup()
   {
      int avail = Bars(_Symbol, tf) - 1;
      int cnt = MathMin(WarmupBars, avail);
      if(cnt < 4*PivotPeriod + 10) return false;
      double h[], l[], c[]; datetime t[];
      if(CopyHigh(_Symbol, tf, 1, cnt, h) != cnt) return false;
      if(CopyLow(_Symbol, tf, 1, cnt, l) != cnt)  return false;
      if(CopyClose(_Symbol, tf, 1, cnt, c) != cnt) return false;
      if(CopyTime(_Symbol, tf, 1, cnt, t) != cnt) return false;
      for(int k = 0; k < cnt; k++) AddBar(h[k], l[k], c[k], t[k]);
      lastBar = t[cnt-1];
      ready = true;
      return true;
   }

   // Feed any newly closed bars; true if at least one was added.
   bool Update()
   {
      if(!ready) return Warmup();
      datetime lastClosed = iTime(_Symbol, tf, 1);
      if(lastClosed <= lastBar) return false;
      int shift = iBarShift(_Symbol, tf, lastBar, true);
      int from = (shift > 1) ? shift - 1 : 1;
      for(int s = from; s >= 1; s--)
      {
         AddBar(iHigh(_Symbol, tf, s), iLow(_Symbol, tf, s), iClose(_Symbol, tf, s), iTime(_Symbol, tf, s));
         lastBar = iTime(_Symbol, tf, s);
      }
      return true;
   }
};

string IST(const datetime t)
{
   if(t <= 0) return "-";
   string s = TimeToString((datetime)((long)t + ServerToISTMinutes * 60), TIME_DATE | TIME_MINUTES);   // yyyy.mm.dd hh:mi
   return StringSubstr(s, 8);                                                                          // dd hh:mi
}
string Age(const datetime t)
{
   if(t <= 0) return "";
   long s = (long)TimeCurrent() - (long)t;
   if(s < 0) s = 0;
   if(s < 3600)  return StringFormat("%dm", (int)(s / 60));
   if(s < 86400) return StringFormat("%dh%02dm", (int)(s / 3600), (int)(s % 3600 / 60));
   return StringFormat("%dd%02dh", (int)(s / 86400), (int)(s % 86400 / 3600));
}
string Px(const double v) { return DoubleToString(v, 2); }
bool ChartOn() { return !MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE); }
int HhmmToMin(const string s)
{
   string p[];
   if(StringSplit(s, ':', p) != 2) return -1;
   int h = (int)StringToInteger(p[0]), m = (int)StringToInteger(p[1]);
   if(h < 0 || h > 23 || m < 0 || m > 59) return -1;
   return h * 60 + m;
}
bool InISTWindow(const string from, const string to)
{
   int a = HhmmToMin(from), b = HhmmToMin(to);
   if(a < 0 || b < 0 || a == b) return false;
   int now = (int)(((long)TimeCurrent() + ServerToISTMinutes * 60) % 86400) / 60;
   return a < b ? (now >= a && now < b) : (now >= a || now < b);
}

#define DZ_BAR_SECONDS 3600
datetime g_dzsKey = 0;
double   g_dzsZ[4] = {0, 0, 0, 0};
bool DZSession(const datetime t, double &z1, double &z2, double &z3, double &z4, datetime &sess)
{
   int maxLen = MathMax(DZShortLen, DZLongLen);
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_H1, t, (maxLen + 5) * 24, r);
   if(got < 2) return false;
   int st[]; ArrayResize(st, got);
   int n = 0; st[n++] = 0;
   for(int i = 1; i < got; i++)
      if((long)r[i].time - (long)r[i-1].time > DZ_BAR_SECONDS) st[n++] = i;
   int si = n - 1;
   if(si < maxLen) return false;
   sess = r[st[si]].time;
   if(sess == g_dzsKey) { z1 = g_dzsZ[0]; z2 = g_dzsZ[1]; z3 = g_dzsZ[2]; z4 = g_dzsZ[3]; return true; }
   double sumS = 0, sumL = 0;
   for(int k = 1; k <= maxLen; k++)
   {
      int b = st[si - k], e = st[si - k + 1] - 1;
      double hi = r[b].high, lo = r[b].low;
      for(int j = b + 1; j <= e; j++) { if(r[j].high > hi) hi = r[j].high; if(r[j].low < lo) lo = r[j].low; }
      if(k <= DZShortLen) sumS += hi - lo;
      if(k <= DZLongLen)  sumL += hi - lo;
   }
   double o = r[st[si]].open, h5 = sumS / DZShortLen / 2.0, h10 = sumL / DZLongLen / 2.0;
   z1 = o + h5; z2 = o + h10; z3 = o - h5; z4 = o - h10;
   g_dzsKey = sess; g_dzsZ[0] = z1; g_dzsZ[1] = z2; g_dzsZ[2] = z3; g_dzsZ[3] = z4;
   return true;
}

string g_pTxt[]; color g_pClr[];
// one panel line; MT5 shows at most 63 characters per label, so longer lines wrap (indented) at a space
void P(const string s, const color c)
{
   string rest = s; bool first = true;
   while(true)
   {
      string line = (first ? "" : "     ") + rest;
      int cut = StringLen(line);
      if(cut > 63)
      {
         cut = 63;
         for(int k = 62; k > 20; k--) if(StringGetCharacter(line, k) == ' ') { cut = k; break; }
      }
      PushS(g_pTxt, StringSubstr(line, 0, cut));
      int n = ArraySize(g_pClr); ArrayResize(g_pClr, n + 1); g_pClr[n] = c;
      if(cut >= StringLen(line)) return;
      rest = StringSubstr(line, cut); StringTrimLeft(rest);
      if(rest == "") return;
      first = false;
   }
}

void RenderPanel()
{
   int lineH = PanelFontSize + 5;
   int h = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
   int perCol = MathMax(10, (h - PanelTop - 10) / lineH);
   int n = ArraySize(g_pTxt);
   for(int k = 0; k < n; k++)
   {
      string nm = "V6S3_PNL_" + IntegerToString(k);
      if(ObjectFind(0, nm) < 0)
      {
         ObjectCreate(0, nm, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, nm, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(0, nm, OBJPROP_FONT, "Consolas");
         ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      }
      ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, PanelFontSize);
      ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, 8 + (k / perCol) * PanelColWidth);
      ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, PanelTop + (k % perCol) * lineH);
      ObjectSetString(0, nm, OBJPROP_TEXT, g_pTxt[k] == "" ? " " : g_pTxt[k]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, g_pClr[k]);
   }
   for(int k = n; k < 400; k++) { string nm = "V6S3_PNL_" + IntegerToString(k); if(ObjectFind(0, nm) < 0) break; ObjectDelete(0, nm); }
}


//===================== timeframes =====================
#define NTF 7
ENUM_TIMEFRAMES g_tf[NTF] = {PERIOD_H4, PERIOD_H2, PERIOD_H1, PERIOD_M30, PERIOD_M15, PERIOD_M10, PERIOD_M5};
string          g_tfn[NTF] = {"H4", "H2", "H1", "M30", "M15", "M10", "M5"};
#define TM5 6
CMajorMinor g_mm[NTF];        // [TM5] is also the structure timeframe (fed bar by bar below)
datetime    g_m5Last = 0;     // last M5 bar fed into g_mm[TM5]

//===================== order blocks (M3 / M5) =====================
#define OB_SLOTS  3
#define OB_FIELDS 5
int g_obH[2];                 // [0] M3, [1] M5
string g_obN[2] = {"M3", "M5"};
int g_obChart = INVALID_HANDLE;
struct OBZ { int tf; int side; double top, btm; datetime obT; };
OBZ g_ob[];
void ReadOBs()
{
   ArrayResize(g_ob, 0);
   for(int t = 0; t < 2; t++)
   {
      if(g_obH[t] == INVALID_HANDLE || BarsCalculated(g_obH[t]) < 10) continue;
      for(int side = 0; side < 2; side++)
         for(int sl = 0; sl < OB_SLOTS; sl++)
         {
            int b = (side * OB_SLOTS + sl) * OB_FIELDS;
            double top[], btm[], ot[];
            if(CopyBuffer(g_obH[t], b, 0, 1, top) != 1 || CopyBuffer(g_obH[t], b + 1, 0, 1, btm) != 1 || CopyBuffer(g_obH[t], b + 2, 0, 1, ot) != 1) continue;
            if(top[0] == EMPTY_VALUE) continue;
            int n = ArraySize(g_ob); ArrayResize(g_ob, n + 1);
            g_ob[n].tf = t; g_ob[n].side = side; g_ob[n].top = top[0]; g_ob[n].btm = btm[0]; g_ob[n].obT = (datetime)(long)ot[0];
         }
   }
}
long ObId(const int tf, const datetime t) { return (long)t * 10 + tf; }
long g_used[];
bool UsedOB(const long id) { for(int k = 0; k < ArraySize(g_used); k++) if(g_used[k] == id) return true; return false; }

//===================== aligning levels (lot sizing) =====================
// value shared by >= MinAlignTFs timeframes (Major or Minor; M5 counts only with Majors)
bool AlignedNear(const int side, const double price, const double tol, double &lvl, string &members)
{
   double vals[]; int mask[]; string desc[];
   for(int t = 0; t < NTF; t++)
   {
      if(!g_mm[t].ready) continue;
      for(int j = (side > 0 ? 0 : 2); j < (side > 0 ? 2 : 4); j++)
      {
         if(t == TM5 && (j % 2 == 1)) continue;
         int x = (j == 0) ? g_mm[t].w_MajSupX : (j == 1) ? g_mm[t].w_MinSupX : (j == 2) ? g_mm[t].w_MajResX : g_mm[t].w_MinResX;
         double y = (j == 0) ? g_mm[t].w_MajSupY : (j == 1) ? g_mm[t].w_MinSupY : (j == 2) ? g_mm[t].w_MajResY : g_mm[t].w_MinResY;
         if(x < 0) continue;
         y = NormalizeDouble(y, _Digits);
         int k = -1;
         for(int q = 0; q < ArraySize(vals); q++) if(vals[q] == y) { k = q; break; }
         string lab = g_tfn[t] + ((j % 2 == 0) ? " Maj" : " Min");
         if(k < 0) { k = ArraySize(vals); PushD(vals, y); PushI(mask, 0); PushS(desc, lab); } else desc[k] += ", " + lab;
         mask[k] |= (1 << t);
      }
   }
   bool found = false; double best = 1e18;
   for(int k = 0; k < ArraySize(vals); k++)
   {
      int c = 0, m = mask[k]; while(m) { c += (m & 1); m >>= 1; }
      if(c < MinAlignTFs || MathAbs(vals[k] - price) > tol) continue;
      if(MathAbs(vals[k] - price) < best) { best = MathAbs(vals[k] - price); lvl = vals[k]; members = desc[k]; found = true; }
   }
   return found;
}
// all aligning levels of one side (for the panel)
string AlignList(const int side, const double bid)
{
   string r = ""; int shown = 0;
   double vals[]; int mask[];
   for(int t = 0; t < NTF; t++)
   {
      if(!g_mm[t].ready) continue;
      for(int j = (side > 0 ? 0 : 2); j < (side > 0 ? 2 : 4); j++)
      {
         if(t == TM5 && (j % 2 == 1)) continue;
         int x = (j == 0) ? g_mm[t].w_MajSupX : (j == 1) ? g_mm[t].w_MinSupX : (j == 2) ? g_mm[t].w_MajResX : g_mm[t].w_MinResX;
         double y = (j == 0) ? g_mm[t].w_MajSupY : (j == 1) ? g_mm[t].w_MinSupY : (j == 2) ? g_mm[t].w_MajResY : g_mm[t].w_MinResY;
         if(x < 0) continue;
         y = NormalizeDouble(y, _Digits);
         int k = -1;
         for(int q = 0; q < ArraySize(vals); q++) if(vals[q] == y) { k = q; break; }
         if(k < 0) { k = ArraySize(vals); PushD(vals, y); PushI(mask, 0); }
         mask[k] |= (1 << t);
      }
   }
   for(int k = 0; k < ArraySize(vals) && shown < 6; k++)
   {
      int c = 0, m = mask[k]; while(m) { c += (m & 1); m >>= 1; }
      if(c >= MinAlignTFs) { r += StringFormat(" %s(%d)", Px(vals[k]), c); shown++; }
   }
   return r == "" ? " none" : r;
}

//===================== setups =====================
struct Setup
{
   bool     on;
   int      stage;      // 0 waiting for MSS, 1 waiting for an OB, 2 waiting for the pullback
   double   maj, mss;
   int      bars;
   datetime majT, mssT, t0;
   int      obTF; double obTop, obBtm; datetime obT;
};
Setup    g_su[2];         // [0] buy, [1] sell
int      g_prevX[2] = {-1, -1};
string   g_event = "", g_lastTrade = "";
CTrade   g_trade;
bool     g_live = false;  // false while the warm-up replays history (no OB search, no trading)

string StageName(const int st) { return st == 0 ? "wait MSS" : st == 1 ? "wait OB" : "wait pullback"; }

bool GetPos(ulong &ticket, int &dir)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      ticket = t; dir = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      return true;
   }
   return false;
}

// one closed M5 candle (open bt): Major/Minor update, fresh Majors, MSS, cancellations
void M5Bar(const datetime bt, const double h, const double l, const double c)
{
   CMajorMinor *m = GetPointer(g_mm[TM5]);
   m.AddBar(h, l, c, bt);
   datetime now = bt + PeriodSeconds(PERIOD_M5);
   bool created[2] = {false, false};
   for(int s = 0; s < 2; s++)
   {
      int d = s == 0 ? 1 : -1;
      int X = d > 0 ? m.w_MajSupX : m.w_MajResX; double Y = d > 0 ? m.w_MajSupY : m.w_MajResY;
      if(X < 0 || X == g_prevX[s]) continue;
      g_prevX[s] = X;
      int rx = d > 0 ? m.w_MinResX : m.w_MinSupX; double ry = d > 0 ? m.w_MinResY : m.w_MinSupY;
      int gx = d > 0 ? m.w_MajResX : m.w_MajSupX; double gy = d > 0 ? m.w_MajResY : m.w_MajSupY;
      double lvl = rx >= gx ? ry : gy;
      if((lvl - Y) * d <= MinSwing || (rx < 0 && gx < 0)) continue;
      g_su[s].on = true; g_su[s].stage = 0; g_su[s].maj = Y; g_su[s].mss = lvl; g_su[s].bars = 0; g_su[s].majT = now; g_su[s].t0 = now;
      created[s] = true;
      if(g_live) g_event = StringFormat("%s fresh M5 Major %s %s, MSS level %s (swing %.1f)", IST(now), d > 0 ? "support" : "resistance", Px(Y), Px(lvl), MathAbs(lvl - Y));
   }
   for(int s = 0; s < 2; s++)
   {
      if(!g_su[s].on || created[s]) continue;
      int d = s == 0 ? 1 : -1;
      g_su[s].bars++;
      if(d > 0 ? c < g_su[s].maj : c > g_su[s].maj) { g_su[s].on = false; if(g_live) g_event = StringFormat("%s %s setup off -- M5 closed beyond the Major", IST(now), d > 0 ? "BUY" : "SELL"); continue; }
      if(g_su[s].bars > ValidBars) { g_su[s].on = false; if(g_live) g_event = StringFormat("%s %s setup expired", IST(now), d > 0 ? "BUY" : "SELL"); continue; }
      if(g_su[s].stage == 0 && (d > 0 ? c > g_su[s].mss : c < g_su[s].mss))
      {
         g_su[s].stage = 1; g_su[s].mssT = now; g_su[s].t0 = now;
         if(g_live) g_event = StringFormat("%s MSS %s: M5 closed %s %s", IST(now), d > 0 ? "up" : "down", Px(c), d > 0 ? "above" : "below");
      }
   }
}

// every new minute (now = open of the new M1 candle): OB search for setups that had their MSS
void M1Step(const datetime now, const double px)
{
   ReadOBs();
   for(int s = 0; s < 2; s++)
   {
      if(!g_su[s].on || g_su[s].stage != 1) continue;
      int d = s == 0 ? 1 : -1; int side = s;
      int best = -1;
      for(int k = 0; k < ArraySize(g_ob); k++)
      {
         if(g_ob[k].side != side || UsedOB(ObId(g_ob[k].tf, g_ob[k].obT))) continue;
         if(d > 0 ? !(g_ob[k].top < px && g_ob[k].btm >= g_su[s].maj - 1e-9) : !(g_ob[k].btm > px && g_ob[k].top <= g_su[s].maj + 1e-9)) continue;
         if(best < 0 || (d > 0 ? g_ob[k].top > g_ob[best].top : g_ob[k].btm < g_ob[best].btm)) best = k;
      }
      if(best < 0) continue;
      g_su[s].stage = 2; g_su[s].t0 = now;
      g_su[s].obTF = g_ob[best].tf; g_su[s].obTop = g_ob[best].top; g_su[s].obBtm = g_ob[best].btm; g_su[s].obT = g_ob[best].obT;
      g_event = StringFormat("%s %s OB picked: %s %s-%s, waiting for the pullback", IST(now), d > 0 ? "BUY" : "SELL", g_obN[g_ob[best].tf], Px(g_ob[best].btm), Px(g_ob[best].top));
   }
}

bool NoEntryHour()
{
   if(NoEntryFromHourIST < 0 || NoEntryToHourIST < 0) return false;
   int h = (int)((((long)TimeCurrent() + ServerToISTMinutes * 60) % 86400) / 3600);
   return NoEntryFromHourIST <= NoEntryToHourIST ? (h >= NoEntryFromHourIST && h <= NoEntryToHourIST)
                                                 : (h >= NoEntryFromHourIST || h <= NoEntryToHourIST);
}

// every tick: pullback entries
void TickEntries()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   for(int s = 0; s < 2; s++)
   {
      if(!g_su[s].on || g_su[s].stage != 2) continue;
      int d = s == 0 ? 1 : -1;
      if(d > 0 ? bid < g_su[s].obBtm : bid > g_su[s].obTop)              // traded through the OB before entry
      { g_su[s].on = false; g_event = StringFormat("%s %s setup off -- price through the OB before entry", IST(TimeCurrent()), d > 0 ? "BUY" : "SELL"); continue; }
      if(TimeCurrent() < g_su[s].t0) continue;
      ulong tk; int pd;
      if(GetPos(tk, pd)) continue;                                        // one position at a time: keep waiting
      if(TimeCurrent() < g_su[s].mssT + MinWaitMin * 60) continue;
      double edge = d > 0 ? g_su[s].obTop : g_su[s].obBtm;
      if(d > 0 ? bid > edge : bid < edge) continue;                       // not touched yet
      if(NoEntryHour()) continue;                                         // keep waiting until the window ends
      double entry = d > 0 ? ask : bid;
      double sl = d > 0 ? g_su[s].obBtm - SLBuffer : g_su[s].obTop + SLBuffer;
      double risk = (entry - sl) * d;
      g_su[s].on = false;                                                  // setup used
      int n = ArraySize(g_used); ArrayResize(g_used, n + 1); g_used[n] = ObId(g_su[s].obTF, g_su[s].obT);
      string ob = StringFormat("%s OB %s-%s", g_obN[g_su[s].obTF], Px(g_su[s].obBtm), Px(g_su[s].obTop));
      if(risk <= 0 || risk < MinRisk || risk > MaxRisk)
      { g_event = StringFormat("%s SKIP %s at %s -- SL distance %.2f outside %.0f-%.0f (%s)", IST(TimeCurrent()), d > 0 ? "BUY" : "SELL", Px(entry), risk, MinRisk, MaxRisk, ob); Print("V6S_ICT_3 ", g_event); continue; }
      double tp = entry + d * RR * risk;
      double alv; string mem;
      bool al = AlignedNear(d, g_su[s].maj, AlignTol, alv, mem);
      double lots = NormalizeDouble(BaseLots * (al ? AlignedLotMult : 1.0), 2);
      bool ok = false;
      if(EnableTrading)
      {
         string cm = StringFormat("V6S3 MSS %s %s", d > 0 ? "B" : "S", g_obN[g_su[s].obTF]);
         ok = d > 0 ? g_trade.Buy(lots, _Symbol, 0.0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), cm)
                    : g_trade.Sell(lots, _Symbol, 0.0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), cm);
      }
      g_lastTrade = StringFormat("%s %s %.2f lot @ %s | Major %s, MSS %s, %s | SL %s (risk %.2f) TP %s 1:%.1f%s%s", IST(TimeCurrent()),
                                 d > 0 ? "BUY" : "SELL", lots, Px(entry), Px(g_su[s].maj), IST(g_su[s].mssT), ob, Px(sl), risk, Px(tp), RR,
                                 al ? StringFormat(" | aligned %s [%s]", Px(alv), mem) : "",
                                 EnableTrading ? (ok ? " -- SENT" : " -- FAILED " + g_trade.ResultRetcodeDescription()) : " -- (trading off)");
      g_event = g_lastTrade;
      Print("V6S_ICT_3 ", g_lastTrade);
   }
}

//===================== chart + panel =====================
void DrawSetupLines()
{
   if(!ChartOn()) return;
   ObjectsDeleteAll(0, "V6S3_SU_");
   datetime r = iTime(_Symbol, PERIOD_M5, 0) + 30 * PeriodSeconds(PERIOD_M5);
   for(int s = 0; s < 2; s++)
   {
      if(!g_su[s].on) continue;
      color c = s == 0 ? clrLime : clrOrangeRed;
      string a = "V6S3_SU_" + IntegerToString(s);
      ObjectCreate(0, a + "_maj", OBJ_TREND, 0, g_su[s].majT, g_su[s].maj, r, g_su[s].maj);
      ObjectSetInteger(0, a + "_maj", OBJPROP_COLOR, c); ObjectSetInteger(0, a + "_maj", OBJPROP_WIDTH, 2);
      ObjectCreate(0, a + "_mss", OBJ_TREND, 0, g_su[s].majT, g_su[s].mss, r, g_su[s].mss);
      ObjectSetInteger(0, a + "_mss", OBJPROP_COLOR, c); ObjectSetInteger(0, a + "_mss", OBJPROP_STYLE, STYLE_DOT);
      if(g_su[s].stage == 2)
      {
         ObjectCreate(0, a + "_ob", OBJ_RECTANGLE, 0, g_su[s].t0, g_su[s].obTop, r, g_su[s].obBtm);
         ObjectSetInteger(0, a + "_ob", OBJPROP_COLOR, s == 0 ? C'0,90,0' : C'110,0,0'); ObjectSetInteger(0, a + "_ob", OBJPROP_FILL, true);
         ObjectSetInteger(0, a + "_ob", OBJPROP_BACK, true);
      }
      for(int k = 0; k < 3; k++) { string nm = a + (k == 0 ? "_maj" : k == 1 ? "_mss" : "_ob"); ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false); ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true); }
   }
}

void DrawPanel()
{
   if(!ShowPanel || !ChartOn()) return;
   ArrayResize(g_pTxt, 0); ArrayResize(g_pClr, 0);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   P(StringFormat("V6S_ICT_3.15 MSS-OB  %s IST  bid %s", IST(TimeCurrent()), Px(bid)), clrWhite);
   P(StringFormat("swing>=%.0f wait>=%dm no %02d-%02d IST risk %.0f-%.0f 1:%.1f lot %.2f/x%.1f", MinSwing, MinWaitMin, NoEntryFromHourIST,
                  NoEntryToHourIST, MinRisk, MaxRisk, RR, BaseLots, AlignedLotMult), clrSilver);
   ulong tk; int pd;
   if(GetPos(tk, pd) && PositionSelectByTicket(tk))
      P(StringFormat("TRADE %s %.2f @ %s SL %s TP %s P/L %.2f", pd > 0 ? "BUY" : "SELL", PositionGetDouble(POSITION_VOLUME), Px(PositionGetDouble(POSITION_PRICE_OPEN)),
                     Px(PositionGetDouble(POSITION_SL)), Px(PositionGetDouble(POSITION_TP)), PositionGetDouble(POSITION_PROFIT)), pd > 0 ? clrLime : clrOrangeRed);
   else P("TRADE none", clrSilver);
   for(int s = 0; s < 2; s++)
   {
      if(!g_su[s].on) { P(s == 0 ? "BUY setup: none" : "SELL setup: none", clrSilver); continue; }
      P(StringFormat("%s setup: %s | Major %s (%s) MSS lvl %s | %d/%d bars", s == 0 ? "BUY" : "SELL", StageName(g_su[s].stage), Px(g_su[s].maj),
                     IST(g_su[s].majT), Px(g_su[s].mss), g_su[s].bars, ValidBars), s == 0 ? clrLime : clrOrangeRed);
      if(g_su[s].stage == 2)
         P(StringFormat("   OB %s %s-%s, entry from %s", g_obN[g_su[s].obTF], Px(g_su[s].obBtm), Px(g_su[s].obTop),
                        IST(MathMax(g_su[s].t0, g_su[s].mssT + MinWaitMin * 60))), clrYellow);
   }
   if(g_event != "") P("EVENT: " + g_event, clrAqua);
   if(g_lastTrade != "" && g_lastTrade != g_event) P("LAST TRADE: " + g_lastTrade, clrDeepSkyBlue);
   P("ALIGNING S (TFs):" + AlignList(1, bid), clrLimeGreen);
   P("ALIGNING R (TFs):" + AlignList(-1, bid), clrTomato);
   double z1, z2, z3, z4; datetime ds;
   if(DZSession(TimeCurrent(), z1, z2, z3, z4, ds))
      P(StringFormat("DZ (from %s) Z2 %s Z1 %s | Z3 %s Z4 %s", IST(ds), Px(z2), Px(z1), Px(z3), Px(z4)), clrDodgerBlue);
   string ob = "";
   for(int k = 0; k < ArraySize(g_ob); k++)
      ob += StringFormat(" %s%s %s-%s", g_obN[g_ob[k].tf], g_ob[k].side == 0 ? "+" : "-", Px(g_ob[k].btm), Px(g_ob[k].top));
   P("OBs:" + (ob == "" ? " none" : ob), clrSilver);
   RenderPanel();
}

//===================== events =====================
datetime g_m1Last = 0;

int OnInit()
{
   for(int t = 0; t < NTF; t++) g_mm[t].Init(g_tf[t], g_tfn[t]);
   g_obH[0] = iCustom(_Symbol, PERIOD_M3, "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, false);
   g_obH[1] = iCustom(_Symbol, PERIOD_M5, "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, false);
   if(DrawChartTFZones && ChartOn())
   {
      g_obChart = iCustom(_Symbol, PERIOD_CURRENT, "OB_Detector_v1.07", OBLength, OBVolume, OBMitigation, true);
      if(g_obChart != INVALID_HANDLE && !MQLInfoInteger(MQL_TESTER)) ChartIndicatorAdd(0, 0, g_obChart);
   }
   for(int s = 0; s < 2; s++) { g_su[s].on = false; g_prevX[s] = -1; }
   ArrayResize(g_used, 0); g_event = ""; g_lastTrade = ""; g_m5Last = 0; g_m1Last = 0; g_live = false;
   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   ObjectsDeleteAll(0, "V6S3_");
   PrintFormat("V6S_ICT_3 v3.15 MSS-OB on %s | lots %.2f (x%.1f aligned), 1:%.1f, magic %I64d, trading %s", _Symbol, BaseLots, AlignedLotMult, RR, MagicNumber,
               EnableTrading ? "ON" : "off");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   for(int t = 0; t < 2; t++) if(g_obH[t] != INVALID_HANDLE) IndicatorRelease(g_obH[t]);
   if(g_obChart != INVALID_HANDLE) IndicatorRelease(g_obChart);
   if(!MQLInfoInteger(MQL_TESTER)) ObjectsDeleteAll(0, "V6S3_");
}

// M5 structure: warm-up replay, then every newly closed M5 candle in order
bool FeedM5()
{
   if(g_m5Last == 0)
   {
      int avail = Bars(_Symbol, PERIOD_M5) - 1, cnt = MathMin(WarmupBars, avail);
      if(cnt < 4 * PivotPeriod + 10) return false;
      MqlRates r[];
      if(CopyRates(_Symbol, PERIOD_M5, 1, cnt, r) != cnt) return false;
      for(int k = 0; k < cnt; k++) M5Bar(r[k].time, r[k].high, r[k].low, r[k].close);
      g_mm[TM5].ready = true; g_m5Last = r[cnt - 1].time;
      return true;
   }
   datetime lc = iTime(_Symbol, PERIOD_M5, 1);
   if(lc <= g_m5Last) return false;
   int shift = iBarShift(_Symbol, PERIOD_M5, g_m5Last, true);
   int from = shift > 1 ? shift - 1 : 1;
   for(int s = from; s >= 1; s--)
   {
      M5Bar(iTime(_Symbol, PERIOD_M5, s), iHigh(_Symbol, PERIOD_M5, s), iLow(_Symbol, PERIOD_M5, s), iClose(_Symbol, PERIOD_M5, s));
      g_m5Last = iTime(_Symbol, PERIOD_M5, s);
   }
   return true;
}

void OnTick()
{
   bool ready = true;
   for(int t = 0; t < NTF; t++) if(t != TM5) { g_mm[t].Update(); if(!g_mm[t].ready) ready = false; }
   bool m5new = FeedM5();
   if(!ready || g_m5Last == 0) return;
   if(!g_live) { g_live = true; g_m1Last = iTime(_Symbol, PERIOD_M1, 0); }
   datetime m1 = iTime(_Symbol, PERIOD_M1, 0);
   bool dirty = m5new;
   if(m1 != g_m1Last) { g_m1Last = m1; M1Step(m1, iClose(_Symbol, PERIOD_M1, 1)); dirty = true; }
   TickEntries();
   if(dirty) { DrawSetupLines(); DrawPanel(); }
}
//+------------------------------------------------------------------+
