//+------------------------------------------------------------------+
//| OB_MTF_FreshTrader_EA.mq5                                        |
//| v2.26: frozen HTF detection-only entry classification M5/M3    |
//+------------------------------------------------------------------+
#property strict
#property version   "2.26"

#include <Trade/Trade.mqh>
CTrade Trade;

input string InpGVPrefix               = "OBSTATE_";
input ENUM_TIMEFRAMES InpHTF1          = PERIOD_M5;
input ENUM_TIMEFRAMES InpHTF2          = PERIOD_M3;
input ENUM_TIMEFRAMES InpLTF           = PERIOD_M1;
input double InpLots                   = 0.01;
input double InpSLBufferPrice          = 0.50;
input double InpMinimumSLDistance      = 7.00; // Minimum SL distance; logical SL is kept when farther
input double InpHTFMarketDistanceMax   = 4.00;  // Frozen first-detection distance below this = market entry
input double InpHTFPullbackDistanceMin = 7.00;  // Frozen first-detection distance must be above this for pullback
input double InpHTFPullbackDistanceMax = 14.00; // Frozen first-detection distance up to this = pullback entry
input ulong  InpMagicNumber            = 26071502;
input int    InpDeviationPoints        = 30;
input bool   InpEnableTrading          = true;
input bool   InpEnableHTFEntries       = true;
input bool   InpEnableLTFEntries       = true;
input bool   InpEnableTrailing         = true;
input bool   InpShowPanel              = true;
input bool   InpTreatStartupAsBaseline = true;
input bool   InpShowResetButtons        = true;

enum SETUP_SOURCE { SOURCE_NONE=0, SOURCE_HTF=1, SOURCE_LTF=2 };
enum ENTRY_MODE   { ENTRY_NONE=0, ENTRY_MARKET=1, ENTRY_PENDING=2 };

struct OBState
{
   bool valid;
   int dir;
   ENUM_TIMEFRAMES tf;
   datetime origin_time;
   datetime detected_time;
   double detected_price;
   double high;
   double low;
   bool virgin;
   string key;
   uint hash;
};

struct TFTracker
{
   ENUM_TIMEFRAMES tf;
   bool initialized;
   OBState latest;
   OBState previous;
};

struct TradeSetup
{
   bool valid;
   SETUP_SOURCE source;
   ENTRY_MODE mode;
   int dir;
   datetime event_time;
   double entry;
   double logical_sl;
   uint zone_hash;
   string zone_key;
   string label;
};

TFTracker G_HTF1, G_HTF2, G_LTF;
int G_BiasDir=0;
datetime G_BiasTime=0;
string G_BiasSource="NONE";
OBState G_BiasOB;
TradeSetup G_QueuedSetup;
string G_MetaHashGV,G_MetaDirGV,G_MetaSourceGV,G_MetaTimeGV,G_MetaTFGV;

bool  G_EADeleteExpected=false;
ulong G_EADeleteTicket=0;
bool  G_EACloseExpected=false;
ulong G_ActivePendingTicket=0;
uint  G_ActivePendingHash=0;
int   G_ActivePendingTFMinutes=0;
uint  G_ActivePositionHash=0;
int   G_ActivePositionTFMinutes=0;

// Manual-cancel blocks are latched in memory and mirrored to terminal
// Global Variables. They can be released only by the matching reset button.
uint G_ManualBlockM5=0;
uint G_ManualBlockM3=0;
uint G_ManualBlockM1=0;

void ClearEADeleteExpectation()
{
   G_EADeleteExpected=false;
   G_EADeleteTicket=0;
}

void ClearActivePendingSnapshot()
{
   G_ActivePendingTicket=0;
   G_ActivePendingHash=0;
   G_ActivePendingTFMinutes=0;
}

void ClearActivePositionSnapshot()
{
   G_ActivePositionHash=0;
   G_ActivePositionTFMinutes=0;
}

ENUM_TIMEFRAMES TFByMinutes(const int tf_minutes)
{
   if(tf_minutes==TFMinutes(InpLTF))  return InpLTF;
   if(tf_minutes==TFMinutes(InpHTF2)) return InpHTF2;
   if(tf_minutes==TFMinutes(InpHTF1)) return InpHTF1;
   return PERIOD_CURRENT;
}

void ProcessQueuedSetup();
string G_ButtonM5="OBEA_RESET_M5",G_ButtonM3="OBEA_RESET_M3",G_ButtonM1="OBEA_RESET_M1";

void ClearOB(OBState &ob)
{
   ob.valid=false; ob.dir=0; ob.tf=PERIOD_CURRENT; ob.origin_time=0;
   ob.detected_time=0; ob.detected_price=0.0; ob.high=0.0; ob.low=0.0;
   ob.virgin=false; ob.key=""; ob.hash=0;
}

void ClearSetup(TradeSetup &s)
{
   s.valid=false; s.source=SOURCE_NONE; s.mode=ENTRY_NONE; s.dir=0;
   s.event_time=0; s.entry=0.0; s.logical_sl=0.0; s.zone_hash=0;
   s.zone_key=""; s.label="";
}

int TFMinutes(const ENUM_TIMEFRAMES tf)
{
   int seconds=PeriodSeconds(tf);
   return (seconds>0 ? seconds/60 : (int)tf);
}

string TFText(const ENUM_TIMEFRAMES tf){ return "M"+IntegerToString(TFMinutes(tf)); }

string SafeSymbol()
{
   string s=_Symbol;
   StringReplace(s,".","_"); StringReplace(s,"#","_"); StringReplace(s," ","_");
   return s;
}

string GVBase(const ENUM_TIMEFRAMES tf)
{
   return InpGVPrefix+_Symbol+"_"+IntegerToString(TFMinutes(tf));
}

bool ReadGV(const string name,double &value)
{
   if(!GlobalVariableCheck(name)) return false;
   value=GlobalVariableGet(name); return true;
}

double NormPrice(const double price)
{
   return NormalizeDouble(price,(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS));
}

uint Hash32(const string text)
{
   uint hash=2166136261;
   for(int i=0;i<StringLen(text);i++){ hash^=(uint)StringGetCharacter(text,i); hash*=16777619; }
   return hash;
}

string BuildZoneKey(const ENUM_TIMEFRAMES tf,const int dir,const datetime origin_time,const double high,const double low)
{
   return TFText(tf)+"|"+IntegerToString(dir)+"|"+IntegerToString((long)origin_time)+"|"+
          DoubleToString(high,_Digits)+"|"+DoubleToString(low,_Digits);
}

datetime EventTime(const OBState &ob){ return (ob.detected_time>0 ? ob.detected_time : ob.origin_time); }
bool SameOB(const OBState &a,const OBState &b){ return (a.valid && b.valid && a.key==b.key); }

bool ReadLatestOB(const ENUM_TIMEFRAMES tf,OBState &out)
{
   ClearOB(out);
   string base=GVBase(tf);
   double bias,high,low,virgin,origin,detected_time=0.0,detected_price=0.0;
   if(!ReadGV(base+"_BIAS",bias)) return false;
   if(!ReadGV(base+"_LATEST_HIGH",high)) return false;
   if(!ReadGV(base+"_LATEST_LOW",low)) return false;
   if(!ReadGV(base+"_LATEST_VIRGIN",virgin)) return false;
   if(!ReadGV(base+"_LATEST_TIME",origin)) return false;
   ReadGV(base+"_LATEST_DETECTED_TIME",detected_time);
   ReadGV(base+"_LATEST_DETECTED_PRICE",detected_price);
   int dir=(int)MathRound(bias);
   if(dir!=1 && dir!=-1) return false;
   if(high<=low || origin<=0) return false;
   out.valid=true; out.dir=dir; out.tf=tf; out.origin_time=(datetime)((long)origin);
   out.detected_time=(datetime)((long)detected_time); out.detected_price=detected_price;
   out.high=high; out.low=low; out.virgin=(virgin>0.5);
   out.key=BuildZoneKey(tf,dir,out.origin_time,high,low); out.hash=Hash32(out.key);
   return true;
}

bool ReadPreviousOB(const ENUM_TIMEFRAMES tf,OBState &out)
{
   ClearOB(out);
   string base=GVBase(tf);
   double bias,high,low,virgin,origin,detected_time=0.0,detected_price=0.0;
   if(!ReadGV(base+"_PREVIOUS_BIAS",bias)) return false;
   if(!ReadGV(base+"_PREVIOUS_HIGH",high)) return false;
   if(!ReadGV(base+"_PREVIOUS_LOW",low)) return false;
   if(!ReadGV(base+"_PREVIOUS_VIRGIN",virgin)) return false;
   if(!ReadGV(base+"_PREVIOUS_TIME",origin)) return false;
   ReadGV(base+"_PREVIOUS_DETECTED_TIME",detected_time);
   ReadGV(base+"_PREVIOUS_DETECTED_PRICE",detected_price);
   int dir=(int)MathRound(bias);
   if(dir!=1 && dir!=-1) return false;
   if(high<=low || origin<=0) return false;
   out.valid=true; out.dir=dir; out.tf=tf; out.origin_time=(datetime)((long)origin);
   out.detected_time=(datetime)((long)detected_time); out.detected_price=detected_price;
   out.high=high; out.low=low; out.virgin=(virgin>0.5);
   out.key=BuildZoneKey(tf,dir,out.origin_time,high,low); out.hash=Hash32(out.key);
   return true;
}

bool UpdateTracker(TFTracker &tracker,bool &new_event)
{
   new_event=false;
   OBState incoming, published_previous;
   if(!ReadLatestOB(tracker.tf,incoming)) return false;
   bool has_previous=ReadPreviousOB(tracker.tf,published_previous);

   if(!tracker.initialized)
   {
      tracker.initialized=true;
      tracker.latest=incoming;
      if(has_previous) tracker.previous=published_previous;
      if(!InpTreatStartupAsBaseline) new_event=true;
      return true;
   }

   if(!SameOB(tracker.latest,incoming))
   {
      tracker.previous=(has_previous?published_previous:tracker.latest);
      tracker.latest=incoming;
      new_event=true;
      return true;
   }

   tracker.latest.virgin=incoming.virgin;
   tracker.latest.detected_time=incoming.detected_time;
   tracker.latest.detected_price=incoming.detected_price;
   tracker.latest.high=incoming.high;
   tracker.latest.low=incoming.low;
   if(has_previous) tracker.previous=published_previous;
   return true;
}

void RefreshBias()
{
   G_BiasDir=0; G_BiasTime=0; G_BiasSource="NONE"; ClearOB(G_BiasOB);
   bool a=G_HTF1.latest.valid,b=G_HTF2.latest.valid;
   if(!a && !b) return;
   if(a && (!b || EventTime(G_HTF1.latest)>=EventTime(G_HTF2.latest)))
   {
      G_BiasOB=G_HTF1.latest; G_BiasDir=G_HTF1.latest.dir;
      G_BiasTime=EventTime(G_HTF1.latest); G_BiasSource=TFText(G_HTF1.tf); return;
   }
   G_BiasOB=G_HTF2.latest; G_BiasDir=G_HTF2.latest.dir;
   G_BiasTime=EventTime(G_HTF2.latest); G_BiasSource=TFText(G_HTF2.tf);
}


string BlockedGV(const ENUM_TIMEFRAMES tf)
{
   return "OBEA_BLOCK_"+IntegerToString((long)InpMagicNumber)+"_"+SafeSymbol()+"_"+IntegerToString(TFMinutes(tf));
}

uint GetManualBlockSlot(const ENUM_TIMEFRAMES tf)
{
   if(tf==InpHTF1) return G_ManualBlockM5;
   if(tf==InpHTF2) return G_ManualBlockM3;
   return G_ManualBlockM1;
}

void SetManualBlockSlot(const ENUM_TIMEFRAMES tf,const uint value)
{
   if(tf==InpHTF1) G_ManualBlockM5=value;
   else if(tf==InpHTF2) G_ManualBlockM3=value;
   else G_ManualBlockM1=value;
}

void LoadManualBlock(const ENUM_TIMEFRAMES tf)
{
   const string name=BlockedGV(tf);
   const uint value=(GlobalVariableCheck(name) ?
                     (uint)MathRound(GlobalVariableGet(name)) : 0);
   SetManualBlockSlot(tf,value);
}

void PersistManualBlock(const ENUM_TIMEFRAMES tf)
{
   const uint value=GetManualBlockSlot(tf);
   const string name=BlockedGV(tf);
   if(value!=0)
   {
      // Recreate the terminal GV if anything external removed it.
      if(!GlobalVariableCheck(name) ||
         (uint)MathRound(GlobalVariableGet(name))!=value)
         GlobalVariableSet(name,(double)value);
   }
}

uint BlockedHash(const ENUM_TIMEFRAMES tf);

bool IsBlocked(const ENUM_TIMEFRAMES tf,const uint hash)
{
   uint value=GetManualBlockSlot(tf);
   if(value==0 && GlobalVariableCheck(BlockedGV(tf)))
   {
      value=(uint)MathRound(GlobalVariableGet(BlockedGV(tf)));
      SetManualBlockSlot(tf,value);
   }

   // Manual blocking belongs to the exact setup that the user cancelled or
   // manually closed. A different, genuinely new zone on the same timeframe
   // must remain eligible without requiring a reset.
   return (value!=0 && hash!=0 && value==hash);
}

void ReleaseStaleManualBlockForNewZone(const ENUM_TIMEFRAMES tf,const OBState &latest)
{
   const uint blocked_hash=BlockedHash(tf);
   if(blocked_hash==0 || !latest.valid || latest.hash==0 || latest.hash==blocked_hash)
      return;

   // The publisher now reports a different latest zone. The old exact-zone
   // block must not appear as a timeframe-wide lock. Do not clear the old
   // zone's traded history; simply release its manual block latch.
   SetManualBlockSlot(tf,0);
   const string name=BlockedGV(tf);
   if(GlobalVariableCheck(name))
      GlobalVariableDel(name);

   Print("New zone released old exact-zone manual block",
         " | TF=",TFText(tf),
         " | old hash=",blocked_hash,
         " | new hash=",latest.hash,
         " | new event=",TimeToString(EventTime(latest),TIME_DATE|TIME_SECONDS));
}

void BlockZone(const ENUM_TIMEFRAMES tf,const uint hash)
{
   if(hash==0) return;
   SetManualBlockSlot(tf,hash);
   GlobalVariableSet(BlockedGV(tf),(double)hash);
   Print("Setup manually blocked and latched until reset: ",TFText(tf)," hash=",hash);
}

// Forward declarations used by reset handling.
string TradedGV(const uint hash);
bool ClearTradedMark(const uint hash);

uint ReleaseBlockedZone(const ENUM_TIMEFRAMES tf)
{
   const string name=BlockedGV(tf);
   uint released_hash=GetManualBlockSlot(tf);

   if(released_hash==0 && GlobalVariableCheck(name))
      released_hash=(uint)MathRound(GlobalVariableGet(name));

   SetManualBlockSlot(tf,0);
   if(GlobalVariableCheck(name))
      GlobalVariableDel(name);

   // Explicit reset means the user permits this exact blocked setup to be
   // considered again. Clear only its traded marker; never wipe the traded
   // history of other M5/M3/M1 zones. BuildHTFSetup/ValidLTFSequence will
   // still require the zone to be current, bias-valid and virgin.
   if(released_hash!=0)
      ClearTradedMark(released_hash);

   ClearSetup(G_QueuedSetup);

   // A manually cancelled order may leave metadata until its history event
   // finishes. When no managed exposure exists, clear that stale metadata so
   // the released setup can be submitted immediately.
   if(!HasPosition() && !HasPending())
      ClearExposureMeta();

   Print("Manual setup block released and exact zone re-armed: ",TFText(tf),
         " hash=",released_hash);
   return released_hash;
}

uint BlockedHash(const ENUM_TIMEFRAMES tf)
{
   uint value=GetManualBlockSlot(tf);
   if(value==0 && GlobalVariableCheck(BlockedGV(tf)))
   {
      value=(uint)MathRound(GlobalVariableGet(BlockedGV(tf)));
      SetManualBlockSlot(tf,value);
   }
   return value;
}

int SetupTFMinutes(const TradeSetup &setup)
{
   if(StringFind(setup.zone_key,"M5|")==0) return 5;
   if(StringFind(setup.zone_key,"M3|")==0) return 3;
   if(StringFind(setup.zone_key,"M1|")==0) return 1;
   return 0;
}

string TradedGV(const uint hash)
{
   return "OBEA_TR_"+IntegerToString((long)InpMagicNumber)+"_"+SafeSymbol()+"_"+IntegerToString((long)hash);
}

bool IsTraded(const uint hash){ return (hash!=0 && GlobalVariableCheck(TradedGV(hash))); }
void MarkTraded(const uint hash){ if(hash!=0) GlobalVariableSet(TradedGV(hash),(double)TimeCurrent()); }

// Reset must re-arm only the exact setup that the user explicitly released.
// Virgin-state validation remains the final eligibility gate, so removing this
// one traded marker cannot revive a zone that the publisher reports non-virgin.
bool ClearTradedMark(const uint hash)
{
   if(hash==0) return false;
   const string name=TradedGV(hash);
   if(!GlobalVariableCheck(name)) return true;
   ResetLastError();
   if(GlobalVariableDel(name))
   {
      Print("Reset cleared traded mark for exact zone: hash=",hash);
      return true;
   }
   Print("Reset could not clear traded mark: hash=",hash,
         " | error=",GetLastError());
   return false;
}

string FrozenGV(const uint hash,const string field)
{
   return "OBEA_FRZ_"+IntegerToString((long)InpMagicNumber)+"_"+SafeSymbol()+"_"+
          IntegerToString((long)hash)+"_"+field;
}

// Freeze the first valid detection snapshot for a zone.  The publisher may
// refresh its DETECTED_TIME / DETECTED_PRICE values while the same rectangle
// remains current; those refreshes must never move an already calculated setup.
bool GetFrozenHTFDetection(const OBState &ob,datetime &frozen_time,double &frozen_price)
{
   frozen_time=0;
   frozen_price=0.0;
   if(!ob.valid || ob.hash==0) return false;

   const string time_gv =FrozenGV(ob.hash,"DT");
   const string price_gv=FrozenGV(ob.hash,"DP");

   if(GlobalVariableCheck(time_gv) && GlobalVariableCheck(price_gv))
   {
      frozen_time =(datetime)((long)GlobalVariableGet(time_gv));
      frozen_price=GlobalVariableGet(price_gv);
      return (frozen_time>0 && frozen_price>0.0);
   }

   if(ob.detected_time<=0 || ob.detected_price<=0.0) return false;

   frozen_time =ob.detected_time;
   frozen_price=ob.detected_price;
   GlobalVariableSet(time_gv,(double)frozen_time);
   GlobalVariableSet(price_gv,frozen_price);

   Print("HTF detection snapshot frozen",
         " | ",TFText(ob.tf),
         " | hash=",ob.hash,
         " | time=",TimeToString(frozen_time,TIME_DATE|TIME_SECONDS),
         " | price=",DoubleToString(frozen_price,_Digits));
   return true;
}

// Freeze event time for every setup source so a publisher refresh cannot make
// the same zone look newer on every tick.
datetime FrozenEventTime(const uint hash,const datetime proposed_time)
{
   if(hash==0 || proposed_time<=0) return proposed_time;
   const string name=FrozenGV(hash,"EVT");
   if(GlobalVariableCheck(name))
      return (datetime)((long)GlobalVariableGet(name));
   GlobalVariableSet(name,(double)proposed_time);
   return proposed_time;
}

void SaveExposureMeta(const TradeSetup &setup)
{
   GlobalVariableSet(G_MetaHashGV,(double)setup.zone_hash);
   GlobalVariableSet(G_MetaDirGV,(double)setup.dir);
   GlobalVariableSet(G_MetaSourceGV,(double)setup.source);
   GlobalVariableSet(G_MetaTimeGV,(double)setup.event_time);
   GlobalVariableSet(G_MetaTFGV,(double)SetupTFMinutes(setup));
}

void ClearExposureMeta()
{
   if(GlobalVariableCheck(G_MetaHashGV)) GlobalVariableDel(G_MetaHashGV);
   if(GlobalVariableCheck(G_MetaDirGV)) GlobalVariableDel(G_MetaDirGV);
   if(GlobalVariableCheck(G_MetaSourceGV)) GlobalVariableDel(G_MetaSourceGV);
   if(GlobalVariableCheck(G_MetaTimeGV)) GlobalVariableDel(G_MetaTimeGV);
   if(GlobalVariableCheck(G_MetaTFGV)) GlobalVariableDel(G_MetaTFGV);
}

uint MetaHash(){ return GlobalVariableCheck(G_MetaHashGV)?(uint)MathRound(GlobalVariableGet(G_MetaHashGV)):0; }
datetime MetaTime(){ return GlobalVariableCheck(G_MetaTimeGV)?(datetime)((long)GlobalVariableGet(G_MetaTimeGV)):0; }
int MetaTFMinutes(){ return GlobalVariableCheck(G_MetaTFGV)?(int)MathRound(GlobalVariableGet(G_MetaTFGV)):0; }

bool ManagedPosition(ENUM_POSITION_TYPE &type,double &open_price,double &sl,double &tp)
{
   if(!PositionSelect(_Symbol)) return false;
   if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) return false;
   type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   open_price=PositionGetDouble(POSITION_PRICE_OPEN); sl=PositionGetDouble(POSITION_SL); tp=PositionGetDouble(POSITION_TP);
   return true;
}

bool ManagedPending(ulong &ticket,ENUM_ORDER_TYPE &type,double &price)
{
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong t=OrderGetTicket(i); if(t==0) continue;
      if(OrderGetString(ORDER_SYMBOL)!=_Symbol) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC)!=InpMagicNumber) continue;
      ENUM_ORDER_TYPE ot=(ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot!=ORDER_TYPE_BUY_LIMIT && ot!=ORDER_TYPE_BUY_STOP && ot!=ORDER_TYPE_SELL_LIMIT && ot!=ORDER_TYPE_SELL_STOP) continue;
      ticket=t; type=ot; price=OrderGetDouble(ORDER_PRICE_OPEN); return true;
   }
   return false;
}

bool ParseSetupIdentityFromComment(const string comment,uint &hash,int &tf_minutes)
{
   hash=0;
   tf_minutes=0;

   if(StringFind(comment,"OBEA|")!=0)
      return false;

   if(StringFind(comment,"HTF-"+TFText(InpHTF1)+"|")>=0)
      tf_minutes=TFMinutes(InpHTF1);
   else if(StringFind(comment,"HTF-"+TFText(InpHTF2)+"|")>=0)
      tf_minutes=TFMinutes(InpHTF2);
   else if(StringFind(comment,"LTF-"+TFText(InpLTF)+"|")>=0)
      tf_minutes=TFMinutes(InpLTF);

   const int first_pipe=StringFind(comment,"|");
   const int second_pipe=(first_pipe>=0 ? StringFind(comment,"|",first_pipe+1) : -1);
   if(second_pipe<0 || second_pipe+1>=StringLen(comment))
      return false;

   const long parsed_hash=StringToInteger(StringSubstr(comment,second_pipe+1));
   if(parsed_hash<=0)
      return false;

   hash=(uint)parsed_hash;
   return (tf_minutes>0);
}

datetime FrozenEventTimeByHash(const uint hash)
{
   if(hash==0) return 0;
   const string name=FrozenGV(hash,"EVT");
   if(!GlobalVariableCheck(name)) return 0;
   return (datetime)((long)GlobalVariableGet(name));
}

bool ResolveManagedPendingIdentity(const ulong ticket,uint &hash,int &tf_minutes,datetime &event_time)
{
   hash=0;
   tf_minutes=0;
   event_time=0;

   // First use the in-memory snapshot only when it belongs to this ticket.
   if(G_ActivePendingTicket==ticket)
   {
      hash=G_ActivePendingHash;
      tf_minutes=G_ActivePendingTFMinutes;
   }

   // Then use persistent metadata, but never let a zero overwrite a valid value.
   const uint meta_hash=MetaHash();
   const int meta_tf=MetaTFMinutes();
   if(hash==0 && meta_hash!=0) hash=meta_hash;
   if(tf_minutes<=0 && meta_tf>0) tf_minutes=meta_tf;

   // The live order comment is the authoritative recovery path when terminal
   // metadata was cleared or briefly unavailable.
   if(OrderSelect(ticket))
   {
      uint comment_hash=0;
      int comment_tf=0;
      if(ParseSetupIdentityFromComment(OrderGetString(ORDER_COMMENT),comment_hash,comment_tf))
      {
         hash=comment_hash;
         tf_minutes=comment_tf;
      }
   }

   if(hash!=0)
   {
      if(meta_hash==hash) event_time=MetaTime();
      if(event_time<=0) event_time=FrozenEventTimeByHash(hash);
   }

   if(hash==0 || tf_minutes<=0)
      return false;

   // Repair both the live snapshot and persistent metadata immediately.
   G_ActivePendingTicket=ticket;
   G_ActivePendingHash=hash;
   G_ActivePendingTFMinutes=tf_minutes;
   GlobalVariableSet(G_MetaHashGV,(double)hash);
   GlobalVariableSet(G_MetaTFGV,(double)tf_minutes);
   if(event_time>0) GlobalVariableSet(G_MetaTimeGV,(double)event_time);
   return true;
}

void RefreshActivePendingSnapshot()
{
   ulong ticket;
   ENUM_ORDER_TYPE type;
   double price;

   if(!ManagedPending(ticket,type,price))
      return;

   uint hash=0;
   int tf_minutes=0;
   datetime event_time=0;
   G_ActivePendingTicket=ticket;

   if(!ResolveManagedPendingIdentity(ticket,hash,tf_minutes,event_time))
   {
      // Never destroy a previously valid snapshot merely because one refresh
      // cannot resolve metadata.
      Print("Managed pending identity unresolved; holding order unchanged",
            " | ticket=",ticket);
   }
}

bool HasPosition(){ ENUM_POSITION_TYPE t; double a,b,c; return ManagedPosition(t,a,b,c); }
bool HasPending(){ ulong t; ENUM_ORDER_TYPE ot; double p; return ManagedPending(t,ot,p); }

int ExposureDirection()
{
   ENUM_POSITION_TYPE pt; double a,b,c;
   if(ManagedPosition(pt,a,b,c)) return (pt==POSITION_TYPE_BUY?1:-1);
   ulong t; ENUM_ORDER_TYPE ot; double p;
   if(ManagedPending(t,ot,p)) return (ot==ORDER_TYPE_BUY_LIMIT || ot==ORDER_TYPE_BUY_STOP)?1:-1;
   return 0;
}

bool CancelPending()
{
   // One delete request at a time.  Wait for OnTradeTransaction before any
   // replacement order is submitted.
   if(G_EADeleteExpected) return false;

   ulong ticket;
   ENUM_ORDER_TYPE type;
   double price;

   if(!ManagedPending(ticket,type,price))
   {
      ClearExposureMeta();
      ClearActivePendingSnapshot();
      return true;
   }

   RefreshActivePendingSnapshot();

   uint resolved_hash=0;
   int resolved_tf=0;
   datetime resolved_time=0;
   if(!ResolveManagedPendingIdentity(ticket,resolved_hash,resolved_tf,resolved_time))
   {
      Print("Pending deletion blocked: managed order identity unresolved",
            " | ticket=",ticket);
      return false;
   }

   G_EADeleteExpected=true;
   G_EADeleteTicket=ticket;

   Print("EA pending deletion requested",
         " | ticket=",ticket,
         " | hash=",resolved_hash,
         " | TF=M",resolved_tf);

   if(!Trade.OrderDelete(ticket))
   {
      Print("Pending cancellation failed: ",
            Trade.ResultRetcode()," ",
            Trade.ResultRetcodeDescription());
      ClearEADeleteExpectation();
      return false;
   }

   return true;
}

bool ClosePosition()
{
   ENUM_POSITION_TYPE type; double a,b,c;
   if(!ManagedPosition(type,a,b,c)) return true;

   // This flag prevents an EA-requested bias exit from being interpreted as a
   // manual close. It is cleared when the matching exit deal is received.
   G_EACloseExpected=true;
   if(!Trade.PositionClose(_Symbol,InpDeviationPoints))
   {
      G_EACloseExpected=false;
      Print("Position close failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription());
      return false;
   }
   return true;
}

double ApplyMinimumSL(const int dir,const double entry,const double logical_sl)
{
   // Used by LTF setups: preserve the logical SL unless it is closer than
   // the configured minimum distance.
   if(dir==1)
   {
      if(entry-logical_sl<InpMinimumSLDistance) return NormPrice(entry-InpMinimumSLDistance);
      return NormPrice(logical_sl);
   }
   if(logical_sl-entry<InpMinimumSLDistance) return NormPrice(entry+InpMinimumSLDistance);
   return NormPrice(logical_sl);
}

double ResolveInitialSL(const TradeSetup &setup,const double entry)
{
   // HTF and LTF both use the logical OB-based SL first.
   // The configured distance is only a minimum floor:
   //   logical distance < minimum -> extend SL to exactly the minimum
   //   logical distance >= minimum -> preserve the logical SL
   return ApplyMinimumSL(setup.dir,entry,setup.logical_sl);
}

bool SLGeometryValid(const int dir,const double entry,const double sl)
{
   if(dir==1 && sl>=entry) return false;
   if(dir==-1 && sl<=entry) return false;
   double broker_min=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   if(broker_min<=0.0) return true;
   return (dir==1 ? entry-sl>=broker_min : sl-entry>=broker_min);
}

double CurrentHTFDistance(const OBState &ob)
{
   if(ob.dir==1)
      return SymbolInfoDouble(_Symbol,SYMBOL_ASK)-ob.high;

   return ob.low-SymbolInfoDouble(_Symbol,SYMBOL_BID);
}

bool BuildHTFSetup(const OBState &ob,TradeSetup &setup)
{
   ClearSetup(setup);
   if(!InpEnableHTFEntries || !ob.valid || ob.dir!=G_BiasDir || !ob.virgin ||
      IsTraded(ob.hash) || IsBlocked(ob.tf,ob.hash))
      return false;

   datetime frozen_detection_time=0;
   double frozen_detection_price=0.0;
   if(!GetFrozenHTFDetection(ob,frozen_detection_time,frozen_detection_price))
      return false;

   double distance=0.0,logical_sl=0.0;
   if(ob.dir==1)
   {
      distance=frozen_detection_price-ob.high;
      logical_sl=ob.low-InpSLBufferPrice;
   }
   else
   {
      distance=ob.low-frozen_detection_price;
      logical_sl=ob.high+InpSLBufferPrice;
   }

   if(distance<0.0 || distance>InpHTFPullbackDistanceMax) return false;

   setup.valid=true;
   setup.source=SOURCE_HTF;
   setup.dir=ob.dir;
   setup.event_time=FrozenEventTime(ob.hash,frozen_detection_time);
   setup.logical_sl=logical_sl;
   setup.zone_hash=ob.hash;
   setup.zone_key=ob.key;
   setup.label="HTF-"+TFText(ob.tf);

   if(distance<InpHTFMarketDistanceMax)
   {
      // Fresh detection inside the market-entry band. Classification is
      // frozen from the original detection distance.
      setup.mode=ENTRY_MARKET;
      setup.entry=0.0;
   }
   else if(distance>InpHTFPullbackDistanceMin &&
           distance<=InpHTFPullbackDistanceMax)
   {
      // Fresh detection inside the pullback band. The half-distance entry is
      // calculated once from the frozen detection price and never follows
      // later market-price movement.
      setup.mode=ENTRY_PENDING;
      setup.entry=(ob.dir==1
                   ? ob.high+distance*0.5
                   : ob.low-distance*0.5);
   }
   else
   {
      // Detection distances from the market limit through the pullback
      // minimum are intentionally ignored. Exactly the pullback minimum is
      // also ignored because the rule requires distance to be above it.
      ClearSetup(setup);
      return false;
   }

   return true;
}

bool ValidLTFSequence(TradeSetup &setup)
{
   ClearSetup(setup);
   if(!InpEnableLTFEntries || (G_BiasDir!=1 && G_BiasDir!=-1)) return false;
   if(!G_LTF.latest.valid || !G_LTF.previous.valid) return false;
   if(G_LTF.latest.dir!=G_BiasDir || G_LTF.previous.dir!=G_BiasDir) return false;
   if(!G_LTF.latest.virgin || IsTraded(G_LTF.latest.hash) || IsBlocked(G_LTF.tf,G_LTF.latest.hash)) return false;
   if(!G_BiasOB.valid || G_BiasOB.dir!=G_BiasDir) return false;
   setup.valid=true; setup.source=SOURCE_LTF; setup.mode=ENTRY_PENDING; setup.dir=G_BiasDir;
   setup.event_time=FrozenEventTime(G_LTF.latest.hash,EventTime(G_LTF.latest));
   setup.entry=(G_BiasDir==1?G_LTF.latest.high:G_LTF.latest.low);
   setup.logical_sl=(G_BiasDir==1?G_BiasOB.low-InpSLBufferPrice:G_BiasOB.high+InpSLBufferPrice);
   setup.zone_hash=G_LTF.latest.hash; setup.zone_key=G_LTF.latest.key; setup.label="LTF-"+TFText(G_LTF.tf);
   return true;
}

bool SetupStillValid(const TradeSetup &setup)
{
   if(!setup.valid || setup.dir!=G_BiasDir || IsTraded(setup.zone_hash)) return false;
   ENUM_TIMEFRAMES setup_tf=(setup.source==SOURCE_LTF?G_LTF.tf:(StringFind(setup.zone_key,TFText(G_HTF1.tf)+"|")==0?G_HTF1.tf:G_HTF2.tf));
   if(IsBlocked(setup_tf,setup.zone_hash)) return false;
   if(setup.source==SOURCE_HTF)
   {
      OBState current; ClearOB(current);
      if(setup.zone_key==G_HTF1.latest.key) current=G_HTF1.latest;
      else if(setup.zone_key==G_HTF2.latest.key) current=G_HTF2.latest;
      else return false;
      if(!current.valid || current.dir!=setup.dir || !current.virgin) return false;
   }
   else if(setup.source==SOURCE_LTF)
   {
      if(!G_LTF.latest.valid || G_LTF.latest.key!=setup.zone_key || !G_LTF.latest.virgin) return false;
   }
   return true;
}

bool SendMarket(const TradeSetup &setup)
{
   OBState source_ob; ClearOB(source_ob);
   if(setup.zone_key==G_HTF1.latest.key) source_ob=G_HTF1.latest;
   else if(setup.zone_key==G_HTF2.latest.key) source_ob=G_HTF2.latest;

   if(!source_ob.valid)
   {
      Print("Market setup rejected: source HTF zone is no longer current.");
      return false;
   }

   // IMPORTANT: HTF market/pending classification is decided only once from
   // the price captured at the first detection of this exact M5/M3 zone.
   // Never reclassify or reject the setup using later live-price movement.

   double market_entry=(setup.dir==1?SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID));
   double sl=ResolveInitialSL(setup,market_entry);
   if(!SLGeometryValid(setup.dir,market_entry,sl)){ Print("Market setup rejected: invalid SL geometry."); return false; }

   // Preserve the exact setup identity BEFORE sending a direct market order.
   // This metadata must remain available for the complete position lifetime so
   // that a manual desktop/mobile/web close can latch the matching M5/M3 block.
   SaveExposureMeta(setup);

   Trade.SetExpertMagicNumber(InpMagicNumber); Trade.SetDeviationInPoints(InpDeviationPoints); Trade.SetTypeFillingBySymbol(_Symbol);
   string comment="OBEA|"+setup.label+"|"+IntegerToString((long)setup.zone_hash);
   bool ok=(setup.dir==1?Trade.Buy(InpLots,_Symbol,0.0,sl,0.0,comment):Trade.Sell(InpLots,_Symbol,0.0,sl,0.0,comment));
   if(!ok)
   {
      Print("Market order failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription());
      if(!HasPosition() && !HasPending()) ClearExposureMeta();
      return false;
   }

   // A market order can fill at a slightly different price from the quote used
   // in the request. Recalculate from the ACTUAL position open price and enforce:
   // logical OB SL when its distance is >= minimum, otherwise exactly minimum.
   ENUM_POSITION_TYPE opened_type; double actual_open,current_sl,current_tp;
   if(ManagedPosition(opened_type,actual_open,current_sl,current_tp))
   {
      const int actual_dir=(opened_type==POSITION_TYPE_BUY?1:-1);
      const double required_sl=ResolveInitialSL(setup,actual_open);
      if(SLGeometryValid(actual_dir,actual_open,required_sl) &&
         MathAbs(current_sl-required_sl)>(_Point*0.5))
      {
         if(!Trade.PositionModify(_Symbol,required_sl,current_tp))
            Print("Post-fill minimum/logical SL correction failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription(),
                  " | Open=",DoubleToString(actual_open,_Digits)," | Required SL=",DoubleToString(required_sl,_Digits));
         else
            Print("Post-fill SL confirmed from actual open price",
                  " | Open=",DoubleToString(actual_open,_Digits),
                  " | Logical SL=",DoubleToString(setup.logical_sl,_Digits),
                  " | Final SL=",DoubleToString(required_sl,_Digits));
      }
   }

   G_ActivePositionHash=setup.zone_hash;
   G_ActivePositionTFMinutes=SetupTFMinutes(setup);
   MarkTraded(setup.zone_hash);
   Print("Market order opened from ",setup.label,". Zone marked traded: ",setup.zone_key); return true;
}

bool SendPending(const TradeSetup &setup)
{
   double entry=NormPrice(setup.entry),sl=ResolveInitialSL(setup,entry);
   if(!SLGeometryValid(setup.dir,entry,sl)){ Print("Pending setup rejected: invalid SL geometry."); return false; }
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   Trade.SetExpertMagicNumber(InpMagicNumber); Trade.SetDeviationInPoints(InpDeviationPoints); Trade.SetTypeFillingBySymbol(_Symbol);
   string comment="OBEA|"+setup.label+"|"+IntegerToString((long)setup.zone_hash);
   bool ok=false;
   if(setup.dir==1)
      ok=(entry<ask?Trade.BuyLimit(InpLots,entry,_Symbol,sl,0.0,ORDER_TIME_GTC,0,comment):Trade.BuyStop(InpLots,entry,_Symbol,sl,0.0,ORDER_TIME_GTC,0,comment));
   else
      ok=(entry>bid?Trade.SellLimit(InpLots,entry,_Symbol,sl,0.0,ORDER_TIME_GTC,0,comment):Trade.SellStop(InpLots,entry,_Symbol,sl,0.0,ORDER_TIME_GTC,0,comment));
   if(!ok)
   {
      Print("Pending order failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription()," | Entry=",DoubleToString(entry,_Digits)," | SL=",DoubleToString(sl,_Digits)); return false;
   }
   SaveExposureMeta(setup);
   RefreshActivePendingSnapshot();
   Print("Pending order placed from ",setup.label,
         " | Event time=",TimeToString(setup.event_time,TIME_DATE|TIME_SECONDS),
         " | Entry=",DoubleToString(entry,_Digits),
         " | SL=",DoubleToString(sl,_Digits));
   return true;
}

bool ExecuteSetup(const TradeSetup &setup)
{
   if(!InpEnableTrading || !setup.valid || HasPosition()) return false;

   if(HasPending())
   {
      ulong pending_ticket=0;
      ENUM_ORDER_TYPE pending_type;
      double pending_price=0.0;
      if(!ManagedPending(pending_ticket,pending_type,pending_price))
         return false;

      uint existing_hash=0;
      int existing_tf=0;
      datetime pending_time=0;
      if(!ResolveManagedPendingIdentity(pending_ticket,existing_hash,existing_tf,pending_time))
      {
         // Unknown ownership means HOLD. It must never mean cancel.
         Print("Replacement blocked: live pending identity unresolved",
               " | ticket=",pending_ticket,
               " | candidate=",setup.zone_hash);
         return false;
      }

      // The exact zone already owns the pending order. Never cancel, move or
      // recreate it merely because price or publisher values changed.
      if(existing_hash==setup.zone_hash)
         return false;

      // Only a genuinely different and newer setup may replace the order.
      if(pending_time<=0 || setup.event_time<=pending_time)
         return false;

      // Request deletion once, then wait until the trade transaction confirms
      // that the old pending order is gone. Do not place a replacement in the
      // same tick as OrderDelete().
      if(G_EADeleteExpected) return false;
      CancelPending();
      return false;
   }

   // If a deletion request is still awaiting confirmation, do not send.
   if(G_EADeleteExpected) return false;

   return (setup.mode==ENTRY_MARKET ? SendMarket(setup) : SendPending(setup));
}

void QueueOrReplace(const TradeSetup &candidate)
{
   if(!candidate.valid) return;

   const bool has_pending=HasPending();
   uint pending_hash=0;
   datetime pending_time=0;

   if(has_pending)
   {
      ulong pending_ticket=0;
      ENUM_ORDER_TYPE pending_type;
      double pending_price=0.0;
      int pending_tf=0;

      if(!ManagedPending(pending_ticket,pending_type,pending_price) ||
         !ResolveManagedPendingIdentity(pending_ticket,pending_hash,pending_tf,pending_time))
      {
         // A managed pending order with unresolved ownership is protected.
         // Do not queue a replacement that could delete and recreate it.
         return;
      }
   }

   // The exact setup already owns the live pending order.
   if(has_pending && pending_hash==candidate.zone_hash)
      return;

   // A candidate older than (or equal to) the setup owning the live pending
   // order can never replace it, regardless of timeframe. Detection time—not
   // M5/M3/M1 priority—decides which valid setup is preferred.
   if(has_pending && (pending_time<=0 || candidate.event_time<=pending_time))
      return;

   if(G_QueuedSetup.valid &&
      (G_QueuedSetup.zone_hash==candidate.zone_hash ||
       G_QueuedSetup.zone_key==candidate.zone_key))
      return;

   // Keep only the newest eligible candidate across M5, M3 and M1. This allows
   // a brand-new LTF setup to replace an older HTF pending order, and equally
   // allows a newer HTF setup to replace an older LTF/HTF pending order.
   if(!G_QueuedSetup.valid || candidate.event_time>G_QueuedSetup.event_time)
   {
      G_QueuedSetup=candidate;
      Print("Newest setup queued across all timeframes: ",candidate.label,
            " | hash=",candidate.zone_hash,
            " | event=",TimeToString(candidate.event_time,TIME_DATE|TIME_SECONDS));
   }
}

void ProcessAvailableSetups()
{
   // Entry discovery is state-driven, not event-tick-driven.
   // This lets the EA evaluate a valid live-detected HTF OB even when the
   // publisher/EA was attached after that OB first appeared.
   if(HasPosition())
      return;

   TradeSetup candidate;

   // Evaluate both HTFs continuously. BuildHTFSetup itself rejects:
   // baseline zones, non-virgin zones, traded zones, wrong bias and
   // distances outside the permitted range.
   if(BuildHTFSetup(G_HTF1.latest,candidate))
      QueueOrReplace(candidate);

   if(BuildHTFSetup(G_HTF2.latest,candidate))
      QueueOrReplace(candidate);

   // LTF sequence is also continuously evaluated from latest + previous GV.
   // An opposite M1 sequence cannot block a valid HTF setup.
   if(ValidLTFSequence(candidate))
      QueueOrReplace(candidate);
}

string SetupRejectReason(const ENUM_TIMEFRAMES tf,const uint expected_hash)
{
   OBState ob; ClearOB(ob);

   if(tf==G_HTF1.tf) ob=G_HTF1.latest;
   else if(tf==G_HTF2.tf) ob=G_HTF2.latest;
   else if(tf==G_LTF.tf)
   {
      if(!G_LTF.latest.valid) return "latest M1 OB is invalid/missing";
      if(expected_hash!=0 && G_LTF.latest.hash!=expected_hash) return "blocked M1 zone is no longer the latest zone";
      if(!G_LTF.previous.valid) return "previous M1 OB is invalid/missing";
      if(G_LTF.latest.dir!=G_LTF.previous.dir) return "latest two M1 OBs are not sequential in one direction";
      if(G_LTF.latest.dir!=G_BiasDir) return "M1 sequence disagrees with HTF bias";
      if(!G_LTF.latest.virgin) return "latest M1 zone is no longer virgin";
      if(IsTraded(G_LTF.latest.hash)) return "latest M1 zone is already traded";
      return "M1 setup did not pass an unspecified eligibility check";
   }
   else return "unsupported reset timeframe";

   if(!ob.valid) return "latest HTF OB is invalid/missing";
   if(expected_hash!=0 && ob.hash!=expected_hash) return "blocked HTF zone is no longer the latest zone";
   if(ob.dir!=G_BiasDir) return "HTF zone no longer controls/agrees with combined bias";
   if(!ob.virgin) return "HTF zone is no longer virgin";
   if(ob.detected_time<=0 || ob.detected_price<=0.0) return "HTF zone has no live detection time/price";
   if(IsTraded(ob.hash)) return "HTF zone is already traded";

   double distance=(ob.dir==1 ? ob.detected_price-ob.high : ob.low-ob.detected_price);
   if(distance<0.0) return "HTF detection distance is negative";
   if(distance>InpHTFPullbackDistanceMax) return "HTF detection distance is above pullback maximum";

   if(distance<InpHTFMarketDistanceMax)
      return "HTF market setup is eligible from its frozen detection snapshot but was not queued";

   if(distance<=InpHTFPullbackDistanceMin)
      return "HTF detection distance is between the market and pullback entry bands";

   if(distance<=InpHTFPullbackDistanceMax)
      return "HTF pullback setup is eligible but was not queued";

   return "HTF setup did not pass an unspecified eligibility check";
}

bool RearmReleasedSetup(const ENUM_TIMEFRAMES tf,const uint released_hash)
{
   // Pull the newest publisher values before rebuilding the released setup.
   bool changed=false;
   if(tf==G_HTF1.tf) UpdateTracker(G_HTF1,changed);
   else if(tf==G_HTF2.tf) UpdateTracker(G_HTF2,changed);
   else if(tf==G_LTF.tf) UpdateTracker(G_LTF,changed);

   RefreshBias();

   // Normally reset receives the exact manually blocked hash. In rare cases
   // (for example after delayed trade metadata recovery) the persisted block
   // can exist without its hash and ReleaseBlockedZone() returns zero. Do not
   // leave the still-current virgin setup permanently rejected as "traded".
   // Fall back only to the CURRENT zone of the selected timeframe and clear
   // only that one traded marker. Never clear all traded-zone history.
   uint rearm_hash=released_hash;
   if(rearm_hash==0)
   {
      if(tf==G_HTF1.tf && G_HTF1.latest.valid)
         rearm_hash=G_HTF1.latest.hash;
      else if(tf==G_HTF2.tf && G_HTF2.latest.valid)
         rearm_hash=G_HTF2.latest.hash;
      else if(tf==G_LTF.tf && G_LTF.latest.valid)
         rearm_hash=G_LTF.latest.hash;

      if(rearm_hash!=0)
      {
         ClearTradedMark(rearm_hash);
         Print("Reset hash fallback: cleared traded mark for current ",TFText(tf),
               " zone hash=",rearm_hash);
      }
   }

   if(HasPosition() || HasPending())
   {
      Print("Reset release cannot submit ",TFText(tf),
            ": managed exposure already exists.");
      return false;
   }

   TradeSetup setup; ClearSetup(setup);
   bool built=false;

   if(tf==G_HTF1.tf) built=BuildHTFSetup(G_HTF1.latest,setup);
   else if(tf==G_HTF2.tf) built=BuildHTFSetup(G_HTF2.latest,setup);
   else if(tf==G_LTF.tf) built=ValidLTFSequence(setup);

   if(!built)
   {
      Print("Reset released ",TFText(tf),
            " but setup is not currently eligible: ",
            SetupRejectReason(tf,released_hash));
      return false;
   }

   if(rearm_hash!=0 && setup.zone_hash!=rearm_hash)
   {
      Print("Reset released old ",TFText(tf),
            " zone hash=",rearm_hash,
            "; current eligible zone is newer hash=",setup.zone_hash,".");
   }

   G_QueuedSetup=setup;
   Print("Reset re-armed setup: ",setup.label,
         " | ",setup.zone_key,
         " | event=",TimeToString(setup.event_time,TIME_DATE|TIME_SECONDS));

   ProcessQueuedSetup();
   return true;
}

void ProcessQueuedSetup()
{
   if(!G_QueuedSetup.valid) return;
   if(HasPosition()){ ClearSetup(G_QueuedSetup); return; }
   if(!SetupStillValid(G_QueuedSetup))
   {
      Print("Queued setup cleared: no longer valid | ",G_QueuedSetup.zone_key); ClearSetup(G_QueuedSetup); return;
   }
   if(ExecuteSetup(G_QueuedSetup)) ClearSetup(G_QueuedSetup);
}

void EnforceBiasExit()
{
   if(G_BiasDir!=1 && G_BiasDir!=-1) return;
   int exposure_dir=ExposureDirection();
   if(exposure_dir==0 || exposure_dir==G_BiasDir) return;
   if(HasPosition()) ClosePosition();
   if(HasPending()) CancelPending();
   ClearSetup(G_QueuedSetup);
}

int PositionTFMinutesFromComment()
{
   if(!PositionSelect(_Symbol)) return 0;
   if((ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) return 0;

   const string comment=PositionGetString(POSITION_COMMENT);
   if(StringFind(comment,"HTF-M5")>=0) return 5;
   if(StringFind(comment,"HTF-M3")>=0) return 3;
   if(StringFind(comment,"LTF-M1")>=0) return 1;
   return 0;
}

int ActivePositionTFMinutesResolved()
{
   int tf_minutes=G_ActivePositionTFMinutes;
   if(tf_minutes<=0) tf_minutes=MetaTFMinutes();
   if(tf_minutes<=0) tf_minutes=PositionTFMinutesFromComment();

   // Repair the in-memory and persistent identity as soon as it is recovered
   // from the live position comment. This prevents a reset, restart or delayed
   // trade transaction from making an HTF position look like an unknown/LTF one.
   if(tf_minutes>0)
   {
      G_ActivePositionTFMinutes=tf_minutes;
      if(!GlobalVariableCheck(G_MetaTFGV) || MetaTFMinutes()!=tf_minutes)
         GlobalVariableSet(G_MetaTFGV,(double)tf_minutes);
   }
   return tf_minutes;
}

bool ActivePositionIsHTF()
{
   const int tf_minutes=ActivePositionTFMinutesResolved();
   return (tf_minutes==TFMinutes(InpHTF1) || tf_minutes==TFMinutes(InpHTF2));
}

void EnforceHTFPositionMinimumSL()
{
   ENUM_POSITION_TYPE pos_type;
   double open_price,current_sl,current_tp;
   if(!ManagedPosition(pos_type,open_price,current_sl,current_tp)) return;

   const int tf_minutes=ActivePositionTFMinutesResolved();
   if(tf_minutes!=TFMinutes(InpHTF1) && tf_minutes!=TFMinutes(InpHTF2)) return;

   const int dir=(pos_type==POSITION_TYPE_BUY?1:-1);
   const double minimum_sl=NormPrice(dir==1
                                      ? open_price-InpMinimumSLDistance
                                      : open_price+InpMinimumSLDistance);

   // Only correct an absent/too-close SL. A farther logical OB SL is preserved.
   bool correction_needed=false;
   if(dir==1)
      correction_needed=(current_sl<=0.0 || (open_price-current_sl)<InpMinimumSLDistance-(_Point*0.5));
   else
      correction_needed=(current_sl<=0.0 || (current_sl-open_price)<InpMinimumSLDistance-(_Point*0.5));

   if(!correction_needed) return;
   if(!SLGeometryValid(dir,open_price,minimum_sl))
   {
      Print("HTF minimum SL correction skipped: invalid geometry",
            " | Open=",DoubleToString(open_price,_Digits),
            " | Required SL=",DoubleToString(minimum_sl,_Digits),
            " | TF=M",tf_minutes);
      return;
   }

   if(!Trade.PositionModify(_Symbol,minimum_sl,current_tp))
      Print("HTF minimum SL correction failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription(),
            " | Open=",DoubleToString(open_price,_Digits),
            " | Current SL=",DoubleToString(current_sl,_Digits),
            " | Required SL=",DoubleToString(minimum_sl,_Digits),
            " | TF=M",tf_minutes);
   else
      Print("HTF minimum SL restored",
            " | Open=",DoubleToString(open_price,_Digits),
            " | Previous SL=",DoubleToString(current_sl,_Digits),
            " | Final SL=",DoubleToString(minimum_sl,_Digits),
            " | TF=M",tf_minutes);
}

void TrailPosition()
{
   if(!InpEnableTrailing) return;
   ENUM_POSITION_TYPE pos_type; double open_price,current_sl,current_tp;
   if(!ManagedPosition(pos_type,open_price,current_sl,current_tp)) return;
   int dir=(pos_type==POSITION_TYPE_BUY?1:-1);
   if(dir!=G_BiasDir || !G_BiasOB.valid || G_BiasOB.dir!=dir) return;
   double candidate=NormPrice(dir==1?G_BiasOB.low-InpSLBufferPrice:G_BiasOB.high+InpSLBufferPrice);

   // HTF positions may trail only to an SL that still respects the minimum
   // distance from the ACTUAL position open price. If the logical OB SL is
   // farther than the minimum it is retained; if nearer, it is expanded.
   if(ActivePositionIsHTF())
      candidate=ApplyMinimumSL(dir,open_price,candidate);

   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID),ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   if(dir==1)
   {
      if(current_sl>0.0 && candidate<=current_sl) return;
      if(candidate>=bid-min_stop) return;
   }
   else
   {
      if(current_sl>0.0 && candidate>=current_sl) return;
      if(candidate<=ask+min_stop) return;
   }
   if(!Trade.PositionModify(_Symbol,candidate,current_tp))
      Print("Trailing SL failed: ",Trade.ResultRetcode()," ",Trade.ResultRetcodeDescription());
}

void SyncFilledZone()
{
   ENUM_POSITION_TYPE type;
   double a,b,c;

   if(ManagedPosition(type,a,b,c))
   {
      uint hash=MetaHash();
      int tf_minutes=MetaTFMinutes();
      if(hash==0) hash=G_ActivePendingHash;
      if(tf_minutes<=0) tf_minutes=G_ActivePendingTFMinutes;

      if(hash!=0 && !IsTraded(hash))
      {
         MarkTraded(hash);
         Print("Order filled. Zone marked traded: ",hash);
      }

      // Keep the setup identity while the position is open. It is required to
      // latch the correct M5/M3/M1 manual block if the user closes the position.
      G_ActivePositionHash=hash;
      G_ActivePositionTFMinutes=tf_minutes;
      ClearActivePendingSnapshot();
      ClearEADeleteExpectation();
      return;
   }

   if(HasPending())
   {
      RefreshActivePendingSnapshot();
      ClearActivePositionSnapshot();
      return;
   }

   // Exposure metadata is cleared only after the exit/cancellation transaction
   // has had a chance to identify whether it was manual or EA initiated.
}

string DirText(const int dir)
{
   if(dir==1) return "BULLISH / BUY ALLOWED";
   if(dir==-1) return "BEARISH / SELL ALLOWED";
   return "NONE / BOTH BLOCKED";
}

string OBLine(const OBState &ob)
{
   if(!ob.valid) return "No valid OB";
   string detected="baseline";
   if(ob.detected_time>0) detected=TimeToString(ob.detected_time,TIME_DATE|TIME_SECONDS)+" @ "+DoubleToString(ob.detected_price,_Digits);
   return TFText(ob.tf)+" "+(ob.dir==1?"BULL":"BEAR")+" | H "+DoubleToString(ob.high,_Digits)+" | L "+DoubleToString(ob.low,_Digits)+
          " | Virgin "+(ob.virgin?"true":"false")+" | Origin "+TimeToString(ob.origin_time,TIME_DATE|TIME_MINUTES)+" | Detected "+detected;
}

void CreateResetButton(const string name,const string text,const int x)
{
   if(ObjectFind(0,name)<0) ObjectCreate(0,name,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,name,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,name,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,name,OBJPROP_YDISTANCE,142);
   ObjectSetInteger(0,name,OBJPROP_XSIZE,82);
   ObjectSetInteger(0,name,OBJPROP_YSIZE,22);
   ObjectSetString(0,name,OBJPROP_TEXT,text);
   ObjectSetInteger(0,name,OBJPROP_FONTSIZE,8);
   ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
}

void CreateResetButtons()
{
   if(!InpShowResetButtons) return;
   CreateResetButton(G_ButtonM5,"RESET M5",5);
   CreateResetButton(G_ButtonM3,"RESET M3",92);
   CreateResetButton(G_ButtonM1,"RESET M1",179);
}

void DeleteResetButtons()
{
   ObjectDelete(0,G_ButtonM5);
   ObjectDelete(0,G_ButtonM3);
   ObjectDelete(0,G_ButtonM1);
}

string BlockText(const ENUM_TIMEFRAMES tf)
{
   uint hash=BlockedHash(tf);
   return (hash==0?"NONE":IntegerToString((long)hash));
}

void DrawPanel()
{
   if(!InpShowPanel){ Comment(""); return; }
   string exposure="NONE"; int exp_dir=ExposureDirection(); if(exp_dir==1) exposure="BUY"; if(exp_dir==-1) exposure="SELL";
   string sequence="NONE";
   if(G_LTF.latest.valid && G_LTF.previous.valid && G_LTF.latest.dir==G_LTF.previous.dir)
      sequence=(G_LTF.latest.dir==1?"BULL + BULL":"BEAR + BEAR");
   string queued="NONE";
   if(G_QueuedSetup.valid)
      queued=G_QueuedSetup.label+" "+(G_QueuedSetup.dir==1?"BUY":"SELL")+" @ "+TimeToString(G_QueuedSetup.event_time,TIME_DATE|TIME_SECONDS);
   Comment("OB MTF FRESH TRADER v2.25\n",
           "Symbol: ",_Symbol," | Chart: ",TFText((ENUM_TIMEFRAMES)_Period),"\n",
           "Combined HTF: ",DirText(G_BiasDir)," | Source: ",G_BiasSource," | Event: ",(G_BiasTime>0?TimeToString(G_BiasTime,TIME_DATE|TIME_SECONDS):"n/a"),"\n",
           OBLine(G_HTF1.latest),"\n",OBLine(G_HTF2.latest),"\n",OBLine(G_LTF.latest),"\n",
           "M1 sequence: ",sequence,"\n",
           "Exposure: ",exposure," | Pending: ",(HasPending()?"YES":"NO")," | Trading: ",(InpEnableTrading?"ENABLED":"READ ONLY"),"\n",
           "Queued setup: ",queued,"\n",
           "Manual blocks: M5=",BlockText(InpHTF1)," | M3=",BlockText(InpHTF2)," | M1=",BlockText(InpLTF));
}

int OnInit()
{
   if(_Period!=InpLTF) Print("Warning: attach this EA to ",TFText(InpLTF),".");
   Trade.SetExpertMagicNumber(InpMagicNumber); Trade.SetDeviationInPoints(InpDeviationPoints); Trade.SetTypeFillingBySymbol(_Symbol);
   G_HTF1.tf=InpHTF1; G_HTF2.tf=InpHTF2; G_LTF.tf=InpLTF;
   G_HTF1.initialized=false; G_HTF2.initialized=false; G_LTF.initialized=false;
   ClearOB(G_HTF1.latest); ClearOB(G_HTF1.previous); ClearOB(G_HTF2.latest); ClearOB(G_HTF2.previous); ClearOB(G_LTF.latest); ClearOB(G_LTF.previous); ClearOB(G_BiasOB); ClearSetup(G_QueuedSetup);
   string meta="OBEA_META_"+IntegerToString((long)InpMagicNumber)+"_"+SafeSymbol();
   G_MetaHashGV=meta+"_HASH"; G_MetaDirGV=meta+"_DIR"; G_MetaSourceGV=meta+"_SOURCE"; G_MetaTimeGV=meta+"_TIME"; G_MetaTFGV=meta+"_TF";
   ClearEADeleteExpectation();
   G_EACloseExpected=false;
   ClearActivePendingSnapshot();
   ClearActivePositionSnapshot();
   LoadManualBlock(InpHTF1);
   LoadManualBlock(InpHTF2);
   LoadManualBlock(InpLTF);
   bool dummy=false; UpdateTracker(G_HTF1,dummy); UpdateTracker(G_HTF2,dummy); UpdateTracker(G_LTF,dummy);
   RefreshBias(); SyncFilledZone(); CreateResetButtons(); DrawPanel(); return INIT_SUCCEEDED;
}

void OnDeinit(const int reason){ DeleteResetButtons(); Comment(""); }

void OnTick()
{
   // Manual blocks are durable and may be cleared only through Reset M5/M3/M1.
   PersistManualBlock(InpHTF1);
   PersistManualBlock(InpHTF2);
   PersistManualBlock(InpLTF);

   bool new_h1=false,new_h2=false,new_ltf=false;
   UpdateTracker(G_HTF1,new_h1);
   UpdateTracker(G_HTF2,new_h2);
   UpdateTracker(G_LTF,new_ltf);

   // Manual blocks are exact-zone blocks. As soon as a genuinely different
   // latest zone exists on that timeframe, remove the stale latch so the panel
   // shows NONE and the new setup can participate in global newest-selection.
   ReleaseStaleManualBlockForNewZone(InpHTF1,G_HTF1.latest);
   ReleaseStaleManualBlockForNewZone(InpHTF2,G_HTF2.latest);
   ReleaseStaleManualBlockForNewZone(InpLTF,G_LTF.latest);

   RefreshBias();
   SyncFilledZone();
   EnforceBiasExit();

   // Repair any HTF SL that is missing or closer than the configured floor,
   // then prevent trailing from tightening it inside that same floor.
   EnforceHTFPositionMinimumSL();
   TrailPosition();
   EnforceHTFPositionMinimumSL();

   if(InpEnableTrading)
   {
      ProcessAvailableSetups();
      ProcessQueuedSetup();
   }

   RefreshActivePendingSnapshot();
   DrawPanel();
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // A pending order disappearing because it was FILLED is not a manual
   // cancellation. Only a genuinely cancelled pending order may latch a block.
   if(trans.type==TRADE_TRANSACTION_ORDER_DELETE && trans.order>0)
   {
      ENUM_ORDER_STATE history_state=ORDER_STATE_STARTED;
      bool have_history=HistoryOrderSelect(trans.order);
      if(have_history)
         history_state=(ENUM_ORDER_STATE)HistoryOrderGetInteger(trans.order,ORDER_STATE);

      const bool order_was_filled=
         (have_history &&
          (history_state==ORDER_STATE_FILLED || history_state==ORDER_STATE_PARTIAL));

      const bool ea_delete=
         (G_EADeleteExpected &&
          (G_EADeleteTicket==0 || G_EADeleteTicket==trans.order));

      uint hash=G_ActivePendingHash;
      int tf_minutes=G_ActivePendingTFMinutes;
      if(hash==0) hash=MetaHash();
      if(tf_minutes<=0) tf_minutes=MetaTFMinutes();

      if(order_was_filled)
      {
         // Preserve metadata until the position/deal transaction is processed.
         G_ActivePositionHash=hash;
         G_ActivePositionTFMinutes=tf_minutes;
         ClearEADeleteExpectation();
         ClearActivePendingSnapshot();
         SyncFilledZone();
         return;
      }

      if(ea_delete)
      {
         Print("EA deletion confirmed; queued newer setup remains eligible",
               " | ticket=",trans.order,
               " | hash=",hash,
               " | TF=M",tf_minutes);
         ClearEADeleteExpectation();
      }
      else
      {
         ENUM_TIMEFRAMES tf=TFByMinutes(tf_minutes);
         if(hash!=0 && tf!=PERIOD_CURRENT)
         {
            BlockZone(tf,hash);
            ClearSetup(G_QueuedSetup);
            Print("Manual/external pending cancellation blocked timeframe",
                  " | ticket=",trans.order,
                  " | TF=",TFText(tf),
                  " | hash=",hash);
         }
         else
         {
            Print("Pending deleted externally, but setup identity unavailable",
                  " | ticket=",trans.order,
                  " | hash=",hash,
                  " | TF=M",tf_minutes);
         }
      }

      ClearExposureMeta();
      ClearActivePendingSnapshot();
      return;
   }

   // Detect a closed managed position. Manual desktop/mobile/web closure blocks
   // the exact setup timeframe until its reset button is pressed. EA bias exits,
   // stop-loss exits and broker-side exits do not create a manual block.
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD && trans.deal>0 && HistoryDealSelect(trans.deal))
   {
      const string deal_symbol=HistoryDealGetString(trans.deal,DEAL_SYMBOL);
      const ulong deal_magic=(ulong)HistoryDealGetInteger(trans.deal,DEAL_MAGIC);
      const ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal,DEAL_ENTRY);

      if(deal_symbol==_Symbol && deal_magic==InpMagicNumber &&
         (entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY))
      {
         const ENUM_DEAL_REASON reason=(ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal,DEAL_REASON);
         const bool manual_reason=
            (reason==DEAL_REASON_CLIENT || reason==DEAL_REASON_MOBILE || reason==DEAL_REASON_WEB);

         uint hash=G_ActivePositionHash;
         int tf_minutes=G_ActivePositionTFMinutes;
         if(hash==0) hash=MetaHash();
         if(tf_minutes<=0) tf_minutes=MetaTFMinutes();

         if(manual_reason && !G_EACloseExpected)
         {
            ENUM_TIMEFRAMES tf=TFByMinutes(tf_minutes);
            if(hash!=0 && tf!=PERIOD_CURRENT)
            {
               BlockZone(tf,hash);
               ClearSetup(G_QueuedSetup);
               Print("Manual position close blocked setup timeframe",
                     " | deal=",trans.deal,
                     " | TF=",TFText(tf),
                     " | hash=",hash);
            }
            else
            {
               Print("Manual position close detected, but setup identity unavailable",
                     " | deal=",trans.deal,
                     " | hash=",hash,
                     " | TF=M",tf_minutes);
            }
         }
         else if(G_EACloseExpected)
         {
            Print("EA position close confirmed; no manual block latched",
                  " | deal=",trans.deal,
                  " | hash=",hash,
                  " | TF=M",tf_minutes);
         }

         G_EACloseExpected=false;
         ClearExposureMeta();
         ClearActivePositionSnapshot();
         ClearActivePendingSnapshot();
         return;
      }
   }

   SyncFilledZone();
}
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
{
   if(id!=CHARTEVENT_OBJECT_CLICK) return;

   ENUM_TIMEFRAMES reset_tf=PERIOD_CURRENT;
   if(sparam==G_ButtonM5) reset_tf=InpHTF1;
   else if(sparam==G_ButtonM3) reset_tf=InpHTF2;
   else if(sparam==G_ButtonM1) reset_tf=InpLTF;
   else return;

   const uint released_hash=ReleaseBlockedZone(reset_tf);

   ObjectSetInteger(0,sparam,OBJPROP_STATE,false);
   if(InpEnableTrading)
   {
      // Reset is isolated to the selected timeframe. A failed M3 reset must
      // not activate M1 or M5 during the same chart-event cycle.
      RearmReleasedSetup(reset_tf,released_hash);
   }
   DrawPanel();
   ChartRedraw();
}
//+------------------------------------------------------------------+
