"""2026-11-27 ("proceed with 1-5"): ⏳ freshness watchdog, 💊⚡ for off-watchlist FDA stocks, neutral shock wording,
event results from the real decision date + approval tally."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import catalysts
import fresh


class Freshness(unittest.TestCase):
    def setUp(self):
        fresh._pending.clear()

    def test_cboe_three_old_files_alert_once_then_recover(self):
        sent, st, t = [], {}, [1_000_000.0]
        w = fresh.FreshWatch(lambda text, quiet: sent.append(text), now=lambda: t[0])
        for s in ("PYPD", "VNDA", "GILD"):
            fresh.bad("CBOE options", s, "Nov 23 15:31")
        fresh.ok("CBOE options", "AAPL")
        with mock.patch.object(fresh, "_now", lambda: t[0]):
            w.check(st)
            self.assertEqual(len(sent), 1)
            self.assertIn("OLD data", sent[0])
            self.assertIn("PYPD", sent[0])
            t[0] += 400
            w.check(st)
            self.assertEqual(len(sent), 1)                    # no repeat
            self.assertIn("Old data", fresh.line(st))
            for s in ("PYPD", "VNDA", "GILD"):
                fresh.ok("CBOE options", s)
            t[0] += 400
            w.check(st)
        self.assertIn("fresh data again", sent[-1])

    def test_yahoo_needs_half_of_stocks(self):
        st = {}
        for s in ("A", "B", "C"):
            fresh.bad("Yahoo prices", s, "14:00 UTC")
        for s in "DEFGHIJ":
            fresh.ok("Yahoo prices", s)
        fresh.merge(st)
        self.assertEqual(fresh.stale(st, "Yahoo prices"), "")   # 3 thin stocks quiet out of 10: normal

    def test_news_silence(self):
        st = {}
        with mock.patch.object(fresh, "_now", lambda: 1_000_000.0):
            fresh.beat("News feeds")
            fresh.merge(st)
        with mock.patch.object(fresh, "_weekday_daytime", lambda n: True):
            self.assertEqual(fresh.stale(st, "News feeds", 1_000_000.0 + 3600), "")
            self.assertIn("nothing new", fresh.stale(st, "News feeds", 1_000_000.0 + 30 * 3600))


class OffWatchlistShock(unittest.TestCase):
    def _state(self, days_ahead):
        today = datetime.now(ZoneInfo("America/New_York")).date()
        iso = (today + timedelta(days=days_ahead)).isoformat()
        return {"_fdacal": {"ev": {f"PYPD:{iso}": {"t": "PYPD", "name": "PolyPid Ltd.", "date": iso}}, "docs": []}}

    def test_big_drop_near_date_is_posted(self):
        sent = []
        w = catalysts.FdaNewsWatch(lambda *a: sent.append(a[0]), ["CAPR"], headlines=lambda t: [],
                                   quote=lambda t: (4.57, 5.545, "regular"))
        self.assertTrue(w._shock(self._state(3), "PYPD"))
        self.assertIn("PYPD DOWN", sent[0])
        self.assertIn("check the news", sent[0])
        self.assertNotIn("probably REJECTED", sent[0])

    def test_far_from_date_no_price_fetch(self):
        calls = []
        w = catalysts.FdaNewsWatch(lambda *a: None, [], headlines=lambda t: [],
                                   quote=lambda t: calls.append(t) or (1, 1, "regular"))
        self.assertFalse(w._shock(self._state(20), "PYPD"))
        self.assertEqual(calls, [])


class ResultFromDecisionDay(unittest.TestCase):
    def test_early_approval_measured_from_decision(self):
        days = [(date(2026, 11, 2) + timedelta(days=i)) for i in range(40)]
        days = [d for d in days if d.weekday() < 5 and d != date(2026, 11, 26)]
        bars = []
        for d in days:
            iso = d.isoformat()
            c = 4.57 if iso >= "2026-11-25" else 5.55
            v = 2_000_000 if iso == "2026-11-25" else 200_000
            bars.append((iso, c, c, v))
        st = {"_fdacal": {"decided": {"PYPD:2026-11-28": {"at": "2026-11-25", "approved": True, "title": "x"}}},
              "_opth": {"PYPD:2026-11-28": {"t": "PYPD", "event": "2026-11-28", "kind": "fda", "label": "FDA",
                                            "snaps": {}}}}
        sent = []
        w = catalysts.ResultWatch(lambda t, s: sent.append(t), lambda t, s: sent.append(t), [], bars=lambda t: bars)
        self.assertTrue(w.check(st, now=datetime(2026, 12, 1, 12)))
        row = st["_evres"]["rows"][-1]
        self.assertEqual(row["day"], "2026-11-25")
        self.assertTrue(row["approved"])
        self.assertEqual(row["early"], 3)
        self.assertIn("APPROVED ✅", sent[0])
        self.assertIn("3 days early", sent[0])
        self.assertIn("After approvals (1 so far): fell in 1 of 1", sent[0])


class IbkrBackup(unittest.TestCase):
    def test_decode(self):
        import ibopts
        got = ibopts.decode('{"px": 4.6, "iv30": null, "rows": [["2026-12-18", "C", 5.0, 0, 3, 0.4, 0.6]]}')
        self.assertEqual(got[0], 4.6)
        self.assertEqual(got[2][0][0], date(2026, 12, 18))
        self.assertIsNone(ibopts.decode("not json"))
        self.assertIsNone(ibopts.decode('{"err": "no price"}'))

    def test_history_uses_backup_when_cboe_old(self):
        import options
        import ibopts
        rows = [(date(2026, 12, 18), "C", 5.0, 0, 3, 0.4, 0.6), (date(2026, 12, 18), "P", 5.0, 0, 3, 0.8, 1.0)]
        today = date(2026, 11, 30)
        fb = ibopts.Fallback(run=lambda sym: (4.6, None, rows), today=lambda: today)

        def cboe(sym):
            raise options.Stale("2026-11-23T15:31:28")
        now = datetime(2026, 11, 30, 16, 0, tzinfo=ZoneInfo("America/New_York"))
        w = options.HistoryWatch([], fetch=cboe, now=lambda: now, backup=fb)
        st = {"_fdacal": {"ev": {"PYPD:2026-12-10": {"t": "PYPD", "date": "2026-12-10"}}}}
        w.check(st)
        for _ in range(100):
            if fb.res:
                break
            __import__('time').sleep(0.02)
        w.retry_at = float("-inf")
        self.assertTrue(w.check(st))
        snap = st["_opth"]["PYPD:2026-12-10"]["snaps"]["2026-11-30"]
        self.assertEqual(snap["src"], "ibkr")
        self.assertEqual(snap["exps"][0][2], round((0.5 + 0.9) / 4.6 * 100, 1))   # straddle from delayed mids


if __name__ == "__main__":
    unittest.main()
