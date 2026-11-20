"""📼 options history before FDA dates (2026-11-20)."""
import unittest
from datetime import date, datetime, timedelta

import options

D = date(2026, 11, 27)


def rows():
    return [(D, "C", 7.5, 9000, 1600, 3.6, 4.0), (D, "P", 7.5, 38000, 7000, 3.7, 4.2),
            (D, "P", 5.0, 2800, 900, 1.5, 1.8), (date(2026, 11, 20), "C", 9.0, 100, 10, 0.1, 0.2)]


class FakeNow:
    def __init__(self, s):
        self.t = datetime.strptime(s, "%Y-%m-%d %H:%M")

    def __call__(self):
        return self.t


class TestSnapshot(unittest.TestCase):
    def test_compact_record(self):
        s = options.snapshot(7.68, 512.75, rows(), date(2026, 11, 21))
        e = s["exps"][0]
        self.assertEqual(e[:2], ["2026-11-27", 7.5])
        self.assertAlmostEqual(e[2], round((3.8 + 3.95) / 7.68 * 100, 1))
        self.assertEqual(e[3:], [1600, 7900, 9000, 40800])
        self.assertEqual(s["top"][0][:3], ["2026-11-27", "P", 7.5])
        self.assertEqual(len(s["exps"]), 1)    # expired Nov 20 left out


class TestHistoryWatch(unittest.TestCase):
    def state(self):
        return {"_cal": [["CAPR", "2026-11-22", "fda", "FDA decision date (PDUFA)"],
                         ["BZUN", "2026-11-24", "earnings", "earnings"],
                         ["XXXX", "2026-11-23", "fda", "not on watchlist"]]}

    def watch(self, when, calls):
        def fetch(sym):
            calls.append(sym)
            return 7.68, 512.75, rows()
        return options.HistoryWatch(["CAPR", "BZUN"], fetch=fetch, now=FakeNow(when))

    def test_saves_once_per_day_near_close(self):
        st, calls = self.state(), []
        w = self.watch("2026-11-20 15:45", calls)
        self.assertTrue(w.check(st))
        self.assertFalse(w.check(st))
        self.assertEqual(calls, ["CAPR", "BZUN"])               # watchlist FDA + earnings (not XXXX, not on it)
        self.assertIn("2026-11-20", st["_opth"]["CAPR:2026-11-22"]["snaps"])

    def test_not_before_close_or_weekend(self):
        st, calls = self.state(), []
        self.assertFalse(self.watch("2026-11-20 11:00", calls).check(st))
        self.assertFalse(self.watch("2026-11-21 16:00", calls).check(st))   # Saturday
        self.assertEqual(calls, [])

    def test_keeps_recording_after_event_then_stops(self):
        st, calls = self.state(), []
        self.watch("2026-11-20 16:00", calls).check(st)
        st["_cal"] = []                                         # _cal drops passed events
        self.assertTrue(self.watch("2026-11-23 16:00", calls).check(st))   # Monday reaction
        self.assertFalse(self.watch("2026-11-30 16:00", calls).check(st))  # 8 days after: done
        self.assertEqual(sorted(st["_opth"]["CAPR:2026-11-22"]["snaps"]), ["2026-11-20", "2026-11-23"])

    def test_manual_record_for_other_stock(self):
        st, calls = {"_opth": {"SVRA:2026-11-22": {"t": "SVRA", "event": "2026-11-22", "label": "PDUFA",
                                                   "snaps": {}}}}, []
        self.assertTrue(self.watch("2026-11-20 16:00", calls).check(st))
        self.assertEqual(calls, ["SVRA"])

    def test_fetch_failure_silent(self):
        st = self.state()

        def bad(sym):
            raise OSError("down")
        w = options.HistoryWatch(["CAPR"], fetch=bad, now=FakeNow("2026-11-20 16:00"))
        self.assertFalse(w.check(st))

    def test_size_cap(self):
        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(options.HIST_MAX_EVENTS + 5)]
        st = {"_opth": {f"T{i}:{d}": {"t": f"T{i}", "event": d, "label": "x", "snaps": {}} for i, d in enumerate(days)}}
        self.watch("2026-11-20 16:00", []).check(st)
        self.assertEqual(len(st["_opth"]), options.HIST_MAX_EVENTS)
        self.assertNotIn("T0:2026-01-01", st["_opth"])

    def test_history_lines(self):
        st, calls = self.state(), []
        self.watch("2026-11-20 16:00", calls).check(st)
        out = options.history_lines(st["_opth"]["CAPR:2026-11-22"])
        self.assertIn("CAPR", out[0])
        self.assertIn("next exp 2026-11-20", out[1])       # same-day expiry still counts on its day
        self.assertIn("OI C/P 100/0", out[1])


if __name__ == "__main__":
    unittest.main()
