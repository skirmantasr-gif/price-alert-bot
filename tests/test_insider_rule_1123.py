"""👥 2026-11-23: '2 insiders, stock over $5' clusters are no longer bought (no edge in the backtest)."""
import unittest

import paper
from tests.helpers import ny

TUE = (2026, 11, 3)


def sig(tk, n, p0):
    return {"tk": tk, "src": "insider", "d": "up", "c": "low", "p0": p0, "n": n}


class InsiderRule(unittest.TestCase):
    def test_two_insiders_over_five_dollars_skipped(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("BIG2", 2, 45.0))
        self.assertNotIn("BIG2", st["pf"]["insider"]["pending"])
        self.assertAlmostEqual(st["pf"]["insider"]["cash"], paper.START_EUR)

    def test_three_insiders_or_cheap_stock_still_bought(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("BIG3", 3, 45.0))
        paper.on_signal(st, sig("CHEAP2", 2, 3.5))
        paper.on_signal(st, {"tk": "OLD", "src": "insider", "d": "up", "c": "low", "p0": 45.0})  # no count
        self.assertEqual(set(st["pf"]["insider"]["pending"]), {"BIG3", "CHEAP2", "OLD"})

    def test_cluster_growing_to_three_is_bought_then(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("GROW", 2, 20.0))
        paper.on_signal(st, sig("GROW", 3, 20.5))
        self.assertIn("GROW", st["pf"]["insider"]["pending"])

    def test_learn_record_passes_count(self):
        import learn
        st = {}
        seen = []
        orig = learn.paper
        learn.paper = type("P", (), {"record": staticmethod(lambda e: seen.append(e))})
        try:
            learn.record(st, "insider", "AAA", "up", "low", "rules", "2 insiders bought $1M", 12.0, extra={"n": 2})
        finally:
            learn.paper = orig
        self.assertEqual(seen[-1]["n"], 2)
        self.assertTrue(paper.insider_no_edge(seen[-1]))


if __name__ == "__main__":
    unittest.main()
