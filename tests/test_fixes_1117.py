"""2026-11-17 check: old SEC filings re-posted in 🎯, same story 3× in 📰, question headlines as 🎯, insider feed timeouts."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import buyout
import insiders


class InsiderTradeNotMajor(unittest.TestCase):
    def test_bw_cfo_buy_headlines(self):
        for h in ["Babcock & Wilcox Enterprises (BW) CFO Frymyer bought 10,000 shares for $42.1K at $4.21",
                  "CFO pays $4.21 a share for 10,000 Babcock & Wilcox (BW) shares.",
                  "Babcock (NYSE:BW) CFO Acquires 10,000 Shares of Company Stock"]:
            self.assertTrue(buyout.INSIDER_TRADE.search(h), h)

    def test_real_news_not_caught(self):
        for h in ["Omnicom's Acxiom Names Laben CEO",
                  "Wendy's Proposes $52 Million Deal to Acquire 163 Restaurants from Bankrupt Franchisee",
                  "Acme acquires 51% stake in Beta for $200 million",
                  "Acme announces $50 million public offering of common shares"]:
            self.assertFalse(buyout.INSIDER_TRADE.search(h), h)


class SameEvent(unittest.TestCase):
    def ew(self, h, t="OMC", name="Omnicom Group Inc."):
        return buyout.event_words(h, t, name)

    def test_omnicom_three_headlines_one_story(self):
        a = self.ew("Omnicom’s Acxiom Names Gary Laben Chief Executive Officer")
        b = self.ew("Omnicom's Acxiom Names Laben CEO 11/17/2026")
        c = self.ew("Omnicom Appoints Gary Laben CEO of Acxiom, Succeeding Jarrod Martin")
        self.assertNotIn("omnicom", a)
        self.assertTrue(buyout.same_event(a, b))
        self.assertTrue(buyout.same_event(a, c))

    def test_wendys_same_deal(self):
        a = self.ew("Wendy’s has a plan to save 163 locations from bankruptcy closure", "WEN", "Wendy's Co")
        b = self.ew("Wendy’s Proposes $52 Million Deal to Acquire 163 Restaurants from Bankrupt Franchisee",
                    "WEN", "Wendy's Co")
        self.assertTrue(buyout.same_event(a, b))

    def test_different_stories_kept(self):
        a = self.ew("Omnicom's Acxiom Names Gary Laben CEO")
        b = self.ew("Omnicom Announces $500 Million Notes Offering Due 2031")
        self.assertFalse(buyout.same_event(a, b))
        self.assertFalse(buyout.same_event(set(), {"x"}))


class Live(unittest.TestCase):
    """_live with fake Yahoo headlines: what gets posted."""

    def run_live(self, titles, bo=None, sec=()):
        now = datetime.now(timezone.utc)
        heads = [{"title": x, "link": "https://e.x/" + str(i), "date": now - timedelta(minutes=10), "source": "S",
                  "desc": ""} for i, x in enumerate(titles)]
        sent = []
        w = buyout.BuyoutWatch.__new__(buyout.BuyoutWatch)
        w.send = lambda msg, quiet=False: sent.append(msg)
        w.last_google = {}
        w._spike = lambda *a: False
        w._sec = lambda t: list(sec)
        bo = bo or {"cands": {"OMC": {"name": "Omnicom Group Inc."}}, "init": ["news:OMC", "sec:OMC"]}
        with mock.patch.object(buyout, "_rss", return_value=heads), \
                mock.patch.object(buyout, "google", return_value=[]), \
                mock.patch.object(buyout.time, "sleep"), \
                mock.patch.object(buyout.extras, "remember"), \
                mock.patch.object(buyout, "SEC_CONTACT", ""):
            w._live(bo)
        return sent, bo

    def test_one_post_for_one_story(self):
        sent, bo = self.run_live(["Omnicom’s Acxiom Names Gary Laben Chief Executive Officer",
                                  "Omnicom's Acxiom Names Laben CEO 11/17/2026",
                                  "Omnicom Appoints Gary Laben CEO of Acxiom, Succeeding Jarrod Martin"])
        self.assertEqual(len(sent), 1)
        self.assertIn("Laben", sent[0])
        # next poll, a fourth site: still the same story
        sent2, _ = self.run_live(["Acxiom taps Gary Laben as CEO, Omnicom says"], bo)
        self.assertEqual(sent2, [])

    def test_question_not_takeover(self):
        bo = {"cands": {"TLRY": {"name": "Tilray Brands Inc"}}, "init": ["news:TLRY", "sec:TLRY"]}
        sent, _ = self.run_live(["Why Didn't Tilray Brands Make a Bid for Aurora Cannabis?"], bo)
        self.assertEqual(sent, [])

    def test_real_takeover_still_posted(self):
        bo = {"cands": {"TLRY": {"name": "Tilray Brands Inc"}}, "init": ["news:TLRY", "sec:TLRY"]}
        sent, _ = self.run_live(["Tilray Brands receives buyout offer from Canopy at $2 per share"], bo)
        self.assertEqual(len(sent), 1)
        self.assertIn("takeover", sent[0])


class OldSecFilings(unittest.TestCase):
    def test_old_filings_skipped(self):
        now = datetime.now(timezone.utc)
        files = [{"form": "8-K", "items": ["1.01"], "date": now - timedelta(days=13), "acc": "a1", "link": "l"},
                 {"form": "SCHEDULE 13D/A", "items": [], "date": now - timedelta(days=11), "acc": "a2", "link": "l"},
                 {"form": "SCHEDULE 13D", "items": [], "date": now - timedelta(hours=5), "acc": "a3", "link": "l"},
                 {"form": "8-K", "items": ["1.01"], "date": None, "acc": "a4", "link": "l"}]
        import news
        w = buyout.BuyoutWatch.__new__(buyout.BuyoutWatch)
        with mock.patch.object(news, "sec_filings", return_value=files), \
                mock.patch.object(news, "sec_reorg", return_value=False):
            got = [x["acc"] for x in w._sec("OPK")]
        self.assertEqual(got, ["a3", "a4"])   # undated kept (can't tell), 11–13 days old dropped


class InsiderFeedRetry(unittest.TestCase):
    def test_retries_then_ok(self):
        calls = []

        def fp(start):
            calls.append(start)
            if len(calls) < 3:
                raise TimeoutError()
            return [("acc", 1)]
        with mock.patch.object(insiders, "feed_page", fp), mock.patch.object(insiders.time, "sleep") as sl:
            self.assertEqual(insiders.feed_page_retry(0), [("acc", 1)])
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sl.call_args_list], [5, 15])

    def test_gives_up_after_three(self):
        with mock.patch.object(insiders, "feed_page", side_effect=TimeoutError()), \
                mock.patch.object(insiders.time, "sleep"):
            with self.assertRaises(TimeoutError):
                insiders.feed_page_retry(100)

    def test_other_errors_not_retried(self):
        with mock.patch.object(insiders, "feed_page", side_effect=ValueError("bad xml")) as fp, \
                mock.patch.object(insiders.time, "sleep"):
            with self.assertRaises(ValueError):
                insiders.feed_page_retry(0)
        self.assertEqual(fp.call_count, 1)


if __name__ == "__main__":
    unittest.main()
