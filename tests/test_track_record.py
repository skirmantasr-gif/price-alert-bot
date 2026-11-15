"""📏 Track record line inside alerts (real hit rate of the same kind of call, once there are enough results)."""
import unittest
from unittest import mock

import learn


def calls(n, src="news", by="ai", c="medium", r=2.0, d="up"):
    return [{"t": i, "tk": "AAA", "src": src, "by": by, "d": d, "c": c, "p0": 10, "r": r} for i in range(n)]


class Track(unittest.TestCase):
    def test_hidden_below_minimum(self):
        st = {"p": calls(29)}
        self.assertEqual(learn.track_line("news", ("up", "medium", "x", "ai"), st), "")

    def test_shown_with_rate_and_edge(self):
        st = {"p": calls(20, r=2.0) + calls(10, r=-3.0) + calls(5, r=0.5)}
        x = learn.track_line("news", ("up", "medium", "x", "ai"), st)
        self.assertIn('right 57% of the last 35 "Likely" news calls (AI)', x)
        self.assertIn("average +0.4%", x)

    def test_down_call_counts_signed(self):
        st = {"p": calls(30, r=-2.0, d="down")}
        self.assertIn("right 100%", learn.track_line("news", ("down", "medium", "x", "ai"), st))

    def test_only_same_kind(self):
        st = {"p": calls(40, c="high") + calls(40, src="sec") + calls(40, by="rules")}
        self.assertEqual(learn.track_line("news", ("up", "medium", "x", "ai"), st), "")

    def test_last_60_only_and_unchecked_ignored(self):
        old = calls(100, r=-5.0)
        new = [dict(p, t=1000 + p["t"]) for p in calls(60, r=5.0)]
        pending = [{k: v for k, v in p.items() if k != "r"} for p in calls(50)]
        x = learn.track_line("news", ("up", "medium", "x", "ai"), {"p": old + new + pending})
        self.assertIn("right 100% of the last 60", x)

    def test_neutral_and_off(self):
        st = {"p": calls(40)}
        self.assertEqual(learn.track_line("news", ("neutral", "low", "", "ai"), st), "")
        with mock.patch.object(learn, "ON", False):
            self.assertEqual(learn.track_line("news", ("up", "medium", "x", "ai"), st), "")


if __name__ == "__main__":
    unittest.main()


class InNewsAlert(unittest.TestCase):
    def test_line_under_the_impact_guess(self):
        import news
        w = news.NewsWatcher.__new__(news.NewsWatcher)
        it = {"ticker": "AAA", "kind": "news", "impact": ("up", "medium", "deal", "ai")}
        with mock.patch.object(news.NewsWatcher, "_format", lambda self, it: "📰 head\n<a href='x'>link</a>"), \
                mock.patch.object(learn, "_st_ro", {"p": calls(30)}):
            msg = w.format(it)
        lines = msg.split("\n")
        self.assertIn("Likely UP", lines[1])
        self.assertTrue(lines[2].startswith("📏 Track record: right 100% of the last 30"))
        self.assertTrue(lines[3].startswith("<a"))
