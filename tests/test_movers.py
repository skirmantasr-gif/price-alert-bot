"""🏆 Daily winners & losers + mover diary (no network: data functions are mocked)."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

import movers

DAY = datetime.now(movers.NY).strftime("%Y-%m-%d")


def row(s, pct, price=10.0, vol=1e6, cap=500e6, sector="Health Care"):
    return {"s": s, "name": s + " Inc.", "price": price, "pct": pct, "vol": vol, "cap": cap, "sector": sector,
            "industry": ""}


class Filters(unittest.TestCase):
    def test_junk_filter(self):
        self.assertTrue(movers.tradeable(row("AAA", 50)))
        self.assertFalse(movers.tradeable(row("PENNY", 300, price=0.4)))
        self.assertFalse(movers.tradeable(row("TINY", 80, cap=30e6)))
        self.assertFalse(movers.tradeable(row("THIN", 60, vol=50_000)))          # $500k traded
        self.assertFalse(movers.tradeable({**row("X", 5), "pct": None}))

    def test_top_only_right_direction(self):
        rows = [row("A", 5), row("B", 12), row("C", -3), row("D", -20), row("E", 0.5)]
        self.assertEqual([r["s"] for r in movers.top(rows, 2, True)], ["B", "A"])
        self.assertEqual([r["s"] for r in movers.top(rows, 5, False)], ["D", "C"])

    def test_names_cleaned(self):
        self.assertEqual(movers._clean_name("Gogo Inc. Common Stock"), "Gogo Inc.")
        self.assertEqual(movers._clean_name("GIBO Holdings Limited Class A Ordinary Shares"), "GIBO Holdings Limited")


class Signs(unittest.TestCase):
    def test_day_before_vs_before_open(self):
        e = {"p": 40.0, "f": {"vr": 2.5, "gap": 20.0}, "sched": True, "bo": False, "news_pre": 2, "pre_rank": 1}
        self.assertEqual(movers.signs(e), ["volume build-up", "scheduled event"])
        self.assertEqual(movers.known_pre(e), ["our pre-market list", "news before open"])
        self.assertAlmostEqual(movers.after_open(e), 16.7)       # 1.40 / 1.20 - 1
        self.assertEqual(movers.signs({"p": 5, "f": {}}), [])
        self.assertIsNone(movers.after_open({"p": 5, "f": {}}))

    def test_generic_headlines(self):
        self.assertTrue(movers.GENERIC.search("Top Midday Decliners"))
        self.assertTrue(movers.GENERIC.search("Telos Corp (TLS) Q3 2026 Earnings Call Transcript"))
        self.assertFalse(movers.GENERIC.search("Strathcona acquires Greenfire"))


class CloseReport(unittest.TestCase):
    def run_close(self, mv, quotes, rows, ai=None):
        sent = []
        w = movers.MoversWatch(lambda t, s: sent.append((t, s)) or True, ["WL1", "WL2"])
        now_t = int(datetime.now().timestamp())
        live = {s: {"price": q[0], "pct": q[1], "vol": q[2], "t": now_t, "pre": None} for s, q in quotes.items()}
        with mock.patch.object(movers, "traded_today", lambda d: True), \
                mock.patch.object(movers, "nasdaq_rows", lambda: rows), \
                mock.patch.object(movers, "quotes_all", lambda syms, pause=0: {s: live[s] for s in syms if s in live}), \
                mock.patch.object(movers, "features", lambda s, d: {"vr": 3.0, "gap": 10.0} if s == "UP1" else {}), \
                mock.patch.object(movers, "headlines", lambda s, since: []), \
                mock.patch.object(movers.impact, "ai_enabled", lambda: ai is not None), \
                mock.patch.object(movers, "explain", lambda m: ai or {}), \
                mock.patch.object(movers.time, "sleep", lambda x: None):
            res = w._close_job(DAY, {"DN1": {}}, {"UP1": [15.0, 1, "up"]}, ["OLD"])
            w._finish_close(mv, res, {})
        return sent

    def test_full_day(self):
        rows = [row(s, 0) for s in ("UP1", "UP2", "DN1", "WL1", "WL2", "OLD", "PENNY")]
        rows[-1]["price"] = 0.3
        quotes = {"UP1": (12.0, 40.0, 5e6), "UP2": (5.0, 10.0, 1e6), "DN1": (3.0, -30.0, 2e6),
                  "WL1": (2.0, 4.0, 1e6), "WL2": (2.0, -2.0, 1e6), "OLD": (9.0, 6.0, 1e6), "PENNY": (0.4, 300, 9e6)}
        yday = (datetime.now(movers.NY) - timedelta(days=1)).strftime("%Y-%m-%d")
        mv = {"diary": [{"d": yday, "s": "OLD", "side": "up", "p": 30, "next": None}]}
        ai = {0: {"cause": "earnings", "reason": "Q3 beat, raised outlook", "scheduled": True, "sympathy_with": "",
                  "headline_id": -1}}
        sent = self.run_close(mv, quotes, rows, ai)
        self.assertEqual(len(sent), 1)
        text, silent = sent[0]
        self.assertTrue(silent)
        self.assertIn("$UP1</b> +40.0%", text)
        self.assertNotIn("PENNY", text)                         # $0.40 stock filtered out
        self.assertIn("Q3 beat, raised outlook", text)
        self.assertIn("opened +10%, then +27% open → close", text)
        self.assertIn("🔮 day before: volume build-up, scheduled event", text)
        self.assertIn("⭐ <b>Your watchlist</b>", text)
        self.assertIn("Yesterday's movers today: winners +6.0%", text)
        d = {e["s"]: e for e in mv["diary"] if e["d"] == DAY}
        self.assertEqual(d["UP1"]["pre"], ["our pre-market list"])
        self.assertTrue(d["DN1"]["bo"])
        self.assertEqual(d["DN1"]["signs"], ["buyout candidate"])
        self.assertEqual(mv["diary"][0]["next"], 6.0)          # day-2 result filled in

    def test_holiday_sends_nothing(self):
        sent = []
        w = movers.MoversWatch(lambda t, s: sent.append(t) or True)
        with mock.patch.object(movers, "traded_today", lambda d: False):
            res = w._close_job(DAY, {}, {}, [])
        w._finish_close({}, res, {})
        self.assertEqual(sent, [])


class Weekly(unittest.TestCase):
    def test_week_review(self):
        es = [{"d": f"2026-11-0{i}", "s": f"S{i}", "side": "up" if i % 2 else "down", "p": 20.0, "cause": c,
               "signs": ["scheduled event"] if i == 3 else [], "pre": ["news before open"], "ao": 2.0,
               "sched": i == 3, "next": 1.0, "pre_rank": None}
              for i, c in zip(range(2, 8), ["earnings", "earnings", "fda_trial", "no_news", "buyout", "earnings"])]
        sent = []
        movers.MoversWatch(lambda t, s: sent.append(t) or True)._weekly({"diary": es})
        self.assertIn("results 2, FDA / trial 1", sent[0])
        self.assertIn("🔮 Signs the day before: <b>1 of 5</b>", sent[0])   # last 5 trading days only


if __name__ == "__main__":
    unittest.main()
