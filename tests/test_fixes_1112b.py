"""2026-11-12 (b): ☀️ failure line, 📣 candidates kept, 👥 history backfill, new sources scored but not traded."""
import logging
import unittest
from datetime import datetime, timedelta
from unittest import mock

import buyout
import extras
import insiders
import learn
import movers
import paper

from tests.test_insiders import CEO, DIR, FORM4, TX


class FailLine(unittest.TestCase):
    def test_counts_and_reports_yesterday(self):
        c = extras.FailCounter()
        lg = logging.getLogger("test-fails")
        lg.addHandler(c)
        lg.propagate = False
        y = datetime(2026, 11, 11, 12)
        with mock.patch.object(extras, "local_now", lambda: y):
            lg.warning("Movers %s job failed: %s", "close", "HTTPError 403 from api.nasdaq.com")
            lg.warning("Movers %s job failed: %s", "close", "HTTPError 403 from api.nasdaq.com")
            lg.warning("Contract scan HKEX failed: 403")
            lg.info("fine")
        state = {}
        self.assertTrue(c.flush_to(state))
        self.assertEqual(state["_fail"]["2026-11-11"]["Movers close job failed"], [2, "HTTPError 403 from api.nasdaq.com"])
        with mock.patch.object(extras, "FAILS", c), mock.patch.object(extras, "local_now", lambda: y + timedelta(days=1)):
            line = extras.fail_line(state)
        self.assertIn("Problems yesterday</b> (3)", line)
        self.assertIn("Movers close job failed ×2 (HTTPError 403 from api.nasdaq.com)", line)
        with mock.patch.object(extras, "FAILS", extras.FailCounter()), \
                mock.patch.object(extras, "local_now", lambda: y + timedelta(days=5)):
            self.assertEqual(extras.fail_line(state), "✅ No errors logged yesterday")

    def test_keeps_three_days(self):
        state = {"_fail": {f"2026-11-0{i}": {"x": [1, ""]} for i in range(1, 6)}}
        c = extras.FailCounter()
        c.counts = {"2026-11-06": {"y": [1, ""]}}
        c.flush_to(state)
        self.assertEqual(sorted(state["_fail"]), ["2026-11-04", "2026-11-05", "2026-11-06"])


def bo_row(t, score):
    return {"t": t, "score": score, "hits": ["smallcap"], "price": 10.0, "mcap": 5e8, "ttm": 1e8, "yoy": 0.1,
            "ev_s": 2.0, "debt": 0, "cash": 1e7, "dil": 0.0, "off_high": -0.4, "spikes": 0, "avg_vol": 1e5,
            "name": t + " Inc", "sector": "Industrials"}


class SignalCandidatesKept(unittest.TestCase):
    def test_low_fingerprint_signal_kept_others_dropped(self):
        today = extras.local_now().strftime("%Y-%m-%d")
        old = (extras.local_now() - timedelta(days=40)).strftime("%Y-%m-%d")
        bo = {"cands": {"ZNTX": {"since": today, "price": 9.0, "signal": "strategic_review"},
                        "OLDS": {"since": old, "price": 9.0, "signal": "strategic_review"},
                        "PLAIN": {"since": today, "price": 9.0}}}
        w = buyout.BuyoutWatch(lambda *a: True)
        w.result = ([bo_row("ZNTX", 1), bo_row("OLDS", 1), bo_row("PLAIN", 1)], [], 0)
        w._finish_screen(bo, first=False)
        self.assertIn("ZNTX", bo["cands"])          # fresh 📣 signal: kept despite score 1
        self.assertNotIn("OLDS", bo["cands"])       # older than 30 days: normal rule again
        self.assertNotIn("PLAIN", bo["cands"])


def xml(name, rel, d, sh, px=5.0):
    return FORM4.format(tk="ZNTX", name=name, rel=rel, tx=TX.format(d=d, code="P", sh=sh, px=px)).encode()


class InsiderHistory(unittest.TestCase):
    def test_earlier_buy_found_through_company_history(self):
        docs = {"new1": xml("Smith John", CEO, "2026-11-11", 20000),   # seen live
                "old1": xml("Doe Jane", DIR, "2026-11-03", 20000)}     # filed before the bot watched
        w = insiders.InsiderWatch(lambda *a: None, lambda *a: None, [])
        with mock.patch.object(insiders, "feed_page", lambda start: [("new1", 77)] if start == 0 else []), \
                mock.patch.object(insiders, "filing_xml", lambda cik, acc: docs[acc]), \
                mock.patch.object(insiders, "recent_form4", lambda cik: ["new1", "old1"]), \
                mock.patch.object(insiders.time, "sleep", lambda s: None):
            res = w._scan(set())
        self.assertEqual([a for a, _, _ in res["filings"]], ["new1", "old1"])
        self.assertEqual(res["looked"], [77])
        book = {}
        for acc, f, _ in res["filings"]:
            insiders.add_buys(book, f, acc, "2026-11-12")
        self.assertEqual(insiders.cluster(book["ZNTX"], "2026-11-12")[0], 2)
        with mock.patch.object(insiders, "feed_page", lambda start: [("new2", 77)] if start == 0 else []), \
                mock.patch.object(insiders, "filing_xml", lambda cik, acc: xml("Roe Rick", DIR, "2026-11-12", 100)), \
                mock.patch.object(insiders, "recent_form4", lambda cik: self.fail("looked up twice")), \
                mock.patch.object(insiders.time, "sleep", lambda s: None):
            self.assertEqual(w._scan(set(), {77})["looked"], [])     # already looked up lately


class ScoredNotTraded(unittest.TestCase):
    def test_new_sources_not_traded(self):
        with mock.patch.object(paper, "_state_ref", {"pf": {}}), mock.patch.object(paper, "on_signal") as on:
            for src in ("salesignal", "insider", "premarket"):
                paper.record({"src": src, "d": "up", "c": "high", "tk": "ZNTX"})
            on.assert_not_called()
            paper.record({"src": "news", "d": "up", "c": "high", "tk": "ZNTX"})
            on.assert_called_once()

    def test_premarket_list_recorded(self):
        calls = []
        w = movers.MoversWatch(lambda *a: True)
        res = {"day": "2026-11-13", "checked": 10, "traded": 5,
               "up": [{"s": "UPP", "name": "Up", "pct": 20.0, "price": 5.0, "cap": 3e8, "pre_min": 30, "heads": []}],
               "down": [{"s": "DWN", "name": "Down", "pct": -15.0, "price": 4.0, "cap": 3e8, "pre_min": 30, "heads": []}]}
        with mock.patch.object(learn, "record", lambda st, src, tk, d, c, by, text="", price=None:
                               calls.append((src, tk, d, price))):
            w._finish_pre({}, res, {})
        self.assertEqual(calls, [("premarket", "UPP", "up", 5.0), ("premarket", "DWN", "down", 4.0)])

    def test_report_names(self):
        for src in ("salesignal", "insider", "premarket"):
            self.assertIn(src, learn.SRC_NAMES)


if __name__ == "__main__":
    unittest.main()
