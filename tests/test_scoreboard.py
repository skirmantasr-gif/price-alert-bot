"""📊 Scoreboard (2026-11-15): every alert kind measured against the S&P over the same days."""
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import extras
import learn
import paper
import scoreboard

NY = ZoneInfo("America/New_York")
TUE_11 = datetime(2026, 11, 17, 11, 0, tzinfo=NY).timestamp()   # Tue Nov 17, during the session
DAYS = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]


def closes_for(stock, spy):
    table = {"AAA": stock, "SPY": spy}
    return lambda tk: (list(zip(DAYS, table[tk])), NY, (16, 0))


class Note(unittest.TestCase):
    def setUp(self):
        scoreboard._spy[:] = [0.0, None]

    def test_records_price_and_spy_once_per_day(self):
        st = {}
        with mock.patch.object(scoreboard, "PRICE", lambda tk: 100.0 if tk == "SPY" else None):
            self.assertTrue(scoreboard.note("price", "AAA", "up", 10.0, state=st, now=TUE_11))
            self.assertFalse(scoreboard.note("price", "AAA", "up", 10.5, state=st, now=TUE_11 + 600))   # same day
            self.assertTrue(scoreboard.note("news", "AAA", "up", 10.5, state=st, now=TUE_11 + 600))     # other kind
        ev = st["_sb"]["ev"]
        self.assertEqual(len(ev), 2)
        self.assertEqual((ev[0]["p0"], ev[0]["b0"]), (10.0, 100.0))

    def test_ignores_no_direction_or_no_price(self):
        st = {}
        with mock.patch.object(scoreboard, "PRICE", lambda tk: None):
            self.assertFalse(scoreboard.note("news", "AAA", "neutral", 10.0, state=st))
            self.assertFalse(scoreboard.note("news", "AAA", "up", None, state=st))
        self.assertNotIn("ev", st.get("_sb", {}))

    def test_learn_record_feeds_the_scoreboard(self):
        st = {}
        with mock.patch.object(learn, "paper", None), mock.patch.object(scoreboard, "PRICE", lambda tk: 100.0):
            learn.record(st, "insider", "AAA", "up", "medium", "rules", "x", 10.0)
        self.assertEqual(st["_sb"]["ev"][0]["k"], "insider")


class Evaluate(unittest.TestCase):
    def ev(self, d="up"):
        return {"_sb": {"ev": [{"t": int(TUE_11), "k": "price", "tk": "AAA", "d": d, "p0": 10.0, "b0": 100.0}]}}

    def test_excess_vs_spy_after_1_and_5_closes(self):
        st = self.ev()
        # stock: +5% by the first close, +10% by the 5th; SPY +1% and +2%
        n = scoreboard.evaluate(st, closes_for([10.5, 10.6, 10.7, 10.8, 11.0, 11.1, 11.2],
                                               [101, 101, 101, 101, 102, 102, 102]))
        e = st["_sb"]["ev"][0]
        self.assertEqual(n, 2)
        self.assertEqual((e["r1"], e["x1"]), (5.0, 4.0))
        self.assertEqual((e["r5"], e["x5"]), (10.0, 8.0))

    def test_down_alert_signed(self):
        st = self.ev("down")
        scoreboard.evaluate(st, closes_for([10.5, 10.6, 10.7, 10.8, 11.0, 11.1, 11.2],
                                           [101, 101, 101, 101, 102, 102, 102]))
        self.assertEqual(st["_sb"]["ev"][0]["x5"], -8.0)    # stock beat SPY by 8 points: the DOWN call was 8% wrong

    def test_waits_for_enough_closes(self):
        st = self.ev()
        scoreboard.evaluate(st, lambda tk: (list(zip(DAYS[:2], [10.5, 10.6] if tk == "AAA" else [101, 101])),
                                            NY, (16, 0)))
        e = st["_sb"]["ev"][0]
        self.assertIn("x1", e)
        self.assertNotIn("x5", e)

    def test_alert_after_the_close_starts_next_day(self):
        st = self.ev()
        st["_sb"]["ev"][0]["t"] = int(datetime(2026, 11, 17, 17, 0, tzinfo=NY).timestamp())
        scoreboard.evaluate(st, closes_for([10.5, 11.0, 10.7, 10.8, 11.0, 11.1, 11.2],
                                           [101, 101, 101, 101, 102, 102, 102]))
        self.assertEqual(st["_sb"]["ev"][0]["r1"], 10.0)    # first close AFTER 17:00 Tue = Wed's 11.0


class Verdicts(unittest.TestCase):
    def test_too_few(self):
        self.assertEqual(scoreboard.verdict([5.0] * 19)[0], "⏳")

    def test_possible_edge_then_confirmed_or_not(self):
        self.assertEqual(scoreboard.verdict([2.0] * 20)[0], "✅?")
        self.assertEqual(scoreboard.verdict([2.0] * 40)[0], "✅")
        self.assertEqual(scoreboard.verdict([2.0] * 20 + [-1.0] * 20)[0], "⚠️")

    def test_loses_and_noise(self):
        self.assertEqual(scoreboard.verdict([-2.0] * 20)[0], "❌")
        self.assertEqual(scoreboard.verdict([0.5, -0.5] * 10)[0], "➖")

    def test_big_average_from_few_winners_is_not_an_edge(self):
        # +1.5% average, but only 30% of alerts beat the S&P: luck from a couple of big winners
        self.assertEqual(scoreboard.verdict([10.0] * 6 + [-2.0] * 14)[0], "➖")


class Weekly(unittest.TestCase):
    def test_sunday_post_once(self):
        sent = []
        b = scoreboard.Board(lambda tk: ([], NY, (16, 0)), lambda t, s=None: sent.append(t) or True)
        b.last_eval = 10 ** 12
        sun = extras.local_now().replace(year=2026, month=11, day=22, hour=10, minute=30)
        st = {"_sb": {"ev": [{"t": 1, "k": "price", "tk": "AAA", "d": "up", "p0": 1, "x1": 1.0, "x5": 2.0}]}}
        with mock.patch.object(extras, "local_now", return_value=sun):
            b.check(st)
            b.check(st)
        self.assertEqual(len(sent), 1)
        self.assertIn("Scoreboard", sent[0])
        self.assertIn("📈 Price-move alerts", sent[0])
        self.assertIn("too few (1/20)", sent[0])


class OutlookPaused(unittest.TestCase):
    def test_paused_portfolio_no_new_buys_but_still_sells(self):
        st = paper.new_state(TUE_11)
        st["pf"]["outlook"]["open"]["AAA"] = {"t": int(TUE_11), "p0": 10.0, "eur": 10, "src": "outlook"}
        paper.on_signal(st, {"tk": "BBB", "src": "outlook", "d": "up", "c": "high", "p0": 5.0}, now=TUE_11)
        self.assertNotIn("BBB", st["pf"]["outlook"]["pending"])
        paper.on_signal(st, {"tk": "AAA", "src": "outlook", "d": "down", "c": "high", "p0": 9.0}, now=TUE_11)
        self.assertIn("exit", st["pf"]["outlook"]["open"]["AAA"])
        self.assertIn("paused since", paper.account(st, "outlook", TUE_11, lambda tk: None))


if __name__ == "__main__":
    unittest.main()
