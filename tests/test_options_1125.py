"""2026-11-25: deep in-the-money contracts left out of 🎲 lines, lean says where bets sit vs price, $M amounts."""
import unittest
from datetime import date, timedelta

import options


def u(exp, cp, k, usd, vol=1000, oi=100):
    return (exp, cp, k, vol, oi, usd)


class DeepItmTest(unittest.TestCase):
    def test_deep_itm(self):
        self.assertTrue(options.deep_itm(u("2026-12-18", "P", 240, 1), 104))
        self.assertTrue(options.deep_itm(u("2026-12-18", "C", 60, 1), 104))
        self.assertFalse(options.deep_itm(u("2026-12-18", "P", 60, 1), 104))     # far OTM put = crash bet, kept
        self.assertFalse(options.deep_itm(u("2026-12-18", "C", 160, 1), 104))    # lottery call, kept
        self.assertFalse(options.deep_itm(u("2026-12-18", "P", 110, 1), 104))
        self.assertFalse(options.deep_itm(u("2026-12-18", "P", 240, 1), None))


EXP = (date.today() + timedelta(days=20)).isoformat()


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.old_get = options.get
        self.snap = {"price": 103.88, "unusual": [
            u(EXP, "P", 240, 80.9e6, 5960, 926), u(EXP, "P", 200, 30e6),
            u(EXP, "C", 101, 1.7e6, 1496, 0), u(EXP, "C", 97.5, 1.6e6),
            u(EXP, "P", 103, 1.3e6)]}
        options.get = lambda t: self.snap

    def tearDown(self):
        options.get = self.old_get

    def test_orcl_case(self):
        sent = []
        options.UnusualWatch(lambda t, s: sent.append(t), ["ZNTX"], now=lambda: 1000.0).check({}, True)
        self.assertEqual(len(sent), 1)
        m = sent[0]
        self.assertNotIn("$240", m)
        self.assertNotIn("$200", m)
        self.assertIn("+2 deep in-the-money trades ($110.9M) left out", m)
        self.assertIn("about $1.7M traded", m)
        self.assertNotIn("k traded", m.replace("$", ""))
        self.assertIn("bets centred near", m)
        self.assertIn("price now $104", m)
        self.assertNotIn("above ~", m)

    def test_only_deep_no_message(self):
        self.snap["unusual"] = self.snap["unusual"][:2]
        sent, st = [], {}
        options.UnusualWatch(lambda t, s: sent.append(t), ["ZNTX"], now=lambda: 1000.0).check(st, True)
        self.assertEqual(sent, [])
        self.assertEqual(len(st["_opt"]["seen"]), 2)    # not re-checked every 30 min


if __name__ == "__main__":
    unittest.main()
