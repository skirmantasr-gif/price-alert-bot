"""2026-11-25 Telegram proofread: pre-market prev close / market line, options expiry year + brand-new wording."""
import unittest
from datetime import date, timedelta
from unittest import mock

import bot
import context
import options

OPEN = 1_800_000_000          # a made-up 09:30 ET regular-session start


def meta(**kw):
    m = {"currentTradingPeriod": {"regular": {"start": OPEN, "end": OPEN + 23400}},
         "chartPreviousClose": 113.68, "regularMarketPrice": 109.50, "regularMarketTime": OPEN - 60_000}
    m.update(kw)
    return m


class PrevClose(unittest.TestCase):
    def test_pre_market_uses_last_regular_close(self):
        self.assertEqual(bot.prev_close_of(meta(), now=OPEN - 3600), 109.50)

    def test_regular_session_uses_chart_prev_close(self):
        m = meta(chartPreviousClose=109.50, regularMarketPrice=104.0, regularMarketTime=OPEN + 600)
        self.assertEqual(bot.prev_close_of(m, now=OPEN + 600), 109.50)

    def test_after_hours_unchanged(self):
        m = meta(chartPreviousClose=109.50, regularMarketPrice=103.87, regularMarketTime=OPEN + 23400)
        self.assertEqual(bot.prev_close_of(m, now=OPEN + 25000), 109.50)

    def test_missing_fields(self):
        self.assertIsNone(bot.prev_close_of({}, now=OPEN))
        self.assertEqual(bot.prev_close_of({"previousClose": 5}, now=OPEN), 5.0)


class MarketLine(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(context, "_us", lambda s: True),
                  mock.patch.object(context, "sector_etf", lambda s, p=None: "XBI"),
                  mock.patch.object(context, "day_change", lambda s: 0.066 if s == "XBI" else -0.005)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_before_open_says_last_session(self):
        with mock.patch.object(context, "before_open", lambda now=None: True):
            line = context.market_line("ZNTX", 0.07)
            self.assertTrue(line.startswith("🌐 Last session:"))
            self.assertNotIn("mostly the market", line)
            self.assertFalse(context.mostly_market("ZNTX", 0.07))

    def test_regular_says_same_day(self):
        with mock.patch.object(context, "before_open", lambda now=None: False):
            line = context.market_line("ZNTX", 0.07)
            self.assertTrue(line.startswith("🌐 Same day:"))
            self.assertIn("mostly the market", line)

    def test_before_open_reads_spy_period(self):
        with mock.patch.object(context, "_meta", lambda s: meta()):
            self.assertTrue(context.before_open(now=OPEN - 1))
            self.assertFalse(context.before_open(now=OPEN + 1))


class OptionsWording(unittest.TestCase):
    def test_far_expiry_has_year(self):
        far = date.today() + timedelta(days=800)
        self.assertIn(str(far.year), options._d(far.isoformat()))
        near = date.today() + timedelta(days=20)
        self.assertNotIn(str(near.year), options._d(near.isoformat()))

    def test_brand_new_contract(self):
        self.assertIn("brand-new", options._x(1360, 0))
        self.assertEqual(options._x(600, 100), "6× the open positions")

    def test_zero_money(self):
        self.assertEqual(options._m(0), "$0")
        self.assertEqual(options._m(505e3), "$505k")


if __name__ == "__main__":
    unittest.main()
