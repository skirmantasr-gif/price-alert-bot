"""🏦 IBKR paper mirror: event selection, sizing, paper-only safety, order flow against a fake broker."""
import sys
import types
import unittest
from unittest import mock

import ibkr


class FakeTrade:
    def __init__(self, contract, order, fill_px=None, status="Filled"):
        self.contract, self.order = contract, order
        self.fills = [1] if fill_px else []
        self.orderStatus = types.SimpleNamespace(status=status if fill_px or status != "Filled" else "Submitted")
        self.log = [types.SimpleNamespace(message="")]

    def isDone(self):
        return True


class FakeIB:
    def __init__(self, accounts=("DUR123",), positions=(), fill_px=10.3):
        self.accounts, self.pos, self.fill_px = list(accounts), list(positions), fill_px
        self.placed, self.cancelled, self.execs = [], [], []
        self.disconnected = False

    def managedAccounts(self):
        return self.accounts

    def qualifyContracts(self, c):
        return [c]

    def openTrades(self):
        return []

    def positions(self):
        return [types.SimpleNamespace(contract=types.SimpleNamespace(symbol=t), position=q) for t, q in self.pos]

    def placeOrder(self, c, o):
        self.placed.append((c.symbol, o.action, o.totalQuantity))
        if self.fill_px:
            ex = types.SimpleNamespace(execId=f"e{len(self.placed)}", side="BOT" if o.action == "BUY" else "SLD",
                                       orderId=len(self.placed), shares=o.totalQuantity, price=self.fill_px)
            self.execs.append(types.SimpleNamespace(execution=ex, contract=c))
        return FakeTrade(c, o, self.fill_px)

    def cancelOrder(self, o):
        self.cancelled.append(o)

    def sleep(self, s):
        pass

    def reqExecutions(self, f):
        return list(self.execs)

    def accountSummary(self):
        return [types.SimpleNamespace(tag="NetLiquidation", value="1000123.45")]

    def portfolio(self):
        return [types.SimpleNamespace(unrealizedPNL=12.5)]

    def connect(self, *a, **k):
        pass

    def disconnect(self):
        self.disconnected = True


def fake_lib():
    m = types.ModuleType("ib_async")
    m.Stock = lambda sym, ex, cur: types.SimpleNamespace(symbol=sym)
    m.MarketOrder = lambda action, qty, tif="DAY": types.SimpleNamespace(action=action, totalQuantity=qty, tif=tif)
    m.ExecutionFilter = lambda: None
    return m


class Pure(unittest.TestCase):
    def test_paper_only(self):
        self.assertTrue(ibkr.is_paper(["DUR123"]))
        self.assertFalse(ibkr.is_paper(["U1234567"]))
        self.assertFalse(ibkr.is_paper(["DU1", "U2"]))
        self.assertFalse(ibkr.is_paper([]))

    def test_live_port_never_enabled(self):
        with mock.patch.object(ibkr, "PORT", 4001), mock.patch.object(ibkr, "HAVE_LIB", True), \
                mock.patch.object(ibkr, "ON", True):
            self.assertFalse(ibkr.enabled())

    def test_shares_for(self):
        self.assertEqual(ibkr.shares_for(10, 10.0), 100)       # €10 -> $1,000
        self.assertEqual(ibkr.shares_for(15, 3.33), 450)
        self.assertEqual(ibkr.shares_for(10, 2000.0), 0)       # one share too expensive
        self.assertEqual(ibkr.shares_for(1000, 1.0), 5000)     # capped at $5,000
        self.assertEqual(ibkr.shares_for(10, 0), 0)

    def test_new_events_only_after_start_and_once(self):
        st = {"pf": {"insider": {"log": [{"t": 100, "a": "buy", "tk": "OLD", "at": 100, "p": 5}]}},
              "ib": {"since": 200}}
        log = st["pf"]["insider"]["log"]
        log += [{"t": 300, "a": "order", "tk": "AAA"}, {"t": 310, "a": "buy", "tk": "AAA", "at": 305, "p": 10, "eur": 10},
                {"t": 400, "a": "sell", "tk": "AAA", "at": 399, "p": 11}]
        ev = ibkr.new_events(st)
        self.assertEqual([(e["a"], e["tk"]) for e in ev], [("buy", "AAA"), ("sell", "AAA")])
        self.assertEqual(ibkr.new_events(st), [])

    def test_other_portfolios_not_mirrored(self):
        st = {"pf": {"strong": {"log": [{"t": 300, "a": "buy", "tk": "AAA", "at": 1}]}}, "ib": {"since": 0}}
        self.assertEqual(ibkr.new_events(st), [])

    def test_reason(self):
        self.assertIn("no KID", ibkr.reason(["Order rejected - reason:No Trading Permission ... Key Information Document (KID)<br>"]))
        self.assertEqual(ibkr.reason(["Order rejected <br>bad   thing", "Order Canceled - reason:"]), "Order rejected bad thing")

    def test_fill_line(self):
        x = ibkr.fill_line("BOT", "AAA", 100, 10.30, 10.25)
        self.assertIn("bought 100 <b>$AAA</b> at 10.3", x)
        self.assertIn("real worse 0.49%", x)
        self.assertIn("real better", ibkr.fill_line("SLD", "AAA", 100, 10.30, 10.25))


class Flow(unittest.TestCase):
    def run_job(self, job, ib):
        sent = []
        m = ibkr.Mirror(lambda t, s=True: sent.append(t) or True)
        with mock.patch.dict(sys.modules, {"ib_async": fake_lib()}), mock.patch.object(m, "_connect", lambda: ib):
            m._do(job)
        return m, sent

    def test_buy_places_market_order_and_posts_fill(self):
        ib = FakeIB()
        m, sent = self.run_job({"a": "buy", "tk": "AAA", "p": 10.25, "eur": 10}, ib)
        self.assertEqual(ib.placed, [("AAA", "BUY", 97)])
        self.assertTrue(any("bought 97" in x and "practice 10.25" in x for x in sent))
        self.assertIn("net value $1,000,123", m.line)
        self.assertTrue(ib.disconnected)

    def test_sell_uses_ibkr_position(self):
        ib = FakeIB(positions=[("AAA", 97), ("BBB", 5)])
        _, sent = self.run_job({"a": "sell", "tk": "AAA", "p": 11.0}, ib)
        self.assertEqual(ib.placed, [("AAA", "SELL", 97)])
        self.assertTrue(any("sold 97" in x for x in sent))

    def test_sell_without_position_places_nothing(self):
        ib = FakeIB(positions=[])
        _, sent = self.run_job({"a": "sell", "tk": "AAA", "p": 11.0}, ib)
        self.assertEqual(ib.placed, [])
        self.assertTrue(any("nothing to sell" in x for x in sent))

    def test_closed_market_says_waiting(self):
        ib = FakeIB(fill_px=None)
        _, sent = self.run_job({"a": "buy", "tk": "AAA", "p": 10.0, "eur": 10}, ib)
        self.assertTrue(any("next market open" in x for x in sent))

    def test_fills_posted_once(self):
        ib = FakeIB()
        m, sent = self.run_job({"a": "buy", "tk": "AAA", "p": 10.25, "eur": 10}, ib)
        n = len([x for x in sent if "bought" in x])
        with mock.patch.dict(sys.modules, {"ib_async": fake_lib()}), mock.patch.object(m, "_connect", lambda: ib):
            m._do({"a": "sync"})
        self.assertEqual(n, 1)
        self.assertEqual(len([x for x in sent if "bought" in x]), 1)

    def test_live_account_refused(self):
        m = ibkr.Mirror(lambda *a: True)
        fake = FakeIB(accounts=["U999"])
        lib = fake_lib()
        lib.IB = lambda: fake
        with mock.patch.dict(sys.modules, {"ib_async": lib}):
            with self.assertRaises(RuntimeError):
                m._connect()
        self.assertTrue(fake.disconnected)


if __name__ == "__main__":
    unittest.main()
