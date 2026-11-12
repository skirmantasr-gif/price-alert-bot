"""🎲 Options context: expiry dates, max pain, walls, expected move, unusual activity. No network."""
import unittest
from datetime import date
from unittest import mock

import options

TODAY = date(2026, 11, 12)


def chain(price=10.0, iv30=60.0, extra=()):
    """Monthly Nov 20 chain: big call OI at 12, big put OI at 8, ATM straddle 0.50 + 0.50."""
    exp = "261120"
    opts = [
        (f"ZNTX{exp}C00010000", 0.45, 0.55, 300, 50),
        (f"ZNTX{exp}P00010000", 0.45, 0.55, 300, 50),
        (f"ZNTX{exp}C00012000", 0.05, 0.10, 5000, 100),
        (f"ZNTX{exp}P00008000", 0.05, 0.10, 4000, 100),
        (f"ZNTX{exp}C00009000", 1.00, 1.10, 100, 10),
        ("ZNTX261113C00010000", 0.20, 0.25, 50, 10),   # weekly expiry tomorrow
    ] + list(extra)
    return {"data": {"current_price": price, "iv30": iv30, "options": [
        {"option": o, "bid": b, "ask": a, "open_interest": oi, "volume": v} for o, b, a, oi, v in opts]}}


class Basics(unittest.TestCase):
    def test_monthly_expiry(self):
        self.assertEqual(options.monthly_expiry(date(2026, 11, 12)), date(2026, 11, 20))
        self.assertEqual(options.monthly_expiry(date(2026, 11, 21)), date(2026, 12, 18))

    def test_summary(self):
        s = options.summarize(*options.parse(chain()), today=TODAY)
        self.assertEqual((s["exp"], s["days"]), ("2026-11-20", 8))
        self.assertEqual((s["call_wall"], s["put_wall"]), (12.0, 8.0))
        self.assertAlmostEqual(s["move_pct"], 10.0)          # straddle 1.00 on a $10 stock
        self.assertIsNotNone(s["max_pain"])
        self.assertAlmostEqual(s["pc_ratio"], 4300 / 5400)

    def test_wide_quotes_fall_back_to_implied_volatility(self):
        wide = chain()
        for o in wide["data"]["options"]:
            if "00010000" in o["option"] and "261120" in o["option"]:
                o["bid"], o["ask"] = 0.05, 2.00
        s = options.summarize(*options.parse(wide), today=TODAY)
        self.assertAlmostEqual(s["move_pct"], 60 * (8 / 365) ** 0.5, places=3)

    def test_penny_stock_no_expected_move(self):
        s = options.summarize(*options.parse(chain(price=0.6, iv30=200)), today=TODAY)
        self.assertIsNone(s["move_pct"])

    def test_unusual(self):
        u = options.unusual(options.parse(chain(extra=[("ZNTX261120C00011000", 0.40, 0.50, 100, 3000)]))[2], TODAY)
        self.assertEqual(u[0][:5], ("2026-11-20", "C", 11.0, 3000, 100))
        self.assertAlmostEqual(u[0][5], 3000 * 0.45 * 100)

    def test_line_and_alert_window(self):
        s = options.summarize(*options.parse(chain()), today=TODAY)
        txt = options.line("ZNTX", s)
        self.assertIn("options expect ±10% by then", txt)
        self.assertIn("call wall $12.00", txt)
        with mock.patch.object(options, "get", lambda t: s):
            self.assertEqual(options.near_expiry("ZNTX"), "")            # 8 days: not in alerts yet
            near = dict(s, days=3)
        with mock.patch.object(options, "get", lambda t: near):
            self.assertIn("expiry Fri Nov 20, 3 days", options.near_expiry("ZNTX"))

    def test_failure_is_silent_and_retried(self):
        with mock.patch.object(options, "ON", True), mock.patch.object(options, "_get", side_effect=OSError), \
                mock.patch.dict(options._cache, clear=True):
            self.assertIsNone(options.get("ZNTX"))
            self.assertLess(options._cache["ZNTX"][0], __import__("time").time() - options.CACHE_SECONDS + 400)


class Unusual(unittest.TestCase):
    def test_once_per_contract_per_day(self):
        s = options.summarize(*options.parse(chain()), today=TODAY)
        s["unusual"] = [("2026-11-20", "C", 11.0, 3000, 100, 135000.0)]
        sent = []
        w = options.UnusualWatch(lambda t, q: sent.append((t, q)), ["ZNTX"])
        state = {}
        with mock.patch.object(options, "get", lambda t: s):
            self.assertTrue(w.check(state, True))
            w.last = float("-inf")
            self.assertFalse(w.check(state, True))
            w.last = float("-inf")
            self.assertFalse(w.check(state, False))                       # market closed: nothing
        self.assertEqual(len(sent), 1)
        self.assertIn("3,000 calls $11.00 Fri Nov 20 · 30× the open positions", sent[0][0])
        self.assertTrue(sent[0][1])                                       # quiet


if __name__ == "__main__":
    unittest.main()
