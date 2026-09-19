"""TM-STR's run_once against a FAKE broker: entry, no-repeat, restart
persistence, decision-only mode, bias-flip close, reverse-in-one-cycle,
redundant signal, rejected order retry, partial + breakeven management, and
the stale-feed pause.

SAFETY: several tests run with enable_trading=True. setUp replaces every
order path with a mock, booby-traps MetaTrader5.order_send so it raises if
ever reached, and asserts all of that before any test body runs.
"""
import dataclasses
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import MetaTrader5 as mt5

from v6_sentinel import broker, sl_manager, trade_manager, trend_bias, trend_config, trend_entry, trend_main

MAGIC = 26091803
BULL = trend_bias.Bias(1, "ATR", 100)
BEAR = trend_bias.Bias(-1, "CISD", 200)


class _Pos:
    def __init__(self, ticket, typ, vol, price, sl, comment, magic=MAGIC):
        self.ticket, self.type, self.volume, self.price_open, self.sl, self.tp = ticket, typ, vol, price, sl, 0.0
        self.comment, self.magic, self.symbol = comment, magic, "XAUUSD"


def _fresh(kind, t, swing=None):
    return NS(last_cisd=kind, last_cisd_time=t, bar_time=t, last_cisd_has_swing=swing is not None,
              last_cisd_swing_level=swing or 0.0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.positions, self.alerts = [], [], []
        self.tick, self.fail_next, self.next_ticket = [4378.0, 4378.1], False, 100
        self.fresh, self.bias, self.far = {}, None, 4295.0
        self.lines = {5: [("ATR1", 4370.0), ("ATR2", 4360.0)], 15: []}
        self._tick_time = 1000

        def patch(target, **kw):
            p = mock.patch(target, **kw)
            m = p.start()
            self.addCleanup(p.stop)
            return m

        patch("MetaTrader5.order_send", side_effect=RuntimeError("REAL mt5.order_send reached"))
        patch("MetaTrader5.symbol_info", return_value=NS(volume_step=0.01))
        patch("MetaTrader5.symbol_info_tick", side_effect=self._symbol_tick)
        patch("v6_sentinel.bridge.read_lines", return_value=(1.0, 2.0))          # feed fresh unless a test says otherwise
        patch("v6_sentinel.broker.get_tick_price", side_effect=lambda s: tuple(self.tick))
        patch("v6_sentinel.broker.get_positions", side_effect=lambda s, m: [p for p in self.positions if p.magic == m])
        patch("v6_sentinel.broker.send_market_order", side_effect=self._send)
        patch("v6_sentinel.broker.close_position", side_effect=self._close)
        patch("v6_sentinel.broker.modify_position_sl", side_effect=self._modify)
        patch("v6_sentinel.cisd_bridge.fresh_cisd", side_effect=lambda s, tf: self.fresh.get(tf))
        patch("v6_sentinel.sl_basis.line_values", side_effect=lambda s, tf, c: self.lines.get(tf, []))
        patch("v6_sentinel.trend_bias.compute_bias", side_effect=lambda t, s, tf: self.bias)
        patch("v6_sentinel.trend_main._trailing_far_line", side_effect=lambda s, tf, d: self.far)
        patch("v6_sentinel.alerts.send_alert", side_effect=self.alerts.append)

        # ---- hard safety guard: nothing here may ever reach the real broker ----
        for fn in (broker.send_market_order, broker.close_position, broker.modify_position_sl, mt5.order_send):
            self.assertIsInstance(fn, mock.Mock)

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---- fake broker ----
    def _symbol_tick(self, sym):
        self._tick_time += 1
        return NS(time=self._tick_time)

    def _get_open(self):
        return list(self.positions)

    def _send(self, symbol, direction, lots, sl, magic, deviation, comment):
        self.calls.append(("OPEN", direction, sl, magic, comment))
        if self.fail_next:
            return broker.OrderResult(False, 10044, "session closed", None)
        self.next_ticket += 1
        typ = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
        self.positions.append(_Pos(self.next_ticket, typ, lots, self.tick[1] if direction == 1 else self.tick[0],
                                   sl, comment, magic))
        return broker.OrderResult(True, 10009, "", self.next_ticket)

    def _close(self, symbol, position, deviation, volume=None, comment=""):
        self.calls.append(("CLOSE", position.ticket, volume, comment))
        if volume is None:
            self.positions.remove(position)
        else:
            position.volume = round(position.volume - volume, 8)
        return broker.OrderResult(True, 10009, "", position.ticket)

    def _modify(self, symbol, ticket, new_sl, tp=0.0):
        self.calls.append(("MODSL", ticket, round(new_sl, 3)))
        for p in self.positions:
            if p.ticket == ticket:
                p.sl = new_sl
        return broker.OrderResult(True, 10009, "", ticket)

    # ---- helpers ----
    def _rt(self, enable=True):
        f = lambda n: os.path.join(self.tmp, n)
        cfg = dataclasses.replace(
            trend_config.load_symbol_config("XAUUSD"), enable_trading=enable, state_file=f("tm.json"),
            sl_state_file=f("sl.json"), eligibility_state_file=f("el.json"), bridge_bar_flip_state_file=f("bbf.json"),
            heartbeat_file=f("hb.json"), decision_log_file=f("log.jsonl"))
        return trend_main._SymbolRuntime(
            cfg=cfg, tracker=None, eligibility=trend_entry.TrendEligibilityStore(cfg.eligibility_state_file),
            sl_mgr=sl_manager.SLManager(cfg.sl_state_file, cfg.breakeven_trigger_points, cfg.sl_buffer),
            tm_mgr=trade_manager.TradeManager(cfg.state_file, cfg.partial1_trigger_points, cfg.partial1_fraction,
                                              cfg.partial2_trigger_points, cfg.partial2_fraction),
            feed_watch=trend_main.BiasFeedWatch())

    def _long(self, ticket=11, vol=0.10, price=4300.0):
        self.positions.append(_Pos(ticket, mt5.POSITION_TYPE_BUY, vol, price, 4290.0, "V6S-TM-STR-M5CD"))

    # ---- entry ----
    def test_entry_opens_one_buy_with_right_magic_sl_and_comment(self):
        rt = self._rt()
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 1000)
        trend_main.run_once(rt)
        self.assertEqual(self.calls, [("OPEN", 1, 4358.0, MAGIC, "V6S-TM-STR-M5CD")])

    def test_same_m5_cisd_does_not_trade_again_and_a_new_one_does(self):
        rt = self._rt()
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 1000)
        trend_main.run_once(rt)
        trend_main.run_once(rt)
        self.assertEqual(len(self.calls), 1)
        self.fresh[5] = _fresh("bullish", 1300)
        self.positions.clear()
        trend_main.run_once(rt)
        self.assertEqual(len(self.calls), 2)

    def test_trading_off_makes_zero_broker_calls(self):
        rt = self._rt(enable=False)
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 1000)
        trend_main.run_once(rt)
        self.assertEqual(self.calls, [])

    def test_rejected_order_does_not_consume_the_event_and_is_retried(self):
        rt = self._rt()
        self.bias, self.fresh[5], self.fail_next = BULL, _fresh("bullish", 4000), True
        trend_main.run_once(rt)
        self.assertFalse(rt.eligibility.is_traded(5, 4000))
        self.fail_next = False
        trend_main.run_once(rt)
        self.assertEqual([c[0] for c in self.calls], ["OPEN", "OPEN"])
        self.assertTrue(rt.eligibility.is_traded(5, 4000))

    def test_same_direction_signal_while_long_is_ignored_and_marked_handled(self):
        rt = self._rt()
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 3000)
        self._long()
        trend_main.run_once(rt)
        self.assertFalse(any(c[0] == "OPEN" for c in self.calls))
        self.assertTrue(rt.eligibility.is_traded(5, 3000))

    # ---- bias flip ----
    def test_bias_flipping_against_an_open_buy_closes_it_immediately(self):
        rt = self._rt()
        self.bias = BEAR
        self._long()
        trend_main.run_once(rt)
        self.assertEqual(self.calls, [("CLOSE", 11, None, "V6S-TM-STR-M5CD-BF")])
        self.assertEqual(self.positions, [])

    def test_flip_plus_matching_cisd_in_one_cycle_closes_then_reverses(self):
        rt = self._rt()
        self.bias, self.fresh[5] = BEAR, _fresh("bearish", 2000)
        self.lines[5] = [("ATR1", 4385.0), ("ATR2", 4395.0)]
        self._long(12)
        trend_main.run_once(rt)
        self.assertEqual([c[0] for c in self.calls], ["CLOSE", "OPEN"])
        self.assertEqual(self.calls[1][1:3], (-1, 4397.0))

    def test_no_bias_never_closes_an_open_trade(self):
        rt = self._rt()
        self.bias = None
        self._long()
        trend_main.run_once(rt)
        self.assertFalse(any(c[0] == "CLOSE" and c[2] is None for c in self.calls))
        self.assertEqual(len(self.positions), 1)

    # ---- management ----
    def test_partial_at_plus_10_then_breakeven_sl(self):
        rt = self._rt()
        self.bias, self.tick, self.far = BULL, [4311.0, 4311.1], 4295.0
        self._long(15)
        trend_main.run_once(rt)
        self.assertIn(("CLOSE", 15, 0.07, "V6S-TM-STR-M5CD-P1"), self.calls)
        trend_main.run_once(rt)
        self.assertIn(("MODSL", 15, 4300.0), self.calls)

    # ---- stale-feed pause (the watch's own logic is tested purely in test_v6s_tm_logic) ----
    def test_stale_feed_pauses_new_entries_and_alerts_once(self):
        rt = self._rt()
        rt.feed_watch = NS(update=mock.Mock(side_effect=[(True, "stale"), (True, None)]))
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 1000)
        trend_main.run_once(rt)
        trend_main.run_once(rt)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("PAUSED", self.alerts[0])

    def test_stale_feed_does_not_stop_a_bias_flip_close(self):
        rt = self._rt()
        rt.feed_watch = NS(update=mock.Mock(return_value=(True, None)))
        self.bias = BEAR
        self._long()
        trend_main.run_once(rt)
        self.assertEqual(self.calls, [("CLOSE", 11, None, "V6S-TM-STR-M5CD-BF")])

    def test_stale_feed_does_not_stop_managing_an_open_trade(self):
        rt = self._rt()
        rt.feed_watch = NS(update=mock.Mock(return_value=(True, None)))
        self.bias, self.tick = BULL, [4311.0, 4311.1]
        self._long(15)
        trend_main.run_once(rt)
        self.assertIn(("CLOSE", 15, 0.07, "V6S-TM-STR-M5CD-P1"), self.calls)

    def test_recovery_sends_the_resumed_alert_and_entries_work_again(self):
        rt = self._rt()
        rt.feed_watch = NS(update=mock.Mock(return_value=(False, "recovered")))
        self.bias, self.fresh[5] = BULL, _fresh("bullish", 1000)
        trend_main.run_once(rt)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("resumed", self.alerts[0])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
