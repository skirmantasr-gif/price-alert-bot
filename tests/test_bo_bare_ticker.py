import unittest

import buyout


class BareTicker(unittest.TestCase):
    NAME = "Kennametal Inc."

    def test_party_headline_is_not_the_company(self):
        self.assertFalse(buyout.in_title("Legislature approves KMT's NT$20,000 payout bill", "KMT", self.NAME))
        self.assertFalse(buyout.major("Legislature approves KMT's NT$20,000 payout bill", "KMT", self.NAME))

    def test_market_headlines_still_match(self):
        for h in ("KMT shares jump on buyout talk", "Kennametal to be acquired by Sandvik",
                  "$KMT agrees to deal with Sandvik", "Sandvik offers to buy Kennametal (KMT)",
                  "KMT agrees to merger with Sandvik", "NYSE: KMT board backs takeover"):
            self.assertTrue(buyout.in_title(h, "KMT", self.NAME), h)

    def test_longer_tickers_unchanged(self):
        self.assertTrue(buyout.in_title("CDNL faces investigation", "CDNL", "Cardinal Infrastructure Group Inc."))


if __name__ == "__main__":
    unittest.main()
