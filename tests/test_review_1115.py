"""2026-11-15 check: grouped price-feed warnings, stricter 📰 major news, insider value check, dilution vs size."""
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import bot
import buyout
import context
import insiders

NY = ZoneInfo("America/New_York")
TUE_NOON = datetime(2026, 11, 17, 12, 0, tzinfo=NY).timestamp()
SUN_NOON = datetime(2026, 11, 15, 12, 0, tzinfo=NY).timestamp()


class FeedReport(unittest.TestCase):
    def errors(self, now, tickers=("AAA", "BBB", "CCC")):
        return {t: {"since": now - 20 * 60, "notified": False, "err": "HTTP Error 429"} for t in tickers}

    def test_one_message_for_all_stocks(self):
        sent, e = [], self.errors(TUE_NOON)
        bot.feed_report(e, sent.append, TUE_NOON)
        self.assertEqual(len(sent), 1)
        self.assertIn("3 stocks", sent[0])
        self.assertIn("$AAA", sent[0])
        bot.feed_report(e, sent.append, TUE_NOON + 60)   # not repeated
        self.assertEqual(len(sent), 1)

    def test_not_before_15_min(self):
        sent, e = [], self.errors(TUE_NOON)
        for v in e.values():
            v["since"] = TUE_NOON - 60
        bot.feed_report(e, sent.append, TUE_NOON)
        self.assertEqual(sent, [])

    def test_quiet_on_weekend_and_no_recovery_message(self):
        sent, e = [], self.errors(SUN_NOON)
        bot.feed_report(e, sent.append, SUN_NOON)
        self.assertEqual(sent, [])
        with mock.patch.object(bot, "fetch_price", lambda t: (5.0, 5.0, "stale")):
            for t in list(e):
                bot.check(t, {}, e)
        bot.feed_report(e, sent.append, SUN_NOON + 60)
        self.assertEqual(sent, [])

    def test_one_recovery_message(self):
        sent, e = [], self.errors(TUE_NOON)
        bot.feed_report(e, sent.append, TUE_NOON)
        with mock.patch.object(bot, "fetch_price", lambda t: (5.0, 5.0, "stale")):
            for t in ("AAA", "BBB", "CCC"):
                bot.check(t, {"AAA": {}, "BBB": {}, "CCC": {}}, e)
        bot.feed_report(e, sent.append, TUE_NOON + 60)
        self.assertEqual(len(sent), 2)
        self.assertTrue(sent[1].startswith("✅ Price feed recovered"))
        self.assertIn("$CCC", sent[1])
        self.assertEqual(e, {})


class MajorNews(unittest.TestCase):
    def test_word_used_as_plain_word_is_not_the_company(self):
        self.assertFalse(buyout.major("FDA Approves Drug X After Trial Versus Surgery", "SRGX", "Surgery Partners, Inc."))
        self.assertFalse(buyout.major("Zorq CEO: The AI Bottleneck Is Memory Bandwidth, Not Compute", "BNDX", "Bandwidth Inc."))
        self.assertFalse(buyout.major("Drug Y Journey From Phase 1 to FDA Approval", "JRNX", "Journey Medical Corporation"))

    def test_company_named_passes(self):
        self.assertTrue(buyout.major("Bandwidth Inc. Announces CFO Transition", "BNDX", "Bandwidth Inc."))
        self.assertTrue(buyout.major("Journey Medical Receives FDA Approval", "JRNX", "Journey Medical Corporation"))
        self.assertTrue(buyout.major("Zentrox names new CEO", "ZNTX", "Zentrox Group Inc."))
        self.assertTrue(buyout.major("Surgery Partners prices $500 million offering of notes", "SRGX", "Surgery Partners, Inc."))
        self.assertTrue(buyout.major("Qwendy's announces $300 million public offering", "QWN", "The Qwendy's Company"))

    def test_offering_and_collaboration_need_deal_words(self):
        self.assertFalse(buyout.major("Qwendy’s Guyana offering customers chance to win a car", "QWN", "The Qwendy's Company"))
        self.assertFalse(buyout.major("Qwendy's Teases Exciting Collaboration with a game", "QWN", "The Qwendy's Company"))
        self.assertTrue(buyout.major("Zentrox enters strategic collaboration with BigCo", "ZNTX", "Zentrox Limited"))

    def test_opinion_and_form4_dropped(self):
        self.assertFalse(buyout.major("New CEO Might Change The Case For Investing In Zentrox Group (ZNTX)", "ZNTX", "Zentrox Group Inc."))
        self.assertFalse(buyout.major("Should Guidance Require Action From Zentrox (ZNTX) Investors?", "ZNTX", "Zentrox Inc."))
        self.assertFalse(buyout.major("Form 4 Qbex & Wylox: John Doe (Director, CEO) For: 13 November", "QW", "Qbex & Wylox, Inc."))

    def test_same_story_other_edition_same_key(self):
        a = buyout.news_key("Zentrox names CEO For: 13 November By Investing.com")
        self.assertEqual(a, buyout.news_key("Zentrox names CEO For: 13 November"))
        self.assertEqual(a, buyout.news_key("Zentrox names CEO For: 13 November - Investing.com India"))


class InsiderValue(unittest.TestCase):
    def test_sec_wins_when_yahoo_far_off(self):
        with mock.patch.object(insiders, "SEC_CONTACT", "x@y"), \
                mock.patch.object(insiders, "sec_shares", lambda cik: 9.7e6), \
                mock.patch("movers.market_cap", lambda tk: 2.1e6):
            self.assertAlmostEqual(insiders.market_cap("AAA", 3.88, 123), 9.7e6 * 3.88)

    def test_yahoo_kept_when_close(self):
        with mock.patch.object(insiders, "SEC_CONTACT", "x@y"), \
                mock.patch.object(insiders, "sec_shares", lambda cik: 1e7), \
                mock.patch("movers.market_cap", lambda tk: 4.5e7):
            self.assertEqual(insiders.market_cap("AAA", 4.0, 123), 4.5e7)

    def test_no_price_or_cik_yahoo_only(self):
        with mock.patch("movers.market_cap", lambda tk: 2.1e6):
            self.assertEqual(insiders.market_cap("AAA"), 2.1e6)


class DilutionSize(unittest.TestCase):
    def line(self, cash, burn, cap):
        with mock.patch.object(context, "_us", lambda s: True), \
                mock.patch.object(context, "sector", lambda s: "Industrials"), \
                mock.patch.object(context, "cash_burn", lambda s: (cash, burn)), \
                mock.patch.object(context, "filings", lambda s: []), \
                mock.patch.object(context, "market_cap", lambda s: cap):
            return context.dilution_line("AAA")

    def test_big_company_small_burn_is_medium(self):
        self.assertIn("medium", self.line(42e6, 29e6, 2.2e9))

    def test_small_company_big_burn_is_high(self):
        self.assertIn("HIGH", self.line(0.5e6, 4.6e6, 38e6))

    def test_unknown_cap_stays_high(self):
        self.assertIn("HIGH", self.line(42e6, 29e6, None))


if __name__ == "__main__":
    unittest.main()
