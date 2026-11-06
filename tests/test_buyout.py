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


if __name__ == "__main__":
    unittest.main()
