"""📰 News: reorganization ≠ buyout, stale / late headlines, Alpaca fast check, AI guard rails."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import alpaca
import impact
import news

NOW = datetime.now(timezone.utc)

HEADER_FAMILY = ("<SEC-HEADER>\nFILER:\nCOMPANY CONFORMED NAME: ZENTROX S.A.\nCENTRAL INDEX KEY: 0000000001\n"
                 "FILER:\nCOMPANY CONFORMED NAME: ZENTROX HOLDINGS, INC.\nCENTRAL INDEX KEY: 0000000002\n<DOCUMENT>")
HEADER_DEAL = ("<SEC-HEADER>\nCOMPANY CONFORMED NAME: ZENTROX INC\nCENTRAL INDEX KEY: 0000000001\n"
               "COMPANY CONFORMED NAME: BIGBUYER CORP\nCENTRAL INDEX KEY: 0000000003\n<DOCUMENT>")


def filing(form="425", items=(), acc=None):
    filing.n = getattr(filing, "n", 0) + 1
    return {"form": form, "items": list(items), "doc": "d.htm", "acc": acc or f"0000000001-26-{filing.n:06d}",
            "link": "https://www.sec.gov/doc", "cik": "1"}


def sec_text(main, header):
    return lambda url, limit: header if url.endswith(".txt") else main


class Reorganization(unittest.TestCase):
    def check(self, f, main, header=HEADER_DEAL):
        with mock.patch.object(news, "_sec_text", sec_text(main, header)):
            return news.sec_reorg(f)

    def test_company_merging_into_own_holding_company(self):
        self.assertTrue(self.check(filing(), "The merger is part of the company's proposed redomiciliation to the US.",
                                   HEADER_FAMILY))
        self.assertTrue(self.check(filing("S-4"), "Each share will be converted into one share of common stock of "
                                   "a wholly owned subsidiary, which merges with and into the Company."))

    def test_real_buyout_is_not_a_reorganization(self):
        self.assertFalse(self.check(filing("DEFM14A"), "Holders receive $3.16 per share in cash, a premium of 58%.",
                                    HEADER_FAMILY))
        self.assertFalse(self.check(filing("SC TO-T"), "Its redomiciliation ..."))             # not a merger form
        self.assertFalse(self.check(filing(), "BigBuyer will acquire Zentrox in a merger."))

    def test_8k_needs_item_101_and_a_merger(self):
        self.assertFalse(self.check(filing("8-K", ["8.01"]), "our redomiciliation"))
        self.assertFalse(self.check(filing("8-K", ["1.01"]), "loan amendment mentioning our redomiciliation"))
        self.assertTrue(self.check(filing("8-K", ["1.01"]), "merger agreement for our redomiciliation"))

    def test_fetch_error_means_normal_alert(self):
        def boom(url, limit):
            raise OSError
        with mock.patch.object(news, "_sec_text", boom):
            self.assertFalse(news.sec_reorg(filing()))

    def test_reorg_message_is_not_a_buyout_alarm(self):
        w = news.NewsWatcher(["ZNTX"], lambda *a: True)
        msg = w._format({"kind": "sec", "ticker": "ZNTX", "form": "425", "items": [], "desc": "", "reorg": True,
                         "link": "https://www.sec.gov/x", "date": NOW})
        self.assertTrue(msg.startswith("📄"))
        self.assertIn("NOT a buyout", msg)
        self.assertEqual(impact.rule_sec({"form": "425", "items": [], "reorg": True})[0], "neutral")


def alp(i, title, minutes_ago, symbols=("ZNTX",)):
    return {"id": i, "headline": title, "summary": "", "url": "https://www.benzinga.com/x",
            "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "symbols": list(symbols), "source": "benzinga"}


class FastAlpacaCheck(unittest.TestCase):
    def setUp(self):
        alpaca._items.clear()
        self.p = [mock.patch.object(alpaca, "ON", True), mock.patch.object(news.alpaca, "ON", True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        alpaca._items.clear()

    def feed(self, *batches):
        b = list(batches)
        return mock.patch.object(alpaca, "_get", lambda params: b.pop(0) if len(b) > 1 else b[0])

    def test_symbol_filter(self):
        with self.feed({"news": [alp(1, "Zentrox Gets FDA Approval", 1), alp(2, "Other Co News", 1, ["OTHR"])]}):
            alpaca._last = float("-inf")
            got = news.alpaca_news(["ZNTX"])
        self.assertEqual([g["ticker"] for g in got], ["ZNTX"])
        self.assertEqual(got[0]["source"], "Benzinga")

    def test_backlog_silent_then_new_headline_sent_and_slow_sources_skipped(self):
        out = []
        w = news.NewsWatcher(["ZNTX"], lambda m, *a: out.append(m) or True)
        st = {}
        with self.feed({"news": [alp(1, "Zentrox Reports Q3 Results", 90)]},
                       {"news": [alp(2, "Zentrox Receives FDA Approval", 1), alp(1, "Zentrox Reports Q3 Results", 90)]}), \
                mock.patch.object(news, "yahoo_news", side_effect=AssertionError("slow source in fast mode")), \
                mock.patch.object(news, "fda_news", side_effect=AssertionError("slow source in fast mode")):
            alpaca._last = float("-inf")
            w.check(st, fast=True)
            self.assertEqual(out, [])                       # first look: existing items skipped quietly
            alpaca._last = float("-inf")
            w.check(st, fast=True)
        self.assertEqual(len(out), 1)
        self.assertIn("FDA Approval", out[0])
        self.assertNotIn("⏰", out[0])                       # 1 minute old: not late

    def test_stale_headline_never_sent(self):
        out = []
        w = news.NewsWatcher(["ZNTX"], lambda m, *a: out.append(m) or True)
        st = {"_news": {"init": ["Alpaca"]}}                # source already known
        with self.feed({"news": [alp(1, "Zentrox Receives FDA Approval", 60 * (news.NEWS_MAX_AGE_HOURS + 2))]}):
            alpaca._last = float("-inf")
            w.check(st, fast=True)
        self.assertEqual(out, [])

    def test_late_headline_is_labelled(self):
        w = news.NewsWatcher(["ZNTX"], lambda *a: True)
        msg = w._format({"kind": "news", "ticker": "ZNTX", "title": "Zentrox wins award", "link": "https://x",
                         "source": "Some Site", "date": NOW - timedelta(hours=5)})
        self.assertIn("⏰ Published 5h ago", msg)


class Guard(unittest.TestCase):
    def test_high_needs_known_source_and_agreeing_rules(self):
        it = {"title": "Zentrox receives FDA approval", "source": "Reuters"}
        self.assertEqual(impact._guard(it, "up", "high"), "high")
        self.assertEqual(impact._guard({**it, "source": "PR Newswire"}, "up", "high"), "medium")
        self.assertEqual(impact._guard({**it, "title": "Zentrox update"}, "up", "high"), "medium")
        self.assertEqual(impact._guard(it, "down", "high"), "medium")
        self.assertEqual(impact._guard(it, "up", "medium"), "medium")

    def test_ai_reason_is_cleaned(self):
        r = impact._clean_reason("Buy now <b>here</b> https://evil.example/x and scam.com today " + "word " * 30)
        self.assertNotIn("http", r)
        self.assertNotIn("<", r)
        self.assertNotIn("scam.com", r)
        self.assertLessEqual(len(r.split()), 16)


if __name__ == "__main__":
    unittest.main()


def award(gid, amount=50e6):
    return {"kind": "gov", "ticker": "ZNTX", "award_id": gid, "gid": gid, "amount": amount, "recipient": "ZENTROX INC",
            "agency": "Department of Defense", "sub_agency": "Army", "desc": "TEST SUPPLIES", "signed": "2026-11-01",
            "start": "2026-11-01", "end": "2027-11-01", "date": None, "link": "https://www.usaspending.gov/award/x"}


class UsaspendingRouting(unittest.TestCase):
    """USAspending.gov answers GitHub's servers with HTTP 503, so the phone runs it alone (gov_only)."""
    no_other = {n: mock.patch.object(news, n, side_effect=AssertionError(n + " called in gov-only mode"))
                for n in ("google_news", "yahoo_news", "seekingalpha_news", "google_contracts", "google_calendar",
                          "sec_filings", "fda_news", "prnewswire_news", "alpaca_news")}

    def test_gov_only_backlog_silent_then_new_award_sent(self):
        out = []
        w = news.NewsWatcher(["ZNTX"], lambda m, *a: out.append(m) or True, gov_only=True)
        st = {}
        batches = [[award("A1")], [award("A2"), award("A1")]]
        with mock.patch.object(news, "usaspending", lambda t: batches.pop(0)), \
                mock.patch.object(news, "USASPENDING_ON", False), \
                mock.patch.object(impact, "ai_enabled", lambda: False):
            for p in self.no_other.values():
                p.start()
            try:
                w.check(st)
                self.assertEqual(out, [])                   # first look: existing awards skipped quietly
                w.last_usaspending = float("-inf")
                w.check(st)
            finally:
                for p in self.no_other.values():
                    p.stop()
        self.assertEqual(len(out), 1)
        self.assertIn("ZNTX", out[0])

    def test_gov_only_waits_three_hours_between_queries(self):
        calls = []
        w = news.NewsWatcher(["ZNTX"], lambda *a: True, gov_only=True)
        with mock.patch.object(news, "usaspending", lambda t: calls.append(1) or []):
            w.check({})
            w.check({})
        self.assertEqual(len(calls), 1)

    def test_github_skips_usaspending(self):
        w = news.NewsWatcher([], lambda *a: True)
        with mock.patch.object(news, "USASPENDING_ON", False), \
                mock.patch.object(news, "usaspending", side_effect=AssertionError("USAspending called on GitHub")), \
                mock.patch.object(news, "fda_news", lambda t: []), mock.patch.object(news, "prnewswire_news", lambda t: []):
            w.check({})
            self.assertIn("via the phone", w.sources_label())

    def test_default_off_only_for_github_role(self):
        import importlib, os
        for role, flag, want in (("github", "", False), ("phone", "", True), ("", "", True), ("github", "true", True),
                                 ("phone", "false", False)):
            with mock.patch.dict(os.environ, {"HEARTBEAT_ROLE": role, "USASPENDING": flag}):
                self.assertEqual(importlib.reload(news).USASPENDING_ON, want, (role, flag))
        importlib.reload(news)


class UsaspendingGroups(unittest.TestCase):
    """Too many company names in one search → HTTP 503 from USAspending.gov, so names go in small groups."""

    def test_names_split_into_groups_and_results_merged(self):
        import json
        names = {f"Z{i:02d}": [f"Zentrox {i:02d} Inc"] for i in range(18)}
        bodies = []

        class Resp:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.data

        def fake(req, timeout=0):
            body = json.loads(req.data)
            bodies.append(body["filters"]["recipient_search_text"])
            res = [{"Recipient Name": n.upper(), "Award Amount": 50e6, "Base Obligation Date": "2099-01-01",
                    "generated_internal_id": "same" if n.endswith("00 Inc") or n.endswith("08 Inc") else n}
                   for n in body["filters"]["recipient_search_text"]]
            return Resp(json.dumps({"results": res}).encode())

        with mock.patch.dict(news.GOV_NAMES, names, clear=True), \
                mock.patch.object(news.urllib.request, "urlopen", fake), mock.patch.object(news.time, "sleep"):
            got = news.usaspending(list(names))
        self.assertEqual([len(b) for b in bodies], [8, 8, 2])
        self.assertTrue(all(len(b) <= news.USASPENDING_GROUP for b in bodies))
        self.assertEqual(len(got), 17)                       # same award id in two groups → listed once
        self.assertEqual(got[0]["ticker"], "Z00")
