"""Fixes from the 2026-11-09 Telegram review (real headlines of that day, company names kept generic where possible)."""
import unittest
from unittest import mock

import bot
import buyout
import contracts
import extras
import insights
import news
from tests.helpers import ny

DAY = (2026, 11, 9)


class WrongCompany(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(news.COMPANIES, {"SLS": {"q": ['"SELLAS"'], "match": ["SELLAS", "SLS"]}})
        p.start()
        self.addCleanup(p.stop)

    def test_same_symbol_on_another_exchange_is_not_ours(self):
        self.assertFalse(news._matches("Solaris Resources (TSX: SLS) receives final tranche", "SLS"))
        self.assertFalse(news._matches("Solaris Resources SLS.TO receives final tranche", "SLS"))
        self.assertTrue(news._matches("SELLAS (SLS) reports Phase 3 data", "SLS"))
        self.assertTrue(news._matches("SLS shares jump after trial update", "SLS"))

    def test_funding_tranche_is_not_a_contract_win(self):
        ok, _, _ = news.contract_info("Solaris Resources Receives Final $50 Million Tranche of $200 Million Funding Package")
        self.assertFalse(ok)
        ok, gov, amt = news.contract_info("Zentrox receives $40 million BARDA funding award")
        self.assertTrue(ok and gov and amt == 40e6)

    def test_roundup_of_many_stocks_skipped(self):
        it = {"id": 1, "title": "Dole Posts Downbeat Q3 Results, Joins NVIDIA And Other Big Stocks Moving Lower",
              "desc": "", "link": "x", "date": None, "symbols": ["DOLE", "NVDA", "GLXY", "AAPL", "TSLA"],
              "source": "Benzinga"}
        single = {**it, "id": 2, "title": "Galaxy Digital prices offering", "symbols": ["GLXY"]}
        with mock.patch.dict(news.COMPANIES, {"GLXY": {"q": ["GLXY"], "match": ["Galaxy Digital", "GLXY"]}}), \
                mock.patch.object(news.alpaca, "recent", lambda: [it, single]):
            got = [x["title"] for x in news.alpaca_news(["GLXY"])]
        self.assertEqual(got, ["Galaxy Digital prices offering"])


class Quiet(unittest.TestCase):
    def loud(self, title, **extra):
        w = news.NewsWatcher.__new__(news.NewsWatcher)
        w._when = lambda d: ""
        msg = w.format({"kind": "news", "ticker": "ZNTX", "title": title, "source": "Yahoo Finance", "link": "x",
                        "date": None, **extra})
        return msg.startswith("🚨")

    def test_routine_items_are_quiet(self):
        self.assertFalse(self.loud("Zentrox Appoints Accomplished Commercial Leader Jo Smith to Board of Directors"))
        self.assertFalse(self.loud("Pomerantz probes Zentrox over delayed trial data"))
        self.assertFalse(self.loud("Zentrox at healthcare conference: pipeline in focus"))
        self.assertFalse(self.loud("Zentrox reports Phase 3 data", minor=True))  # AI says routine

    def test_real_news_stays_loud(self):
        self.assertTrue(self.loud("Zentrox reports positive Phase 3 data"))
        self.assertTrue(self.loud("Zentrox appoints new CEO"))
        self.assertTrue(self.loud("Zentrox Reports Third Quarter 2026 Results and Hosts Conference Call"))
        self.assertTrue(self.loud("Zentrox presents Phase 3 topline data at ESMO conference"))


class Buyout(unittest.TestCase):
    def test_band_inside_a_compound_word_is_not_the_company(self):
        title = "Best IPTV Service for Rural Viewers: My Bandwidth-First Buying Approach"
        self.assertFalse(buyout.mentions(title, "BAND", "Bandwidth Inc."))
        self.assertTrue(buyout.mentions("Bandwidth explores a sale", "BAND", "Bandwidth Inc."))
        self.assertIsNone(buyout.BO_WORDS.search(title))
        self.assertTrue(buyout.BO_WORDS.search("Zentrox received a takeover approach from a rival"))

    def test_related_line_shows_the_headline_once(self):
        extras.RECENT.pop("ZNTX", None)
        for _ in range(2):
            extras.remember("ZNTX", "📰 $ZNTX Zentrox names new chairman", "x")
        lines = extras.related("ZNTX")
        self.assertEqual(len(lines), 1)
        self.assertIn("Zentrox names new chairman", lines[0])


class ContractDupes(unittest.TestCase):
    def test_same_company_and_buyer_within_days(self):
        sent = [["LUNR", 10000.0, "2026-11-09", contracts._buyer_key("NASA")]]
        self.assertTrue(contracts.already_sent(sent, "LUNR", 15e9, "NASA", "2026-11-09"))
        self.assertFalse(contracts.already_sent(sent, "LUNR", 15e9, "U.S. Army", "2026-11-09"))
        self.assertFalse(contracts.already_sent(sent, "LUNR", 15e9, "NASA", "2026-11-20"))
        av = [["AVAV", 940.0, "2026-11-09", contracts._buyer_key("U.S. Department of Defense")]]
        self.assertTrue(contracts.already_sent(av, "AVAV", 315e6, "Department of Defense", "2026-11-09"))

    def test_old_three_field_entries_still_work(self):
        self.assertTrue(contracts.already_sent([["AVAV", 940.0, "2026-11-09"]], "AVAV", 950e6, "", "2026-11-09"))
        self.assertFalse(contracts.already_sent([["AVAV", 940.0, "2026-11-09"]], "AVAV", 315e6, "DoD", "2026-11-09"))


class Outlook(unittest.TestCase):
    def test_tiny_moves_are_not_judged(self):
        prev = {"t": ny(*DAY, 13), "calls": {"CRTO": ["up", "low", 16.50]}}
        line = insights.recap(prev, {"CRTO": 16.52}, ny(*DAY, 14))
        self.assertIn("➖", line)
        self.assertNotIn("right", line)

    def test_call_wrong_twice_is_dropped(self):
        state = {}
        prev = {"t": 0, "calls": {"GLXY": ["down", "low", 14.0]}}
        insights.track_misses(state, prev, {"GLXY": 14.5}, "2026-11-09")
        miss = insights.track_misses(state, prev, {"GLXY": 14.5}, "2026-11-09")
        self.assertEqual(miss["GLXY"], ["2026-11-09", "down", 2])
        self.assertEqual(insights.track_misses(state, None, {}, "2026-11-10"), {})  # new day, fresh start
        insights.track_misses(state, prev, {"GLXY": 14.5}, "2026-11-10")
        self.assertNotIn("GLXY", insights.track_misses(state, prev, {"GLXY": 13.5}, "2026-11-10"))  # right -> reset


class OpenHold(unittest.TestCase):
    def test_first_minutes_after_open(self):
        start = ny(*DAY, 9, 30)
        bot.LAST_CHART["ZNTX"] = {"meta": {"currentTradingPeriod": {"regular": {"start": start, "end": start + 23400}}}}
        self.addCleanup(bot.LAST_CHART.pop, "ZNTX", None)
        self.assertTrue(bot.in_open_hold("ZNTX", start + 5 * 60))
        self.assertFalse(bot.in_open_hold("ZNTX", start + 16 * 60))
        self.assertFalse(bot.in_open_hold("ZNTX", start - 60))


if __name__ == "__main__":
    unittest.main()
