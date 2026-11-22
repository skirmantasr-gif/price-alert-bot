"""Alert facts + permanent history for a later trust model (2026-11-22)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import scoreboard


class Facts(unittest.TestCase):
    def setUp(self):
        fd, self.hist = tempfile.mkstemp()
        os.close(fd)
        os.remove(self.hist)
        self.addCleanup(lambda: os.path.exists(self.hist) and os.remove(self.hist))
        p = mock.patch.multiple(scoreboard, HISTORY=self.hist, CONTEXT=lambda tk: {"day": -12.5, "cap_m": 340.0},
                                _spy_now=lambda: 600.0)
        p.start()
        self.addCleanup(p.stop)

    def test_note_saves_facts(self):
        st = {"_mood": {"m": {"status": "defensive", "score": -4}}}
        self.assertTrue(scoreboard.note("news", "CAPR", "down", 7.5, state=st, now=1_795_000_000,
                                        ctx={"conf": "medium", "by": "ai"}))
        f = st["_sb"]["ev"][0]["f"]
        self.assertEqual((f["mood"], f["mood_score"], f["day"], f["cap_m"], f["conf"], f["by"]),
                         ("defensive", -4, -12.5, 340.0, "medium", "ai"))
        self.assertIn("hour_ny", f)
        self.assertIn("wday", f)

    def test_context_failure_does_not_block(self):
        scoreboard.CONTEXT = lambda tk: 1 / 0
        st = {}
        self.assertTrue(scoreboard.note("price", "SLS", "up", 14.0, state=st, now=1_795_000_000))
        self.assertIn("f", st["_sb"]["ev"][0])

    def test_finished_alerts_archived_once(self):
        t0 = 1_795_000_000
        st = {"_sb": {"ev": [{"t": t0 - 30 * 86400, "k": "news", "tk": "OLD", "d": "up", "p0": 1, "b0": 1,
                              "r1": 1, "x1": 1, "r5": 2, "x5": 2},
                             {"t": t0, "k": "news", "tk": "CAPR", "d": "down", "p0": 10.0, "b0": 600.0, "f": {"day": 1}}]}}
        days = [("2026-11-%02d" % d, 10.0 - d * 0.1) for d in range(10, 30)]

        def closes(tk):
            from datetime import timezone
            return ([(d, 600.0) for d, _ in days] if tk == "SPY" else days), timezone.utc, (16, 0)
        with mock.patch.object(scoreboard, "_after", lambda c, ts, tz, hm: c):
            scoreboard.evaluate(st, closes)
            scoreboard.evaluate(st, closes)
        lines = [json.loads(x) for x in open(self.hist)]
        self.assertEqual([x["tk"] for x in lines], ["OLD", "CAPR"])   # backfill + new one, each once
        self.assertEqual(lines[1]["f"], {"day": 1})
        self.assertIn("x5", lines[1])


if __name__ == "__main__":
    unittest.main()
