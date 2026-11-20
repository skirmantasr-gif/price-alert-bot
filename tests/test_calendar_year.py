"""Yearless dates in headlines (2026-11-20: CRTO/SRPT/CHRS/CAPR earnings saved as 2027-11-xx)."""
import unittest
from datetime import date, timedelta

import extras


class YearlessDates(unittest.TestCase):
    def test_old_article_date_dropped(self):
        ev = extras.extract_events("Criteo to report third quarter results on Nov 4", date(2026, 11, 12))
        self.assertEqual(ev, [])

    def test_next_year_still_rolls(self):
        ev = extras.extract_events("Acme to announce fourth quarter results on Feb 12", date(2026, 12, 10))
        self.assertEqual(ev, [("2027-02-12", "earnings", "Q4 earnings")])

    def test_recent_past_within_week_kept_as_is(self):
        self.assertEqual(extras._first_date("results on Nov 18", 0, date(2026, 11, 20)), None)   # past: not upcoming
        self.assertEqual(extras._first_date("results on Nov 24", 0, date(2026, 11, 20)), date(2026, 11, 24))

    def test_explicit_year_unchanged(self):
        ev = extras.extract_events("PDUFA target action date of November 22, 2026", date(2026, 11, 20))
        self.assertEqual(ev[0][:2], ("2026-11-22", "fda"))

    def test_far_earnings_entries_pruned(self):
        today = extras.local_now().date()
        st = {"_cal": [["CRTO", (today + timedelta(days=350)).isoformat(), "earnings", "Q3 earnings"],
                       ["BZUN", (today + timedelta(days=4)).isoformat(), "earnings", "earnings"],
                       ["XYZ", (today + timedelta(days=300)).isoformat(), "fda", "FDA decision date (PDUFA)"]]}
        up = extras.calendar_upcoming(st, 400)
        self.assertEqual([e[0] for e in up], ["BZUN", "XYZ"])
        self.assertEqual(len(st["_cal"]), 2)


if __name__ == "__main__":
    unittest.main()
