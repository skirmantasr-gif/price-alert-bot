"""👥 Insider-cluster practice portfolio (2026-11-15): only insider signals, slower exits, other portfolios untouched."""
import unittest

import paper
from tests.helpers import data_of, flat, ny, session

TUE, WED, THU = (2026, 11, 3), (2026, 11, 4), (2026, 11, 5)


def sig(tk="AAA", src="insider", c="low", d="up", p0=10.0):
    return {"tk": tk, "src": src, "d": d, "c": c, "p0": p0}


class InsiderPortfolio(unittest.TestCase):
    def test_insider_signal_only_goes_to_insider_portfolio(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig(c="high"))
        self.assertIn("AAA", st["pf"]["insider"]["pending"])
        for k in ("strong", "likely", "outlook", "calm"):
            self.assertNotIn("AAA", st["pf"][k]["pending"], k)

    def test_news_signal_never_reaches_insider_portfolio(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig(src="news", c="high"))
        self.assertIn("AAA", st["pf"]["strong"]["pending"])
        self.assertNotIn("AAA", st["pf"]["insider"]["pending"])

    def test_every_cluster_ten_euros(self):          # owner 2026-11-20: "€10 per cluster" (was €10 quiet / €15 loud)
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("AAA", c="low"))
        paper.on_signal(st, sig("BBB", c="high"))
        pend = st["pf"]["insider"]["pending"]
        self.assertEqual((pend["AAA"]["eur"], pend["BBB"]["eur"]), (10.0, 10.0))

    def test_tops_up_when_cash_runs_out_and_no_position_cap(self):
        st = paper.new_state(ny(*TUE, 6))
        for i in range(14):                              # €140 of clusters on a €100 start, more than MAX_OPEN
            paper.on_signal(st, sig(f"T{i:02d}"))
        pf = st["pf"]["insider"]
        self.assertEqual(len(pf["pending"]), 14)
        self.assertAlmostEqual(pf["topup"], 40.0)
        self.assertAlmostEqual(pf["cash"], 0.0)
        self.assertAlmostEqual(paper.base(pf), 140.0)
        self.assertTrue(any(x.get("a") == "topup" for x in pf["log"]))
        self.assertNotIn("topup", st["pf"]["strong"])     # other portfolios keep their cash limit

    def test_statement_shows_money_put_in(self):
        pf = {"topup": 20.0}
        self.assertEqual(paper._put_in(pf), " on €100 + €20 added")
        self.assertEqual(paper._put_in({}), "")

    def test_no_profit_lock_but_wider_stop_and_target(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig())
        for pf in st["pf"].values():
            for o in pf["pending"].values():
                o["t"] = ny(*TUE, 7)
        # up 6%, then back to +1%: the normal profit lock would sell; insider keeps it. Then -10%: still held.
        path = [(10.0, 10.0), (10.6, 10.6), (10.1, 10.1), (9.0, 9.0)] + [(9.0, 9.0)] * 74
        data = data_of({"AAA": session(TUE, path)})
        paper.step(st, ny(*TUE, 16, 30), data)
        self.assertIn("AAA", st["pf"]["insider"]["open"])
        # since 2026-11-20 (backtest): no stop and no target - a -16% drop and then +40% are both held
        path2 = [(8.4, 8.4)] * 78
        data = data_of({"AAA": session(TUE, path) + session(WED, path2)})
        paper.step(st, ny(*WED, 16, 30), data)
        self.assertIn("AAA", st["pf"]["insider"]["open"])
        path3 = [(14.0, 14.0)] * 78
        data = data_of({"AAA": session(TUE, path) + session(WED, path2) + session(THU, path3)})
        paper.step(st, ny(*THU, 16, 30), data)
        self.assertIn("AAA", st["pf"]["insider"]["open"])
        self.assertEqual(paper.exits("likely")["stop"], paper.STOP)   # the other portfolios keep their stop

    def test_sold_after_90_days(self):
        st = paper.new_state(ny(*TUE, 6))
        pf = st["pf"]["insider"]
        t0 = ny(*TUE, 10)
        pf["open"]["AAA"] = {"p0": 10.0, "eur": 10.0, "cost": 10.0, "t": t0 - 91 * 86400, "day": "x",
                             "src": "insider", "c": "low"}
        pf["cash"] -= 10
        data = data_of({"AAA": flat(TUE, 10.2)})
        paper.step(st, ny(*TUE, 16, 30), data)
        self.assertNotIn("AAA", pf["open"])
        self.assertIn("90 days", pf["closed"][-1]["why"])

    def test_account_view_shows_insider_exits(self):
        st = paper.new_state(ny(*TUE, 6))
        pf = st["pf"]["insider"]
        pf["open"]["AAA"] = {"p0": 10.0, "eur": 10.0, "cost": 10.0, "t": ny(*TUE, 10), "day": "x",
                             "src": "insider", "c": "low"}
        txt = paper.account(st, "insider", ny(*TUE, 12), data_of({"AAA": flat(TUE, 10.0)}))
        self.assertNotIn("stop ≤", txt)
        self.assertNotIn("target ≥", txt)
        self.assertIn("time limit", txt)
        self.assertNotIn("profit lock", txt)

    def test_old_state_gets_insider_portfolio_fresh(self):
        st = paper.new_state(ny(*TUE, 6))
        del st["pf"]["insider"]
        paper.add_portfolios(st, ny(*TUE, 7))
        self.assertEqual(st["pf"]["insider"]["cash"], paper.START_EUR)
        self.assertEqual(st["pf"]["insider"]["open"], {})


if __name__ == "__main__":
    unittest.main()
