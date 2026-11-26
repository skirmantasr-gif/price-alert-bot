"""💼 holdings.py: −15% stop warnings and December tax-loss reminders (made-up tickers and numbers)."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import holdings

LT = ZoneInfo("Europe/Vilnius")
FILE = {"as_of": "2026-11-15", "source": "test statement",
        "positions": [{"t": "ZNTX", "qty": 100, "cost_usd": 1000},     # bought at $10
                      {"t": "AAA", "qty": 10},                          # no cost known: top = best close
                      {"t": "BBB", "qty": 50, "cost_usd": 250}],
        "tax": {"realized_eur": 1000, "exempt_eur": 500, "harvest": ["ZNTX", "BBB"]}}


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(FILE, f)
        self.sent = []
        self.px = {"ZNTX": [("2026-11-14", 9.0), ("2026-11-20", 8.0), ("2026-12-01", 8.0)],
                   "AAA": [("2026-11-10", 50.0), ("2026-11-16", 20.0), ("2026-11-20", 18.0)],
                   "BBB": [("2026-11-16", 5.0), ("2026-12-01", 6.0)]}
        self.clock = datetime(2026, 11, 27, 23, 30, tzinfo=LT)   # 16:30 New York

    def tearDown(self):
        os.remove(self.path)

    def watch(self):
        return holdings.HoldingsWatch(lambda t, s: self.sent.append(t) or True, file=self.path,
                                      closes=lambda t: self.px[t], fx=lambda: 1.25, now=lambda: self.clock)


class Stop(Base):
    def test_top_of(self):
        self.assertEqual(holdings.top_of(FILE["positions"][0], self.px["ZNTX"], "2026-11-15")[0], 10.0)
        top, src = holdings.top_of(FILE["positions"][1], self.px["AAA"], "2026-11-15")
        self.assertEqual(top, 20.0)            # the $50 close before as_of doesn't count
        self.assertIn("2026-11-16", src)

    def test_first_run_summary_then_once(self):
        st = {}
        w = self.watch()
        self.assertTrue(w.check(st))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("already below", self.sent[0])
        self.assertIn("$ZNTX", self.sent[0])     # -20% from the $10 buy price
        self.assertNotIn("$AAA", self.sent[0])   # -10% from its $20 top
        # next trading day, AAA breaks -15%: one new warning; ZNTX not repeated
        self.px["AAA"].append(("2026-11-30", 16.0))
        self.clock = datetime(2026, 11, 30, 23, 30, tzinfo=LT)
        w.last = float("-inf")
        w.check(st)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("fell 15%", self.sent[1])
        self.assertIn("$AAA", self.sent[1])
        self.assertNotIn("$ZNTX", self.sent[1])

    def test_rearm_after_recovery(self):
        st = {"_hold": {"init": True, "alerted": {"ZNTX": "2026-11-20"}}}
        self.px["ZNTX"].append(("2026-11-25", 9.5))   # -5%: re-armed
        w = self.watch()
        w.check(st)
        self.assertNotIn("ZNTX", st["_hold"]["alerted"])

    def test_quiet_before_close_and_on_holiday(self):
        for clock in (datetime(2026, 11, 27, 20, 0, tzinfo=LT), datetime(2026, 11, 26, 23, 30, tzinfo=LT)):
            self.clock = clock
            st = {}
            self.watch().check(st)
            self.assertEqual(self.sent, [])

    def test_no_file_no_messages(self):
        w = holdings.HoldingsWatch(lambda t, s: self.sent.append(t), file=self.path + ".missing")
        self.assertFalse(w.check({}))
        self.assertEqual(self.sent, [])


class Tax(Base):
    def test_december_reminder(self):
        st = {"_hold": {"init": True, "stop_day": "2026-12-01"}}
        self.clock = datetime(2026, 12, 1, 9, 5, tzinfo=LT)      # Tuesday: first December run
        w = self.watch()
        w.check(st)
        tax = [t for t in self.sent if "Tax-loss check" in t]
        self.assertEqual(len(tax), 1)
        # ZNTX: 100 × $8 − $1000 = −$200 = −€160; BBB: 50 × $6 − $250 = +$50 = +€40 → total −€120 → €880 left
        self.assertIn("€880", tax[0])
        self.assertIn("still €380 over", tax[0])
        # same day again: nothing; Wednesday: nothing; Monday: again
        for clock, n in ((datetime(2026, 12, 1, 15, 0, tzinfo=LT), 1), (datetime(2026, 12, 2, 9, 5, tzinfo=LT), 1),
                         (datetime(2026, 12, 7, 9, 5, tzinfo=LT), 2)):
            self.clock = clock
            w.last = float("-inf")
            w.check(st)
            self.assertEqual(len([t for t in self.sent if "Tax-loss check" in t]), n, clock)

    def test_not_in_november(self):
        self.clock = datetime(2026, 11, 30, 9, 5, tzinfo=LT)
        self.watch().check({"_hold": {"init": True}})
        self.assertFalse([t for t in self.sent if "Tax-loss" in t])


if __name__ == "__main__":
    unittest.main()
