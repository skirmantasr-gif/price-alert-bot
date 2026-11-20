import unittest
from datetime import datetime, timezone

import insiders

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


class Expectation(unittest.TestCase):
    def test_segments(self):
        two_big = insiders.expectation(2, 25.44, NOW)
        self.assertIn("no real edge", two_big)
        self.assertIn("until ~Feb 18", two_big)
        self.assertIn("2 insiders, stock over $5", two_big)
        self.assertIn("1,289 cases", two_big)
        self.assertIn("leans UP</b>, slightly", insiders.expectation(4, 54.31, NOW))
        cheap = insiders.expectation(2, 1.35, NOW)
        self.assertIn("lottery-like", cheap)
        self.assertIn("lottery-like", insiders.expectation(5, 1.29, NOW))
        self.assertEqual(insiders.expectation(3, None, NOW), "")

    def test_in_message(self):
        rows = [("2026-11-09", "Young Scott A.", "EVP, Chief Operating Officer", 150e3, False, "a", 5900),
                ("2026-11-18", "Lewis Harold G", "Director", 399e3, False, "b", 15600)]
        text, loud = insiders.message("UPBD", "Upbound Group", 2, 549e3, rows, 1.5e9, 25.44, 25.64)
        self.assertIn("🔮 Expected until", text)
        self.assertIn("Past results, not a promise", text)


if __name__ == "__main__":
    unittest.main()
