"""💊 FDA calendar from SEC filings + 📼 event results (2026-11-20)."""
import unittest
from datetime import date, datetime
from unittest import mock

import catalysts as c

TODAY = date(2026, 11, 20)


class FindDates(unittest.TestCase):
    def dates(self, text):
        return [x[0] for x in c.find_dates(text, TODAY)]

    def test_plain(self):
        self.assertEqual(self.dates("The FDA assigned a PDUFA target action date of March 11, 2027. Next."), ["2027-03-11"])

    def test_extension_takes_new_date(self):
        f = c.find_dates("This resulted in a three-month extension of the PDUFA target action date from August 22, "
                         "2026 to November 22, 2026. Other text.", TODAY)
        self.assertEqual(f[0][:1] + (f[0][2],), ("2026-11-22", True))

    def test_rent_date_in_same_sentence_ignored(self):   # CAPR 10-Q (dry run): rent date 2027-11-22 won before
        t = ("Following the FDA's extension of the PDUFA target action date to November 22, 2026, the Company updated "
             "its estimated contractual and rent commencement dates to November 22, 2026 and November 22, 2027.")
        self.assertEqual(self.dates(t), ["2026-11-22"])

    def test_date_before_words(self):
        self.assertEqual(self.dates("An NDA with a March 29, 2027, PDUFA date is under FDA review."), ["2027-03-29"])

    def test_boilerplate_and_reauthorization_skipped(self):
        self.assertEqual(self.dates("There were reports of the FDA failing to meet its PDUFA goal dates, for example "
                                    "on December 11, 2026."), [])
        self.assertEqual(self.dates("In August 2026 the FDA released a commitment letter for PDUFA VIII, due "
                                    "January 15, 2027."), [])

    def test_early_approval_and_exclusivity_skipped(self):
        self.assertEqual(self.dates("The FDA approved the sNDA four months ahead of its PDUFA goal date of January 4, 2027."), [])
        self.assertEqual(self.dates("Final approval is subject to expiration of orphan exclusivity, PDUFA January 10, 2027."), [])

    def test_potential_pdufa_skipped(self):
        self.assertEqual(self.dates("FDA Dec. 27, 2026 Day 60 filing decision. Priority review and potential PDUFA "
                                    "date at Day 74."), [])

    def test_past_dates_ignored(self):
        self.assertEqual(self.dates("PDUFA date of October 1, 2026."), [])


class Helpers(unittest.TestCase):
    def test_ticker_of(self):
        self.assertEqual(c.ticker_of(["CAPRICOR THERAPEUTICS, INC.  (CAPR)  (CIK 0001133869)"]),
                         ("CAPR", "CAPRICOR THERAPEUTICS, INC."))
        self.assertEqual(c.ticker_of(["DBV Technologies S.A.  (DBVT, DBVTF)  (CIK 0001613780)"])[0], "DBVT")
        self.assertEqual(c.ticker_of(["Some Private Co  (CIK 0000000001)"]), (None, None))

    def test_other_company(self):
        names = {"capricor", "dyne", "sarepta"}
        self.assertTrue(c.other_company("Capricor Therapeutics' BLA has a PDUFA date of November 22, 2026.",
                                        "dyne", names))
        self.assertFalse(c.other_company("Dyne's BLA has a PDUFA date of January 21, 2027.", "dyne", names))
        self.assertFalse(c.other_company("PDUFA date of January 21, 2027.", "dyne", names))

    def test_merge_extension_replaces_older(self):
        cal = {}
        c.merge(cal, "EXEL", "Exelixis", "2026-08-01", [("2026-12-03", "old", False)])
        c.merge(cal, "EXEL", "Exelixis", "2026-10-29", [("2027-03-03", "updated", True)])
        self.assertEqual(sorted(cal), ["EXEL:2027-03-03"])

    def test_merge_keeps_two_products(self):
        cal = {}
        c.merge(cal, "COGT", "Cogent", "2026-11-05", [("2026-11-30", "GIST", False), ("2026-12-30", "NonAdvSM", False)])
        self.assertEqual(len(cal), 2)


class Calendar(unittest.TestCase):
    def test_scan_merges_and_lists_once(self):
        sent = []
        cal = c.FdaCalendar(lambda text, silent: sent.append(text) or True, ["CAPR"])
        cal.thread = mock.Mock(is_alive=lambda: False)
        cal.result = [("CAPR", "Capricor", "2026-11-10", [("2026-11-22", "PDUFA of November 22, 2026", True)], "a:1"),
                      (None, None, None, [], "b:2")]
        cal.last = c.time.monotonic()                         # no new scan in this test
        st = {}
        with mock.patch.object(c.extras, "local_now", lambda: datetime(2026, 11, 20, 15, 0)), \
                mock.patch.object(c.date, "today", create=True) if False else mock.patch.object(c, "date", wraps=date) as d:
            d.today.return_value = TODAY
            self.assertTrue(cal.check(st))
            self.assertIn("CAPR:2026-11-22", st["_fdacal"]["ev"])
            self.assertEqual(sorted(st["_fdacal"]["docs"]), ["a:1", "b:2"])
            lists = [x for x in sent if x.startswith("💊")]
            self.assertEqual(len(lists), 1)                  # first list right after the first scan
            self.assertIn("$CAPR", lists[0])
            self.assertIn("⭐ watchlist", lists[0])
            self.assertTrue(any(x.startswith("⏰") for x in sent))   # Sunday decision: warned on Friday too
            n = len(sent)
            cal.check(st)
            self.assertEqual(len(sent), n)                   # neither again


def bars(closes, vols, start=1):
    return [(f"2026-11-{start + i:02d}", x, x, v) for i, (x, v) in enumerate(zip(closes, vols))]


class Results(unittest.TestCase):
    def test_reaction_waits_then_picks_busier_day(self):
        b = bars([10] * 8 + [10, 4], [100] * 8 + [120, 900], start=10)    # event Nov 18 (index 8), crash Nov 19
        self.assertIsNone(c.reaction(b[:9], "2026-11-18", date(2026, 11, 18)))
        k, vr = c.reaction(b, "2026-11-18", date(2026, 11, 20))
        self.assertEqual(b[k][0], "2026-11-19")
        self.assertGreater(vr, 5)

    def test_implied_from_last_snapshot_before(self):
        rec = {"snaps": {"2026-11-19": {"exps": [["2026-11-20", 7.5, 46.0, 1, 2, 10, 20],
                                                 ["2026-11-27", 7.5, 101.0, 1, 2, 9281, 38154]]},
                         "2026-11-23": {"exps": [["2026-11-27", 7.5, 30.0, 0, 0, 1, 1]]}}}
        imp, pc, exp = c.implied(rec, "2026-11-23")
        self.assertEqual((imp, exp), (101.0, "2026-11-27"))
        self.assertAlmostEqual(pc, 38154 / 9281)

    def test_result_post_and_scorecard(self):
        sent_main, sent_other = [], []
        rec = {"t": "CAPR", "event": "2026-11-22", "label": "FDA decision date (PDUFA)", "kind": "fda",
               "snaps": {"2026-11-20": {"exps": [["2026-11-27", 7.5, 101.0, 1, 2, 9281, 38154]]}}}
        b = [(f"2026-11-{d:02d}", 8, 8, 100) for d in (10, 11, 12, 13, 16, 17, 18, 19, 20)] + \
            [("2026-11-23", 3, 3, 900), ("2026-11-24", 3, 3, 300)]
        w = c.ResultWatch(lambda t, s: sent_main.append(t), lambda t, s: sent_other.append(t), ["CAPR"], bars=lambda t: b)
        st = {"_opth": {"CAPR:2026-11-22": rec}}
        self.assertTrue(w.check(st, now=datetime(2026, 11, 24, 23, 0)))
        self.assertEqual(sent_other, [])
        m = sent_main[0]
        self.assertIn("$CAPR</b> after its FDA decision", m)
        self.assertIn("-62.5%", m)
        self.assertIn("±101%", m)
        self.assertIn("smaller than priced", m)
        self.assertIn("Scorecard (FDA decisions, 1 so far): moved more than options priced in 0 of 1", m)
        self.assertFalse(w.check(st, now=datetime(2026, 11, 25, 23, 0)))   # once

    def test_non_watchlist_to_other_channel(self):
        sent_main, sent_other = [], []
        rec = {"t": "SVRA", "event": "2026-11-22", "label": "x", "kind": "fda", "snaps": {}}
        b = [(f"2026-11-{d:02d}", 5, 5, 100) for d in (10, 11, 12, 13, 16, 17, 18, 19, 20)] + \
            [("2026-11-23", 7, 7, 800), ("2026-11-24", 7, 7, 300)]
        w = c.ResultWatch(lambda t, s: sent_main.append(t), lambda t, s: sent_other.append(t), ["CAPR"], bars=lambda t: b)
        w.check({"_opth": {"SVRA:2026-11-22": rec}}, now=datetime(2026, 11, 24, 23, 0))
        self.assertEqual(len(sent_other), 1)
        self.assertIn("No options snapshot", sent_other[0])

    def test_earnings_result_uses_past_pattern(self):
        sent = []
        rec = {"t": "BZUN", "event": "2026-11-24", "label": "earnings", "kind": "earnings", "snaps": {}}
        b = [(f"2026-11-{d:02d}", 3, 3, 100) for d in (13, 16, 17, 18, 19, 20, 23)] + \
            [("2026-11-24", 2.7, 2.7, 700), ("2026-11-25", 2.7, 2.7, 200)]
        pat = lambda t: [{"d": "2026-08-20", "day": -0.08}, {"d": "2026-05-20", "day": 0.12}, {"d": "2026-12-01", "day": 0.5}]
        w = c.ResultWatch(lambda t, s: sent.append(t), lambda t, s: None, ["BZUN"], pattern=pat, bars=lambda t: b)
        w.check({"_opth": {"BZUN:2026-11-24": rec}}, now=datetime(2026, 11, 26, 23, 0))
        self.assertIn("after its earnings", sent[0])
        self.assertIn("Past report days: middle move ±10% (2 reports)", sent[0])


class ClosedDay(unittest.TestCase):
    def test_calendar(self):
        self.assertFalse(c.trading_day(date(2026, 11, 22)))             # Sunday
        self.assertFalse(c.trading_day(date(2026, 11, 26)))             # Thanksgiving
        self.assertEqual(c.last_trading_before(date(2026, 11, 22)), date(2026, 11, 20))
        self.assertEqual(c.last_trading_before(date(2026, 11, 26)), date(2026, 11, 25))
        self.assertEqual(c.last_trading_before(date(2027, 3, 28)), date(2027, 3, 25))   # Easter Sunday, Good Friday shut

    def state(self):
        return {"_cal": [["CAPR", "2026-11-22", "fda", "FDA decision date (PDUFA)"],
                         ["BZUN", "2026-11-22", "earnings", "earnings"]],
                "_fdacal": {"ev": {"SVRA:2026-11-22": {"t": "SVRA", "date": "2026-11-22"},
                                   "VRTX:2026-11-30": {"t": "VRTX", "date": "2026-11-30"},
                                   "X:2026-11-26": {"t": "X", "date": "2026-11-26"}}, "docs": [], "scanned": "x",
                             "v": c.CAL_V}}

    def test_events_today(self):
        self.assertEqual(c.closed_day_events(self.state(), date(2026, 11, 20)),
                         [("CAPR", "2026-11-22"), ("SVRA", "2026-11-22")])
        self.assertEqual(c.closed_day_events(self.state(), date(2026, 11, 25)), [("X", "2026-11-26")])
        self.assertEqual(c.closed_day_events(self.state(), date(2026, 11, 27)), [])   # VRTX Mon: a trading day

    def run_check(self, when):
        sent = []
        cal = c.FdaCalendar(lambda text, silent: sent.append((text, silent)) or True, ["CAPR"])
        cal.last = c.time.monotonic()
        st = self.state()
        st["_fdacal"]["list_week"] = "2026-W47"
        st["_fdacal"]["listed_once"] = True
        with mock.patch.object(c.extras, "local_now", lambda: when):
            cal.check(st)
            cal.check(st)
        return sent

    def test_warning_once_loud_for_watchlist(self):
        sent = self.run_check(datetime(2026, 11, 20, 9, 5))
        self.assertEqual(len(sent), 1)
        text, silent = sent[0]
        self.assertFalse(silent)
        self.assertIn("$CAPR</b> FDA decision due <b>Sun Nov 22</b> — a Sunday, markets closed ⭐", text)
        self.assertIn("$SVRA", text)
        self.assertIn("last trading day before", text)
        self.assertIn("reopens Mon", text)

    def test_not_before_nine(self):
        self.assertEqual(self.run_check(datetime(2026, 11, 20, 8, 30)), [])


class Fixes1120b(unittest.TestCase):
    def test_updated_counts_as_extension(self):
        f = c.find_dates("The updated PDUFA target action date is March 3, 2027.", TODAY)
        self.assertTrue(f[0][2])

    def test_rebuild_on_new_rules(self):
        cal = c.FdaCalendar(lambda *a: True)
        cal.last = c.time.monotonic()
        st = {"_fdacal": {"ev": {"X:2027-01-01": {"t": "X", "date": "2027-01-01"}}, "docs": ["a:1"], "warned": ["W"]}}
        with mock.patch.object(c.extras, "local_now", lambda: datetime(2026, 11, 21, 12, 0)):
            cal.check(st)
        f = st["_fdacal"]
        self.assertEqual((f["ev"], f["docs"], f["v"], f["warned"]), ({}, [], c.CAL_V, ["W"]))

    def test_oldest_filing_first(self):
        cal = c.FdaCalendar(lambda *a: True)
        cal.last = c.time.monotonic()
        cal.thread = mock.Mock(is_alive=lambda: False)
        cal.result = [("EXEL", "Exelixis", "2026-10-29", [("2027-03-03", "updated", True)], "b:2"),
                      ("EXEL", "Exelixis", "2026-08-01", [("2026-12-03", "old", False)], "a:1")]
        st = {"_fdacal": {"ev": {}, "docs": [], "v": c.CAL_V, "list_week": "x", "listed_once": True}}
        with mock.patch.object(c.extras, "local_now", lambda: datetime(2026, 11, 21, 12, 0)):
            cal.check(st)
        self.assertEqual(sorted(st["_fdacal"]["ev"]), ["EXEL:2027-03-03"])

    def test_quarterly_report_needs_own_sentence(self):
        found = [("2026-11-30", "s", False, "povetacicept in IgAN, with a PDUFA date of November 30, 2026."),
                 ("2027-01-21", "s", False, "We were assigned a PDUFA date of January 21, 2027.")]
        hits = {"j": {"display_names": ["Jade Biosciences, Inc.  (JBIO)  (CIK 1)"], "ciks": ["1"], "form": "10-Q",
                      "file_date": "2026-11-13"}}
        with mock.patch.object(c, "_sec", side_effect=[__import__("json").dumps({"hits": {"hits": [
                {"_id": "0001-26-1:j.htm", "_source": hits["j"]}]}}).encode(), b"x"] + [b'{"hits":{"hits":[]}}'] * 9), \
                mock.patch.object(c, "find_dates", lambda text, today: found), mock.patch.object(c.time, "sleep"):
            out = c.FdaCalendar(lambda *a: True)._job(set(), 4)
        self.assertEqual([x[0] for x in out[0][3]], ["2027-01-21"])


if __name__ == "__main__":
    unittest.main()
