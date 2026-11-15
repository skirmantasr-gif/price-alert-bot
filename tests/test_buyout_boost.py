"""🎯 Buyout re-weighting (2026-11-15, 68-deal study): sector + volume build-up boosts change ranking only."""
import unittest
from unittest import mock

import buyout
from tests.test_buyout import fundamentals


def chart(vols):
    return [(i, 10.0 if i < len(vols) - 20 else 5.0, v) for i, v in enumerate(vols)], {"longName": "Zentrox Inc"}


def score(sect, vols):
    with mock.patch.object(buyout, "fundamentals", lambda t: fundamentals()), \
            mock.patch.object(buyout, "daily_chart", lambda t: chart(vols)):
        return buyout.score("ZNTX", sect)


FLAT = [1e5] * 220


class Boost(unittest.TestCase):
    def test_no_boost_for_flat_volume_other_sector(self):
        r = score("Industrials", FLAT)
        self.assertEqual((r["boost"], r["boosts"]), (0, []))

    def test_sector_boost(self):
        for s in ("Healthcare", "Basic Materials", "Communication Services"):
            self.assertEqual(score(s, FLAT)["boosts"], ["sector"], s)
        self.assertEqual(score("Technology", FLAT)["boost"], 0)

    def test_volume_buildup_10_days(self):
        r = score("Industrials", [1e5] * 210 + [1.6e5] * 10)
        self.assertEqual(r["boosts"], ["buildup"])
        self.assertEqual(score("Industrials", [1e5] * 210 + [1.3e5] * 10)["boost"], 0)

    def test_one_3x_day_in_last_30(self):
        v = [1e5] * 220
        v[-25] = 3.5e5
        self.assertEqual(score("Industrials", v)["boosts"], ["buildup"])
        v = [1e5] * 220
        v[-60] = 5e5   # too long ago
        self.assertEqual(score("Industrials", v)["boost"], 0)

    def test_base_score_unchanged(self):
        self.assertEqual(score("Healthcare", [1e5] * 210 + [2e5] * 10)["score"], score("Industrials", FLAT)["score"]
                         + (score("Healthcare", FLAT)["score"] - score("Industrials", FLAT)["score"]))

    def test_qualifies(self):
        m = buyout.MIN_SCORE
        self.assertTrue(buyout.qualifies({"score": m, "boost": 0}))
        self.assertTrue(buyout.qualifies({"score": m - 1, "boost": 2}))
        self.assertFalse(buyout.qualifies({"score": m - 1, "boost": 1}))
        self.assertFalse(buyout.qualifies({"score": m - 2, "boost": 2}))

    def test_table_shows_boost(self):
        r = {"t": "AAA", "score": 5, "boost": 2, "sector": "Healthcare", "mcap": 4e8, "ev_s": 1.2, "yoy": 0.2,
             "off_high": -0.4}
        self.assertIn("5+2", buyout.table([r], "t", {}))


if __name__ == "__main__":
    unittest.main()
