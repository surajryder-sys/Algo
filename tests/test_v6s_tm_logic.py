"""TM-STR pure logic: M15 bias arbitration, M5 entry gating, one-trade-per-
CISD eligibility, initial-SL selection, and the bias-feed watch."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from v6_sentinel import trend_bias, trend_entry, trend_main
from v6_sentinel.flip_state import Confirmed


def _fs(confirmed, event_time=None, since=1000):
    return NS(confirmed=confirmed, confirmed_since_time=since,
              last_event=(NS(bar_time=event_time) if event_time is not None else None))


class _FakeTracker:
    def __init__(self, fs):
        self.fs = fs

    def update(self, symbol, tf):
        return self.fs


def _cisd(kind, t):
    return NS(last_cisd=kind, last_cisd_time=t)


class BiasTests(unittest.TestCase):
    def _bias(self, fs, standing_cisd):
        with mock.patch("v6_sentinel.cisd_bridge.read_cisd", return_value=standing_cisd):
            return trend_bias.compute_bias(_FakeTracker(fs), "X", 15)

    def test_atr_newer_than_cisd_wins(self):
        b = self._bias(_fs(Confirmed.BULL, 900), _cisd("bearish", 500))
        self.assertEqual((b.direction, b.source, b.event_time), (1, "ATR", 900))

    def test_cisd_newer_than_atr_wins(self):
        b = self._bias(_fs(Confirmed.BULL, 300), _cisd("bearish", 500))
        self.assertEqual((b.direction, b.source, b.event_time), (-1, "CISD", 500))

    def test_tie_goes_to_atr(self):
        b = self._bias(_fs(Confirmed.BULL, 500), _cisd("bearish", 500))
        self.assertEqual((b.direction, b.source), (1, "ATR"))

    def test_no_cisd_means_atr_alone_and_weak_is_down(self):
        b = self._bias(_fs(Confirmed.BEAR, 900), None)
        self.assertEqual((b.direction, b.source), (-1, "ATR"))

    def test_no_atr_event_yet_uses_confirmed_since_time(self):
        b = self._bias(_fs(Confirmed.BULL, None, since=777), None)
        self.assertEqual(b.event_time, 777)

    def test_atr_with_nothing_to_offer_gives_no_bias(self):
        self.assertIsNone(self._bias(None, _cisd("bullish", 500)))


class EntryTests(unittest.TestCase):
    BULL = trend_bias.Bias(1, "ATR", 100)
    BEAR = trend_bias.Bias(-1, "CISD", 100)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fresh, self.lines = {}, {5: [("ATR1", 4370.0), ("ATR2", 4360.0), ("ST", 4365.0)], 15: []}
        for target, fn in (("v6_sentinel.cisd_bridge.fresh_cisd", lambda s, tf: self.fresh.get(tf)),
                           ("v6_sentinel.sl_basis.line_values", lambda s, tf, c: self.lines.get(tf, []))):
            p = mock.patch(target, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _cisd(kind, t, swing=None):
        return NS(last_cisd=kind, last_cisd_time=t, bar_time=t, last_cisd_has_swing=swing is not None,
                  last_cisd_swing_level=swing or 0.0)

    def _store(self, name="el.json"):
        return trend_entry.TrendEligibilityStore(os.path.join(self.tmp, name))

    def _sig(self, bias, el, bid=4378.0, ask=4378.1):
        return trend_entry.find_signal("X", bias, (5,), el, 2.0, bid, ask)

    def test_fresh_m5_cisd_matching_bias_buys_with_farthest_m5_line(self):
        self.fresh[5] = self._cisd("bullish", 1000)
        s = self._sig(self.BULL, self._store())
        self.assertEqual((s.direction, s.sl, s.sl_source, s.trigger), (1, 4358.0, "M5/ATR2", "M5CD"))

    def test_bias_against_the_cisd_means_no_trade(self):
        self.fresh[5] = self._cisd("bullish", 1000)
        self.assertIsNone(self._sig(self.BEAR, self._store()))

    def test_no_fresh_cisd_means_no_trade(self):
        self.assertIsNone(self._sig(self.BULL, self._store()))

    def test_same_cisd_event_cannot_fire_twice_but_a_new_one_can(self):
        el = self._store()
        self.fresh[5] = self._cisd("bullish", 1000)
        el.mark_traded(5, self._sig(self.BULL, el).event_time)
        self.assertIsNone(self._sig(self.BULL, el))
        self.fresh[5] = self._cisd("bullish", 1300)
        self.assertIsNotNone(self._sig(self.BULL, el))

    def test_eligibility_survives_a_restart(self):
        self._store().mark_traded(5, 1000)
        self.assertTrue(self._store().is_traded(5, 1000))
        self.assertFalse(self._store().is_traded(5, 1001))

    def test_m5_has_no_usable_line_so_farthest_m15_line(self):
        self.fresh[5] = self._cisd("bullish", 2000)
        self.lines.update({5: [("ST", 4390.0)], 15: [("ATR1", 4340.0), ("ATR2", 4330.0)]})  # M5 line is above price
        s = self._sig(self.BULL, self._store())
        self.assertEqual((s.sl, s.sl_source), (4328.0, "M15/ATR2"))

    def test_no_line_anywhere_falls_back_to_the_cisd_swing(self):
        self.lines.update({5: [], 15: []})
        self.fresh[5] = self._cisd("bullish", 2000, swing=4350.0)
        s = self._sig(self.BULL, self._store())
        self.assertEqual((s.sl, s.sl_source), (4348.0, "SWING"))

    def test_no_line_and_no_swing_is_no_trade_and_the_event_is_not_consumed(self):
        self.lines.update({5: [], 15: []})
        self.fresh[5] = self._cisd("bullish", 2000, swing=None)
        el = self._store()
        self.assertIsNone(self._sig(self.BULL, el))
        self.assertFalse(el.is_traded(5, 2000))

    def test_sell_uses_farthest_line_above_entry(self):
        self.lines.update({5: [("ATR1", 4385.0), ("ATR2", 4395.0), ("ST", 4360.0)], 15: []})
        self.fresh[5] = self._cisd("bearish", 3000)
        s = self._sig(self.BEAR, self._store())
        self.assertEqual((s.direction, s.sl, s.sl_source), (-1, 4397.0, "M5/ATR2"))

    def test_a_line_exactly_at_entry_is_not_usable(self):
        self.lines.update({5: [("ST", 4378.1)], 15: []})
        self.fresh[5] = self._cisd("bearish", 3001, swing=4400.0)
        s = self._sig(self.BEAR, self._store(), bid=4378.1)
        self.assertEqual(s.sl_source, "SWING")


class FeedWatchTests(unittest.TestCase):
    def _warm(self, w):
        """Two distinct ticks = an observed change = prices are flowing."""
        self.assertEqual(w.update(0, 100, True), (False, None))
        self.assertEqual(w.update(1, 101, True), (False, None))

    def test_fresh_feed_never_blocks(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        for t in range(2, 300):
            self.assertEqual(w.update(t, 100 + t, True), (False, None))

    def test_stale_but_under_the_threshold_does_not_block(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        self.assertEqual(w.update(2, 102, False), (False, None))        # goes stale at t=2
        self.assertEqual(w.update(61, 161, False), (False, None))       # 59s stale

    def test_blocks_and_alerts_once_after_the_threshold(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        w.update(2, 102, False)
        self.assertEqual(w.update(62, 162, False), (True, "stale"))     # 60s
        self.assertEqual(w.update(63, 163, False), (True, None))        # still blocked, no repeat alert
        self.assertEqual(w.update(90, 190, False), (True, None))

    def test_recovery_unblocks_and_says_so_once(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        w.update(2, 102, False)
        w.update(62, 162, False)
        self.assertEqual(w.update(64, 164, True), (False, "recovered"))
        self.assertEqual(w.update(65, 165, True), (False, None))

    def test_a_second_outage_alerts_again(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        w.update(2, 102, False)
        w.update(62, 162, False)
        w.update(64, 164, True)
        w.update(70, 170, False)
        self.assertEqual(w.update(130, 230, False), (True, "stale"))

    def test_closed_market_is_never_a_fault(self):
        w = trend_main.BiasFeedWatch()
        for t in range(0, 1200, 5):                # tick time never changes, feed stale the whole time
            self.assertEqual(w.update(t, 555, False), (False, None))

    def test_a_single_first_tick_is_only_a_baseline_not_flowing(self):
        w = trend_main.BiasFeedWatch()
        for t in range(0, 300):
            self.assertEqual(w.update(t, 100, False), (False, None))

    def test_market_closing_while_blocked_unblocks_silently(self):
        w = trend_main.BiasFeedWatch()
        self._warm(w)
        w.update(2, 102, False)
        self.assertEqual(w.update(62, 162, False), (True, "stale"))
        # ticks stop; once the flowing window passes it is no longer a fault
        self.assertEqual(w.update(62 + 130, 162, False), (False, None))

    def test_no_tick_at_all_does_not_crash_or_block(self):
        w = trend_main.BiasFeedWatch()
        self.assertEqual(w.update(0, None, False), (False, None))


if __name__ == "__main__":
    unittest.main()
