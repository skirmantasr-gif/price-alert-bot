"""🏛️ Contract scanner: pre-filter, sources, first-report check, 🚨 rules, Alpaca pass."""
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
