"""🎯 forecast scoring (2026-11-20)."""
import json
import os
import tempfile
import unittest
from datetime import datetime

import predict

PREDS = [
    {"id": "a", "made": "2026-11-17", "claim": "SLS closes Nov 17 above $10", "p": 0.65, "due": "2026-11-17",
     "rule": {"type": "close_above", "ticker": "SLS", "level": 10}},
    {"id": "b", "made": "2026-11-09", "claim": "LOQTORZI Q3 >= $15M", "p": 0.55, "outcome": False, "note": "$14.0M"},
    {"id": "c", "made": "2026-11-16", "claim": "DOX above $44.79 on Dec 18", "p": 0.58, "due": "2026-12-18",
     "rule": {"type": "close_above", "ticker": "DOX", "level": 44.79}},
    {"id": "d", "made": "2026-11-20", "claim": "CAPR approved", "p": 0.35, "rule": {"type": "manual"}},
]


class Resolve(unittest.TestCase):
    def test_price_rule_after_close(self):
        price = lambda t, d: ("2026-11-17", 19.15)
        self.assertEqual(predict.resolve(PREDS[0], datetime(2026, 11, 18, 9, 0), price)[0], True)
        self.assertIsNone(predict.resolve(PREDS[0], datetime(2026, 11, 17, 20, 0), price))   # before the close
        self.assertIsNone(predict.resolve(PREDS[2], datetime(2026, 11, 20, 9, 0), price))    # not due

    def test_close_below(self):
        x = {"id": "e", "p": 0.6, "due": "2026-12-04", "rule": {"type": "close_below", "ticker": "SLS", "level": 19.15}}
        self.assertEqual(predict.resolve(x, datetime(2026, 12, 5, 9, 0), lambda t, d: ("2026-12-04", 14.0))[0], True)

    def test_manual(self):
        self.assertEqual(predict.resolve(PREDS[1], datetime(2026, 11, 20, 9, 0)), (False, "$14.0M"))
        self.assertIsNone(predict.resolve(PREDS[3], datetime(2026, 11, 20, 9, 0)))

    def test_brier(self):
        self.assertAlmostEqual(predict.brier([{"p": 0.65, "outcome": True}, {"p": 0.55, "outcome": False}]),
                               ((0.35 ** 2) + (0.55 ** 2)) / 2)


class Watch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(PREDS, self.tmp)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def watch(self, sent, when):
        return predict.PredictionWatch(lambda t, s: sent.append(t) or True, self.tmp.name,
                                       price=lambda t, d: ("2026-11-17", 19.15), now=lambda: when)

    def test_first_run_one_summary_then_one_post_per_new(self):
        sent, st = [], {}
        self.watch(sent, datetime(2026, 11, 20, 12, 0)).check(st)
        self.assertEqual(len(sent), 1)
        self.assertIn("Forecast tracking started</b>: 2 of Claude's calls already settled, 2 open", sent[0])
        self.assertIn("Brier", sent[0])
        # CAPR outcome written into the file later -> posted once
        PREDS2 = PREDS[:3] + [dict(PREDS[3], outcome=False, note="CRL")]
        with open(self.tmp.name, "w") as f:
            json.dump(PREDS2, f)
        self.watch(sent, datetime(2026, 11, 23, 12, 0)).check(st)
        self.assertEqual(len(sent), 2)
        self.assertIn("CAPR approved", sent[1])
        self.assertIn("❌", sent[1])
        self.assertIn("Said <b>35%</b>", sent[1])
        self.assertIn("3 resolved", sent[1])
        self.watch(sent, datetime(2026, 11, 24, 12, 0)).check(st)
        self.assertEqual(len(sent), 2)

    def test_missing_file_quiet(self):
        sent = []
        w = predict.PredictionWatch(lambda t, s: sent.append(t), "/nonexistent/p.json")
        self.assertFalse(w.check({}))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
