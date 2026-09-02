//+------------------------------------------------------------------+
//|   ATRTrailDual_MajorMinor.mq5                                    |
//|   Merge of SurajBot_ATRTrail_FINAL_LIVEFIXED_REALTIME_DUAL.mq5    |
//|   (two independent ATR trailing-stop lines + bridge publish) and  |
//|   MajorMinor_Secret_ShortTerm.mq5 (ZigZag-based Major/Minor swing |
//|   structure S/R rays), combined into one indicator so both run    |
//|   from a single chart attachment instead of two.                  |
//|                                                                    |
//|   Neither original does per-tick/heavy work (both are closed-bar, |
//|   incremental, and only touch chart objects once per OnCalculate  |
//|   call), so the merge is a straight combine -- no logic changed   |
//|   on either side, just folded into one OnInit/OnCalculate/OnDeinit|
//|   and one Inputs menu.                                             |
//|                                                                    |
//|   EnableMajorMinor (default true) gates the ENTIRE Major/Minor    |
//|   block -- computation and drawing both skip while off, and any   |
//|   already-drawn S/R rays are cleared the moment it's switched off.|
//|   ATR Trail Dual (both lines + its existing bridge publish) always|
//|   runs regardless of this switch.                                 |
//|                                                                    |
//|   Major/Minor is NOT wired into the bridge JSON here -- it stays  |
//|   chart-object-only for now, same as the original. Publishing its |
//|   levels for a Python bot to read is deferred to a later change.  |
//+------------------------------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 6
#property indicator_plots   2

//--- Plot 1: fast trail (ATRPeriod / KeyValue)
#property indicator_label1  "ATR Trailing Stop"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrGray, clrGray
#property indicator_width1  2

//--- Plot 2: slow trail (ATRPeriod2 / KeyValue2)
#property indicator_label2  "ATR Trailing Stop 2"
#property indicator_type2   DRAW_COLOR_LINE
#property indicator_color2  clrGray, clrGray
#property indicator_width2  2

//===================== Inputs: ATR Trail Dual =====================
//--- ATR Trailing Stop parameters (line 1)
input double KeyValue   = 2;
input int    ATRPeriod  = 2;

//--- ATR Trailing Stop parameters (line 2)
input double KeyValue2  = 2;
input int    ATRPeriod2 = 300;

//--- Bridge publish (unchanged from the original ATR Dual indicator --
//    Major/Minor data is deliberately NOT added to this file, see header)
input bool   PublishToFile       = true;
input string FileBridgeFolder    = "OBBridge";  // same Common Files folder as OB_ATR_Bridge_Indicator
input string BridgeSymbol        = "";          // empty = use the attached chart's symbol
input int    PublishEverySeconds = 2;           // throttles the bridge file write + event-time backward scan, not the trail calc itself

//===================== Inputs: Major/Minor structure =====================
input bool  EnableMajorMinor = true;   // master on/off for the whole Major/Minor block -- default on
input int   PivotPeriod      = 5;      // Short Term S&R Pivot Period
input bool  ShowMajor        = true;
input bool  ShowMinor        = true;
input color LineColor        = clrYellow;
input ENUM_LINE_STYLE MajorStyle = STYLE_SOLID;
input ENUM_LINE_STYLE MinorStyle = STYLE_SOLID;
input int   MajorWidth   = 2;
input int   MinorWidth   = 1;

#define NAME_MAJOR_SUPPORT    "MmSecret_MajorSupport"
#define NAME_MAJOR_RESISTANCE "MmSecret_MajorResistance"
#define NAME_MINOR_SUPPORT    "MmSecret_MinorSupport"
#define NAME_MINOR_RESISTANCE "MmSecret_MinorResistance"

// Always reprocessed fully on every call, regardless of prev_calculated --
// see CalcTrail's own comment at its use site for the real 3-bar drift
// this fixes (a reattach-triggered iATR(300) settling lag).
#define SAFETY_REPROCESS_BARS 50

//===================== ATR Trail Dual buffers/state =====================
double TrailStop[];
double ColorBuffer[];
double ATRBuffer[];
double TrendBuffer[];

double TrailStop2[];
double ColorBuffer2[];
double ATRBuffer2[];
double TrendBuffer2[];

int ATRHandle;
int ATRHandle2;

#define LABEL_NAME  "ATR_Trail_Label"
#define LABEL_NAME2 "ATR_Trail_Label2"

bool loggedInsufficientBars1 = false;
bool loggedInsufficientBars2 = false;
bool loggedCopyFail1 = false;
bool loggedCopyFail2 = false;

datetime g_last_publish_time = 0;

// Incremental caches for FindEventTime/FindStructureEventTime below --
// fixed 2026-09-02, confirmed live: "indicator is too slow, 38453 ms" on
// XAUUSD M3. Root cause was those two functions doing an UNBOUNDED
// backward linear scan from the current closed bar all the way back to
// bar 0 every single publish cycle (every PublishEverySeconds), looking
// for the last trend flip -- three separate full-history scans per call
// (line1's own flip, line2's own flip, the combined structure's flip).
// A trend line that hasn't flipped in a long stretch (routinely observed
// -- many hours, sometimes longer) turns that into a scan across
// essentially the WHOLE chart's loaded M3 history, repeated every few
// seconds forever. Cached below the same way CalcTrail already caches
// its own incremental state via prev_calculated: once the flip index is
// known, reuse it in O(1) on every later call, and only pay for a real
// backward scan again the ONE time the trend actually changes.
datetime g_cachedEventTime1 = 0;   int g_cachedTrend1 = 0;   int g_cachedIdx1 = -1;
datetime g_cachedEventTime2 = 0;   int g_cachedTrend2 = 0;   int g_cachedIdx2 = -1;
datetime g_cachedStructTime = 0;   int g_cachedStructT1 = 0; int g_cachedStructT2 = 0; int g_cachedStructIdx = -1;

//===================== Major/Minor confirmed (committed) state =====================
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
int    gc_MajSupX=-1, gc_MajResX=-1, gc_MinSupX=-1, gc_MinResX=-1;
double gc_MajSupY,    gc_MajResY,    gc_MinSupY,    gc_MinResY;

//===================== Major/Minor working (this-call scratch) state =====================
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
//| Display ATR Trailing Stop value (top-left, colored by trend)     |
//+------------------------------------------------------------------+
void DisplayTrailValue(string name, int yDistance, string prefix, double value, int trend)
{
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, yDistance);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 13);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);

   color txtColor = (trend > 0) ? clrLime : clrRed;

   string text = prefix + DoubleToString(value, _Digits);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, txtColor);
}

//+------------------------------------------------------------------+
//| Compute one trail line into the given buffers                    |
//+------------------------------------------------------------------+
void CalcTrail(string tag, int rates_total, int prev_calculated, int atrPeriod, double keyValue,
               int atrHandle, double &atrBuf[],
               double &trailBuf[], double &colorBuf[], double &trendBuf[],
               const double &close[],
               bool &loggedInsufficientBars, bool &loggedCopyFail)
{
   if (rates_total < atrPeriod + 2)
     {
      if (!loggedInsufficientBars)
        {
         Print(tag, ": waiting for history -- have ", rates_total,
               " bars, need ", atrPeriod + 2, " (ATRPeriod=", atrPeriod, ")");
         loggedInsufficientBars = true;
        }
      return;
     }

   int copied = CopyBuffer(atrHandle, 0, 0, rates_total, atrBuf);
   if (copied <= 0)
     {
      if (!loggedCopyFail)
        {
         Print(tag, ": CopyBuffer failed, returned ", copied, ", error ", GetLastError());
         loggedCopyFail = true;
        }
      return;
     }

   // Incremental, not full-history: trailBuf/colorBuf/trendBuf are real
   // indicator buffers (SetIndexBuffer'd in OnInit), so MT5 keeps every
   // already-computed bar intact between calls -- closed-bar trail values
   // never change once written.
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;

   // Safety margin: always reprocess at least the last SAFETY_REPROCESS_BARS
   // bars regardless of prev_calculated, so an iATR() settling-lag drift on
   // reattach self-corrects instead of staying wrong forever.
   int safety_start = rates_total - SAFETY_REPROCESS_BARS;
   if(safety_start < 0)
      safety_start = 0;
   if(safety_start < start)
      start = safety_start;

   for (int i = start; i < rates_total; i++)
     {
      double src  = close[i];
      double src1 = (i > 0) ? close[i - 1] : close[i];
      double prevStop = (i > 0) ? trailBuf[i - 1] : src;
      int trendPrev   = (i > 0) ? (int)trendBuf[i - 1] : 1;

      double nLoss = keyValue * atrBuf[i];

      double stop;
      if (src > prevStop && src1 > prevStop)
         stop = MathMax(prevStop, src - nLoss);
      else if (src < prevStop && src1 < prevStop)
         stop = MathMin(prevStop, src + nLoss);
      else if (src > prevStop)
         stop = src - nLoss;
      else
         stop = src + nLoss;

      trailBuf[i] = stop;

      int trend = trendPrev;
      if (src1 < prevStop && src > prevStop)
         trend = 1;
      else if (src1 > prevStop && src < prevStop)
         trend = -1;

      colorBuf[i] = (trend > 0) ? 0 : 1;
      trendBuf[i] = trend;
     }
}

//+------------------------------------------------------------------+
//| Chart symbol, or BridgeSymbol override if one is set              |
//+------------------------------------------------------------------+
string EffectiveSymbol()
{
   return (BridgeSymbol == "") ? _Symbol : BridgeSymbol;
}

//+------------------------------------------------------------------+
//| Bar time of the most recent trend flip in ONE line's trend buffer|
//| -- cached (see g_cachedEventTime1/2 above): only checks the GAP   |
//| since the last call (bounded, cheap) instead of the whole history.|
//| Deliberately scans that gap rather than just comparing            |
//| trendBuf[reference_idx] to the old cached trend value -- a bare   |
//| equality check would silently miss a flip-then-flip-back that     |
//| happens entirely within one throttle window (PublishEverySeconds),|
//| which IS possible after a reattach/catch-up jump where            |
//| reference_idx can advance by more than one bar between calls (see |
//| SAFETY_REPROCESS_BARS's own comment for that scenario).           |
//+------------------------------------------------------------------+
datetime FindEventTime(const int reference_idx, const datetime &time[], const double &trendBuf[],
                        datetime &cache_time, int &cache_trend, int &cache_idx)
{
   int current_trend = (int)trendBuf[reference_idx];

   if(cache_idx >= 0 && cache_idx <= reference_idx)
     {
      // Always re-verify at least the last SAFETY_REPROCESS_BARS bars,
      // even if cache_idx has already advanced further than that --
      // CalcTrail itself keeps reprocessing that same trailing window
      // every call (iATR settling-lag self-correction), so a trend
      // value back there could still retroactively change even though
      // this cache already "confirmed" past it once. Bounded (at most
      // SAFETY_REPROCESS_BARS bars), so this stays cheap.
      int scan_from = MathMin(cache_idx + 1, reference_idx - SAFETY_REPROCESS_BARS + 1);
      if(scan_from < 1) scan_from = 1;  // never 0 -- the loop reads i-1, so i must start at >=1

      int flip_at = -1;
      for(int i = scan_from; i <= reference_idx; i++)
         if((int)trendBuf[i] != (int)trendBuf[i - 1])
            flip_at = i;  // keep going -- want the LAST flip in the gap, not the first

      if(flip_at < 0 && current_trend == cache_trend)
        {
         cache_idx = reference_idx;
         return cache_time;  // nothing changed in the gap -- cached value still correct
        }
      if(flip_at >= 0)
        {
         cache_time = time[flip_at];
         cache_trend = current_trend;
         cache_idx = reference_idx;
         return cache_time;
        }
      // flip_at<0 but current_trend != cache_trend shouldn't be reachable
      // (no change anywhere in the gap implies the trend can't differ) --
      // falls through to the full scan below as a safe defensive fallback.
     }

   // First-ever call, or the defensive fallback above -- do the real
   // scan once, then cache the result.
   datetime result = time[0];
   for(int i = reference_idx - 1; i >= 0; i--)
     {
      if((int)trendBuf[i] != current_trend)
        {
         result = time[i + 1];
         break;
        }
     }
   cache_time = result;
   cache_trend = current_trend;
   cache_idx = reference_idx;
   return result;
}

//+------------------------------------------------------------------+
//| STRONG only when both lines agree bullish, WEAK only when both   |
//| agree bearish, UNDECISIVE otherwise.                               |
//+------------------------------------------------------------------+
string CombinedState(int trend1, int trend2)
{
   if(trend1 == 1 && trend2 == 1)
      return "STRONG";
   if(trend1 == -1 && trend2 == -1)
      return "WEAK";
   return "UNDECISIVE";
}

//+------------------------------------------------------------------+
//| Bar time the COMBINED state (above) last actually changed --      |
//| cached the same gap-scanning way as FindEventTime above, keyed on |
//| the (t1,t2) PAIR rather than a single trend value.                |
//+------------------------------------------------------------------+
datetime FindStructureEventTime(const int reference_idx, const datetime &time[],
                                 const double &trend1Buf[], const double &trend2Buf[],
                                 datetime &cache_time, int &cache_t1, int &cache_t2, int &cache_idx)
{
   int t1 = (int)trend1Buf[reference_idx];
   int t2 = (int)trend2Buf[reference_idx];

   if(cache_idx >= 0 && cache_idx <= reference_idx)
     {
      // Same SAFETY_REPROCESS_BARS re-verification margin as
      // FindEventTime above -- see its own comment for why.
      int scan_from = MathMin(cache_idx + 1, reference_idx - SAFETY_REPROCESS_BARS + 1);
      if(scan_from < 1) scan_from = 1;  // never 0 -- the loop reads i-1

      int flip_at = -1;
      for(int i = scan_from; i <= reference_idx; i++)
         if((int)trend1Buf[i] != (int)trend1Buf[i - 1] || (int)trend2Buf[i] != (int)trend2Buf[i - 1])
            flip_at = i;  // want the LAST change in the gap

      if(flip_at < 0 && t1 == cache_t1 && t2 == cache_t2)
        {
         cache_idx = reference_idx;
         return cache_time;
        }
      if(flip_at >= 0)
        {
         cache_time = time[flip_at];
         cache_t1 = t1; cache_t2 = t2; cache_idx = reference_idx;
         return cache_time;
        }
     }

   datetime result = time[0];
   for(int i = reference_idx - 1; i >= 0; i--)
     {
      if((int)trend1Buf[i] != t1 || (int)trend2Buf[i] != t2)
        {
         result = time[i + 1];
         break;
        }
     }
   cache_time = result;
   cache_t1 = t1; cache_t2 = t2; cache_idx = reference_idx;
   return result;
}

//+------------------------------------------------------------------+
//| Publish both ATR lines + combined structure to the bridge --      |
//| unchanged from the original ATR Dual indicator. Major/Minor is    |
//| NOT included here, see header.                                    |
//+------------------------------------------------------------------+
void PublishATRBridgeFile(const int rates_total, const datetime &time[])
{
   if(!PublishToFile)
      return;
   if(TimeCurrent() - g_last_publish_time < PublishEverySeconds)
      return;
   g_last_publish_time = TimeCurrent();

   if(rates_total < 2)
      return;  // need at least one fully closed bar to publish anything
   int closed_idx = rates_total - 2;

   string symbol = EffectiveSymbol();
   int tf_minutes = (int)(PeriodSeconds(_Period) / 60);
   if(tf_minutes <= 0)
      tf_minutes = (int)_Period;

   int trend1 = (int)TrendBuffer[closed_idx];
   int trend2 = (int)TrendBuffer2[closed_idx];
   datetime event_time1 = FindEventTime(closed_idx, time, TrendBuffer,  g_cachedEventTime1, g_cachedTrend1, g_cachedIdx1);
   datetime event_time2 = FindEventTime(closed_idx, time, TrendBuffer2, g_cachedEventTime2, g_cachedTrend2, g_cachedIdx2);
   string structure = CombinedState(trend1, trend2);
   datetime structure_event_time = FindStructureEventTime(closed_idx, time, TrendBuffer, TrendBuffer2,
                                                            g_cachedStructTime, g_cachedStructT1, g_cachedStructT2, g_cachedStructIdx);

   string j = "{";
   j += "\"symbol\":\"" + symbol + "\",";
   j += "\"timeframe_minutes\":" + IntegerToString(tf_minutes) + ",";
   j += "\"updated\":" + IntegerToString((long)TimeCurrent()) + ",";
   j += "\"line1\":{";
   j += "\"trail_stop\":" + DoubleToString(TrailStop[closed_idx], 8) + ",";
   j += "\"trend\":" + IntegerToString(trend1) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time1);
   j += "},";
   j += "\"line2\":{";
   j += "\"trail_stop\":" + DoubleToString(TrailStop2[closed_idx], 8) + ",";
   j += "\"trend\":" + IntegerToString(trend2) + ",";
   j += "\"event_time\":" + IntegerToString((long)event_time2);
   j += "},";
   j += "\"structure\":\"" + structure + "\",";
   j += "\"structure_event_time\":" + IntegerToString((long)structure_event_time);
   j += "}";

   FolderCreate(FileBridgeFolder, FILE_COMMON);

   const string final_name = FileBridgeFolder + "\\ATRSTATE_DUAL_" + symbol + "_" + IntegerToString(tf_minutes) + ".json";
   const string tmp_name   = final_name + ".tmp";

   int handle = FileOpen(tmp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      Print("ATR dual bridge file write failed: ", tmp_name, " | error=", GetLastError());
      return;
     }

   FileWriteString(handle, j);
   FileClose(handle);

   // Retried a few times -- confirmed live, error 5004 (cannot open
   // file) on this exact FileMove: a Python reader (this bridge is read
   // continuously, sometimes by more than one process at once) can have
   // final_name open for reading at the exact instant this tries to
   // replace it, and Windows throws a sharing violation on the rename.
   // These windows are only ever as long as a single file read (a few
   // ms), so a handful of immediate retries clears the vast majority of
   // them without meaningfully delaying the publish (this whole function
   // is already throttled to once every PublishEverySeconds).
   bool moved = false;
   int last_error = 0;
   for(int attempt = 0; attempt < 5 && !moved; attempt++)
     {
      if(attempt > 0)
         Sleep(10);
      moved = FileMove(tmp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE);
      if(!moved)
         last_error = GetLastError();
     }
   if(!moved)
      Print("ATR dual bridge file publish failed to finalize after retries: ", final_name, " | error=", last_error);
}

//+------------------------------------------------------------------+
//| Indicator initialization                                         |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Plot buffers (data+color) must come first, in plot order -- MT5 maps
   // plot N to the Nth group of buffers by raw index, NOT filtered by type.
   SetIndexBuffer(0, TrailStop,  INDICATOR_DATA);
   SetIndexBuffer(1, ColorBuffer, INDICATOR_COLOR_INDEX);

   SetIndexBuffer(2, TrailStop2, INDICATOR_DATA);
   SetIndexBuffer(3, ColorBuffer2, INDICATOR_COLOR_INDEX);

   SetIndexBuffer(4, TrendBuffer,  INDICATOR_CALCULATIONS);
   SetIndexBuffer(5, TrendBuffer2, INDICATOR_CALCULATIONS);

   ATRHandle = iATR(NULL, 0, ATRPeriod);
   if (ATRHandle == INVALID_HANDLE)
     {
      Print("ATR handle error (line 1, period ", ATRPeriod, ")");
      return(INIT_FAILED);
     }

   ATRHandle2 = iATR(NULL, 0, ATRPeriod2);
   if (ATRHandle2 == INVALID_HANDLE)
     {
      Print("ATR handle error (line 2, period ", ATRPeriod2, ")");
      return(INIT_FAILED);
     }

   ResetAllConfirmed();

   IndicatorSetString(INDICATOR_SHORTNAME, "ATR Trail Dual + Major/Minor");

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Indicator calculation                                            |
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
   //===================== ATR Trail Dual -- always runs =====================
   CalcTrail("Line1(ATR" + IntegerToString(ATRPeriod) + ")", rates_total, prev_calculated, ATRPeriod,  KeyValue,  ATRHandle,  ATRBuffer,  TrailStop,  ColorBuffer,  TrendBuffer,  close, loggedInsufficientBars1, loggedCopyFail1);
   CalcTrail("Line2(ATR" + IntegerToString(ATRPeriod2) + ")", rates_total, prev_calculated, ATRPeriod2, KeyValue2, ATRHandle2, ATRBuffer2, TrailStop2, ColorBuffer2, TrendBuffer2, close, loggedInsufficientBars2, loggedCopyFail2);

   if (rates_total >= ATRPeriod + 2)
      DisplayTrailValue(LABEL_NAME, 20, "ATR" + IntegerToString(ATRPeriod) + " Trail: ",
                         TrailStop[rates_total - 1], (int)TrendBuffer[rates_total - 1]);

   if (rates_total >= ATRPeriod2 + 2)
      DisplayTrailValue(LABEL_NAME2, 40, "ATR" + IntegerToString(ATRPeriod2) + " Trail: ",
                         TrailStop2[rates_total - 1], (int)TrendBuffer2[rates_total - 1]);

   if (rates_total >= MathMax(ATRPeriod, ATRPeriod2) + 2)
      PublishATRBridgeFile(rates_total, time);

   //===================== Major/Minor structure -- togglable =====================
   // mm_was_enabled's initializer matches EnableMajorMinor's own default (true)
   // so no spurious "just turned off" cleanup fires on the very first bar.
   static bool mm_was_enabled = true;

   if(!EnableMajorMinor)
     {
      if(mm_was_enabled)
        {
         // Just switched off -- clear whatever's currently drawn and reset
         // the redraw trackers so a later re-enable draws fresh instead of
         // silently no-opping against stale (x,y) memory.
         ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
         ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
         g_drawnMajorSupX=-1000000; g_drawnMajorResX=-1000000; g_drawnMinorSupX=-1000000; g_drawnMinorResX=-1000000;
         mm_was_enabled=false;
        }
     }
   else
     {
      mm_was_enabled=true;

      if(rates_total >= 2*PivotPeriod+2)
        {
         if(prev_calculated<=0) ResetAllConfirmed();

         CopyConfirmedToWorking();

         int startBar=gc_ConfirmedUpTo+1;
         if(startBar<0) startBar=0;

         for(int i=startBar; i<rates_total; i++)
           {
            ProcessBar(i, rates_total, PivotPeriod, high, low, close);
            TrackLatestPositions();

            if(i<rates_total-1)
              {
               CommitWorkingToConfirmed();
               gc_ConfirmedUpTo=i;
              }
           }

         DrawFromWorking(time, rates_total);
        }
     }

   return(rates_total);
}

//+------------------------------------------------------------------+
//| Cleanup                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectDelete(0, LABEL_NAME);
   ObjectDelete(0, LABEL_NAME2);
   ObjectDelete(0,NAME_MAJOR_SUPPORT); ObjectDelete(0,NAME_MAJOR_RESISTANCE);
   ObjectDelete(0,NAME_MINOR_SUPPORT); ObjectDelete(0,NAME_MINOR_RESISTANCE);
}
