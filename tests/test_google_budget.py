"""Google budget: hot stocks every round, the rest take turns (2026-11-22)."""
import unittest
from datetime import date, timedelta
from unittest import mock

import news

T = ["CAPR", "SLS", "SVRA", "BZUN", "DOX", "ORCL"]


class Budget(unittest.TestCase):
    def test_hot_by_fda_and_earnings(self):
        today = date(2026, 11, 22)
        st = {"_cal": [["CAPR", "2026-11-25", "fda", "PDUFA"], ["BZUN", "2026-11-24", "earnings", "earnings"],
                       ["DOX", "2026-12-20", "earnings", "earnings"]], "_fdacal": {"ev": {}}}
        self.assertTrue(news.google_hot(st, "CAPR", today))
        self.assertTrue(news.google_hot(st, "BZUN", today))
        self.assertFalse(news.google_hot(st, "DOX", today))
        self.assertFalse(news.google_hot(st, "SLS", today))
        self.assertTrue(news.google_hot(st, "CAPR", today + timedelta(days=5)))      # 2 days after the date
        self.assertFalse(news.google_hot(st, "CAPR", today + timedelta(days=7)))     # 4 days after: done
        self.assertTrue(news.google_hot(st, "CAPR", date(2026, 11, 18)))             # 7 days before
        self.assertFalse(news.google_hot(st, "CAPR", date(2026, 11, 17)))            # 8 days before

    def test_rotation_covers_everyone(self):
        st = {"_cal": [], "_fdacal": {"ev": {}}}
        with mock.patch.object(news, "google_hot", lambda s, t, today=None: t == "CAPR"):
            seen = set()
            for turn in range(3):
                got = news.google_targets(st, T, turn)
                self.assertIn("CAPR", got)
                self.assertEqual(len(got), 3)          # hot + 2 cold
                seen |= got
        self.assertEqual(seen, set(T))                 # 5 cold stocks covered within 3 rounds


if __name__ == "__main__":
    unittest.main()
