"""2026-11-22 check, part 2: 👥 top-up spam / wrong returns, BO obituary + KMT noise, IBKR fill posted twice."""
import unittest

import buyout
import paper


def topped(eq, eqb=None, topup=125.0):
    pf = paper._pf()
    pf["eq"], pf["topup"] = list(eq), topup
    if eqb is not None:
        pf["eqb"] = list(eqb)
    return pf


class Curve(unittest.TestCase):
    def test_no_topup_unchanged(self):
        pf = paper._pf()
        pf["eq"] = [100, 101, 99]
        self.assertEqual(paper.curve(pf), [100, 101, 99])

    def test_topups_removed(self):
        # €100 → +€10 → +€10: value follows the money in, no real gain or loss
        pf = topped([100, 110, 120], [100, 110, 120], topup=20)
        self.assertEqual(paper.curve(pf), [100, 100, 100])
        # a real 10% loss after the top-ups shows as −10%, not as "+€8"
        pf = topped([100, 110, 108], [100, 110, 120], topup=20)
        self.assertAlmostEqual(paper.curve(pf)[-1], 90.0)

    def test_old_points_without_money_in(self):
        # points saved before "eqb" existed: no fake top from the top-ups (€226 is not a +127% top)
        pf = topped([100, 150, 226.9, 220], [235, 235], topup=135)
        c = paper.curve(pf)
        self.assertEqual(c[:2], [100.0, 100.0])
        self.assertLess(max(c), 101)

    def test_snapshot_saves_money_in(self):
        st = {"pf": {"insider": topped([100], topup=10)}, "since": 0}
        st["pf"]["insider"]["cash"] = 110.0
        paper.snapshot(st, 0, lambda tk: None)
        self.assertEqual(st["pf"]["insider"]["eqb"], [110.0])


class TopupPosts(unittest.TestCase):
    def test_not_posted_instantly(self):
        sent = []
        pt = paper.Tracker.__new__(paper.Tracker)
        pt.send = lambda text, silent: sent.append(text) or True
        pt.data = lambda tk: None
        st = {"since": 0, "pf": {k: paper._pf() for k in paper.PORTFOLIOS}}
        st["pf"]["insider"]["log"].append({"t": 100, "a": "topup", "tk": "UPST", "eur": 10.0})
        st["pf"]["insider"]["topup"] = 10.0
        self.assertFalse(pt._trades_now(st, 200))
        self.assertEqual(sent, [])


class IbkrPostedSaved(unittest.TestCase):
    def test_posted_written_to_state_right_away(self):
        import ibkr

        class Ex:
            def __init__(self, i):
                self.execId, self.side, self.orderId, self.shares, self.price = f"e{i}", "BOT", 7, 9, 54.54

        class Fill:
            def __init__(self, i):
                self.execution = Ex(i)
                self.contract = type("C", (), {"symbol": "SEI"})()

        class IB:
            def reqExecutions(self, f):
                return [Fill(1), Fill(2)]

        sent = []
        m = ibkr.Mirror(lambda text, silent=None: sent.append(text) or True)
        st = {}
        m.ib_state = st.setdefault("ib", {})
        import sys, types
        from unittest import mock
        stub = mock.patch.dict(sys.modules, {"ib_async": types.SimpleNamespace(ExecutionFilter=lambda: None)})
        stub.start()
        self.addCleanup(stub.stop)
        m._post_fills(IB())
        self.assertEqual(len(sent), 1)
        self.assertEqual(st["ib"]["posted"], ["e1", "e2"])      # in the state before any sync / restart
        m2 = ibkr.Mirror(lambda text, silent=None: sent.append(text) or True)
        m2.posted = set(st["ib"]["posted"])                      # what a restart loads
        m2._post_fills(IB())
        self.assertEqual(len(sent), 1)


class BoNoise(unittest.TestCase):
    def test_obituary_not_major(self):
        self.assertFalse(buyout.major("Obituary: Thomas Harrison, Former CEO of Omnicom's DAS Group", "OMC",
                                      "Omnicom Group Inc."))
        self.assertTrue(buyout.major("Omnicom names new CFO", "OMC", "Omnicom Group Inc."))

    def test_kmt_party_not_kennametal(self):
        self.assertFalse(buyout.major("Legislature approves KMT’s NT$20,000 payout bill", "KMT", "Kennametal Inc."))


if __name__ == "__main__":
    unittest.main()
