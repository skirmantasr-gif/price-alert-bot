"""🔎 Context check before a message is sent: market/sector, dilution, tax-loss, short selling, dates, listing."""
import unittest
from datetime import date, timedelta
from unittest import mock

import context


def closes(values, end=date(2026, 11, 12)):
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), v) for i, v in enumerate(values)]


class Checks(unittest.TestCase):
    def setUp(self):
        context._c.clear()
        context._shown.clear()

    def test_mostly_the_market(self):
        with mock.patch.object(context, "sector_etf", lambda s, p=None: "XBI"), \
                mock.patch.object(context, "day_change", lambda s: {"XBI": -0.033, "SPY": -0.004}[s]):
            self.assertTrue(context.mostly_market("ZNTX", -0.03))          # biotech fell as much
            self.assertFalse(context.mostly_market("ZNTX", -0.09))         # 3x its sector: its own story
            self.assertFalse(context.mostly_market("ZNTX", 0.03))          # opposite direction
            self.assertIn("mostly the market", context.market_line("ZNTX", -0.03))

    def test_dilution(self):
        today = date.today()
        fl = [((today - timedelta(days=20)).isoformat(), "424B5"), ((today - timedelta(days=200)).isoformat(), "S-3")]
        with mock.patch.object(context, "cash_burn", lambda s: (80e6, 40e6)), \
                mock.patch.object(context, "filings", lambda s: fl), \
                mock.patch.object(context, "market_cap", lambda s: 300e6), \
                mock.patch.object(context, "sector", lambda s: "Healthcare"):
            line = context.dilution_line("ZNTX")
        self.assertIn("HIGH", line)
        self.assertIn("cash for about 2 quarters", line)
        self.assertIn("1 share sale (424B prospectus)", line)
        with mock.patch.object(context, "cash_burn", lambda s: (900e6, 10e6)), \
                mock.patch.object(context, "filings", lambda s: []), mock.patch.object(context, "sector", lambda s: "Healthcare"):
            self.assertEqual(context.dilution_line("ZNTX"), "")            # 90 quarters of cash: fine
        with mock.patch.object(context, "cash_burn", lambda s: (80e6, 40e6)), \
                mock.patch.object(context, "filings", lambda s: []), \
                mock.patch.object(context, "sector", lambda s: "Financial Services"):
            self.assertEqual(context.dilution_line("ZNTX"), "")            # lenders: measure doesn't apply

    def test_dilution_bonds_repeats_and_big_companies(self):
        """ORCL Nov 2026: 2x 424B2 (bonds) + one stock offering filed twice (preliminary + final) -> 1 sale, medium."""
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()
        fl = [(d(9), "424B5", "d181425d424b5.htm"), (d(9), "424B2", "d181336d424b2.htm"),
              (d(11), "424B5", "d181425d424b5.htm"), (d(11), "424B2", "d181336d424b2.htm")]
        with mock.patch.object(context, "cash_burn", lambda s: (20e9, 1e9)), \
                mock.patch.object(context, "filings", lambda s: fl), \
                mock.patch.object(context, "sector", lambda s: "Technology"):
            with mock.patch.object(context, "market_cap", lambda s: 396e9):
                line = context.dilution_line("ORCL")
            self.assertTrue(line.startswith("💧 Dilution risk medium"), line)   # big company: never HIGH
            self.assertIn("1 share sale (424B prospectus)", line)                # repeat + bonds not counted
            with mock.patch.object(context, "market_cap", lambda s: 500e6):
                self.assertIn("HIGH", context.dilution_line("ORCL"))              # same filings, small company
            with mock.patch.object(context, "market_cap", lambda s: None):
                self.assertIn("HIGH", context.dilution_line("ORCL"))              # no market value: keep HIGH
        bonds = [(d(5), "424B2", "a.htm"), (d(6), "424B2", "b.htm")]
        with mock.patch.object(context, "cash_burn", lambda s: (900e6, 10e6)), \
                mock.patch.object(context, "filings", lambda s: bonds), \
                mock.patch.object(context, "market_cap", lambda s: 5e9), \
                mock.patch.object(context, "sector", lambda s: "Technology"):
            self.assertEqual(context.dilution_line("ZNTX"), "")                  # bonds only: no share sale

    def test_listing(self):
        with mock.patch.object(context, "daily", lambda s: {"closes": closes([1.2] * 10 + [0.8] * 35), "divs": []}):
            self.assertIn("Under $1 for 35 trading days: exchange warning likely", context.listing_line("ZNTX"))
        with mock.patch.object(context, "daily", lambda s: {"closes": closes([1.2] * 10 + [0.8] * 5), "divs": []}):
            self.assertEqual(context.listing_line("ZNTX"), "")

    def test_taxloss_only_in_season(self):
        data = {"closes": [(date(2025, 12, 31), 10.0), (date(2026, 11, 12), 4.0)] + [(date(2026, 6, 1), 7.0)] * 30,
                "divs": []}
        data["closes"].sort()
        with mock.patch.object(context, "daily", lambda s: data), mock.patch.object(context, "date") as d:
            d.today.return_value = date(2026, 11, 13)
            d.side_effect = lambda *a: date(*a)
            self.assertIn("Down 60% this year", context.taxloss_line("ZNTX"))
            d.today.return_value = date(2026, 7, 1)
            self.assertEqual(context.taxloss_line("ZNTX"), "")

    def test_short_selling(self):
        with mock.patch.object(context, "short_ratio", lambda s: 0.65):
            self.assertIn("65% of trading", context.short_line("ZNTX"))
        with mock.patch.object(context, "short_ratio", lambda s: 0.45):
            self.assertEqual(context.short_line("ZNTX"), "")

    def test_exdividend_estimate(self):
        divs = [(date.today() - timedelta(days=d), 0.5) for d in (273, 182, 89)]
        with mock.patch.object(context, "daily", lambda s: {"closes": [], "divs": divs}):
            nxt, amt = context.next_exdiv("ZNTX")
            self.assertLessEqual(abs((nxt - date.today()).days), 3)
            self.assertIn("Ex-dividend likely", context.exdiv_line("ZNTX", price=20.0))
            self.assertIn("2.5% of the price", context.exdiv_line("ZNTX", price=20.0))

    def test_fed_calendar_parsing(self):
        page = ("<h4>2026 FOMC Meetings</h4> November 3-4* ... December 8-9 ... "
                "<h4>2027 FOMC Meetings</h4> January 26-27 ... March 16-17*").encode()
        with mock.patch.object(context, "_get", lambda *a, **k: page):
            self.assertEqual(context.fomc_days(), [date(2026, 11, 4), date(2026, 12, 9), date(2027, 1, 27),
                                                   date(2027, 3, 17)])
        with mock.patch.object(context, "fomc_days", lambda: [date(2026, 12, 9)]):
            self.assertIn("Fed rate decision", context.calendar_lines(date(2026, 12, 8))[0])
        self.assertEqual(context.jobs_day(date(2026, 12, 15)), date(2026, 12, 4))

    def test_full_context_once_per_day(self):
        with mock.patch.object(context, "ON", True), \
                mock.patch.object(context, "dilution_line", lambda s: "💧 Dilution risk HIGH: x"), \
                mock.patch.object(context, "listing_line", lambda s: ""), \
                mock.patch.object(context, "taxloss_line", lambda s: ""), \
                mock.patch.object(context, "short_line", lambda s: ""), \
                mock.patch.object(context, "exdiv_line", lambda s, p=None: ""), \
                mock.patch.object(context, "market_line", lambda s, c, p=None: "🌐 Same day: x"):
            self.assertEqual(len(context.lines("ZNTX", change=-0.03)), 2)
            self.assertEqual(context.lines("ZNTX", change=-0.03), ["🌐 Same day: x"])   # later alert: market only
            self.assertEqual(len(context.lines("ZNTX", change=-0.03, once=False)), 2)

    def test_failures_are_silent(self):
        with mock.patch.object(context, "ON", True), mock.patch.object(context, "_get", side_effect=OSError):
            self.assertEqual(context.lines("ZNTX", change=0.05), [])


if __name__ == "__main__":
    unittest.main()
