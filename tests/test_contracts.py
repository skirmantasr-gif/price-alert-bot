"""🏛️ Contract scanner: pre-filter, sources, first-report check, 🚨 rules, Alpaca pass."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

import contracts as c
from tests.helpers import rss

NOW = datetime.now(timezone.utc)


def item(title, hours_ago=0.2, desc="", wire=False, market="US"):
    return {"title": title, "link": "https://example.com/a", "desc": desc, "market": market, "wire": wire,
            "date": NOW - timedelta(hours=hours_ago)}


def reading(**k):
    r = {"is_win": True, "company": "Zentrox Systems", "ticker_hint": "ZNTX", "amount": 300e6, "currency": "USD",
         "kind": "new", "public_buyer": True, "buyer": "US Army", "summary": "Depot support"}
    r.update(k)
    return r


class FakeYahoo:
    def __init__(self, cap=1e9, traded=False):
        self.cap, self.traded = cap, traded

    def fx_rate(self, ccy):
        return 1.0

    def find(self, company, hint):
        return ("ZNTX", "Zentrox Systems Inc.")

    def market_cap_usd(self, sym):
        return self.cap

    def traded_since(self, sym, t, minutes=30):
        return self.traded

    def price(self, sym):
        return 10.0


class NoETF:
    def holders(self, *a):
        return [] if len(a) == 1 else ("", [])


def scanner(extract=None, cap=1e9, traded=False, sent=None):
    out = sent if sent is not None else []
    sc = c.ContractScanner(send=lambda text, silent: out.append((text, silent)) or True,
                           extract=extract or (lambda items: {}), yahoo=FakeYahoo(cap, traded), etfs=NoETF(), ucits=NoETF())
    sc.verify = lambda it, r, name: r
    sc.first_seen = lambda it, r, name: (it.get("date"), "")
    sc.fetch_usaspending = lambda: []
    sc.fetch_asx = lambda: []
    sc.fetch_hkex = lambda: []
    return sc, out


class PreFilter(unittest.TestCase):
    def scan(self, items, label="Test"):
        got = []
        sc, _ = scanner(extract=lambda xs: got.extend(xs) or {})
        st = {"_cw": {"seen": {}, "init": {label: "x"}, "sent": []}}
        sc._scan(label, lambda: items, st["_cw"]["seen"], st["_cw"]["init"], st["_cw"]["sent"])
        return [g["title"] for g in got], sc, st

    def test_needs_amount_and_contract_word(self):
        titles, _, _ = self.scan([item("Zentrox wins $40 million Navy contract"),
                                  item("Zentrox opens new office"),
                                  item("Zentrox contract talks continue")])
        self.assertEqual(titles, ["Zentrox wins $40 million Navy contract"])

    def test_wire_items_may_have_amount_only_in_summary(self):
        titles, _, _ = self.scan([item("Zentrox Secures Major Order", desc="valued at $35 million", wire=True),
                                  item("Zentrox Secures Major Order", desc="valued at $35 million")])
        self.assertEqual(titles, ["Zentrox Secures Major Order"])

    def test_same_headline_only_once(self):
        it = item("Zentrox wins $40 million Navy contract")
        got = []
        sc, _ = scanner(extract=lambda xs: got.extend(xs) or {})
        cw = {"seen": {}, "init": {"T": "x"}, "sent": []}
        for _ in range(2):
            sc._scan("T", lambda: [it], cw["seen"], cw["init"], cw["sent"])
        self.assertEqual(len(got), 1)

    def test_first_look_skips_old_backlog(self):
        got = []
        sc, _ = scanner(extract=lambda xs: got.extend(xs) or {})
        cw = {"seen": {}, "init": {}, "sent": []}
        sc._scan("New", lambda: [item("Zentrox wins $40 million Navy contract", hours_ago=20)], cw["seen"], cw["init"], cw["sent"])
        self.assertEqual(got, [])

    def test_ai_failure_retries_next_time(self):
        calls = []

        def boom(xs):
            calls.append(1)
            raise RuntimeError
        sc, _ = scanner(extract=boom)
        cw = {"seen": {}, "init": {"T": "x"}, "sent": []}
        for _ in range(2):
            sc._scan("T", lambda: [item("Zentrox wins $40 million Navy contract")], cw["seen"], cw["init"], cw["sent"])
        self.assertEqual(len(calls), 2)


ASX_JSON = json.dumps({"data": {"items": [
    {"symbol": "ZTX", "headline": "Zentrox awarded $120m state rail contract", "documentKey": "1-2-A",
     "date": "2026-11-05T22:10:00.000Z", "isPriceSensitive": True, "announcementTypes": ["Other"]},
    {"symbol": "ZTX", "headline": "Change of Director's Interest Notice", "documentKey": "1-3-B",
     "date": "2026-11-05T22:00:00.000Z", "isPriceSensitive": False, "announcementTypes": ["Other"]},
    {"symbol": "ZOR", "headline": "MAP TEST 1", "documentKey": "1-4-C", "date": "2026-11-05T21:00:00.000Z",
     "announcementTypes": ["MAP Test"]}]}}).encode()
HKEX_JSON = json.dumps({"newsInfoLst": [
    {"title": "VOLUNTARY ANNOUNCEMENT\nWINNING OF TENDER", "webPath": "/listedco/a/1.pdf", "relTime": "06/11/2026 18:30",
     "stock": [{"sc": "00777", "sn": "ZENTROX HLDG"}]},
    {"title": "NOTICE OF AGM", "webPath": "/listedco/a/2.pdf", "relTime": "06/11/2026 18:00",
     "stock": [{"sc": "00777", "sn": "ZENTROX HLDG"}]}]}).encode()


class Exchanges(unittest.TestCase):
    def test_asx_items_parsed_and_only_contract_headlines_opened(self):
        with mock.patch.object(c, "_get", lambda url, **k: ASX_JSON):
            items = c.ContractScanner.fetch_asx()
        self.assertEqual(len(items), 2)  # MAP test skipped
        a, b = items
        self.assertEqual(a["symbol"], "ZTX.AX")
        self.assertEqual(a["market"], "Australia")
        self.assertTrue(a["pdf"].endswith("1-2-A"))
        self.assertEqual(b["pdf"], "")
        self.assertEqual(a["date"], datetime(2026, 11, 5, 22, 10, tzinfo=timezone.utc))

    def test_hkex_time_is_hong_kong_and_symbol_padded(self):
        with mock.patch.object(c, "_get", lambda url, **k: HKEX_JSON):
            items = c.ContractScanner.fetch_hkex()
        a, b = items
        self.assertEqual(a["symbol"], "0777.HK")
        self.assertEqual(a["date"], datetime(2026, 11, 6, 10, 30, tzinfo=timezone.utc))
        self.assertTrue(a["pdf"].startswith("https://www1.hkexnews.hk/"))
        self.assertIn("WINNING OF TENDER", a["title"])
        self.assertEqual(b["pdf"], "")

    def exitem(self, title, pdf="https://x/1.pdf", symbol="ZTX.AX"):
        return {"title": title, "link": pdf, "desc": "", "date": NOW - timedelta(minutes=5), "market": "Australia",
                "exchange": True, "symbol": symbol, "pdf": pdf}

    def test_pdf_opened_once_and_amount_read_from_it(self):
        got, opened = [], []
        sc, _ = scanner(extract=lambda xs: got.extend(xs) or {})
        cw = {"seen": {}, "init": {"ASX": "x"}, "sent": []}
        items = [self.exitem("ZTX: Contract award (ASX: ZTX)"), self.exitem("ZTX: Quarterly report", pdf="")]
        body = "Zentrox has been awarded a contract worth A$120 million by the state rail authority."
        with mock.patch.object(c, "pdf_text", lambda url: opened.append(url) or body):
            for _ in range(2):
                sc._scan("ASX", lambda: items, cw["seen"], cw["init"], cw["sent"])
        self.assertEqual(opened, ["https://x/1.pdf"])
        self.assertEqual([g["title"] for g in got], ["ZTX: Contract award (ASX: ZTX)"])
        self.assertIn("A$120 million", got[0]["desc"])

    def test_no_amount_in_pdf_not_sent_to_ai(self):
        got = []
        sc, _ = scanner(extract=lambda xs: got.extend(xs) or {})
        cw = {"seen": {}, "init": {"ASX": "x"}, "sent": []}
        with mock.patch.object(c, "pdf_text", lambda url: "Zentrox was selected as preferred tenderer."):
            sc._scan("ASX", lambda: [self.exitem("ZTX: Preferred tenderer (ASX: ZTX)")], cw["seen"], cw["init"], cw["sent"])
        self.assertEqual(got, [])

    def test_exchange_symbol_used_and_no_google_second_check(self):
        sc, out = scanner()
        sc.verify = lambda *a: self.fail("second check must be skipped for exchange announcements")
        sc.yahoo.find = lambda *a: self.fail("symbol is known from the exchange")
        ok = sc.handle(self.exitem("ZTX: Contract award (ASX: ZTX)"), reading(currency="AUD"), [], NOW.strftime("%Y-%m-%d"))
        self.assertTrue(ok)
        self.assertIn("ZTX.AX", out[0][0])
        self.assertIn("Australia", out[0][0])

    def test_pdf_reader_runs_in_a_separate_process_with_a_time_limit(self):
        with mock.patch.object(c, "_PDF_CHILD", "while True: pass"), mock.patch.object(c, "PDF_SECONDS", 1):
            with self.assertRaises(TimeoutError):
                c._read_pdf_guarded(b"%PDF-1.7", 3)
        with mock.patch.object(c, "_PDF_CHILD", "import sys; sys.exit(3)"):
            with self.assertRaises(RuntimeError):
                c._read_pdf_guarded(b"%PDF-1.7", 3)
        with mock.patch.object(c, "_PDF_CHILD", "import sys, json; print(json.dumps(sys.stdin.buffer.read().decode()))"):
            self.assertEqual(c._read_pdf_guarded(b"%PDF-1.7 text", 3), "%PDF-1.7 text")

    def test_hanging_pdf_is_skipped_not_fatal(self):
        with mock.patch.dict("sys.modules", {"pypdf": mock.MagicMock()}), \
                mock.patch.object(c, "_get", lambda url, timeout=60: b"%PDF-1.7 x"), \
                mock.patch.object(c, "_PDF_CHILD", "while True: pass"), mock.patch.object(c, "PDF_SECONDS", 1):
            self.assertEqual(c.pdf_text("https://x/1.pdf"), "")

    def test_pdf_text_without_pypdf_is_empty(self):
        with mock.patch.dict("sys.modules", {"pypdf": None}):
            self.assertEqual(c.pdf_text("https://x/1.pdf"), "")


class Sources(unittest.TestCase):
    def test_rotation_wire_then_us_then_markets(self):
        sc, _ = scanner()
        order = []
        sc.fetch_wire = lambda: order.append("PRN") or []
        sc.fetch = lambda m: order.append(m[0]) or []
        st = {}
        for _ in range(4):
            sc.check(st)
        self.assertEqual(order[0], "PRN")
        self.assertEqual(order[1], "US")
        self.assertEqual(order[3], c.MARKETS[1][0])

    def test_alpaca_pass_every_call_when_on(self):
        sc, _ = scanner()
        sc.fetch_wire = lambda: []
        sc.fetch = lambda m: []
        seen = []
        with mock.patch.object(c.alpaca, "ON", True), \
                mock.patch.object(c.alpaca, "recent", lambda: seen.append(1) or []):
            sc.check({})
            sc.check({})
        self.assertEqual(len(seen), 2)

    def test_alpaca_items_carry_symbols_for_the_ai(self):
        sc, _ = scanner()
        news = [{"title": "Zentrox wins $40 million Navy contract", "link": "https://x", "desc": "Summary",
                 "date": NOW, "symbols": ["ZNTX"], "source": "Benzinga"}]
        with mock.patch.object(c.alpaca, "recent", lambda: news):
            it = sc.fetch_alpaca()[0]
        self.assertIn("ZNTX", it["desc"])
        self.assertTrue(it["wire"])


class FirstReport(unittest.TestCase):
    def test_finds_earlier_coverage_and_pentagon_list(self):
        sc, _ = scanner()
        sc.first_seen = c.ContractScanner.first_seen.__get__(sc)
        older = format_datetime(NOW - timedelta(hours=7))
        oldest = format_datetime(NOW - timedelta(hours=18))

        def fake_get(url, **k):
            if "site%3Awar.gov" in url:
                return rss([("Contracts for Nov. 5, 2026 - U.S. Department of War", oldest),
                            ("Some other war.gov page", format_datetime(NOW - timedelta(hours=40)))])
            return rss([("Army turns to contractors after depots lose staff - Defence Blog", older),
                        ("Unrelated company declares dividend", format_datetime(NOW - timedelta(hours=30)))])
        with mock.patch.object(c, "_get", fake_get):
            d, src = sc.first_seen(item("Zentrox lands $300 million Army contract", hours_ago=0.5), reading(), "Zentrox Systems Inc.")
        self.assertAlmostEqual((NOW - d).total_seconds() / 3600, 18, delta=0.1)
        self.assertIn("Pentagon", src)

    def test_old_contract_found_at_any_date_but_only_strict_matches(self):
        sc, _ = scanner()
        sc.first_seen = c.ContractScanner.first_seen.__get__(sc)
        urls = []

        def fake_get(url, **k):
            urls.append(url)
            return rss([("Zentrox wins $300m state road contracts - Daily Wire", format_datetime(NOW - timedelta(days=40))),
                        ("Zentrox shares: 300 reasons to buy - Blog", format_datetime(NOW - timedelta(days=60))),
                        ("Zentrox lands $300 million contract - Old News", format_datetime(NOW - timedelta(days=400)))])
        with mock.patch.object(c, "_get", fake_get):
            d, src = sc.first_seen(item("Zentrox wins $300m road contracts", hours_ago=14), reading(buyer="State roads"),
                                   "Zentrox Systems Inc.")
        self.assertAlmostEqual((NOW - d).total_seconds() / 86400, 40, delta=0.1)
        self.assertIn("Daily Wire", src)
        self.assertFalse(any("when%3A" in u for u in urls))

    def test_no_pentagon_search_for_civil_buyers(self):
        sc, _ = scanner()
        sc.first_seen = c.ContractScanner.first_seen.__get__(sc)
        urls = []
        with mock.patch.object(c, "_get", lambda url, **k: urls.append(url) or rss([])):
            sc.first_seen(item("x"), reading(buyer="City of Springfield"), "Zentrox Systems Inc.")
        self.assertFalse(any("war.gov" in u for u in urls))


class Alerts(unittest.TestCase):
    def handle(self, it, r, **k):
        sc, out = scanner(**{x: k.pop(x) for x in ("cap", "traded") if x in k})
        if "first" in k:
            first = k.pop("first")
            sc.first_seen = lambda *a: (NOW - timedelta(hours=first), "the Pentagon's daily contract list")
        sent = []
        ok = sc.handle(it, r, sent, NOW.strftime("%Y-%m-%d"))
        return ok, out, sent

    def test_fresh_big_award_is_loud(self):
        ok, out, sent = self.handle(item("Zentrox lands $300 million Army contract"), reading())
        self.assertTrue(ok)
        text, silent = out[0]
        self.assertFalse(silent)
        self.assertTrue(text.startswith("🚨🏛️"))
        self.assertIn("30% of market cap", text)
        self.assertEqual(sent[0][0], "ZNTX")

    def test_quiet_when_market_already_traded_on_it(self):
        ok, out, _ = self.handle(item("Zentrox lands $300 million Army contract"), reading(), traded=True)
        self.assertTrue(out[0][1])
        self.assertIn("market has been open since", out[0][0])

    def test_quiet_and_labelled_when_first_reported_long_ago(self):
        ok, out, _ = self.handle(item("Zentrox lands $300 million Army contract", hours_ago=0.5), reading(), first=18)
        text, silent = out[0]
        self.assertTrue(silent)
        self.assertIn("Likely first reported 18h ago", text)

    def test_contract_first_reported_over_a_week_ago_not_sent(self):
        ok, out, sent = self.handle(item("Zentrox wins $300m road contracts", hours_ago=14), reading(), first=24 * 40)
        self.assertFalse(ok)
        self.assertEqual(out, [])
        self.assertEqual(sent, [])

    def test_small_relative_to_cap_is_quiet(self):
        ok, out, _ = self.handle(item("Zentrox lands $30 million Army contract"), reading(amount=30e6), cap=5e9)
        self.assertTrue(out[0][1])

    def test_skips_private_buyer_tiny_amount_and_wrong_cap(self):
        if c.GOV_ONLY:
            self.assertFalse(self.handle(item("x"), reading(public_buyer=False))[0])
        self.assertFalse(self.handle(item("x"), reading(amount=1e6))[0])
        self.assertFalse(self.handle(item("x"), reading(), cap=50e9)[0])
        self.assertFalse(self.handle(item("x"), reading(is_win=False))[0])

    def test_same_award_from_second_source_not_repeated(self):
        sc, out = scanner()
        sent = []
        today = NOW.strftime("%Y-%m-%d")
        sc.handle(item("Zentrox lands $300 million Army contract"), reading(), sent, today)
        sc.handle(item("Army picks Zentrox in $310M deal"), reading(amount=310e6), sent, today)
        self.assertEqual(len(out), 1)

    def test_ceiling_words_turn_new_into_ceiling(self):
        ok, out, _ = self.handle(item("Zentrox wins IDIQ contract worth up to $300 million"), reading())
        self.assertIn("Ceiling / framework", out[0][0])

    def test_winner_not_the_listed_company_is_skipped(self):
        ok, out, sent = self.handle(item("Zentrox Science wins $300 million NASA contract"), reading(winner_listed=False))
        self.assertFalse(ok)
        self.assertEqual(out, [])
        ok, out, _ = self.handle(item("Zentrox Science wins $300 million NASA contract"),
                                 reading(summary="Awarded to Zentrox Science, private firm, not Zentrox Systems"))
        self.assertFalse(ok)
        self.assertEqual(out, [])

    def test_second_check_can_reveal_wrong_winner(self):
        sc, out = scanner()
        sc.verify = lambda it, r, name: {**r, "winner_listed": False}
        self.assertFalse(sc.handle(item("Zentrox lands $300 million Army contract"), reading(), [], "d"))
        self.assertEqual(out, [])

    def test_shared_total_is_never_loud(self):
        for it, r in ((item("NASA picks Zentrox for $3 billion lunar framework"), reading(amount=3e9, shared=True)),
                      (item("Zentrox among vendors on $3 billion multiple-award contract"), reading(amount=3e9)),
                      (item("NASA picks Zentrox for lunar deliveries"), reading(amount=3e9, summary="one of 5 companies selected"))):
            ok, out, _ = self.handle(it, r)
            self.assertTrue(ok)
            text, silent = out[0]
            self.assertTrue(silent)
            self.assertFalse(text.startswith("🚨"))
            self.assertIn("Shared by several winners", text)
            self.assertIn("Ceiling / framework", text)

    def test_second_check_coverage_marks_shared(self):
        sc, out = scanner()
        sc.verify = lambda it, r, name: {**r, "shared": True}
        sc.handle(item("Zentrox lands $300 million Army contract"), reading(), [], "d")
        self.assertTrue(out[0][1])

    def test_ai_schema_asks_for_new_fields(self):
        req = c.SCHEMA["properties"]["items"]["items"]["required"]
        self.assertIn("winner_listed", req)
        self.assertIn("shared", req)


if __name__ == "__main__":
    unittest.main()


def award(gid, name="ZENTROX SYSTEMS, INC.", amount=40e6, days_ago=3, desc="DEPOT SUPPORT SERVICES"):
    return {"Award ID": gid, "generated_internal_id": "CONT_AWD_" + gid, "Recipient Name": name, "Award Amount": amount,
            "Awarding Agency": "Department of Homeland Security", "Awarding Sub Agency": "U.S. Coast Guard",
            "Description": desc, "Base Obligation Date": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d")}


class Usaspending(unittest.TestCase):
    """🏛️ All new US federal awards from USAspending.gov → listed US companies (on IBKR / Trading 212 / Revolut)."""

    def run_scan(self, batches, yahoo=None, cap=1e9):
        sc, out = scanner(cap=cap)
        if yahoo:
            sc.yahoo = yahoo
        b = list(batches)
        sc.fetch_usaspending = lambda: b.pop(0)
        st = {}
        for _ in range(len(batches)):
            sc.last_usa = float("-inf")
            sc.last_wire = sc.last_us = float("inf")   # only the USAspending pass in this test
            with mock.patch.object(sc, "fetch", lambda m: []):
                sc.check(st)
        return out, st

    def test_backlog_quiet_then_new_award_sent_with_platforms(self):
        out, st = self.run_scan([[award("A1")], [award("A2", amount=55e6), award("A1")]])
        self.assertEqual(len(out), 1)
        text, silent = out[0]
        self.assertTrue(silent)                                  # official data is days old: never loud
        self.assertIn("$55.00M", text)
        self.assertIn("Trading 212 ✅", text)
        self.assertIn("Revolut ✅", text)
        self.assertIn("usaspending.gov/award/CONT_AWD_A2", text)
        self.assertIn("Homeland Security", text)
        self.assertEqual(set(st["_cw"]["usa"]), {"CONT_AWD_A1", "CONT_AWD_A2"})

    def test_private_or_foreign_or_wrong_name_skipped(self):
        class Y(FakeYahoo):
            def find(self, company, hint):
                return {"Zentrox Systems": ("ZNTX", "Zentrox Systems Inc."),
                        "Acme": None,                                            # private company
                        "Bbb Tech": ("BBB.L", "BBB Tech plc"),                   # only listed in London
                        "American Widget": ("AAA", "American Airlines Group")}.get(company)
        out, _ = self.run_scan([[], [award("B1", "ACME FEDERAL LLC"), award("B2", "BBB TECH LTD"),
                                     award("B3", "AMERICAN WIDGET CO"), award("B4")]], yahoo=Y())
        self.assertEqual(len(out), 1)
        self.assertIn("ZNTX", out[0][0])

    def test_any_market_cap_and_no_double_report(self):
        out, _ = self.run_scan([[], [award("C1", amount=20e6)]], cap=500e9)   # mega cap is reported too
        self.assertEqual(len(out), 1)
        sc, out2 = scanner()
        sent = [["ZNTX", 20.0, NOW.strftime("%Y-%m-%d")]]                       # same award came from the news
        self.assertFalse(sc.handle_award(award("C2", amount=20.5e6), sent, NOW.strftime("%Y-%m-%d")))
        self.assertEqual(out2, [])

    def test_fetch_failure_is_logged_not_raised(self):
        sc, out = scanner()
        sc.fetch_usaspending = mock.Mock(side_effect=OSError("503"))
        sc.last_wire = sc.last_us = float("inf")
        with mock.patch.object(sc, "fetch", lambda m: []):
            sc.check({})
        self.assertEqual(out, [])

    def test_name_match(self):
        self.assertTrue(c._gov_name_match("ROCKET LAB USA, INC.", "Rocket Lab Corporation"))
        self.assertFalse(c._gov_name_match("AMERICAN WIDGET CO", "American Airlines Group Inc."))
        self.assertFalse(c._gov_name_match("GILBANE FEDERAL", "Gilead Sciences, Inc."))
        self.assertEqual(c._gov_query("ROCKET LAB USA, INC."), "Rocket Lab")
        self.assertEqual(c._gov_query("ZENTROX SYSTEMS, INC."), "Zentrox Systems")
        self.assertEqual(c._gov_query("ACME FEDERAL LLC"), "Acme")
