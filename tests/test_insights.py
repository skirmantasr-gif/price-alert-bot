"""🧭 Hourly outlook: the "since the last outlook" comparison line."""
import unittest
from unittest import mock

import insights
from tests.helpers import ny

DAY = (2026, 11, 6)


class Recap(unittest.TestCase):
    def test_right_wrong_and_flat(self):
        prev = {"t": ny(*DAY, 11), "calls": {"AAA": ["up", "medium", 10.0], "BBB": ["down", "low", 20.0],
                                               "CCC": ["up", "low", 5.0]}}
        line = insights.recap(prev, {"AAA": 10.1, "BBB": 20.2, "CCC": 5.0001}, ny(*DAY, 12))
        self.assertIn("(11:00 NY)", line)
        self.assertIn("$AAA ↑ +1.0% ✅", line)
        self.assertIn("$BBB ↓ +1.0% ❌", line)
        self.assertIn("$CCC ↑ +0.0% ➖", line)          # too small to judge
        self.assertTrue(line.endswith("1 of 2 right"))
        self.assertLess(line.index("$AAA"), line.index("$BBB"))   # UP calls first

    def test_nothing_to_compare(self):
        self.assertIsNone(insights.recap(None, {}, ny(*DAY, 12)))
        self.assertIsNone(insights.recap({"t": ny(*DAY, 11), "calls": {}}, {}, ny(*DAY, 12)))
        self.assertIsNone(insights.recap({"t": ny(*DAY, 11), "calls": {"AAA": ["up", "low", 10]}}, {}, ny(*DAY, 12)))
        old = {"t": ny(*DAY, 11) - 5 * 86400, "calls": {"AAA": ["up", "low", 10]}}
        self.assertIsNone(insights.recap(old, {"AAA": 11}, ny(*DAY, 12)))

    def test_previous_day_shows_weekday(self):
        prev = {"t": ny(2026, 11, 5, 15), "calls": {"AAA": ["up", "low", 10]}}
        self.assertIn("(Thu 15:00 NY)", insights.recap(prev, {"AAA": 11}, ny(*DAY, 10)))


class TwoOutlooks(unittest.TestCase):
    def test_second_message_compares_with_first(self):
        prices = {"AAA": 10.0, "BBB": 20.0}
        views = {"AAA": ("up", "medium", "x"), "BBB": ("down", "medium", "y")}
        sent = []
        w = insights.HourlyInsights(["AAA", "BBB"], {"AAA": {"fake": 1}, "BBB": {"fake": 1}}, None, lambda m: sent.append(m) or True)
        state = {}
        with mock.patch.object(insights, "features", lambda t, c, a, now: {"ticker": t, "price": prices[t], "day_pct": 1.0}), \
                mock.patch.object(insights, "rule_view", lambda f, n: views[f["ticker"]]), \
                mock.patch.object(insights.impact, "ai_enabled", lambda: False), \
                mock.patch.object(insights.extras, "calendar_upcoming", lambda *a, **k: []), \
                mock.patch.object(insights, "learn", None):
            with mock.patch("insights.time.time", return_value=ny(*DAY, 11)):
                w._send(state, insights.datetime.fromtimestamp(ny(*DAY, 11), insights.NY), False)
            prices.update(AAA=10.5, BBB=19.0)
            with mock.patch("insights.time.time", return_value=ny(*DAY, 12)):
                w._send(state, insights.datetime.fromtimestamp(ny(*DAY, 12), insights.NY), False)
        self.assertNotIn("↩️", sent[0])                       # first outlook: nothing to compare yet
        self.assertIn("↩️ Since the last outlook (11:00 NY): $AAA ↑ +5.0% ✅ · $BBB ↓ -5.0% ✅ — 2 of 2 right", sent[1])
        self.assertLess(sent[1].index("↩️"), sent[1].index("📈"))


if __name__ == "__main__":
    unittest.main()
