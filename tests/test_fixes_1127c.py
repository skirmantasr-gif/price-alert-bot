"""2026-11-27 ("1. 2. 3."): half-day snapshot window, data time in snapshots, SEC filings in the ⏳ check."""
import json
import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import catalysts
import fresh
import news
import options

NY = ZoneInfo("America/New_York")
ROWS = [(date(2026, 12, 18), "C", 5.0, 0, 3, 0.4, 0.6), (date(2026, 12, 18), "P", 5.0, 0, 3, 0.8, 1.0)]


class HalfDay(unittest.TestCase):
    def test_close_times(self):
        self.assertEqual(catalysts.close_hhmm(date(2026, 11, 27)), "13:00")   # day after Thanksgiving
        self.assertEqual(catalysts.close_hhmm(date(2026, 12, 24)), "13:00")
        self.assertEqual(catalysts.close_hhmm(date(2027, 7, 2)), "16:00")     # Jul 3 2027 is a Saturday
        self.assertEqual(catalysts.close_hhmm(date(2026, 11, 30)), "16:00")
        self.assertEqual(options._snap_start(date(2026, 11, 27)), "12:30")
        self.assertEqual(options._snap_start(date(2026, 11, 30)), "15:30")

    def test_snapshot_on_half_day(self):
        w = options.HistoryWatch([], fetch=lambda s: (4.6, None, ROWS), now=lambda: datetime(2026, 11, 27, 12, 40, tzinfo=NY))
        st = {"_fdacal": {"ev": {"PYPD:2026-12-10": {"t": "PYPD", "date": "2026-12-10"}}}}
        self.assertTrue(w.check(st))
        self.assertIn("2026-11-27", st["_opth"]["PYPD:2026-12-10"]["snaps"])


class DataTime(unittest.TestCase):
    def test_cboe_time_saved(self):
        chain = {"timestamp": "2026-11-30 15:44:10", "data": {"current_price": 4.6, "options": [
            {"option": "PYPD261218C00005000", "bid": 0.4, "ask": 0.6}, {"option": "PYPD261218P00005000", "bid": 0.8, "ask": 1.0}]}}
        now = datetime(2026, 11, 30, 15, 50, tzinfo=NY)
        with mock.patch.object(options, "_get", lambda url, timeout=20: json.dumps(chain).encode()):
            w = options.HistoryWatch([], now=lambda: now)
            st = {"_fdacal": {"ev": {"PYPD:2026-12-10": {"t": "PYPD", "date": "2026-12-10"}}}}
            self.assertTrue(w.check(st))
        self.assertEqual(st["_opth"]["PYPD:2026-12-10"]["snaps"]["2026-11-30"]["at"], "2026-11-30 15:44")


class SecFreshness(unittest.TestCase):
    def setUp(self):
        fresh._pending.clear()

    def test_failures_counted(self):
        with mock.patch.object(news, "sec_cik", lambda t: 123), \
                mock.patch.object(news, "_get", mock.Mock(side_effect=OSError("down"))):
            for t in ("CAPR", "CHRS", "VNDA"):
                with self.assertRaises(OSError):
                    news.sec_filings(t)
        st = {}
        fresh.merge(st)
        self.assertIn("failed lookups for 3 of 3", fresh.stale(st, "SEC filings"))

    def test_no_success_for_a_day(self):
        st = {}
        with mock.patch.object(fresh, "_now", lambda: 1_000_000.0):
            fresh.ok("SEC filings", "CAPR")
            fresh.merge(st)
        with mock.patch.object(fresh, "_weekday_daytime", lambda n: True):
            self.assertEqual(fresh.stale(st, "SEC filings", 1_000_000.0 + 3600), "")
            st["_fresh"]["SEC filings"]["ok"] = {}
            self.assertIn("no successful fetch", fresh.stale(st, "SEC filings", 1_000_000.0 + 30 * 3600))


if __name__ == "__main__":
    unittest.main()
