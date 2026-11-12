"""👥 Insider cluster buys. No network: SEC and Yahoo are mocked."""
import unittest
from unittest import mock

import insiders

FORM4 = """<ownershipDocument><documentType>4</documentType>
<issuer><issuerCik>0000000001</issuerCik><issuerName>Zentrox Inc</issuerName><issuerTradingSymbol>{tk}</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship>{rel}</reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>{tx}</nonDerivativeTable></ownershipDocument>"""
TX = """<nonDerivativeTransaction><transactionDate><value>{d}</value></transactionDate>
<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>{sh}</value></transactionShares>
<transactionPricePerShare><value>{px}</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>"""
CEO = "<isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle>"
DIR = "<isDirector>1</isDirector>"
FUND = "<isTenPercentOwner>1</isTenPercentOwner>"


def f4(name, rel, txs, tk="ZNTX"):
    return insiders.parse_form4(FORM4.format(tk=tk, name=name, rel=rel, tx="".join(
        TX.format(d=d, code=c, sh=sh, px=px) for d, c, sh, px in txs)).encode())


class Parse(unittest.TestCase):
    def test_only_open_market_buys(self):
        f = f4("Smith John", CEO, [("2026-11-10", "P", 10000, 5.0), ("2026-11-10", "S", 500, 5.1),
                                    ("2026-11-10", "A", 9000, 0)])
        self.assertEqual(f["buys"], [("2026-11-10", 10000.0, 5.0)])
        self.assertTrue(f["officer_or_director"])
        self.assertEqual(f["role"], "Chief Executive Officer")

    def test_fund_not_counted(self):
        book = {}
        self.assertFalse(insiders.add_buys(book, f4("Big Fund LP", FUND, [("2026-11-10", "P", 1e6, 5.0)]), "a1", "2026-11-12"))
        self.assertEqual(book, {})

    def test_penny_buys_not_counted(self):
        book = {}
        self.assertFalse(insiders.add_buys(book, f4("Doe Jane", DIR, [("2026-11-10", "P", 1e6, 0.4)]), "a1", "2026-11-12"))


class Cluster(unittest.TestCase):
    def test_two_insiders_needed_and_minimum_total(self):
        book = {}
        insiders.add_buys(book, f4("Smith John", CEO, [("2026-11-10", "P", 10000, 5.0)]), "a1", "2026-11-12")
        self.assertIsNone(insiders.cluster(book["ZNTX"], "2026-11-12"))           # one insider
        insiders.add_buys(book, f4("Doe Jane", DIR, [("2026-11-11", "P", 2000, 5.0)]), "a2", "2026-11-12")
        self.assertIsNone(insiders.cluster(book["ZNTX"], "2026-11-12"))           # $60k < $100k
        insiders.add_buys(book, f4("Doe Jane", DIR, [("2026-11-12", "P", 10000, 5.0)]), "a3", "2026-11-12")
        n, total, rows = insiders.cluster(book["ZNTX"], "2026-11-12")
        self.assertEqual((n, total), (2, 110000))
        self.assertFalse(insiders.add_buys(book, f4("Doe Jane", DIR, [("2026-11-12", "P", 10000, 5.0)]), "a3",
                                           "2026-11-12"))                          # same filing twice

    def test_joint_filing_counts_once(self):
        rows = [["2026-11-10", "GORDON CARL", "Director", 745000, False, "a", 92000],
                ["2026-11-10", "ORBIMED ADVISORS", "Director", 745000, False, "b", 92000]]
        self.assertIsNone(insiders.cluster(rows, "2026-11-12"))

    def test_old_buys_fall_out_of_window(self):
        book = {"ZNTX": [["2026-10-01", "A", "Director", 90000, False, "x", 1], ["2026-11-11", "B", "Director", 90000, False, "y", 1]]}
        self.assertIsNone(insiders.cluster(book["ZNTX"], "2026-11-12"))

    def test_message_loud_with_ceo(self):
        rows = [["2026-11-10", "SMITH JOHN", "Chief Executive Officer", 50000, False, "a", 10000],
                ["2026-11-11", "DOE JANE", "Director", 60000, True, "b", 12000]]
        text, loud = insiders.message("ZNTX", "Zentrox Inc", 2, 110000, rows, 450e6, 5.5, 5.0)
        self.assertTrue(loud)
        self.assertIn("2 insiders bought $110k in 2 days", text)
        self.assertIn("pre-planned (10b5-1)", text)
        self.assertIn("+10% vs their average $5.00", text)
        _, loud2 = insiders.message("ZNTX", "Z", 2, 110000, [r[:2] + ["Director"] + r[3:] for r in rows])
        self.assertFalse(loud2)


class Watch(unittest.TestCase):
    def run_finish(self, st, res, watch=("ZNTX",)):
        main, other = [], []
        w = insiders.InsiderWatch(lambda t, s: main.append((t, s)), lambda t, s: other.append((t, s)), watch)
        with mock.patch.object(insiders, "price_now", lambda tk: 5.0), mock.patch.object(insiders, "market_cap", lambda tk: 3e8), \
                mock.patch.object(insiders, "datetime", wraps=insiders.datetime) as dt:
            dt.now.return_value = insiders.datetime(2026, 11, 12, 12, tzinfo=insiders.timezone.utc)
            w._finish(st, res)
        return main, other

    def test_first_scan_silent_then_posts_and_routes(self):
        a = f4("Smith John", CEO, [("2026-11-10", "P", 20000, 5.0)])
        b = f4("Doe Jane", DIR, [("2026-11-11", "P", 20000, 5.0)])
        c = f4("Roe Rick", DIR, [("2026-11-11", "P", 30000, 5.0)])
        o1 = f4("Smith John", CEO, [("2026-11-10", "P", 20000, 5.0)], tk="OTHR")
        o2 = f4("Doe Jane", DIR, [("2026-11-11", "P", 20000, 5.0)], tk="OTHR")
        st = {}
        self.assertEqual(self.run_finish(st, [("x0", None)]), ([], []))           # first scan only learns
        main, other = self.run_finish(st, [("a1", a), ("a2", b), ("o1", o1), ("o2", o2)])
        self.assertEqual(len(main), 1)                                             # watchlist stock -> main
        self.assertEqual(len(other), 1)                                            # others -> contracts channel
        self.assertIn("$ZNTX</b> INSIDER CLUSTER BUY", main[0][0])
        self.assertEqual(self.run_finish(st, [("a2", b)]), ([], []))                # nothing new
        main, _ = self.run_finish(st, [("a3", c)])                                 # a 3rd insider joins
        self.assertIn("3 insiders", main[0][0])
        self.assertFalse(main[0][1])                                               # loud (3+ buyers)
        self.assertIn("a3", st["seen"])


if __name__ == "__main__":
    unittest.main()
