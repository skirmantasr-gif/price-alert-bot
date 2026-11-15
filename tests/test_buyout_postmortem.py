"""🔬 Buyout post-mortem: fingerprint on data BEFORE the jump, message, movers hook, tally."""
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import buyout
import movers
from tests.test_buyout import fundamentals

NY = ZoneInfo("America/New_York")


def day_ts(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(hour=16, tzinfo=NY).timestamp())


def chart_json():
    # 200 days at $10, then 19 days at $5, then the jump day at $8 on huge volume
    start = day_ts("2026-01-02")
    ts = [start + i * 86400 for i in range(220)]
    closes = [10.0] * 200 + [5.0] * 19 + [8.0]
    vols = [1e5] * 219 + [5e6]
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"close": closes, "volume": vols}]},
                                  "meta": {"longName": "Zentrox Inc"}}]}}, datetime.fromtimestamp(ts[-1], NY).strftime("%Y-%m-%d")


class PostMortem(unittest.TestCase):
    def pm(self, cand=None):
        j, jump_day = chart_json()
        with mock.patch.object(buyout, "_json", lambda url, **k: j), \
                mock.patch.object(buyout, "fundamentals", lambda t: fundamentals()), \
                mock.patch.object(buyout, "sector", lambda t: "Healthcare"):
            return buyout.postmortem("ZNTX", jump_day, cand)

    def test_jump_day_excluded(self):
        pm = self.pm()
        self.assertAlmostEqual(pm["off_high"], -0.5)       # measured at $5, not at the $8 jump
        self.assertNotIn("buildup", pm["boosts"])          # the 50x jump-day volume is not counted

    def test_message_we_had_it(self):
        pm = self.pm({"since": "2026-10-01", "signal": True})
        m = buyout.pm_message(pm, 60, "Zentrox Inc", "Agreed to be acquired", "https://x", "Tally since …")
        self.assertIn("🔬 <b>Buyout post-mortem: $ZNTX</b> +60%", m)
        self.assertIn("✅ We had it: candidate since 2026-10-01 (📣 public sale signal)", m)
        self.assertIn("📒 Tally", m)

    def test_message_missed(self):
        pm = dict(self.pm(), qual=False)
        m = buyout.pm_message(pm, 30, "Zentrox")
        self.assertIn("❌ Not on our list", m)
        self.assertIn("missing:", m)
        pm["qual"] = True
        self.assertIn("⚠️ It qualified", buyout.pm_message(pm, 30, "Zentrox"))


class MoversHook(unittest.TestCase):
    def test_posts_once_with_tally(self):
        sent = []
        w = movers.MoversWatch(lambda *a: True, (), lambda t, s: sent.append(t) or True)
        pm = {"t": "ZNTX", "score": 4, "boost": 1, "hits": ["cheap"], "boosts": ["sector"], "qual": False,
              "off_high": -0.4, "ev_s": 1.1, "sector": "Healthcare", "cand": None, "signal": False, "rumour": False}
        r = {"s": "ZNTX", "name": "Zentrox", "sector": "Healthcare", "pct": 45.0, "price": 8.0, "cap": 3e8,
             "ai": {"cause": "buyout", "reason": "to be acquired"}, "pm": pm}
        res = {"day": "2026-11-16", "up": [r], "down": [], "wl": [], "next": {}, "n": 100, "all": 200}
        mv = {}
        with mock.patch.object(w, "_close_msg", lambda *a: "x"):
            w._finish_close(mv, res, {})
            w._finish_close(mv, res, {})
        self.assertEqual(len(sent), 1)
        self.assertIn("Tally since 2026-11-16: 1 buyouts · we had 0 · fingerprint would have flagged 0", sent[0])
        self.assertEqual(mv["pm"], [["2026-11-16", "ZNTX", 4, 1, False, False]])


if __name__ == "__main__":
    unittest.main()


class StudySummary(unittest.TestCase):
    def test_posted_once(self):
        sent = []
        w = buyout.BuyoutWatch(lambda t, s: sent.append(t) or True)
        state = {"_bo": {"report_day": "2099-01-01"}}
        with mock.patch.object(buyout, "SIGNALS_ON", False), mock.patch.object(w, "_start_screen", lambda bo: None):
            w.check(state)
            w.check(state)
        self.assertEqual(sum("68 real buyouts" in x for x in sent), 1)
        self.assertEqual(state["_bo"]["study_v"], buyout.STUDY_V)

    def test_summary_fits_and_tags_balance(self):
        import re
        s = buyout.STUDY_SUMMARY
        self.assertLess(len(s), 4000)
        for tag in ("b", "i"):
            self.assertEqual(len(re.findall(f"<{tag}>", s)), len(re.findall(f"</{tag}>", s)), tag)
