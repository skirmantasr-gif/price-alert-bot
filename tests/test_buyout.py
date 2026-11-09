"""🎯 Buyout watch: cheapness by sector, reorganizations skipped."""
import unittest
from unittest import mock

import buyout


def fundamentals(ttm_q=25e6, mcap=400e6, debt=50e6, cash=20e6):
    q = lambda vals: [[i, v] for i, v in enumerate(vals)]
    return {"quarterlyTotalRevenue": q([ttm_q * 0.8] + [ttm_q] * 4), "trailingMarketCap": q([mcap]),
            "quarterlyTotalDebt": q([debt]), "quarterlyCashCashEquivalentsAndShortTermInvestments": q([cash]),
            "quarterlyFreeCashFlow": q([-1e6] * 4), "quarterlyOrdinarySharesNumber": q([100e6] * 5)}


def chart():
    bars = [(i, 10.0, 1e5) for i in range(200)] + [(200 + i, 5.0, 1e5) for i in range(20)]
    return bars, {"longName": "Zentrox Inc"}


class Score(unittest.TestCase):
    def score(self, sect, **f):
        with mock.patch.object(buyout, "fundamentals", lambda t: fundamentals(**f)), \
                mock.patch.object(buyout, "daily_chart", lambda t: chart()):
            return buyout.score("ZNTX", sect)

    def test_cheap_threshold_depends_on_sector(self):
        # EV 430M / TTM 100M = 4.3 → too expensive anywhere; EV/S 2.0 → cheap for healthcare, not industrials
        self.assertNotIn("cheap", self.score("Healthcare")["hits"])
        r = self.score("Healthcare", mcap=170e6)
        self.assertAlmostEqual(r["ev_s"], 2.0)
        self.assertIn("cheap", r["hits"])
        self.assertNotIn("cheap", self.score("Industrials", mcap=170e6)["hits"])

    def test_tiny_revenue_never_cheap(self):
        self.assertNotIn("cheap", self.score("Healthcare", ttm_q=4e6, mcap=10e6)["hits"])

    def test_beaten_down_and_stressed(self):
        hits = self.score("Healthcare")["hits"]
        self.assertIn("depressed", hits)
        self.assertIn("stress", hits)
        self.assertIn("commercial", hits)

    def test_not_enough_history(self):
        with mock.patch.object(buyout, "fundamentals", lambda t: {"quarterlyTotalRevenue": [[0, 1]]}), \
                mock.patch.object(buyout, "daily_chart", lambda t: chart()):
            self.assertIsNone(buyout.score("ZNTX", "Healthcare"))


class SecFilter(unittest.TestCase):
    def test_reorganization_filings_skipped(self):
        fl = [{"form": "425", "items": [], "acc": "1", "link": "https://www.sec.gov/a"},
              {"form": "SC 13D", "items": [], "acc": "2", "link": "https://www.sec.gov/b"}]
        import news
        with mock.patch.object(news, "sec_filings", lambda t: fl), \
                mock.patch.object(news, "sec_reorg", lambda f: f["form"] == "425"):
            w = buyout.BuyoutWatch.__new__(buyout.BuyoutWatch)
            out = buyout.BuyoutWatch._sec(w, "ZNTX")
        self.assertEqual([o["acc"] for o in out], ["2"])


class Discovery(unittest.TestCase):
    """Headlines go to the AI in small batches; one bad answer loses only its batch."""

    def run_discover(self, n_heads, answers):
        heads = [{"title": f"Zentrox weighs sale {i}", "source": "Wire", "link": f"https://x/{i}", "date": None, "desc": ""}
                 for i in range(n_heads)]
        calls = []

        def create(**kw):
            calls.append(kw)
            a = answers[len(calls) - 1]
            text = a if isinstance(a, str) else __import__("json").dumps({"targets": a})
            stop = "max_tokens" if text == "CUT" else "end_turn"
            return mock.Mock(stop_reason=stop, content=[mock.Mock(type="text", text=text)])

        client = mock.Mock()
        client.messages.create.side_effect = create
        client.beta.messages.create.side_effect = create
        anth = mock.Mock(Anthropic=lambda **k: client)
        row = lambda t: {"t": t, "score": 6, "hits": [], "name": t, "price": 1.0}
        with mock.patch.dict("sys.modules", {"anthropic": anth}), \
                mock.patch.object(buyout.impact, "ai_enabled", lambda: True), \
                mock.patch.object(buyout, "DISCOVERY_QUERIES", ["q"]), \
                mock.patch.object(buyout, "google", lambda q: heads), \
                mock.patch.object(buyout.time, "sleep", lambda s: None), \
                mock.patch.object(buyout, "yahoo_find", lambda c, h: (h, c)), \
                mock.patch.object(buyout, "score", lambda t, *a: row(t)):
            w = buyout.BuyoutWatch.__new__(buyout.BuyoutWatch)
            out = buyout.BuyoutWatch._discover(w, [])
        return calls, [r["t"] for r, why, link in out]

    def test_split_into_batches(self):
        t = lambda x: [{"company": x + " Inc", "ticker": x, "why": "sale talk"}]
        calls, found = self.run_discover(40, [t("AAA"), t("BBB"), t("AAA")])
        self.assertEqual(len(calls), 3)  # 15 + 15 + 10 headlines
        self.assertEqual(len(__import__("json").loads(calls[2]["messages"][0]["content"])), 10)
        self.assertEqual(found, ["AAA", "BBB"])  # same company from two batches listed once

    def test_cut_off_answer_loses_only_its_batch(self):
        calls, found = self.run_discover(30, ["CUT", [{"company": "Bbb Corp", "ticker": "BBB", "why": "bid"}]])
        self.assertEqual(found, ["BBB"])
        calls, found = self.run_discover(30, ['{"targets": [', [{"company": "Bbb Corp", "ticker": "BBB", "why": "bid"}]])
        self.assertEqual(found, ["BBB"])  # broken JSON too


class OutletName(unittest.TestCase):
    def test_outlet_name_is_not_a_mention(self):
        rss = (b'<rss><channel><item><title>JSW Motors names new head - BW Marketing World</title>'
               b'<source>BW Marketing World</source><description>&lt;a&gt;JSW Motors names new head&lt;/a&gt;'
               b'&amp;nbsp;&lt;font&gt;BW Marketing World&lt;/font&gt;</description></item></channel></rss>')
        with mock.patch.object(buyout, "_get", lambda *a, **k: rss):
            h = buyout._rss("https://news.google.com/rss/search?q=x")[0]
        self.assertEqual(h["title"], "JSW Motors names new head")
        self.assertFalse(buyout.mentions(h["title"] + " " + h["desc"], "BW", "Babcock & Wilcox"))


if __name__ == "__main__":
    unittest.main()
