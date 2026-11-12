"""📊 Earnings patterns: report days matched by volume, moves measured, message built. No network."""
import unittest
from datetime import date, timedelta
from unittest import mock

import earnings
import extras


def bars(n=120, spikes=None):
    """Weekday bars, price 10, volume 1M; spikes = {index: (open, close, volume)}."""
    out, d, px = [], date(2026, 1, 5), 10.0
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o, c, v = (spikes or {}).get(i, (px, px, 1_000_000))
        out.append((d.isoformat(), o, c, v))
        px = c
        d += timedelta(days=1)
    return out


class Reactions(unittest.TestCase):
    def test_busier_of_filing_day_and_next_day(self):
        b = bars(spikes={50: (10, 10, 1_100_000), 51: (9, 8, 5_000_000)})   # filed after the close on day 50
        r = earnings.reactions([b[50][0]], b)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["d"], b[51][0])
        self.assertAlmostEqual(r[0]["day"], -0.2)
        self.assertAlmostEqual(r[0]["gap"], -0.1)
        self.assertAlmostEqual(r[0]["vr"], 5.0)

    def test_quiet_filing_day_dropped_and_one_per_quarter(self):
        b = bars(spikes={40: (10, 11, 4_000_000), 45: (11, 11.5, 2_000_000), 90: (11.5, 11.5, 1_000_000)})
        r = earnings.reactions([b[40][0], b[45][0], b[90][0]], b)
        self.assertEqual([x["d"] for x in r], [b[40][0]])     # day 45: same quarter, less busy; day 90: normal volume
        self.assertAlmostEqual(r[0]["next5"], 11.5 / 11 - 1)

    def test_too_early_in_history_skipped(self):
        self.assertEqual(earnings.reactions([bars()[3][0]], bars()), [])


class Message(unittest.TestCase):
    def test_table_and_summary(self):
        rows = [{"d": "2026-08-18", "pre5": -0.006, "gap": -0.197, "day": -0.228, "next5": -0.052, "vr": 9.9},
                {"d": "2026-05-14", "pre5": -0.042, "gap": 0.131, "day": 0.203, "next5": None, "vr": 6.0}]
        with mock.patch.object(extras, "local_now", lambda: __import__("datetime").datetime(2026, 11, 14, 8)):
            m = earnings.message("ZNTX", "2026-11-17", rows)
        self.assertIn("$ZNTX reports in 3 days", m)
        self.assertIn("Report day: down 1 of 2", m)
        self.assertIn("Next 5 days: down 1 of 1", m)
        self.assertIn("Few reports", m)
        self.assertIn("-22.8%", m)

    def test_no_history(self):
        with mock.patch.object(extras, "local_now", lambda: __import__("datetime").datetime(2026, 11, 16, 8)):
            self.assertIn("No past report days", earnings.message("ZNTX", "2026-11-17", []))


class Watch(unittest.TestCase):
    def test_posts_once_per_report_and_only_watchlist_earnings(self):
        sent = []
        w = earnings.EarningsWatch(lambda text, silent: sent.append(text) or True, ["ZNTX", "ACMB"], lambda t: 1)
        today = extras.local_now().date()
        state = {"_cal": [["ZNTX", (today + timedelta(days=2)).isoformat(), "earnings", "Q3"],
                          ["ACMB", (today + timedelta(days=1)).isoformat(), "fda", "PDUFA"],
                          ["OTHR", (today + timedelta(days=1)).isoformat(), "earnings", "Q3"]]}
        with mock.patch.object(w, "pattern", lambda t: []):
            self.assertTrue(w.check(state))
            w.last = float("-inf")
            self.assertFalse(w.check(state))
        self.assertEqual(len(sent), 1)
        self.assertIn("$ZNTX", sent[0])


if __name__ == "__main__":
    unittest.main()
