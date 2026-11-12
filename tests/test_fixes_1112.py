"""Fixes from the 2026-11-12 check (no network: web calls are mocked)."""
import json
import unittest
import urllib.error
from unittest import mock

import buyout
import contracts
import learn
import movers
import paper


def _search(quotes):
    return json.dumps({"quotes": quotes}).encode()


IDAHO = {"symbol": "IDR", "quoteType": "EQUITY", "exchange": "ASE", "longname": "Idaho Strategic Resources, Inc."}


class TickerHintNeedsSameName(unittest.TestCase):
    def find(self, company, hint, by_query):
        y = contracts.Yahoo()
        with mock.patch.object(contracts, "_get", lambda url, *a, **k: _search(
                next((v for q, v in by_query.items() if q in url), []))):
            return y.find(company, hint)

    def test_other_company_with_same_ticker_rejected(self):
        # Indra Group (Madrid: IDR.MC) won the Caltrans project; the hint "IDR" found Idaho Strategic on NYSE.
        self.assertIsNone(self.find("Indra Group USA", "IDR", {"q=IDR": [IDAHO]}))

    def test_hint_with_matching_name_kept(self):
        q = {"symbol": "IDR", "quoteType": "EQUITY", "exchange": "ASE", "longname": "Idaho Strategic Resources, Inc."}
        self.assertEqual(self.find("Idaho Strategic", "IDR", {"q=IDR": [q]})[0], "IDR")

    def test_local_script_name_trusts_hint(self):
        q = {"symbol": "601222.SS", "quoteType": "EQUITY", "exchange": "SHH", "longname": "Jiangsu Linyang Energy"}
        self.assertEqual(self.find("林洋能源", "601222", {"q=601222": [q]})[0], "601222.SS")


class VoidIDR(unittest.TestCase):
    def test_idr_trade_listed_once(self):
        ids = [v[0] for v in paper.VOIDS]
        self.assertEqual(len(ids), len(set(ids)))
        v = next(v for v in paper.VOIDS if v[0] == "2026-11-12-a")
        self.assertEqual(v[1], paper._tkh("IDR"))
        self.assertLess(v[2], 1794414600 + 60)    # bought Nov 11 ~16:30 UTC
        self.assertGreater(v[3], 1794414600)


NASDAQ_LISTED = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
                 "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
                 "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"
                 "ZZZT|Test Co|Q|Y|N|100|N|N\n"
                 "ABCW|ABC Corp - Warrants|Q|N|N|100|N|N\n"
                 "File Creation Time: 1112202608:00|||||||\n")
OTHER_LISTED = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
                "AA|Alcoa Corp Common Stock|N|AA|N|100|N|AA\n"
                "AB$A|AB Preferred|N|AB-A|N|100|N|AB-A\n")


def _trader_get(big=True):
    def get(url, *a, **k):
        txt = NASDAQ_LISTED if "nasdaqlisted" in url else OTHER_LISTED
        if big:   # the real files hold ~6000 stocks; the sanity check wants > 1000
            first = "XF" if "nasdaqlisted" in url else "YF"
            txt += "".join(f"{first}{chr(65 + i // 26)}{chr(65 + i % 26)}|Filler {i} Inc. Common Stock|N|X|N|100|N|X\n"
                           for i in range(600))
        return txt.encode()
    return get


class MoversBackupList(unittest.TestCase):
    def test_symbol_files_parsed(self):
        with mock.patch.object(movers, "_get", _trader_get()):
            rows = {r["s"]: r for r in movers.trader_rows()}
        self.assertIn("AAPL", rows)
        self.assertIn("AA", rows)
        for gone in ("QQQ", "ZZZT", "ABCW", "AB$A"):          # ETF, test issue, warrant, preferred
            self.assertNotIn(gone, rows)
        self.assertIsNone(rows["AAPL"]["cap"])
        self.assertEqual(rows["AAPL"]["name"], "Apple Inc. -")

    def test_short_files_refused(self):
        with mock.patch.object(movers, "_get", _trader_get(big=False)):
            with self.assertRaises(ValueError):
                movers.trader_rows()

    def test_falls_back_when_screener_blocked(self):
        def blocked():
            raise urllib.error.HTTPError(movers.NASDAQ_URL, 403, "Forbidden", {}, None)
        with mock.patch.object(movers, "nasdaq_rows", blocked), mock.patch.object(movers, "_get", _trader_get()):
            rows, priced = movers.stock_list()
        self.assertFalse(priced)
        self.assertGreater(len(rows), 1000)

    def test_error_names_site_not_address(self):
        e = urllib.error.HTTPError(movers.NASDAQ_URL, 403, "Forbidden", {}, None)
        self.assertEqual(movers._why(e), "HTTPError 403 from api.nasdaq.com")
        self.assertEqual(movers._why(TimeoutError()), "TimeoutError")

    def test_unknown_value_passes_filter_then_checked(self):
        r = {"s": "AAA", "name": "A", "price": 10.0, "pct": 30.0, "vol": 1e6, "cap": None, "sector": "", "industry": ""}
        self.assertTrue(movers.tradeable(r))
        rows = [dict(r, s="SMALL"), dict(r, s="BIG"), dict(r, s="NONE"), dict(r, s="BIG2")]
        caps = {"SMALL": 50e6, "BIG": 900e6, "NONE": None, "BIG2": 2e9}
        with mock.patch.object(movers, "market_cap", caps.get), mock.patch.object(movers.time, "sleep"):
            self.assertEqual([x["s"] for x in movers.cap_ok(rows, 5)], ["BIG", "BIG2"])
            self.assertEqual([x["s"] for x in movers.cap_ok([dict(r, s="BIG"), dict(r, s="BIG2")], 1)], ["BIG"])

    def test_line_without_value(self):
        r = {"s": "AAA", "name": "A", "price": 10.0, "pct": 30.0, "vol": 1e6, "cap": None, "sector": "", "heads": []}
        self.assertNotIn("None", movers.MoversWatch(lambda *a: True)._line(r))


class PassiveStakeNotTakeover(unittest.TestCase):
    def test_passive_holder_reports_quiet(self):
        for t in ("State Street Reveals 4.7% Ownership Stake in MannKind",
                  "A 6.72M-share stake in Ironwood Pharmaceuticals was disclosed by a fund",
                  "Soleus Capital Discloses 7.7% Ownership Stake in Eyenovia"):
            self.assertIsNone(buyout.bo_hit(t), t)

    def test_real_takeover_signals_kept(self):
        for t in ("Elliott builds stake in Acme, pushes for sale",
                  "Activist takes stake in Acme",
                  "Starboard Value discloses stake in Acme",
                  "Acme rejects takeover offer from Monomoy",
                  "Form SCHEDULE 13D/A Greenfire Resources",
                  "Acme explores sale"):
            self.assertIsNotNone(buyout.bo_hit(t), t)


class HitRateCountsFlat(unittest.TestCase):
    def test_flat_lowers_headline_rate(self):
        ps = ([{"d": "up", "r": 3.0}] * 20 + [{"d": "up", "r": -3.0}] * 3 + [{"d": "up", "r": 0.2}] * 11)
        g = learn._group(ps)
        self.assertEqual(g["hit_rate_pct"], 87)      # of the 23 clear moves
        self.assertEqual(g["right_of_all_pct"], 59)  # 20 of 34


if __name__ == "__main__":
    unittest.main()


class TradableOnly(unittest.TestCase):
    def test_untradable_markets_not_scanned(self):
        names = [m[0] for m in contracts.SCAN_MARKETS]
        for gone in ("India", "South Korea", "Taiwan"):
            self.assertNotIn(gone, names)
        for kept in ("US", "Germany", "China", "Hong Kong", "Australia"):
            self.assertIn(kept, names)

    def test_untradable_symbol_skipped_before_any_lookup(self):
        s = contracts.ContractScanner.__new__(contracts.ContractScanner)
        s.skip, s.state = set(), None
        s.yahoo = mock.Mock()
        s.yahoo.fx_rate.return_value = 0.012
        s.yahoo.find.return_value = ("COCHINSHIP.NS", "Cochin Shipyard Limited")
        r = {"is_win": True, "public_buyer": True, "company": "Cochin Shipyard", "ticker_hint": "COCHINSHIP", "amount": 9.8e10, "currency": "INR",
             "kind": "new", "buyer": "Indian Navy"}
        it = {"title": "Cochin Shipyard wins Navy order", "market": "India", "date": None}
        sent = []
        with mock.patch.object(contracts, "TRADABLE_ONLY", True):
            self.assertFalse(s.handle(it, r, sent, "2026-11-12"))
        s.yahoo.market_cap_usd.assert_not_called()
