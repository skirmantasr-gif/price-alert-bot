"""📈 market-implied FDA odds (2026-11-22). Snapshots are the real CAPR / SVRA ones saved on the server Nov 20."""
import unittest
from datetime import datetime

import catalysts
import marketodds as mo

CAPR = {"t": "CAPR", "event": "2026-11-22", "kind": "fda", "label": "FDA decision date (PDUFA)", "snaps": {
    "2026-11-20": {"px": 7.875, "exps": [["2026-11-20", 8.0, 49.8, 3009, 3870, 10385, 20132],
                                         ["2026-11-27", 8.0, 111.1, 2087, 7043, 10033, 43969],
                                         ["2026-12-04", 8.0, 105.7, 14, 521, 377, 1198]]}}}
SVRA = {"t": "SVRA", "event": "2026-11-22", "kind": "fda", "label": "FDA decision date (PDUFA)", "snaps": {
    "2026-11-20": {"px": 4.305, "exps": [["2026-11-20", 4.0, None, 210, 453, 3239, 1911],
                                         ["2026-12-18", 4.0, 33.7, 2375, 262, 9411, 2530]]}}}


class Model(unittest.TestCase):
    def test_capr(self):
        est = mo.implied(1.111, 7.875, 2.0)
        self.assertAlmostEqual(est["d"], 0.746, places=2)
        self.assertTrue(0.2 <= est["p"] <= 0.3, est)

    def test_small_move_high_odds_and_clamps(self):
        self.assertEqual(mo.implied(0.05, 50, None)["p"], 0.95)       # big company, tiny move
        self.assertEqual(mo.implied(3.0, 5, 0)["p"], 0.05)            # move bigger than the model allows
        self.assertEqual(mo.implied(0.5, 10, 9.5)["d"], 0.30)         # cash-rich: drop kept ≥ 30%
        self.assertIsNone(mo.implied(None, 10))

    def test_pick_first_expiry_after_date(self):
        self.assertEqual(mo.pick(CAPR), ("2026-11-20", 7.875, 111.1, "2026-11-27"))

    def test_pick_skips_snapshots_after_news(self):
        self.assertIsNone(mo.pick(SVRA, cutoff="2026-11-20"))         # FDA said "will miss" on Nov 20
        self.assertIsNotNone(mo.pick(SVRA))


class Flow(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.day = datetime(2026, 11, 21, 12, 0)
        self.preds = [{"id": "capr-1120", "p": 0.35, "fda": "CAPR:2026-11-22"}]
        self.w = mo.MarketOdds(lambda text, silent: self.sent.append(text), ["CAPR", "SVRA"],
                               cash=lambda t: 2.0, preds=lambda: self.preds, now=lambda: self.day)
        self.st = {"_opth": {"CAPR:2026-11-22": dict(CAPR), "SVRA:2026-11-22": dict(SVRA)},
                   "_fdacal": {"ev": {}, "docs": [], "missed": {"SVRA:2026-11-22": {"title": "x", "at": "2026-11-20"}}}}

    def run_check(self):
        self.w.last = float("-inf")
        return self.w.check(self.st)

    def test_estimate_then_score(self):
        self.assertTrue(self.run_check())
        self.assertIn("CAPR:2026-11-22", self.st["_mkt"])
        self.assertNotIn("SVRA:2026-11-22", self.st["_mkt"])          # only a post-news snapshot
        self.assertIn("Market-implied odds", self.sent[0])
        self.assertEqual(len(self.sent), 1)                            # CAPR outcome not known yet
        self.preds[0]["outcome"] = False                                # Claude's file says CRL
        self.run_check()
        r = self.st["_mkt"]["CAPR:2026-11-22"]
        self.assertFalse(r["outcome"])
        self.assertEqual(r["claude"], 0.35)
        self.assertIn("Market odds scored", self.sent[-1])
        self.assertIn("Claude said 35%", self.sent[-1])
        n = len(self.sent)
        self.run_check()
        self.assertEqual(len(self.sent), n)                             # posted once

    def test_decided_mark_gives_outcome(self):
        self.run_check()
        catalysts.mark_decided(self.st, "CAPR", "FDA declines to approve Capricor's therapy",
                               datetime(2026, 11, 21).date(), ["Capricor"])
        self.st["_cal"] = []
        self.assertIs(mo.outcome(self.st, "CAPR:2026-11-22", []), None)   # no _cal/_fdacal event → nothing marked
        self.st["_cal"] = [["CAPR", "2026-11-22", "fda", "FDA decision date (PDUFA)"]]
        catalysts.mark_decided(self.st, "CAPR", "FDA declines to approve Capricor's therapy",
                               datetime(2026, 11, 21).date(), ["Capricor"])
        self.assertIs(mo.outcome(self.st, "CAPR:2026-11-22", []), False)
        catalysts.mark_decided(self.st, "CAPR", "Capricor wins FDA approval",
                               datetime(2026, 11, 21).date(), ["Capricor"])        # first mark stays
        self.assertIs(mo.outcome(self.st, "CAPR:2026-11-22", []), False)

    def test_too_early(self):
        self.day = datetime(2026, 11, 10, 12, 0)
        self.assertFalse(self.run_check())

    def test_missed_counts_as_no(self):
        self.assertIs(mo.outcome(self.st, "SVRA:2026-11-22", []), False)


if __name__ == "__main__":
    unittest.main()
