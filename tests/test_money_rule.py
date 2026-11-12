"""💶 Real-money rule line in the daily practice report (owner's rule, 2026-11-12)."""
import unittest
from unittest import mock

import paper


def pf(n_closed, vs, eq=None):
    return {"closed": [{"ret": 0.01, "vs": vs} for _ in range(n_closed)], "open": {}, "pending": {}, "eq": eq or []}


def line(pfs, values, bench=100.0):
    st = {"pf": pfs}
    with mock.patch.object(paper, "_bench", lambda *a: bench), \
            mock.patch.object(paper, "value", lambda p, now, data: values[id(p)]):
        return paper.money_rule(st, 0, None)


class MoneyRule(unittest.TestCase):
    def keys(self):
        return list(paper.PORTFOLIOS)

    def test_not_yet_with_few_trades(self):
        a = pf(5, 0.02)
        t = line({self.keys()[0]: a}, {id(a): 103.2})
        self.assertIn("❌ not yet", t)
        self.assertIn("5/30 closed", t)

    def test_passes_with_30_trades_ahead_of_spy(self):
        a = pf(31, 0.01, eq=[101, 104, 106])
        t = line({self.keys()[0]: a}, {id(a): 107.0})
        self.assertIn("✅", t)
        self.assertIn("31 trades", t)

    def test_behind_spy_does_not_pass(self):
        a = pf(40, -0.01)
        self.assertIn("❌", line({self.keys()[0]: a}, {id(a): 99.0}, bench=104.0))

    def test_paused_after_15pct_drop_from_top(self):
        a = pf(35, 0.01, eq=[110, 130, 140])
        t = line({self.keys()[0]: a}, {id(a): 115.0}, bench=105.0)
        self.assertIn("⏸️", t)
        self.assertIn("-18% from its top", t)


if __name__ == "__main__":
    unittest.main()
