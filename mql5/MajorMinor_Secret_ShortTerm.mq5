//+------------------------------------------------------------------+
//| MajorMinor_Secret_ShortTerm.mq5                                  |
//| Ported from Pine: "Major/Minor-Secret v1.0" (c) TFlab, MPL 2.0    |
//| Short-term Major/Minor swing structure ONLY (long-term dropped).  |
//| All 4 lines yellow: Major = thick, Minor = thin.                  |
//|                                                                    |
//| Algorithm (mirrors the Pine source):                               |
//|  1) ZigZag: detect PP-bar-confirmed pivot highs/lows, keep an      |
//|     alternating sequence of swing points classified H/L/HH/LH/HL/LL|
//|     relative to the previous point of the same family.             |
//|  2) Major/Minor: a second pass tracks a "pending" (minor) copy of  |
//|     each swing point; when price CLOSES beyond the current Major   |
//|     High/Low level, the pending point is promoted to Major (a      |
//|     break-of-structure confirmation). This runs every closed bar,  |
//|     not just on pivot-confirmation bars.                           |
//|  3) Draw 4 horizontal rays (Major/Minor Support/Resistance) at the |
//|     latest point of each category, extended right, replaced as new|
//|     points are confirmed/promoted.                                 |
//|                                                                    |
//| Note on Pine's ta.valuewhen: HighValue/LowValue in the original    |
//| always hold the MOST RECENTLY CONFIRMED pivot of that type, even   |
//| on bars where only the other type fires. Ported here as the        |
//| persistent w_LastHighValue/w_LastLowValue trackers.                |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

input int   PivotPeriod  = 5;          // Short Term S&R Pivot Period
input bool  ShowMajor    = true;
input bool  ShowMinor    = true;
input color LineColor    = clrYellow;
input ENUM_LINE_STYLE MajorStyle = STYLE_SOLID;
input ENUM_LINE_STYLE MinorStyle = STYLE_SOLID;
input int   MajorWidth   = 2;
input int   MinorWidth   = 1;

#define NAME_MAJOR_SUPPORT    "MmSecret_MajorSupport"
#define NAME_MAJOR_RESISTANCE "MmSecret_MajorResistance"
#define NAME_MINOR_SUPPORT    "MmSecret_MinorSupport"
#define NAME_MINOR_RESISTANCE "MmSecret_MinorResistance"

//===================== confirmed (committed) state =====================
string gc_Type[],    gc_TypeAdv[];
double gc_Value[],   gc_ValueAdv[];
int    gc_Index[],   gc_IndexAdv[];
double gc_MajorHighLevel, gc_MajorLowLevel;
int    gc_MajorHighIndex, gc_MajorLowIndex;
string gc_MajorHighType,  gc_MajorLowType;
bool   gc_MajorLevelsSet;
bool   gc_Lock0, gc_Lock1;
double gc_LastHighValue, gc_LastLowValue;
int    gc_LastHighIndex, gc_LastLowIndex;
int    gc_ConfirmedUpTo;
//--- latest known position of each of the 4 lines, tracked as plain data
//    while walking history (cheap); the chart objects themselves are only
//    touched once per OnCalculate call, using whatever these hold then.
int    gc_MajSupX=-1, gc_MajResX=-1, gc_MinSupX=-1, gc_MinResX=-1;
double gc_MajSupY,    gc_MajResY,    gc_MinSupY,    gc_MinResY;

//===================== working (this-call scratch) state =====================
string w_Type[],    w_TypeAdv[];
double w_Value[],   w_ValueAdv[];
int    w_Index[],   w_IndexAdv[];
double w_MajorHighLevel, w_MajorLowLevel;
int    w_MajorHighIndex, w_MajorLowIndex;
string w_MajorHighType,  w_MajorLowType;
bool   w_MajorLevelsSet;
bool   w_Lock0, w_Lock1;
double w_LastHighValue, w_LastLowValue;
int    w_LastHighIndex, w_LastLowIndex;
int    w_MajSupX=-1, w_MajResX=-1, w_MinSupX=-1, w_MinResX=-1;
double w_MajSupY,    w_MajResY,    w_MinSupY,    w_MinResY;

//--- last-drawn cache so we don't recreate chart objects every tick when nothing moved
int    g_drawnMajorSupX=-1000000, g_drawnMajorResX=-1000000, g_drawnMinorSupX=-1000000, g_drawnMinorResX=-1000000;

//+------------------------------------------------------------------+
//| tiny dynamic-array push/pop/insert helpers                       |
//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
//| pivot test: strict local extreme over a 2*PP+1 window             |
//+------------------------------------------------------------------+
bool IsPivotHigh(const double &high[], int c, int PP, int total)
{
   if(c-PP<0 || c+PP>=total) return false;
   double v=high[c];
   for(int k=c-PP;k<=c+PP;k++)
      if(k!=c && high[k]>=v) return false;
   return true;
}
bool IsPivotLow(const double &low[], int c, int PP, int total)
{
   if(c-PP<0 || c+PP>=total) return false;
   double v=low[c];
   for(int k=c-PP;k<=c+PP;k++)
      if(k!=c && low[k]<=v) return false;
   return true;
}

//+------------------------------------------------------------------+
//| base ZigZag mutation helpers -- push a NEW alternating point, or  |
//| replace the LAST point (same family, more extreme). Type suffix   |
//| (H/HH/LH or L/HL/LL) compares against the point two-back, exactly |
//| as the Pine source's ArrayValue.get(size-2) ternary does.         |
//+------------------------------------------------------------------+
void PushHighType()
{
   int n=ArraySize(w_Type); // size BEFORE this push
   string t=(n>2) ? ((w_Value[n-2]<w_LastHighValue) ? "HH":"LH") : "H";
   PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
}
void PushLowType()
{
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastLowValue) ? "HL":"LL") : "L";
   PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
}
void ReplaceLastWithHighType()
{
   RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
   int n=ArraySize(w_Type); // size AFTER removal
   string t=(n>2) ? ((w_Value[n-2]<w_LastHighValue) ? "HH":"LH") : "H";
   PushS(w_Type,t); PushD(w_Value,w_LastHighValue); PushI(w_Index,w_LastHighIndex);
}
void ReplaceLastWithLowType()
{
   RemoveLastS(w_Type); RemoveLastD(w_Value); RemoveLastI(w_Index);
   int n=ArraySize(w_Type);
   string t=(n>2) ? ((w_Value[n-2]<w_LastLowValue) ? "HL":"LL") : "L";
   PushS(w_Type,t); PushD(w_Value,w_LastLowValue); PushI(w_Index,w_LastLowIndex);
}

//+------------------------------------------------------------------+
//| base ZigZag classification -- direct port of ZZ(PP)'s main        |
//| if-bool(HighPivot)-and-bool(LowPivot) / else-if branches.          |
//+------------------------------------------------------------------+
void ClassifyPivot(bool hasHigh, bool hasLow, double thisClose)
{
   int N=ArraySize(w_Type);

   if(hasHigh && hasLow)
   {
      if(N==0)
      {
         // Pine: PASS=1 -- both pivots land on an empty sequence; original discards them.
      }
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

//+------------------------------------------------------------------+
//| break-of-structure promotion -- direct port of the "All Major &   |
//| Minor Pivot Detector" block. Runs every closed bar (gated on      |
//| w_MajorLevelsSet), reacting to CLOSE crossing the current Major   |
//| High/Low level. Only TypeAdv entries get relabelled ('m'->'M');   |
//| the pending level's Value/Index are already correct and untouched.|
//+------------------------------------------------------------------+
void UpdateMajorLevels(double thisClose)
{
   int nAdv=ArraySize(w_ValueAdv);
   if(nAdv<=1) return;
   int nBase=ArraySize(w_Type);
   if(nBase<1) return;

   //--- High Major Detector (bullish break of Major High)
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

   //--- Low Major Detector (bearish break of Major Low)
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

//+------------------------------------------------------------------+
//| process ONE bar (Pine's "current execution bar" = i). Candidate   |
//| pivot center is c = i-PP; needs c-PP>=0 and c+PP<total, i.e.       |
//| i >= 2*PP, to have a full confirmation window.                    |
//+------------------------------------------------------------------+
void ProcessBar(int i, int rates_total, int PP,
                 const double &high[], const double &low[], const double &close[])
{
   int c=i-PP;
   bool hasHigh=false, hasLow=false;

   if(c>=PP && c+PP<rates_total)
   {
      hasHigh=IsPivotHigh(high,c,PP,rates_total);
      hasLow =IsPivotLow(low,c,PP,rates_total);
   }

   // ta.valuewhen-style "hold last confirmed pivot" trackers
   if(hasHigh){ w_LastHighValue=high[c]; w_LastHighIndex=c; }
   if(hasLow) { w_LastLowValue =low[c];  w_LastLowIndex =c; }

   double thisClose=close[i];

   //--- snapshot of base array's last element BEFORE this bar's mutation
   // (mirrors Pine's `expr[1]` -- the value that expression held one bar ago)
   int prevN=ArraySize(w_Value);
   double prevLastValue=(prevN>0) ? w_Value[prevN-1] : EMPTY_VALUE;
   string prevLastType =(prevN>0) ? w_Type[prevN-1]  : "";

   ClassifyPivot(hasHigh, hasLow, thisClose);

   //--- first Major/Minor detector: seeds Major_High/Low the moment the
   // base sequence first reaches 2 points. Re-fires harmlessly (same
   // values) every bar until a 3rd point arrives, exactly like the Pine.
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

   //--- Lock0 / Lock1: seed the Adv (pending) arrays from the first two base points
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

   //--- Adv-array sync: only reacts on the bar the base array actually changed.
   int n=ArraySize(w_Value);
   if(n>1)
   {
      double curLastValue=w_Value[n-1];
      string curLastType =w_Type[n-1];
      if(curLastValue!=prevLastValue)
      {
         string prevFamily=(StringLen(prevLastType)>0) ? StringSubstr(prevLastType,StringLen(prevLastType)-1,1) : "";
         string curFamily =StringSubstr(curLastType,StringLen(curLastType)-1,1);
         if(prevFamily!=curFamily)
         {
            PushS(w_TypeAdv,"m"+curLastType); PushD(w_ValueAdv,curLastValue); PushI(w_IndexAdv,w_Index[n-1]);
         }
         else
         {
            int nA=ArraySize(w_ValueAdv);
            if(nA>0){ w_ValueAdv[nA-1]=curLastValue; w_IndexAdv[nA-1]=w_Index[n-1]; }
         }
      }
   }

   //--- break-of-structure promotion, every closed bar
   if(w_MajorLevelsSet) UpdateMajorLevels(thisClose);
}

//+------------------------------------------------------------------+
//| confirmed <-> working state plumbing.                             |
//| "Confirmed" only ever holds fully-closed bars; the still-forming  |
//| last bar is always reprocessed fresh from a working copy each     |
//| tick (mirrors Pine's var-state model: each tick's live bar starts |
//| from the previous CLOSED bar's end-state, never accumulating      |
//| across ticks of the same unclosed bar).                           |
//+------------------------------------------------------------------+
void CopyConfirmedToWorking()
{
   ArrayCopy(w_Type,gc_Type);       ArrayCopy(w_Value,gc_Value);       ArrayCopy(w_Index,gc_Index);
   ArrayCopy(w_TypeAdv,gc_TypeAdv); ArrayCopy(w_ValueAdv,gc_ValueAdv); ArrayCopy(w_IndexAdv,gc_IndexAdv);
   w_MajorHighLevel=gc_MajorHighLevel; w_MajorLowLevel=gc_MajorLowLevel;
   w_MajorHighIndex=gc_MajorHighIndex; w_MajorLowIndex=gc_MajorLowIndex;
   w_MajorHighType=gc_MajorHighType;   w_MajorLowType=gc_MajorLowType;
   w_MajorLevelsSet=gc_MajorLevelsSet;
   w_Lock0=gc_Lock0; w_Lock1=gc_Lock1;
   w_LastHighValue=gc_LastHighValue; w_LastLowValue=gc_LastLowValue;
   w_LastHighIndex=gc_LastHighIndex; w_LastLowIndex=gc_LastLowIndex;
   w_MajSupX=gc_MajSupX; w_MajSupY=gc_MajSupY; w_MajResX=gc_MajResX; w_MajResY=gc_MajResY;
   w_MinSupX=gc_MinSupX; w_MinSupY=gc_MinSupY; w_MinResX=gc_MinResX; w_MinResY=gc_MinResY;
}
void CommitWorkingToConfirmed()
{
   ArrayCopy(gc_Type,w_Type);       ArrayCopy(gc_Value,w_Value);       ArrayCopy(gc_Index,w_Index);
   ArrayCopy(gc_TypeAdv,w_TypeAdv); ArrayCopy(gc_ValueAdv,w_ValueAdv); ArrayCopy(gc_IndexAdv,w_IndexAdv);
   gc_MajorHighLevel=w_MajorHighLevel; gc_MajorLowLevel=w_MajorLowLevel;
   gc_MajorHighIndex=w_MajorHighIndex; gc_MajorLowIndex=w_MajorLowIndex;
   gc_MajorHighType=w_MajorHighType;   gc_MajorLowType=w_MajorLowType;
   gc_MajorLevelsSet=w_MajorLevelsSet;
   gc_Lock0=w_Lock0; gc_Lock1=w_Lock1;
   gc_LastHighValue=w_LastHighValue; gc_LastLowValue=w_LastLowValue;
   gc_LastHighIndex=w_LastHighIndex; gc_LastLowIndex=w_LastLowIndex;
   gc_MajSupX=w_MajSupX; gc_MajSupY=w_MajSupY; gc_MajResX=w_MajResX; gc_MajResY=w_MajResY;
   gc_MinSupX=w_MinSupX; gc_MinSupY=w_MinSupY; gc_MinResX=w_MinResX; gc_MinResY=w_MinResY;
}
void ResetAllConfirmed()
{
   ArrayResize(gc_Type,0);    ArrayResize(gc_Value,0);    ArrayResize(gc_Index,0);
   ArrayResize(gc_TypeAdv,0); ArrayResize(gc_ValueAdv,0); ArrayResize(gc_IndexAdv,0);
   gc_MajorHighLevel=0; gc_MajorLowLevel=0;
   gc_MajorHighIndex=-1; gc_MajorLowIndex=-1;
   gc_MajorHighType=""; gc_MajorLowType="";
   gc_MajorLevelsSet=false;
   gc_Lock0=true; gc_Lock1=true;
   gc_LastHighValue=0; gc_LastLowValue=0;
   gc_LastHighIndex=-1; gc_LastLowIndex=-1;
   gc_ConfirmedUpTo=-1;
   gc_MajSupX=-1; gc_MajResX=-1; gc_MinSupX=-1; gc_MinResX=-1;
   gc_MajSupY=0;  gc_MajResY=0;  gc_MinSupY=0;  gc_MinResY=0;

   ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
   ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
   g_drawnMajorSupX=-1000000; g_drawnMajorResX=-1000000; g_drawnMinorSupX=-1000000; g_drawnMinorResX=-1000000;
}

//+------------------------------------------------------------------+
//| draw/move one horizontal ray; skips the object call entirely if   |
//| (x,y) hasn't changed since last draw (cheap live-bar re-entry).   |
//+------------------------------------------------------------------+
void DrawSRLine(string name, int &lastX, int x, double y, const datetime &time[], int rates_total,
                 color clr, int width, ENUM_LINE_STYLE style)
{
   if(x<0 || x>=rates_total) return;
   if(x==lastX) return; // unchanged since last draw -- nothing to do

   datetime t1=time[x];
   datetime t2=t1+PeriodSeconds();

   if(ObjectFind(0,name)<0)
      ObjectCreate(0,name,OBJ_TREND,0,t1,y,t2,y);
   else
   {
      ObjectMove(0,name,0,t1,y);
      ObjectMove(0,name,1,t2,y);
   }
   ObjectSetInteger(0,name,OBJPROP_RAY_RIGHT,true);
   ObjectSetInteger(0,name,OBJPROP_RAY_LEFT,false);
   ObjectSetInteger(0,name,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,name,OBJPROP_WIDTH,width);
   ObjectSetInteger(0,name,OBJPROP_STYLE,style);
   ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,name,OBJPROP_HIDDEN,true);
   ObjectSetInteger(0,name,OBJPROP_BACK,false);

   lastX=x;
}

//+------------------------------------------------------------------+
//| Cheap per-bar step: just remember which (x,y) each of the 4 line  |
//| categories most recently belonged to. No chart object calls here  |
//| -- this is what lets the historical replay walk thousands of bars |
//| without visibly redrawing anything each step.                     |
//+------------------------------------------------------------------+
void TrackLatestPositions()
{
   int nAdv=ArraySize(w_TypeAdv);
   if(nAdv<=2) return; // Pine gate: Type.size() > 2

   int x=w_IndexAdv[nAdv-1];
   double y=w_ValueAdv[nAdv-1];
   string t=w_TypeAdv[nAdv-1];

   if(t=="MLL" || t=="MHL")      { w_MajSupX=x; w_MajSupY=y; }
   else if(t=="MHH" || t=="MLH") { w_MajResX=x; w_MajResY=y; }
   else if(t=="mLL" || t=="mHL") { w_MinSupX=x; w_MinSupY=y; }
   else if(t=="mHH" || t=="mLH") { w_MinResX=x; w_MinResY=y; }
}

//+------------------------------------------------------------------+
//| The only place that actually touches chart objects. Called once   |
//| per OnCalculate invocation (once per tick, or once for the whole  |
//| initial history load) using whatever TrackLatestPositions() has   |
//| accumulated -- so a full history replay updates each line exactly |
//| once, not once per historical bar.                                |
//+------------------------------------------------------------------+
void DrawFromWorking(const datetime &time[], int rates_total)
{
   if(ShowMajor)
   {
      if(w_MajSupX>=0) DrawSRLine(NAME_MAJOR_SUPPORT,    g_drawnMajorSupX, w_MajSupX, w_MajSupY, time, rates_total, LineColor, MajorWidth, MajorStyle);
      if(w_MajResX>=0) DrawSRLine(NAME_MAJOR_RESISTANCE, g_drawnMajorResX, w_MajResX, w_MajResY, time, rates_total, LineColor, MajorWidth, MajorStyle);
   }
   if(ShowMinor)
   {
      if(w_MinSupX>=0) DrawSRLine(NAME_MINOR_SUPPORT,    g_drawnMinorSupX, w_MinSupX, w_MinSupY, time, rates_total, LineColor, MinorWidth, MinorStyle);
      if(w_MinResX>=0) DrawSRLine(NAME_MINOR_RESISTANCE, g_drawnMinorResX, w_MinResX, w_MinResY, time, rates_total, LineColor, MinorWidth, MinorStyle);
   }
}

//+------------------------------------------------------------------+
//| Indicator initialization                                          |
//+------------------------------------------------------------------+
int OnInit()
{
   ResetAllConfirmed();
   IndicatorSetString(INDICATOR_SHORTNAME, "Major/Minor-Secret (Short Term)");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Indicator calculation                                             |
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
   if(rates_total < 2*PivotPeriod+2) return(rates_total);

   if(prev_calculated<=0) ResetAllConfirmed();

   CopyConfirmedToWorking();

   int startBar=gc_ConfirmedUpTo+1;
   if(startBar<0) startBar=0;

   for(int i=startBar; i<rates_total; i++)
   {
      ProcessBar(i, rates_total, PivotPeriod, high, low, close);

      // Cheap data-only bookkeeping every bar (mirrors Pine's Drawing()
      // re-executing every bar, so each of the 4 line categories gets
      // credited at the historical moment its family was last active --
      // otherwise only whichever family the very LAST bar belonged to
      // would ever be known about). No chart objects touched here.
      TrackLatestPositions();

      if(i<rates_total-1)
      {
         CommitWorkingToConfirmed();
         gc_ConfirmedUpTo=i;
      }
   }

   // Actual chart object writes happen exactly once per call.
   DrawFromWorking(time, rates_total);

   return(rates_total);
}

//+------------------------------------------------------------------+
//| Cleanup                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
   ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
}
