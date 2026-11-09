"""🧪 Practice portfolio: real-price fills, exits, cash, statements, message timing."""
import unittest
from unittest import mock

import paper
from tests.helpers import data_of, flat, ny, session

TUE, WED, THU, FRI = (2026, 11, 3), (2026, 11, 4), (2026, 11, 5), (2026, 11, 6)


def sig(tk="AAA", d="up", c="high", src="news", p0=10.0):
    return {"tk": tk, "src": src, "d": d, "c": c, "p0": p0}


def placed_at(st, t):
    for pf in st["pf"].values():
        for o in pf["pending"].values():
            o["t"] = t


class Fills(unittest.TestCase):
    def test_order_waits_and_fills_at_next_opening_price_not_alert_price(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig(p0=9.8))           # alert price 9.8, market closed
        placed_at(st, ny(*TUE, 7))
        pf = st["pf"]["strong"]
        self.assertIn("AAA", pf["pending"])
        self.assertAlmostEqual(pf["cash"], paper.START_EUR - paper.SIZE_STRONG_EUR)  # €15 reserved (strong call)
        data = data_of({"AAA": session(TUE, [(10.0, 10.1)] + [(10.1, 10.1)] * 77)})
        paper.step(st, ny(*TUE, 7, 30), data)       # before the open: still waiting
        self.assertIn("AAA", pf["pending"])
        paper.step(st, ny(*TUE, 9, 40), data)
        pos = pf["open"]["AAA"]
        self.assertAlmostEqual(pos["p0"], 10.0 * (1 + paper.slip(data("AAA"), 10.0)))  # open + spread, not 9.8
        self.assertEqual(pos["t"], ny(*TUE, 9, 30))

    def test_spread_depends_on_liquidity_and_currency(self):
        self.assertEqual(paper.slip({"cur": "EUR"}, 50), 0.003)
        self.assertEqual(paper.slip({"cur": "USD", "dollar_vol": 5e5}, 50), 0.01)
        self.assertEqual(paper.slip({"cur": "USD", "dollar_vol": 5e8}, 0.5), 0.01)
        self.assertEqual(paper.slip({"cur": "USD", "dollar_vol": 5e8}, 50), 0.0005)

    def test_down_call_cancels_waiting_order_and_returns_cash(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig())
        paper.on_signal(st, sig(d="down", c="medium"))
        pf = st["pf"]["strong"]
        self.assertEqual(pf["pending"], {})
        self.assertAlmostEqual(pf["cash"], paper.START_EUR)

    def test_portfolio_rules_route_signals(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("AAA", c="medium"))
        paper.on_signal(st, sig("BBB", c="medium", src="outlook"))
        self.assertNotIn("AAA", st["pf"]["strong"]["pending"])    # strong = high only
        self.assertIn("AAA", st["pf"]["likely"]["pending"])
        self.assertIn("BBB", st["pf"]["outlook"]["pending"])
        self.assertNotIn("BBB", st["pf"]["likely"]["pending"])    # outlook calls stay in their own portfolio

    def test_max_open_positions(self):
        st = paper.new_state(ny(*TUE, 6))
        for i in range(paper.MAX_OPEN + 3):
            paper.on_signal(st, sig(f"T{i}", c="medium"))          # €10 each
        self.assertEqual(len(st["pf"]["likely"]["pending"]), paper.MAX_OPEN)
        self.assertGreaterEqual(st["pf"]["likely"]["cash"], -1e-9)
        st = paper.new_state(ny(*TUE, 6))
        for i in range(paper.MAX_OPEN):
            paper.on_signal(st, sig(f"T{i}"))                      # €15 each: cash runs out after 6
        self.assertEqual(len(st["pf"]["strong"]["pending"]), int(paper.START_EUR // paper.SIZE_STRONG_EUR))

    def test_unfilled_order_cancelled_after_7_days(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig())
        placed_at(st, ny(*TUE, 6))
        paper.step(st, ny(*TUE, 6) + 8 * 86400, data_of({}))
        self.assertEqual(st["pf"]["strong"]["pending"], {})
        self.assertAlmostEqual(st["pf"]["strong"]["cash"], paper.START_EUR)


class Exits(unittest.TestCase):
    def _bought(self, bars):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig())
        placed_at(st, ny(*TUE, 7))
        data = data_of({"AAA": bars})
        return st, data

    def test_stop_loss_judged_on_close_sold_at_next_bar_open(self):
        bars = session(TUE, [(10, 10)] * 5 + [(10, 9.0)] + [(8.8, 8.8)] * 72)   # bar 5 closes −10%
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 16), data)
        c = st["pf"]["strong"]["closed"][-1]
        self.assertEqual(c["why"], "stop −8%")
        self.assertEqual(c["t"], bars[6][0])                       # next bar, not the bar that broke the stop
        p0 = 10 * (1 + paper.slip(data("AAA"), 10))
        self.assertAlmostEqual(c["ret"], round(8.8 * (1 - paper.slip(data("AAA"), 8.8)) / p0 - 1, 4))

    def test_take_profit(self):
        bars = session(TUE, [(10, 10)] * 3 + [(10, 11.8)] + [(11.9, 11.9)] * 74)
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 16), data)
        self.assertEqual(st["pf"]["strong"]["closed"][-1]["why"], "take profit +15%")

    def test_no_time_limit_flat_stock_is_kept(self):
        bars = flat(TUE, 10) + flat(WED, 10.05) + flat(THU, 10.05) + flat(FRI, 10.05)
        st, data = self._bought(bars)
        paper.step(st, ny(*FRI, 18), data)
        self.assertIn("AAA", st["pf"]["strong"]["open"])

    def test_profit_lock_sells_soon_after_buying(self):
        # bought at the 09:30 open, up +6% by 09:50, then falls: sold ~30 minutes after buying, not days later
        bars = session(TUE, [(10, 10), (10.2, 10.3), (10.4, 10.6), (10.6, 10.6), (10.5, 10.35), (10.3, 10.3)]
                       + [(10.3, 10.3)] * 72)
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 10, 30), data)
        c = st["pf"]["strong"]["closed"][-1]
        self.assertIn("profit lock", c["why"])
        self.assertEqual(c["t"], bars[5][0])                      # next bar after the 2% drop from the high
        self.assertLessEqual(c["t"] - bars[0][0], 30 * 60)
        self.assertGreater(c["ret"], 0.02)

    def test_profit_lock_needs_plus_3_percent_first(self):
        bars = session(TUE, [(10, 10), (10.2, 10.2), (10.0, 9.95)] + [(9.95, 9.95)] * 75)   # +2%, then −2.5%
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 16), data)
        self.assertIn("AAA", st["pf"]["strong"]["open"])

    def test_high_is_remembered_between_checks(self):
        bars = session(TUE, [(10, 10), (10.5, 10.6)] + [(10.6, 10.6)] * 3 + [(10.35, 10.35)] * 73)
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 9, 45), data)                    # sees the high 10.6
        self.assertAlmostEqual(st["pf"]["strong"]["open"]["AAA"]["hi"], 10.6)
        paper.step(st, ny(*TUE, 11), data)                       # later drop from that high → sold
        self.assertIn("profit lock", st["pf"]["strong"]["closed"][-1]["why"])

    def test_down_call_exits_open_position_at_next_price(self):
        bars = flat(TUE, 10)
        st, data = self._bought(bars)
        paper.step(st, ny(*TUE, 10), data)
        paper.on_signal(st, sig(d="down", c="high"), now=ny(*TUE, 11))
        paper.step(st, ny(*TUE, 12), data)
        self.assertEqual(st["pf"]["strong"]["closed"][-1]["why"], "DOWN call")


class BuyRules(unittest.TestCase):
    def test_no_chasing_cancels_if_price_ran_away(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig(p0=10.0))
        placed_at(st, ny(*TUE, 7))
        paper.step(st, ny(*TUE, 9, 40), data_of({"AAA": session(TUE, [(10.6, 10.6)] * 78)}))   # opens +6%
        pf = st["pf"]["strong"]
        self.assertEqual(pf["open"], {})
        self.assertAlmostEqual(pf["cash"], paper.START_EUR)       # money back
        self.assertIn("not chasing", pf["log"][-1]["why"])

    def test_bigger_bet_on_strong_calls(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("AAA", c="high"))
        paper.on_signal(st, sig("BBB", c="medium"))
        self.assertEqual(st["pf"]["likely"]["pending"]["AAA"]["eur"], 15)
        self.assertEqual(st["pf"]["likely"]["pending"]["BBB"]["eur"], 10)
        placed_at(st, ny(*TUE, 7))
        paper.step(st, ny(*TUE, 10), data_of({"AAA": flat(TUE, 10), "BBB": flat(TUE, 10)}))
        self.assertAlmostEqual(st["pf"]["likely"]["open"]["AAA"]["eur"], 15 * (1 - paper.FEE))


class Statement(unittest.TestCase):
    def setUp(self):
        self.st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(self.st, sig("AAA"))
        placed_at(self.st, ny(*TUE, 7))
        paper.on_signal(self.st, sig("BBB", c="medium"))            # stays waiting (no data)
        self.data = data_of({"AAA": session(TUE, [(10, 10)] * 6 + [(11, 11)] * 72), "SPY": flat(TUE, 500)})
        paper.step(self.st, ny(*TUE, 11), self.data)

    def test_value_is_cash_plus_reserved_plus_stocks(self):
        pf = self.st["pf"]["likely"]
        now = ny(*TUE, 11)
        stocks = sum(p["eur"] * 11 / p["p0"] for p in pf["open"].values())
        self.assertAlmostEqual(paper.value(pf, now, self.data),
                               pf["cash"] + paper.SIZE_EUR * len(pf["pending"]) + stocks)

    def test_account_text_shows_cash_reserved_holdings(self):
        txt = paper.account(self.st, "likely", ny(*TUE, 11), self.data)
        pf = self.st["pf"]["likely"]
        self.assertIn(f"€{paper.value(pf, ny(*TUE, 11), self.data):.2f}", txt)
        self.assertIn("Free cash €75.00", txt)                    # €15 strong call + €10 waiting order
        self.assertIn("reserved €10", txt)
        self.assertIn("$AAA", txt)
        self.assertIn("sells at", txt)
        self.assertIn("high 11", txt)
        self.assertIn("$BBB", txt)                                  # waiting order listed
        self.assertIn("all in cash", paper.account(self.st, "outlook", ny(*TUE, 11), self.data))

    def test_trades_posted_right_away_with_sound_statements_silent(self):
        sent = []
        tr = paper.Tracker(None, lambda text, silent: sent.append((text, silent)) or True, live_trades=True)
        tr.data = self.data
        self.assertTrue(tr._trades_now(self.st, ny(*TUE, 11)))
        self.assertFalse(sent[-1][1])                              # with sound
        self.assertIn("Bought <b>$AAA</b>", sent[-1][0])
        self.assertFalse(tr._trades_now(self.st, ny(*TUE, 11, 5)))  # nothing new: no message
        tr._post(self.st, ny(*TUE, 12), "12:00 New York")
        self.assertTrue(sent[-1][1])                               # statement: silent
        self.assertLess(max(len(t) for t, _ in sent), 4096)

    def test_identical_portfolios_shown_once(self):
        sent = []
        tr = paper.Tracker(None, lambda text, silent: sent.append((text, silent)) or True, live_trades=True)
        tr.data = data_of({"SPY": flat(TUE, 500)})
        st = paper.new_state(ny(*TUE, 6))
        tr._post(st, ny(*TUE, 12), "12:00 New York")
        self.assertIn("same trades so far", sent[-1][0])
        self.assertEqual(sent[-1][0].count("Free cash"), 1)


class Timing(unittest.TestCase):
    def run_at(self, times, weekday=TUE):
        state, sent = {}, []
        tr = paper.Tracker(None, lambda text, silent: sent.append((text, silent)) or True, live_trades=True)
        tr.data = data_of({"SPY": flat(weekday, 500)})
        for h, m in times:
            with mock.patch("paper.time.time", return_value=ny(*weekday, h, m)):
                tr.check(state)
        return sent

    def test_open_statement_then_one_per_hour(self):
        sent = self.run_at([(9, 0), (9, 37), (9, 50), (10, 1), (10, 30), (11, 2)])
        heads = [t.split("\n")[0] for t, _ in sent]
        self.assertEqual(len(heads), 3, heads)
        self.assertIn("market open", heads[0])
        self.assertIn("10:00", heads[1])
        self.assertIn("11:00", heads[2])

    def test_daily_report_once_after_close(self):
        sent = self.run_at([(16, 31), (16, 45), (17, 0)])
        reports = [t for t, _ in sent if "Practice portfolio ·" in t]
        self.assertEqual(len(reports), 1)

    def test_nothing_on_weekends(self):
        self.assertEqual(self.run_at([(9, 40), (12, 0), (16, 40)], weekday=(2026, 11, 7)), [])

    def test_old_saved_version_restarts_once(self):
        state = {"_paper": {"v": 1, "pf": {}}}
        paper.Tracker(None, lambda *a: True).attach(state)
        self.assertEqual(state["_paper"]["v"], paper.VERSION)


class Voids(unittest.TestCase):
    def test_voided_trade_removed_once_with_money_back(self):
        st = {"_paper": paper.new_state(1000), "_learn": {"p": [{"t": 1500, "tk": "AAA"}, {"t": 1500, "tk": "BBB"}]}}
        pfs = list(st["_paper"]["pf"].values())
        pfs[0]["cash"] -= 10
        pfs[0]["open"]["AAA"] = {"p0": 5.0, "eur": 9.985, "cost": 10, "t": 1600, "src": "contract"}
        pfs[1]["cash"] -= 15
        pfs[1]["pending"]["AAA"] = {"t": 1500, "src": "contract", "c": "high", "eur": 15}
        pfs[1]["open"]["BBB"] = {"p0": 5.0, "eur": 9.985, "cost": 10, "t": 1600, "src": "news"}
        with mock.patch.object(paper, "VOIDS", [("v1", paper._tkh("AAA"), 1000, 2000, "wrong alert")]):
            self.assertEqual(paper.apply_voids(st, now=3000), 2)
            self.assertEqual(paper.apply_voids(st, now=3100), 0)  # only once
        self.assertNotIn("AAA", pfs[0]["open"])
        self.assertNotIn("AAA", pfs[1]["pending"])
        self.assertIn("BBB", pfs[1]["open"])
        self.assertAlmostEqual(pfs[0]["cash"], paper.START_EUR)
        self.assertAlmostEqual(pfs[1]["cash"], paper.START_EUR - 15 + 15)
        self.assertEqual([x["tk"] for x in st["_learn"]["p"]], ["BBB"])
        self.assertEqual(st["_paper"]["voided"], ["v1"])
        lines = [x for _, x in paper.trade_lines(st["_paper"], 0, short=False)]
        self.assertTrue(any(x.startswith("↩️ Trade removed") for x in lines))

    def test_void_outside_window_or_unknown_ticker_does_nothing(self):
        st = {"_paper": paper.new_state(1000)}
        pf = next(iter(st["_paper"]["pf"].values()))
        pf["open"]["AAA"] = {"p0": 5.0, "eur": 9.985, "cost": 10, "t": 5000, "src": "news"}
        with mock.patch.object(paper, "VOIDS", [("v1", paper._tkh("AAA"), 1000, 2000, "x"),
                                                ("v2", paper._tkh("ZZZ"), 0, 9999, "x")]):
            self.assertEqual(paper.apply_voids(st, now=3000), 0)
        self.assertIn("AAA", pf["open"])


if __name__ == "__main__":
    unittest.main()
