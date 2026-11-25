import unittest
from datetime import date, timedelta

import options

TODAY = date(2026, 11, 20)
# the watch uses the real date: keep its contracts 10 days out (a fixed date broke on 2026-11-25)
EXP = (date.today() + timedelta(days=10)).isoformat()


def u(exp, cp, k, usd, vol=1000, oi=100):
    return (exp, cp, k, vol, oi, usd)


class Lean(unittest.TestCase):
    def test_up(self):
        rows = [u("2026-11-27", "C", 105, 900e3), u("2026-12-04", "C", 110, 300e3), u("2026-11-27", "P", 95, 300e3)]
        ln = options.lean(rows, TODAY)
        self.assertEqual(ln["dir"], "up")
        self.assertEqual(ln["until"], "2026-11-27")
        self.assertAlmostEqual(ln["strike"], 105)
        self.assertIn("RISE until Fri Nov 27", options.lean_line(ln))
        self.assertIn("calls $1.2M vs puts $300k", options.lean_line(ln))

    def test_long_dated_hedges_left_out(self):
        rows = [u("2027-12-17", "P", 60, 14.6e6), u("2026-11-27", "C", 100, 400e3), u("2026-11-27", "P", 100, 150e3)]
        self.assertEqual(options.lean(rows, TODAY)["dir"], "up")

    def test_expiring_now_left_out(self):
        rows = [u("2026-11-20", "P", 106, 9e6), u("2026-11-21", "P", 100, 2e6), u("2026-11-27", "C", 105, 600e3)]
        self.assertEqual(options.lean(rows, TODAY, price=102.07)["dir"], "up")

    def test_far_strikes_left_out(self):
        rows = [u("2026-12-18", "P", 80, 3.7e6), u("2026-12-18", "P", 70, 1.5e6),
                u("2026-11-27", "P", 102, 584e3), u("2026-11-27", "C", 105, 600e3)]
        ln = options.lean(rows, TODAY, price=102.07)
        self.assertEqual(ln["dir"], "mixed")            # only the near-the-money bets count

    def test_down_and_mixed_and_small(self):
        down = [u("2026-11-27", "P", 100, 1.1e6), u("2026-11-27", "P", 90, 165e3), u("2026-11-27", "C", 100, 270e3)]
        ln = options.lean(down, TODAY)
        self.assertEqual(ln["dir"], "down")
        self.assertIn("FALL until Fri Nov 27", options.lean_line(ln))
        mixed = [u("2026-11-27", "P", 100, 500e3), u("2026-11-27", "C", 100, 450e3)]
        self.assertEqual(options.lean(mixed, TODAY)["dir"], "mixed")
        self.assertIn("mixed", options.lean_line(options.lean(mixed, TODAY)))
        self.assertIsNone(options.lean([u("2026-11-27", "C", 100, 50e3)], TODAY))


class Watch(unittest.TestCase):
    def setUp(self):
        self.old_get = options.get
        self.snap = {"price": 100.0, "unusual": [u(EXP, "P", 100, 1.1e6, 3254, 1500)]}
        options.get = lambda t: self.snap

    def tearDown(self):
        options.get = self.old_get

    def test_hourly_merge_lean_and_score(self):
        sent, clock = [], [1000.0]
        w = options.UnusualWatch(lambda t, s: sent.append(t), ["ORCL"],
                                 close_on=lambda t, d: (d, 95.0), now=lambda: clock[0])
        st = {}
        w.check(st)
        self.assertEqual(len(sent), 1)
        self.assertIn("FALL until", sent[0])
        self.assertIn("none scored yet", sent[0])
        # 25 min later: more contracts, but no second message within the hour
        self.snap["unusual"] = self.snap["unusual"] + [u(EXP, "P", 90, 165e3)]
        clock[0] += 1500
        w.last = float("-inf")
        w.check(st)
        self.assertEqual(len(sent), 1)
        # an hour after the first: one merged message with the waiting contract
        clock[0] += 2200
        w.last = float("-inf")
        w.check(st)
        self.assertEqual(len(sent), 2)
        self.assertIn("$90.00", sent[1])
        leans = st["_opt"]["leans"]
        self.assertEqual(len(leans), 1)                 # one lean per stock, day and direction
        # after the until-date the lean is scored: close 95 < 100 → a FALL was right
        leans[0]["until"] = "2000-01-01"
        w._score(leans, "2026-11-30")
        self.assertTrue(leans[0]["right"])
        self.assertIn("1 of 1 right", options.score_line(leans))

    def test_last_post_survives_restart(self):
        sent = []
        st = {}
        options.UnusualWatch(lambda t, s: sent.append(t), ["ORCL"], now=lambda: 1000.0).check(st)
        self.snap["unusual"] = self.snap["unusual"] + [u(EXP, "C", 110, 200e3)]
        options.UnusualWatch(lambda t, s: sent.append(t), ["ORCL"], now=lambda: 1600.0).check(st)   # new process
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
