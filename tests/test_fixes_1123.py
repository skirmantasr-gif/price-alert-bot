"""2026-11-23 check: cancelled insider orders give their top-up back; IB Gateway down -> orders held, one warning,
retried on reconnect (the gateway sat at its login screen 05:30-17:57 LT and 7 mirror buys were lost)."""
import unittest

import ibkr
import paper
from tests.helpers import data_of, ny, session

TUE = (2026, 11, 3)


def sig(tk, p0=10.0):
    return {"tk": tk, "src": "insider", "d": "up", "c": "low", "p0": p0}


class TopupRefund(unittest.TestCase):
    def full(self):
        st = paper.new_state(ny(*TUE, 6))
        pf = st["pf"]["insider"]
        for i in range(10):                      # €100 start used up
            paper.on_signal(st, sig(f"T{i}"))
        self.assertAlmostEqual(pf["cash"], 0.0)
        paper.on_signal(st, sig("RUN"))          # cash ran out -> €10 top-up
        self.assertAlmostEqual(pf["topup"], 10.0)
        for o in pf["pending"].values():
            o["t"] = ny(*TUE, 7)
        return st, pf

    def test_not_chasing_cancel_takes_topup_back(self):
        st, pf = self.full()
        bars = {f"T{i}": session(TUE, [(10.0, 10.0)] * 78) for i in range(10)}
        bars["RUN"] = session(TUE, [(12.0, 12.0)] * 78)          # opened +20%: not chasing
        paper.step(st, ny(*TUE, 9, 40), data_of(bars))
        self.assertNotIn("RUN", pf["open"])
        self.assertAlmostEqual(pf["topup"], 0.0)
        self.assertAlmostEqual(pf["cash"], 0.0)                  # no idle top-up money
        self.assertEqual(pf["log"][-2]["a"], "untopup")
        self.assertIn("Took back €10", paper._line(pf["log"][-2], ""))

    def test_down_call_cancel_takes_topup_back(self):
        st, pf = self.full()
        paper.on_signal(st, dict(sig("RUN"), d="down", c="high"))
        self.assertAlmostEqual(pf["topup"], 0.0)
        self.assertAlmostEqual(pf["cash"], 0.0)

    def test_cancel_without_topup_keeps_money(self):
        st = paper.new_state(ny(*TUE, 6))
        pf = st["pf"]["insider"]
        paper.on_signal(st, sig("AAA"))
        paper.on_signal(st, dict(sig("AAA"), d="down", c="high"))
        self.assertAlmostEqual(pf["cash"], paper.START_EUR)
        self.assertFalse(pf.get("topup"))


class GatewayDown(unittest.TestCase):
    def mirror(self):
        sent = []
        m = ibkr.Mirror(lambda t, s=True: sent.append((t, s)) or True)
        m.ib_state = {}
        return m, sent

    def test_refused_buy_is_held_and_warned_once(self):
        m, sent = self.mirror()
        job = {"a": "buy", "tk": "AAA", "p": 10.0, "eur": 10, "t": 1000, "gen": 0}
        self.assertTrue(m._gateway_down(job, ConnectionRefusedError(111, "refused"), now=1000))
        self.assertEqual(sent, [])                                  # short blips (daily restart) stay quiet
        self.assertTrue(m._gateway_down({"a": "sync"}, ConnectionRefusedError(), now=1000 + ibkr.DOWN_WARN))
        self.assertTrue(m._gateway_down({"a": "sync"}, ConnectionRefusedError(), now=2000 + ibkr.DOWN_WARN))
        self.assertEqual(len(sent), 1)
        self.assertIn("IB Gateway not reachable", sent[0][0])
        self.assertIn("1 order(s) held", sent[0][0])
        self.assertFalse(sent[0][1])                                # loud
        self.assertEqual(m.ib_state["held"], [{"a": "buy", "tk": "AAA", "p": 10.0, "eur": 10, "t": 1000}])

    def test_back_up_requeues_recent_and_reports(self):
        m, sent = self.mirror()
        m._gateway_down({"a": "buy", "tk": "NEW", "t": 5000}, ConnectionRefusedError(), now=5000)
        m.held.append({"a": "buy", "tk": "OLD", "t": 5000 - ibkr.HOLD_MAX - 10})
        keep = m._back_up(now=6000)
        self.assertEqual([j["tk"] for j in keep], ["NEW"])
        self.assertIn("IB Gateway back", sent[-1][0])
        self.assertIn("1 older than 2 days dropped", sent[-1][0])
        self.assertIsNone(m.down_since)
        self.assertEqual(m._back_up(now=7000), [])                  # nothing more to report

    def test_timeout_while_gateway_up_is_a_real_failure(self):
        m, _ = self.mirror()
        self.assertFalse(m._gateway_down({"a": "buy", "tk": "AAA"}, TimeoutError(), now=1))
        self.assertEqual(m.held, [])

    def test_held_orders_survive_restart(self):
        m, _ = self.mirror()
        st = {"ib": {"held": [{"a": "buy", "tk": "AAA", "t": 1}], "seen": [], "since": 0}}
        m.thread = type("T", (), {"is_alive": lambda self: True})()
        m.sync(st)
        self.assertEqual(m.held, [{"a": "buy", "tk": "AAA", "t": 1}])


if __name__ == "__main__":
    unittest.main()


class MoneyInLine(unittest.TestCase):
    def test_statement_shows_money_in_balance_and_result(self):
        pf = paper._pf()
        pf.update(cash=0.0, topup=74.69)
        pf["open"] = {"AAA": {"p0": 10.0, "eur": 169.75, "cost": 170.0, "t": 0}}
        txt = paper.account({"pf": {"insider": pf}}, "insider", 100, lambda t: None)
        self.assertIn("💶 Money put in <b>€174.69</b> (€100 start + €74.69 added)", txt)
        self.assertIn("balance now <b>€169.75</b>", txt)
        self.assertIn("result <b>-4.94 €</b>", txt)

    def test_no_line_without_topups(self):
        pf = paper._pf()
        self.assertNotIn("Money put in", paper.account({"pf": {"strong": pf}}, "strong", 100, lambda t: None))

    def test_correction_note_line(self):
        e = {"a": "untopup", "tk": "-", "eur": 110, "note": "11 orders cancelled before they were bought"}
        self.assertIn("➖ Took back €110 play money (11 orders cancelled before they were bought)", paper._line(e, ""))
