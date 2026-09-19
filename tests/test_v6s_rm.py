"""RM tests: RM-ICT's default zone-edge SL and its risk-reducing SL override,
the shared line/swing SL logic (RM-STR's behaviour must not change), and the
zone Block announcing each rejection once instead of every cycle."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from v6_sentinel import reversal_ict, sl_basis
from v6_sentinel.nlb_nsb_block import BlockStore

NO_SWING = NS(last_cisd_has_swing=False, last_cisd_swing_level=0.0)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _zone(direction, btm, top, tf="60"):
    return {"symbol": "XAUUSD", "timeframe": tf, "timeframe_name": "H1", "direction": direction,
            "role": "no_short_buffer" if direction == "bull" else "no_long_buffer", "top": top, "btm": btm,
            "formed_time": 1, "formed_time_confirmed": True, "retested": True, "retested_at": 1,
            "retested_source": "live", "zone_id": "Z"}


class IctSlTests(unittest.TestCase):
    """Bullish zone 4360-4380 -> zone-edge SL 4358; entry (ask) 4375 is 17 points away, so the
    override is considered. Override threshold is 15 points, buffer 2."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lines, self.fresh = {}, None
        for target, fn in (("v6_sentinel.cisd_bridge.fresh_cisd", lambda s, tf: self.fresh),
                           ("v6_sentinel.sl_basis.line_values", lambda s, tf, c: self.lines.get(tf, []))):
            p = mock.patch(target, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def _run(self, zone, kind, lines, swing, bid, ask):
        self.lines = lines
        self.fresh = NS(last_cisd=kind, bar_time=100, last_cisd_has_swing=swing is not None,
                        last_cisd_swing_level=swing or 0.0)
        block = os.path.join(self.tmp, "block.json")
        _write_json(block, {"Z": zone})
        elig = reversal_ict.ICTEligibilityStore(os.path.join(self.tmp, "elig.json"))
        sigs = reversal_ict.find_ict_signals("XAUUSD", block, elig, 2.0, bid, ask, 15.0)
        self.assertEqual(len(sigs), 1, "the trade must always still fire")
        return sigs[0].sl, sigs[0].sl_source

    BUY_ZONE = _zone("bull", 4360.0, 4380.0)
    FAR = dict(bid=4374.9, ask=4375.0)        # zone SL 4358 is 17 points from entry
    NEAR = dict(bid=4361.9, ask=4362.0)       # zone SL is only 4 points from entry

    def test_only_a_22_point_line_is_rejected_and_the_same_zone_sl_is_kept(self):
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [("ATR2", 4355.0)], 3: []}, None, **self.FAR), (4358.0, "ZONE"))

    def test_farthest_line_wider_than_zone_sl_is_rejected_with_no_re_search(self):
        lines = {5: [("ATR2", 4355.0), ("ATR1", 4368.0), ("ST", 4372.0)], 3: []}     # tighter lines exist too
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", lines, None, **self.FAR), (4358.0, "ZONE"))

    def test_rejected_m5_pick_does_not_fall_through_to_m3(self):
        lines = {5: [("ATR2", 4355.0)], 3: [("ST", 4363.0)]}
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", lines, None, **self.FAR), (4358.0, "ZONE"))

    def test_farthest_line_that_is_tighter_is_taken(self):
        lines = {5: [("ATR1", 4368.0), ("ST", 4372.0)], 3: []}
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", lines, None, **self.FAR), (4366.0, "M5/ATR1"))

    def test_m5_with_nothing_usable_uses_a_tighter_m3_line(self):
        lines = {5: [("ST", 4380.0)], 3: [("ST", 4363.0)]}
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", lines, None, **self.FAR), (4361.0, "M3/ST"))

    def test_swing_is_used_only_when_tighter(self):
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [], 3: []}, 4362.0, **self.FAR), (4360.0, "SWING"))
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [], 3: []}, 4345.0, **self.FAR), (4358.0, "ZONE"))

    def test_nothing_usable_keeps_the_zone_sl_and_still_fires(self):
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [], 3: []}, None, **self.FAR), (4358.0, "ZONE"))

    def test_a_line_equal_to_the_zone_sl_is_not_tighter(self):
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [("ST", 4360.0)], 3: []}, None, **self.FAR), (4358.0, "ZONE"))

    def test_sl_within_15_points_is_never_touched(self):
        self.assertEqual(self._run(self.BUY_ZONE, "bullish", {5: [("ST", 4368.0)], 3: []}, None, **self.NEAR), (4358.0, "ZONE"))

    def test_zone_size_is_not_a_condition_a_small_zone_with_a_far_sl_can_override(self):
        small = _zone("bull", 4360.0, 4370.0)                       # only 10 points tall
        got = self._run(small, "bullish", {5: [("ATR1", 4368.0)], 3: []}, None, bid=4382.9, ask=4383.0)   # SL 25 away
        self.assertEqual(got, (4366.0, "M5/ATR1"))

    def test_sell_side_mirrors_it(self):
        sell = _zone("bear", 4370.0, 4390.0)                        # zone SL 4392, bid 4375 -> 17 away
        kw = dict(bid=4375.0, ask=4375.1)
        self.assertEqual(self._run(sell, "bearish", {5: [("ATR2", 4395.0), ("ATR1", 4385.0)], 3: []}, None, **kw), (4392.0, "ZONE"))
        self.assertEqual(self._run(sell, "bearish", {5: [("ATR1", 4385.0), ("ST", 4380.0)], 3: []}, None, **kw), (4387.0, "M5/ATR1"))


class SharedLineLogicTests(unittest.TestCase):
    def test_default_rule_takes_the_farthest_usable_line_with_no_tightness_check(self):
        lines = {5: [("ATR1", 4368.0), ("ATR2", 4355.0)]}
        with mock.patch("v6_sentinel.sl_basis.line_values", side_effect=lambda s, tf, c: lines.get(tf, [])):
            self.assertEqual(sl_basis.initial_sl_basis("X", 1, 4375.0, NO_SWING, {}), (4355.0, "M5/ATR2"))

    def test_timeframe_order_is_configurable_m5_then_m15(self):
        lines = {5: [], 15: [("ST", 4300.0)]}
        with mock.patch("v6_sentinel.sl_basis.line_values", side_effect=lambda s, tf, c: lines.get(tf, [])):
            self.assertEqual(sl_basis.initial_sl_basis("X", 1, 4375.0, NO_SWING, {}, timeframes=(5, 15)), (4300.0, "M15/ST"))
            self.assertIsNone(sl_basis.initial_sl_basis("X", 1, 4375.0, NO_SWING, {}))          # default (5, 3) has nothing


class BlockRejectionSpamTests(unittest.TestCase):
    def test_a_rejected_zone_is_announced_once_not_every_cycle(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        scraper = os.path.join(tmp, "zones.json")
        _write_json(scraper, {
            "XAUUSD|15|bear": {"bad": {"start_time": 1789749028, "top": 4718.88, "btm": 4710.893,
                                       "virgin": True, "formed_time_confirmed": True}},     # not on the M15 candle grid
            "XAUUSD|15|bull": {"ok": {"start_time": 1789748100, "top": 4380.0, "btm": 4370.0,
                                      "virgin": True, "formed_time_confirmed": True}},      # on the grid
        })
        store = BlockStore(os.path.join(tmp, "block.json"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(30):
                store.sync_from_scraper(scraper, "XAUUSD")
        self.assertEqual(sum("REJECTED" in line for line in out.getvalue().splitlines()), 1)
        self.assertEqual(len(store.zones()), 1, "the valid zone is still seeded; only the bad one is rejected")

    def test_a_restart_announces_it_once_more(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        scraper = os.path.join(tmp, "zones.json")
        _write_json(scraper, {"XAUUSD|15|bear": {"bad": {"start_time": 1789749028, "top": 4718.88, "btm": 4710.893,
                                                         "virgin": True, "formed_time_confirmed": True}}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for n in range(2):                                   # two "process lifetimes"
                store = BlockStore(os.path.join(tmp, f"block{n}.json"))
                for _ in range(5):
                    store.sync_from_scraper(scraper, "XAUUSD")
        self.assertEqual(sum("REJECTED" in line for line in out.getvalue().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
