"""Configuration for Reversal Manager. Own config, separate from Trend
Manager's (see CLAUDE.md -- each Manager owns its own).

Zone/ATR data source switched 2026-08-20 from tv_scraper's per-symbol
polled files to the TradingView webhook path instead (tv_bridge.receiver
-> tradingview_bot.main -> ZoneStore/AtrStore) -- user's explicit rule:
this Manager's whole mechanism is retest-driven, and only the webhook
path can carry ob_zone_retested's exact retest time; tv_scraper only ever
approximated a retest from its own next-poll Close reading. Unlike
tv_scraper's per-symbol zone files, tradingview_bot.main writes ONE
shared file for every symbol (ZoneStore/AtrStore are internally keyed by
"symbol|timeframe|direction"/"symbol|timeframe"), so all five
SymbolConfig entries below point at the SAME zone_state_file/
atr_state_file rather than each having their own.

live_state_file is intentionally UNCHANGED -- still tv_scraper's own
per-symbol live Close polling. The webhook schema has no live-price-tick
event at all (only sparse zone/ATR events), so Reversal Manager's
_read_live_close() still needs tv_scraper running alongside the webhook
pipeline, just for price -- see the module docstring on why this split
is intentional, not a leftover.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# Same five HTF timeframes for every symbol -- user's own list, no
# per-symbol variation given (unlike Trend Manager's parent timeframes).
HTF_TIMEFRAMES: Tuple[str, ...] = ("240", "120", "60", "30", "15")


@dataclass(frozen=True)
class HtfM1InvalidationRule:
    """single_ob_timeframes: any ONE opposite-direction OB formed on any
    of these timeframes invalidates the setup. double_ob_timeframe: TWO
    DISTINCT opposite OBs on this ONE timeframe also invalidates it (None
    -- the default -- means no such rule for this phase). The double-OB
    case mirrors the same noise-filtering pattern already built for Trend
    Manager's own XAUUSD M1-exit rule (a single opposite OB on the
    confirmation timeframe itself proved too noisy to trust; two in a row
    is the bar instead) -- applied here to whichever timeframe is THIS
    symbol's own htf_m1 confirmation timeframe, when the user wants that
    same treatment (XAUUSD's own HTF-M1 rule excludes its confirmation
    timeframe, M1, from invalidation entirely instead -- different
    symbols can make different calls here)."""
    single_ob_timeframes: Tuple[str, ...]
    double_ob_timeframe: Optional[str] = None


@dataclass(frozen=True)
class HtfM1Config:
    """Per-symbol parameters for the HTF-retest -> LTF-confirm reversal
    rule (see reversal_manager.py's own module docstring, "HTF-M1
    mechanism" section, for the full flow). A symbol with htf_m1=None on
    its own SymbolConfig doesn't have this rule enabled at all -- XAUUSD
    was first (2026-08-25), BTCUSD/ETHUSD added the same day once XAUUSD
    was working; USOIL/USTEC deliberately deferred (their own existing
    M3 OB-or-ATR-flip mechanism already covers similar ground)."""
    confirm_timeframe: str  # "1" for XAUUSD, "3" for BTCUSD/ETHUSD
    # Which timeframes register a "waiting" retest for this mechanism --
    # NOT necessarily the same as module-level HTF_TIMEFRAMES above (the
    # ORIGINAL mechanism's own list): XAUUSD folds M5 in as an ordinary
    # HTF source here (unlike the original mechanism, which gives M5 its
    # own dedicated immediate-fire treatment instead) since M5 has no
    # special relationship to M1, this symbol's own confirmation
    # timeframe. BTCUSD/ETHUSD reuse HTF_TIMEFRAMES as-is (no M5-specific
    # carve-out needed -- M3 is their confirmation timeframe, not M5).
    htf_timeframes: Tuple[str, ...]
    waiting_invalidation: HtfM1InvalidationRule
    active_invalidation: HtfM1InvalidationRule
    sl_buffer: float
    # Which moment active_invalidation's own opposite-OB lookups anchor
    # to, once a trade is open (filled or pending) -- "retest" (the
    # original HTF retest event that armed this setup) or "opened_at"
    # (the trade's own real fill time). XAUUSD's own rule is explicitly
    # retest-anchored ("this setup becomes invalid... for as long as the
    # setup is valid," covering the whole wait-to-pending-to-fill span,
    # not just from the fill onward). BTCUSD/ETHUSD's rule is explicitly
    # fill-anchored instead -- user's own words, 2026-08-25: "time is
    # important, dont refer back to older zones, zones should only form
    # after entering into the trade." Different symbols, deliberately
    # different anchors, both given explicitly -- not unified on purpose.
    active_invalidation_anchor: str = "retest"
    # Same OBD_ATR.pine dual-period pair for every symbol so far (user's
    # own call, 2026-08-25: "Same as XAUUSD (2 and 300)" when asked about
    # BTCUSD/ETHUSD) -- kept per-symbol (not a shared module constant)
    # so a future symbol can get its own tuned periods without touching
    # this dataclass again.
    atr_fast_period: str = "2"
    atr_slow_period: str = "300"
    pullback_fraction: float = 0.45
    # When set, SL for a confirmed HTF-M1 trade comes from the HTF
    # retest zone itself instead of the confirmation (M1 OB's own edge
    # or the ATR trail stop) -- user's own correction, 2026-08-26,
    # replacing that "SL from the confirmation" rule entirely for this
    # symbol. Value is the zone-size threshold (this symbol's own point
    # scale): a waiting zone no wider than this uses its own opposite
    # edge + sl_buffer (same shape as everywhere else); wider than this
    # uses the zone's own CENTER point + sl_buffer instead, so an
    # unusually wide HTF zone doesn't produce an excessively wide stop.
    # See reversal_manager._htf_m1_zone_sl for the exact selection logic
    # among multiple simultaneously-waiting zones. None (default) keeps
    # the original confirmation-based SL rule -- XAUUSD only for now
    # (7.0, its own point scale); BTCUSD/ETHUSD left unset.
    sl_zone_center_threshold: Optional[float] = None


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    zone_state_file: str
    live_state_file: str
    # LTF confirmation timeframes -- XAUUSD has M1/M3/M5. BTCUSD/ETHUSD's
    # own tv_scraper PULL grid has no M1/M3 at all (see
    # project_tv_scraper_multi_symbol_setup memory), so historically only
    # M5 applied for them; BTCUSD switched to M3 on 2026-08-21 once the
    # webhook (push) path started carrying its own M3 OBD_Reversal alert --
    # zone_state_file for BTCUSD is the shared tv_zone_file (webhook data),
    # not tv_scraper's own pull file, so this isn't limited by the scraper
    # grid's panes the way live_state_file still is.
    ltf_timeframes: Tuple[str, ...]
    # Trend Manager's own two parent (bias) timeframes for this symbol --
    # None (default) means the M5-immediate/mitigation-close rules below
    # are NOT enabled for this symbol, keeping the original 2026-08-18
    # behavior (M5 always fires immediately, mitigation always closes).
    # Added 2026-08-19, user's explicit XAUUSD-only rule set:
    # - M5 retest whose direction agrees with AT LEAST ONE parent still
    #   fires immediately, same as before.
    # - M5 retest agreeing with NEITHER parent no longer fires OR gets
    #   dropped -- it's registered as a waiting retest instead, resolved
    #   by the SAME M1/M3/M5 LTF confirmation/invalidation machinery the
    #   HTF (H4/H2/H1/M30/M15) zones already use. SL for a
    #   confirmed-via-LTF fire naturally still comes from the M5 zone's
    #   own edge via the existing multi-waiting-zone SL logic in
    #   _check_direction -- no separate code path needed for that.
    # - Once filled, mitigation of the entry OB no longer auto-closes
    #   the trade -- only a fresh OPPOSITE-direction OB on M1 or M3 does
    #   (see _close_if_opposite_ltf_ob).
    parent_timeframes: Optional[Tuple[str, str]] = None
    # Hard cap on initial SL distance (price units, symbol's own scale)
    # -- None means uncomputed/no cap (BTCUSD/ETHUSD, unchanged). Added
    # same day as parent_timeframes above, same XAUUSD-only scope.
    max_sl_points: Optional[float] = None
    # USOIL/USTEC only (2026-08-19) -- when set, _check_direction's
    # LTF confirmation/invalidation ALSO accepts an ATR trend flip on
    # this timeframe as a peer to a fresh LTF OB (either one confirms or
    # invalidates a waiting retest), and a confirmed fire is always
    # MARKET, skipping the pullback/distance math entirely -- same
    # "m3 is the only execution timeframe... market entry, as its lower
    # time frame" reasoning as Trend Manager's own atr_confirm_timeframe
    # (see that module's SymbolConfig docstring for the full user quote).
    # These two symbols have no M5-immediate tier at all -- harmless
    # by construction, not a separate flag: their tv_scraper grid has no
    # M5 pane, so _fire_m5_immediate's own zone lookup simply never
    # finds anything and no-ops every cycle.
    atr_confirm_timeframe: Optional[str] = None
    atr_state_file: Optional[str] = None
    # The second, independent HTF-retest -> LTF-confirm reversal mechanism
    # (see reversal_manager.py's own docstring for the full rule) -- None
    # means this symbol doesn't have it enabled at all. Added 2026-08-25,
    # XAUUSD first ("this is only for xauusd... once we are done with
    # this we will move to other instruments as well"), BTCUSD/ETHUSD the
    # same day.
    htf_m1: Optional[HtfM1Config] = None


@dataclass(frozen=True)
class Config:
    symbols: list  # list[SymbolConfig]
    poll_seconds: float
    state_file: str
    magic_number: int
    # Execution Bridge writes here (v3/execution_bridge/manual_events.py)
    # the moment it detects a REAL manual cancel/close or SL/TP hit for
    # a Reversal-Manager-sourced position -- read here, never written
    # here. Added 2026-08-18 after a real SL hit left this Manager's
    # own state showing FILLED forever with nothing real behind it.
    manual_events_file: str


def load_config() -> Config:
    # Shared webhook-sourced zone/ATR files -- ONE file each for every
    # symbol (see module docstring). Same env var names tradingview_bot's
    # own config.py uses (TV_ZONE_STATE_FILE/TV_ATR_STATE_FILE), so
    # pointing both processes at the same .env values keeps them in sync
    # by construction rather than by two separately-maintained defaults.
    tv_zone_file = os.getenv("TV_ZONE_STATE_FILE", "tradingview_bot_zones.json")
    tv_atr_file = os.getenv("TV_ATR_STATE_FILE", "tradingview_bot_atr.json")
    return Config(
        symbols=[
            SymbolConfig(
                "XAUUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_XAUUSD_LIVE_FILE", "tv_scraper_xauusd_live.json"),
                ltf_timeframes=("1", "3", "5"),
                parent_timeframes=("5", "15"),
                max_sl_points=20.0,
                # Needed for the HTF-M1 mechanism's own dual-ATR-flip
                # confirmation check (Line 1/period=2 AND Line 2/
                # period=300 on M1) -- same shared webhook ATR file every
                # other symbol's own atr_confirm_timeframe usage already
                # points at, not a separate file.
                atr_state_file=tv_atr_file,
                htf_m1=HtfM1Config(
                    confirm_timeframe="1",
                    htf_timeframes=("240", "120", "60", "30", "15", "5"),
                    # While waiting: opposite OB on M3, M5, or M15
                    # invalidates (M1 excluded -- it's the confirmation
                    # timeframe itself, an opposite OB there is normal
                    # noise). Once a trade is open: narrower, M5/M15 only
                    # -- "a trade can only auto square off if a m5 or m15
                    # opposite side ob forms... else it waits for sl, or
                    # sl trail" (user's own words, 2026-08-25).
                    waiting_invalidation=HtfM1InvalidationRule(single_ob_timeframes=("3", "5", "15")),
                    active_invalidation=HtfM1InvalidationRule(single_ob_timeframes=("5", "15")),
                    sl_buffer=2.0,  # dedicated, NOT SYMBOL_SL_BUFFER's shared 1.0 -- user's explicit call
                    # SL now comes from the HTF zone itself, not the
                    # confirmation -- 2026-08-26, user's own correction
                    # (see HtfM1Config.sl_zone_center_threshold's own
                    # docstring). 7.0 = XAUUSD's own point scale.
                    sl_zone_center_threshold=7.0,
                ),
            ),
            SymbolConfig(
                "BTCUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_BTCUSD_LIVE_FILE", "tv_scraper_live.json"),
                ltf_timeframes=("3",),
                atr_state_file=tv_atr_file,
                htf_m1=HtfM1Config(
                    confirm_timeframe="3",
                    htf_timeframes=HTF_TIMEFRAMES,  # H4/H2/H1/M30/M15 -- no M5-specific carve-out needed here
                    # Same rule both while waiting AND once a trade is
                    # open (unlike XAUUSD, no narrowing on fill) -- one
                    # opposite OB on M15 or M30, OR two opposite OBs on
                    # M3 (the confirmation timeframe itself, given the
                    # same "needs two, not one" noise treatment XAUUSD's
                    # OWN Trend Manager M1-exit rule got). User's own
                    # words, 2026-08-25: "invalidation set m15 or m30,
                    # opposite ob... two opposite ob's on m3 also
                    # invalidates."
                    waiting_invalidation=HtfM1InvalidationRule(
                        single_ob_timeframes=("15", "30"), double_ob_timeframe="3"),
                    active_invalidation=HtfM1InvalidationRule(
                        single_ob_timeframes=("15", "30"), double_ob_timeframe="3"),
                    sl_buffer=20.0,  # reuses BTCUSD's existing Reversal Manager buffer, user's explicit call
                    active_invalidation_anchor="opened_at",
                ),
            ),
            SymbolConfig(
                "ETHUSD",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_ETHUSD_LIVE_FILE", "tv_scraper_ethusd_live.json"),
                # "3" not "5" as of 2026-08-22 -- user changed ETHUSD's
                # actual bottom chart pane from M5 to M3 ("change it to
                # m3 everywhere"), matching BTCUSD's own ltf_timeframes
                # above (already "3", was ahead of this one).
                ltf_timeframes=("3",),
                atr_state_file=tv_atr_file,
                htf_m1=HtfM1Config(
                    confirm_timeframe="3",
                    htf_timeframes=HTF_TIMEFRAMES,
                    waiting_invalidation=HtfM1InvalidationRule(
                        single_ob_timeframes=("15", "30"), double_ob_timeframe="3"),
                    active_invalidation=HtfM1InvalidationRule(
                        single_ob_timeframes=("15", "30"), double_ob_timeframe="3"),
                    sl_buffer=2.0,  # reuses ETHUSD's existing Reversal Manager buffer, user's explicit call
                    active_invalidation_anchor="opened_at",
                ),
            ),
            # USOIL/USTEC (added 2026-08-19) -- see trend_manager's own
            # config.py for the shared-tv_scraper-process rationale.
            # HTF_TIMEFRAMES above still gets used unchanged for these
            # two (_register_htf_retests iterates it for every symbol) --
            # harmless: no H4/H2 alerts are expected to be configured for
            # these two, so those two entries simply never find zone data
            # and no-op, leaving H1/M30/M15 (their real 3 parents) as the
            # only ones that ever actually register a wait. NOT yet wired
            # into Execution Bridge or entries.py's SYMBOL_SL_BUFFER --
            # same "SL buffers pending" gap as Trend Manager's own entries.
            SymbolConfig(
                "USOIL",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                # M3 confirmation removed, M15 is now the default --
                # user's explicit rule, 2026-08-26: "remove m3
                # confirmations and make m15 default... i have changed
                # both alerts, atr alert, also zone alert to m15" (the
                # TradingView/Pine side no longer even sends M3 data).
                # ltf_timeframes is actually UNUSED for USOIL/USTEC in
                # practice -- run_once_symbol always dispatches to
                # _check_direction_atr_or_ob for these two (since
                # atr_confirm_timeframe is set), which reads ONLY
                # atr_confirm_timeframe, never ltf_timeframes; kept in
                # sync anyway so nothing here still points at a
                # timeframe TradingView no longer sends.
                ltf_timeframes=("15",),
                atr_confirm_timeframe="15",
                atr_state_file=tv_atr_file,
            ),
            SymbolConfig(
                "USTEC",
                tv_zone_file,
                os.getenv("SIGNAL_ENGINE_USOIL_USTEC_LIVE_FILE", "tv_scraper_usoil_ustec_live.json"),
                # Same change as USOIL above, same day, same reasoning.
                ltf_timeframes=("15",),
                atr_confirm_timeframe="15",
                atr_state_file=tv_atr_file,
            ),
        ],
        poll_seconds=float(os.getenv("REVERSAL_MANAGER_POLL_SECONDS", "5.0")),
        state_file=os.getenv("REVERSAL_MANAGER_STATE_FILE", "reversal_manager_state.json"),
        magic_number=int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
        manual_events_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_MANUAL_EVENTS_FILE",
                                      "execution_bridge_manual_events_reversal.json"),
    )
