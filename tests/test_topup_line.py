import time
import unittest

import paper


class TopupLine(unittest.TestCase):
    def test_report_with_topup_entry(self):
        now = time.time()
        st = {"pf": {k: paper._pf() for k in paper.PORTFOLIOS}}
        pf = st["pf"]["insider"]
        pf["topup"] = 10.0
        pf["log"] += [{"t": now - 60, "a": "topup", "tk": "CRMD", "eur": 4.7},
                      {"t": now - 50, "a": "order", "tk": "CRMD", "src": "insider", "c": "high", "eur": 10.0}]
        lines = [t for _, t in paper.trade_lines(st, now - 3600)]
        self.assertTrue(any("Added €5 play money" in t and "CRMD" in t for t in lines))

    def test_unknown_entry_does_not_crash(self):
        self.assertIn("XYZ", paper._line({"a": "weird", "tk": "XYZ", "t": 1}, ""))


if __name__ == "__main__":
    unittest.main()
