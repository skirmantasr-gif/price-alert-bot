"""📅 Weekly review message + Sunday schedule. No network."""
import time
import unittest
from datetime import datetime
from unittest import mock

import extras
import paper
import weekly

NOW = time.time()


def state():
    key = list(paper.PORTFOLIOS)[0]
    pf = {"cash": 90.0, "open": {}, "pending": {}, "eq": [100, 104],
          "closed": [{"tk": "ZNTX", "ret": 0.05, "t": NOW - 86400, "vs": 0.02},
                     {"tk": "ACMB", "ret": -0.03, "t": NOW - 2 * 86400, "vs": -0.04},
                     {"tk": "OLD", "ret": 0.10, "t": NOW - 20 * 86400, "vs": 0.1}]}
    return {"_paper": {"pf": {key: pf}, "since": NOW - 30 * 86400},
            "_learn": {"p": [{"t": NOW - 3600, "src": "news", "d": "up", "r": 2.5},
                             {"t": NOW - 7200, "src": "news", "d": "down", "r": 3.0},
                             {"t": NOW - 7200, "src": "news", "d": "up", "r": 0.2},
                             {"t": NOW - 3600, "src": "outlook", "d": "up"},                  # not checked yet
                             {"t": NOW - 30 * 86400, "src": "news", "d": "up", "r": 9.0}]},  # older than a week
            "_bo": {"sig": {"ZNTX:strategic_review": extras.local_now().strftime("%Y-%m-%d")}},
            "_insb": {"posted": {"ACMB": [extras.local_now().strftime("%Y-%m-%d"), 2, 1]}},
            "_wk": {"snap": {list(paper.PORTFOLIOS)[0]: 100.0, "bench": 101.0}}}


class Message(unittest.TestCase):
    def test_contents(self):
        st = state()
        with mock.patch.object(paper, "value", lambda pf, now, data: 103.0), \
                mock.patch.object(paper, "_bench", lambda *a: 102.0):
            m = weekly.message(st, NOW, lambda tk: None, paper, {"news": "News"})
        self.assertIn("+3.00 this week", m)
        self.assertIn("2 closed this week (1 won, avg +1.0%)", m)
        self.assertIn("S&P 500: €102.00 (+1.00 this week)", m)
        self.assertIn("Real-money rule", m)
        self.assertIn("News: 1 of 3 right · 1 wrong · 1 flat", m)
        self.assertIn("Public sale signals: 1 · 👥 insider clusters: 1", m)
        self.assertNotIn("Buyout watch (", m)
        self.assertEqual(st["_wk"]["snap"]["bench"], 102.0)        # next week compares with this

    def test_without_practice(self):
        m = weekly.message({}, NOW, lambda tk: None)
        self.assertIn("No up/down calls", m)


class Schedule(unittest.TestCase):
    def run_at(self, dt, st):
        sent = []
        w = weekly.WeeklyReview(lambda t, s: sent.append((t, s)))
        with mock.patch.object(extras, "local_now", lambda: dt):
            w.check(st)
        return sent

    def test_sunday_once(self):
        st = {}
        self.assertEqual(self.run_at(datetime(2026, 11, 14, 11), st), [])      # Saturday
        self.assertEqual(self.run_at(datetime(2026, 11, 15, 9, 59), st), [])   # Sunday too early
        sent = self.run_at(datetime(2026, 11, 15, 10, 0), st)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0][1])                                            # quiet
        self.assertEqual(self.run_at(datetime(2026, 11, 15, 15, 0), st), [])   # once per week


if __name__ == "__main__":
    unittest.main()
