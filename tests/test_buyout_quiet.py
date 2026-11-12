"""🎯 Buyout channel quieter (2026-11-12): loud only for strong signs; candidate table weekly vs the market."""
import unittest
from datetime import datetime
from unittest import mock

import buyout
import extras
import weekly

from tests.test_fixes_1112b import bo_row


class Loudness(unittest.TestCase):
    def test_only_takeover_filings_loud(self):
        for form, loud in (("SC TO-T", True), ("SC 14D9", True), ("DEFM14A", True), ("425", True),
                           ("SCHEDULE 13D/A", False), ("SC TO-I", False)):
            self.assertEqual(bool(buyout.LOUD_FORM.search(form)), loud, form)

    def test_spike_loud_only_when_big(self):
        self.assertEqual((buyout.SPIKE_LOUD, buyout.SPIKE_LOUD_PACE), (0.15, 5))


class WeeklyTable(unittest.TestCase):
    def run_day(self, bo, day, rows):
        sent = []
        w = buyout.BuyoutWatch(lambda text, silent: sent.append(text))
        bench = {"IWM": {"2026-11-09": 100.0, "2026-11-12": 104.0, "2026-11-13": 104.0},
                 "XBI": {"2026-11-09": 100.0, "2026-11-12": 110.0, "2026-11-13": 110.0}}
        w.result = (rows, [], 0, bench)
        with mock.patch.object(extras, "local_now", lambda: day):
            w._finish_screen(bo, first=False)
        return sent

    def test_table_only_on_friday_with_market_comparison(self):
        a, b = bo_row("ZNTX", 5), dict(bo_row("ACMB", 5), sector="Healthcare")
        a["price"], b["price"] = 12.0, 10.5
        bo = {"cands": {"ZNTX": {"since": "2026-11-09", "price": 10.0}, "ACMB": {"since": "2026-11-09", "price": 10.0}}}
        self.assertEqual(self.run_day(bo, datetime(2026, 11, 12, 17), [a, b]), [])     # Thursday: quiet
        p = bo["perf"]
        self.assertEqual((p["n"], p["ahead"]), (2, 1))       # ZNTX +20% vs IWM +4%; ACMB +5% vs XBI +10%
        self.assertAlmostEqual(p["bench"], 0.07)
        sent = self.run_day(bo, datetime(2026, 11, 13, 17), [a, b])                    # Friday: the week's table
        self.assertEqual(len(sent), 1)
        self.assertIn("week to Fri Nov 13", sent[0])
        self.assertIn("1 of 2 ahead of their market", sent[0])
        self.assertEqual(bo["wk"], {"added": [], "dropped": [], "found": []})

    def test_weekly_review_line(self):
        st = {"_bo": {"cands": {"A": {}, "B": {}}, "perf": {"n": 2, "avg": 0.1, "med": 0.1, "bench": 0.05, "ahead": 1}}}
        m = weekly.message(st, 0, lambda tk: None)
        self.assertIn("Buyout watch (2 candidates): average +10.0% since added vs their market +5.0%", m)


if __name__ == "__main__":
    unittest.main()
