"""🔭 2026-11-23: insider cluster posts end with 5-day / 1-month / 6-month lines from the backtest."""
import unittest
from unittest import mock

import insiders


class Horizons(unittest.TestCase):
    def test_three_lines_and_note(self):
        out = insiders.horizon_lines(2, 66.24, 0.014)
        self.assertEqual(len(out), 4)
        self.assertTrue(out[0].startswith("🔭 <b>Next 5 days</b>: ↔️ about a coin flip — 54%"))
        self.assertIn("middle +1.3%", out[2])
        self.assertIn("own price swings", out[3])

    def test_range_follows_stock_volatility(self):
        calm = insiders.horizon_lines(2, 66.24, 0.01)[1]
        wild = insiders.horizon_lines(2, 66.24, 0.05)[1]
        width = lambda line: [float(x) for x in line.split("usual range ")[1].replace("%", "").split(" … ")]
        self.assertLess(width(calm)[1] - width(calm)[0], width(wild)[1] - width(wild)[0])

    def test_backtest_range_without_volatility(self):
        self.assertIn("usual range -5.8% … +7.2%", insiders.horizon_lines(3, 3.2)[0])

    def test_lean_words(self):
        self.assertIn("📈 leans up", insiders.horizon_lines(3, 20.0)[1])      # 58% rose, +1.7%
        self.assertEqual(insiders.horizon_lines(2, None), [])

    def test_sigma_from_daily_closes(self):
        import context
        closes = [(i, 100 * (1.01 if i % 2 else 0.99) ** 0 * (1 + 0.01 * (-1) ** i)) for i in range(120)]
        with mock.patch.object(context, "daily", lambda tk: {"closes": closes}):
            s = insiders.daily_sigma("AAA")
        self.assertAlmostEqual(s, 0.02, delta=0.002)
        with mock.patch.object(context, "daily", lambda tk: {"closes": closes[:10]}):
            self.assertIsNone(insiders.daily_sigma("AAA"))


if __name__ == "__main__":
    unittest.main()
