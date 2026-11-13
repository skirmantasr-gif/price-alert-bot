import unittest
import buyout
import context
from unittest import mock


class NameMatch(unittest.TestCase):
    def test_two_word_name_needs_both_words(self):
        n = "Zorvex Cannabis Inc."
        self.assertTrue(buyout.mentions("Rival unlikely to acquire Zorvex Cannabis", "ZRVX", n))
        self.assertFalse(buyout.mentions("Miner acquires Zorvex West copper property", "ZRVX", n))
        self.assertFalse(buyout.mentions("Key facts: ZORVEX Phase 3 starts", "ZRVX", n))
        self.assertTrue(buyout.mentions("ZRVX jumps on bid talk", "ZRVX", n))

    def test_generic_second_word_is_skipped(self):
        self.assertTrue(buyout.mentions("Zentrox explores a sale", "ZNTX", "Zentrox Health Inc."))
        self.assertTrue(buyout.mentions("Zentrox explores a sale", "ZNTX", "Zentrox Pharmaceuticals"))

    def test_of_in_name(self):
        n = "Helix of Qarth Limited"
        self.assertTrue(buyout.mentions("Helix of Qarth board rejects $37 bid", "HQX", n))
        self.assertFalse(buyout.mentions("Helix wins award", "HQX", n))

    def test_short_ticker_needs_dollar_or_brackets(self):
        n = "Qbex & Wylox"
        self.assertFalse(buyout.mentions("Lila said to buy BW LPG vessel", "BW", n))
        self.assertTrue(buyout.mentions("$BW jumps", "BW", n))
        self.assertTrue(buyout.mentions("Qbex & Wylox (BW) gets offer", "BW", n))
        self.assertTrue(buyout.mentions("Shares of NYSE: BW rise", "BW", n))


class MajorNews(unittest.TestCase):
    def test_routine_hire_is_not_major(self):
        self.assertIsNone(buyout.MAJOR.search("Zentrox Solutions appoints Jane Doe as Chief People Officer"))
        self.assertIsNotNone(buyout.MAJOR.search("Zentrox appoints new CEO"))
        self.assertIsNotNone(buyout.MAJOR.search("Zentrox names Chief Financial Officer"))
        self.assertIsNotNone(buyout.MAJOR.search("Zentrox CFO to step down"))


class CashWording(unittest.TestCase):
    def test_under_one_quarter(self):
        with mock.patch.object(context, "cash_burn", lambda s: (34.7e6, 92.5e6)), \
             mock.patch.object(context, "filings", lambda s: []), \
             mock.patch.object(context, "market_cap", lambda s: 2.9e9):
            line = context.dilution_line("ZNTX")
        self.assertIn("less than 1 quarter", line)
        self.assertNotIn("about 0", line)


if __name__ == "__main__":
    unittest.main()
