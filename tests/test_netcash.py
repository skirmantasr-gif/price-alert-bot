import unittest
from datetime import date, datetime

import netcash

CASH, LIAB = netcash.CASH, netcash.LIAB
SH = netcash.SHARES[0]


def fake_get(frames, tickers):
    def get(url):
        if url.endswith("company_tickers_exchange.json"):
            return {"data": tickers}
        for (concept, period), rows in frames.items():
            if url.endswith(f"{concept}/{period}.json"):
                return {"data": [{"cik": k, "val": v} for k, v in rows.items()]}
        raise IOError("404")
    return get


class Build(unittest.TestCase):
    def test_quarter(self):
        self.assertEqual(netcash.quarter(date(2026, 11, 20)), ("CY2026Q3I", "CY2026Q2I"))
        self.assertEqual(netcash.quarter(date(2026, 2, 10)), ("CY2025Q3I", "CY2025Q2I"))   # Q4 reports not in yet
        self.assertEqual(netcash.quarter(date(2026, 2, 25)), ("CY2025Q4I", "CY2025Q3I"))
        self.assertEqual(netcash.quarter(date(2026, 5, 25)), ("CY2026Q1I", "CY2025Q4I"))

    def test_build(self):
        q, prev = netcash.quarter(date(2026, 11, 20))
        frames = {
            (CASH, q): {1: 145e6, 2: 120e6, 3: 500e6, 4: 50e6, 5: 80e6},
            (LIAB, q): {1: 169e6, 2: 20e6, 3: 10e6, 4: 5e6, 5: 10e6},
            (SH, q): {1: 124e6, 2: 10e6, 3: 10e6, 4: 10e6, 5: 10e6},
            (netcash.STI[0], q): {4: 30e6},
            (netcash.STI[1], q): {4: 30e6},          # same holding tagged twice: counted once
            (CASH, prev): {6: 100e6}, (LIAB, prev): {6: 10e6}, (SH, prev): {6: 1e6},   # only older report
        }
        tickers = [[1, "Milestone", "MIST", "Nasdaq"], [2, "Cheap Bio", "CHBO", "Nasdaq"], [3, "Bank", "BNKX", "NYSE"],
                   [4, "Shell", "SHLL", "Nasdaq"], [5, "Pricey", "PRCY", "Nasdaq"], [6, "Old", "OLDQ", "NYSE"],
                   [2, "Cheap Bio warrant", "CHBOW", "Nasdaq"]]
        px = {"MIST": 0.37, "CHBO": 3.0, "BNKX": 10.0, "SHLL": 4.0, "PRCY": 20.0, "OLDQ": 30.0}
        rows = netcash.build(date(2026, 11, 20), get=fake_get(frames, tickers),
                             price_of=lambda ts: {t: px[t] for t in ts if t in px},
                             sic_of=lambda c: 6022 if c == 3 else 2834)
        got = [r["t"] for r in rows]
        self.assertNotIn("MIST", got)            # liabilities above its cash
        self.assertNotIn("BNKX", got)            # bank (SIC 6xxx)
        self.assertNotIn("PRCY", got)            # $200M value vs $70M net cash
        self.assertEqual(got, ["CHBO", "OLDQ", "SHLL"])   # 30M/100M, 30M/90M, 40M/75M
        shll = rows[2]
        self.assertAlmostEqual(shll["net"], 75e6)

    def test_message(self):
        rows = [{"t": "CHBO", "cap": 30e6, "net": 100e6, "ratio": 0.3}]
        m = netcash.message(rows, before=["OTHR"])
        self.assertIn("CHBO+", m)
        self.assertIn("30%", m)
        self.assertIn("Not advice", m)
        self.assertEqual(netcash.message([]), "")


class Schedule(unittest.TestCase):
    def run_until_done(self, scr, st):
        scr.check(st)
        if scr.thread:
            scr.thread.join(5)
        return scr.check(st)

    def test_first_build_posts_once_then_fridays(self):
        sent = []
        clock = [datetime(2026, 11, 20, 22, 0)]      # Friday
        rows = [{"t": "CHBO", "name": "x", "cap": 30e6, "net": 100e6, "ratio": 0.3}]
        scr = netcash.NetCashScreen(lambda t, s: sent.append(s) or True, build_fn=lambda: rows, now=lambda: clock[0])
        st = {}
        self.run_until_done(scr, st)
        self.assertEqual(sent, [True])               # first list right away, quiet
        self.run_until_done(scr, st)
        self.assertEqual(len(sent), 1)               # once a week
        clock[0] = datetime(2026, 11, 25, 18, 0)     # Wednesday: no rebuild, no post
        self.run_until_done(scr, st)
        self.assertEqual(len(sent), 1)
        clock[0] = datetime(2026, 11, 27, 16, 0)     # Friday before report time: rebuild, wait
        self.run_until_done(scr, st)
        self.assertEqual(len(sent), 1)
        self.assertEqual(st["_netcash"]["built"], "2026-11-27")
        clock[0] = datetime(2026, 11, 27, 17, 5)
        self.run_until_done(scr, st)
        self.assertEqual(len(sent), 2)

    def test_failed_build_retries_later(self):
        calls = []

        def bad():
            calls.append(1)
            raise IOError("x")
        clock = [datetime(2026, 11, 20, 22, 0)]
        scr = netcash.NetCashScreen(lambda t, s: True, build_fn=bad, now=lambda: clock[0])
        st = {}
        self.run_until_done(scr, st)
        self.run_until_done(scr, st)
        self.assertEqual(len(calls), 1)              # not hammering SEC: next try after 6 hours
        st["_netcash"]["tried"] -= 7 * 3600
        self.run_until_done(scr, st)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
