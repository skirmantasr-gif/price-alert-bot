"""🌡️ Market mood + practice additions (mood-filtered portfolio, worst drop)."""
import copy
import unittest

import extras
import mood
import paper
from tests.helpers import data_of, flat, ny

TUE = (2026, 11, 3)


def series(start, end, n=30):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def closes(qqq, xlp, iwm, xbi):
    """Each arg = % change over the last 20 days."""
    return {s: [100.0] * 9 + [100.0] + [100.0 * (1 + p / 100 * i / 20) for i in range(1, 21)]
            for s, p in (("QQQ", qqq), ("XLP", xlp), ("IWM", iwm), ("XBI", xbi))}


class Compute(unittest.TestCase):
    def test_ret_over_20_days(self):
        c = [100.0] * 10 + [110.0] * 21
        self.assertAlmostEqual(mood.ret(c), 0.0)
        c = [100.0] * 10 + [100.0 + i for i in range(21)]
        self.assertAlmostEqual(mood.ret(c), 20.0)
        self.assertIsNone(mood.ret([1.0] * 20))

    def test_defensive_risk_on_neutral(self):
        self.assertEqual(mood.compute(closes(-5, 1, -6, -8))["status"], "defensive")
        self.assertEqual(mood.compute(closes(5, 0, 4, 6))["status"], "risk-on")
        self.assertEqual(mood.compute(closes(1, 0, -1, 2))["status"], "neutral")
        m = mood.compute(closes(-5, 1, -6, -8))
        self.assertEqual(m["score"], -4)
        self.assertEqual([p["k"] for p in m["parts"]], ["qqq", "spread", "iwm", "xbi"])

    def test_thresholds_inclusive_and_biotech_needs_more(self):
        m = mood.compute(closes(3, 3, 3, 3.9))   # QQQ +1, spread 0, IWM +1, XBI 0 (needs 4)
        self.assertEqual(m["score"], 2)
        self.assertEqual(m["status"], "risk-on")

    def test_missing_data(self):
        self.assertIsNone(mood.compute({}))
        c = closes(-5, 0, -6, -8)
        del c["XBI"], c["XLP"]
        m = mood.compute(c)
        self.assertEqual([p["k"] for p in m["parts"]], ["qqq", "iwm"])
        self.assertEqual(m["status"], "defensive")

    def test_line(self):
        t = mood.line(mood.compute(closes(-5, 1, -6, -8)))
        self.assertIn("🔴 defensive", t)
        self.assertIn("big tech (QQQ) -5.0%", t)
        self.assertIn("growth vs staples (QQQ−XLP) -6.0 pts", t)
        self.assertNotIn("gold", t.lower())
        self.assertEqual(mood.line(None), "🌡️ Market mood: no data")


class Fetch(unittest.TestCase):
    def fake(self, pct):
        calls = []

        def get_json(url):
            calls.append(url)
            sym = url.split("/chart/")[1].split("?")[0]
            return {"chart": {"result": [{"indicators": {"quote": [{"close": closes(*pct)[sym] + [None]}]}}]}}
        return get_json, calls

    def test_cached_for_an_hour(self):
        gj, calls = self.fake((-5, 1, -6, -8))
        state = {}
        self.assertEqual(mood.get(state, gj, 1000)["status"], "defensive")
        self.assertEqual(len(calls), 4)
        mood.get(state, gj, 1000 + 3000)
        self.assertEqual(len(calls), 4)
        mood.get(state, gj, 1000 + 3700)
        self.assertEqual(len(calls), 8)
        self.assertEqual(mood.status(state), "defensive")

    def test_errors_keep_old_value_and_back_off(self):
        gj, _ = self.fake((5, 0, 4, 6))
        state = {}
        mood.get(state, gj, 1000)
        n = []

        def broken(url):
            n.append(url)
            raise OSError("down")
        self.assertEqual(mood.get(state, broken, 1000 + 4000)["status"], "risk-on")
        mood.get(state, broken, 1000 + 4100)   # tried <10 min ago: no new requests
        self.assertEqual(len(n), 4)
        self.assertIsNone(mood.get({}, broken, 5))


def sig(tk="AAA", c="medium", src="news", p0=10.0, m=None):
    s = {"tk": tk, "src": src, "d": "up", "c": c, "p0": p0}
    if m:
        s["mood"] = m
    return s


class MoodPortfolio(unittest.TestCase):
    def test_new_portfolio_is_exact_copy_of_likely(self):
        st = paper.new_state(ny(*TUE, 6))
        del st["pf"]["calm"]
        st["pf"]["likely"]["cash"] = 87.5
        st["pf"]["likely"]["pending"]["AAA"] = {"t": 1, "src": "news", "c": "medium", "alert_p": 10.0, "eur": 10.0}
        paper.add_portfolios(st, 123)
        self.assertEqual(st["pf"]["calm"], st["pf"]["likely"])
        self.assertIsNot(st["pf"]["calm"]["open"], st["pf"]["likely"]["open"])
        self.assertEqual(st["added"]["calm"], 123)
        before = copy.deepcopy(st["pf"]["calm"])
        paper.add_portfolios(st, 999)          # only once
        del st["pf"]["calm"]
        paper.on_signal(st, sig("ZZZ", c="low"))  # a signal before attach() also clones, never starts empty
        self.assertEqual(st["pf"]["calm"]["cash"], 87.5)
        st["pf"]["calm"] = before
        self.assertEqual(st["pf"]["calm"], before)

    def test_skips_buy_only_when_defensive(self):
        st = paper.new_state(ny(*TUE, 6))
        st["mood"] = "defensive"
        paper.on_signal(st, sig("AAA"))
        self.assertIn("AAA", st["pf"]["likely"]["pending"])
        self.assertNotIn("AAA", st["pf"]["calm"]["pending"])
        self.assertEqual(st["pf"]["calm"]["log"][-1]["a"], "skip")
        self.assertEqual(st["pf"]["calm"]["cash"], paper.START_EUR)
        st["mood"] = "neutral"
        paper.on_signal(st, sig("BBB"))
        self.assertIn("BBB", st["pf"]["calm"]["pending"])
        st["mood"] = None                      # unknown mood: trades normally
        paper.on_signal(st, sig("CCC"))
        self.assertIn("CCC", st["pf"]["calm"]["pending"])

    def test_no_skip_line_for_calls_likely_would_not_buy_either(self):
        st = paper.new_state(ny(*TUE, 6))
        st["mood"] = "defensive"
        paper.on_signal(st, sig("AAA", c="low"))
        paper.on_signal(st, sig("BBB", src="outlook"))
        self.assertFalse([e for e in st["pf"]["calm"]["log"] if e["a"] == "skip"])

    def test_down_call_still_sells_in_defensive_mood(self):
        st = paper.new_state(ny(*TUE, 6))
        paper.on_signal(st, sig("AAA"))
        self.assertIn("AAA", st["pf"]["calm"]["pending"])
        st["mood"] = "defensive"
        paper.on_signal(st, {"tk": "AAA", "src": "news", "d": "down", "c": "high", "p0": 10.0})
        self.assertNotIn("AAA", st["pf"]["calm"]["pending"])
        self.assertEqual(st["pf"]["calm"]["cash"], paper.START_EUR)

    def test_skip_line_text_and_statement_merging(self):
        st = paper.new_state(ny(*TUE, 6))
        st["mood"] = "defensive"
        paper.on_signal(st, sig("AAA"), now=ny(*TUE, 6))
        lines = [x for _, x in paper.trade_lines(st, 0, short=False)]
        self.assertTrue(any(x.startswith("⏭️ Skipped <b>$AAA</b> (market mood defensive)") and "🌡️" in x for x in lines))
        st2 = paper.new_state(ny(*TUE, 6))
        groups = paper.Tracker._groups(st2)
        self.assertTrue(any("likely" in g and "calm" in g for g in groups))
        st2["pf"]["strong"]["cash"] = 1.0
        self.assertIn(["likely", "outlook", "calm"], paper.Tracker._groups(st2))


class WorstDrop(unittest.TestCase):
    def test_drawdown(self):
        self.assertIsNone(paper.drawdown([]))
        self.assertIsNone(paper.drawdown([100]))
        self.assertAlmostEqual(paper.drawdown([100, 110, 99, 105]), 99 / 110 - 1)
        self.assertAlmostEqual(paper.drawdown([101, 102]), 0.0)
        self.assertAlmostEqual(paper.drawdown([95, 96]), -0.05)   # below the €100 start counts too

    def test_snapshot_records_bench_and_report_shows_drops(self):
        st = paper.new_state(ny(*TUE, 9))
        data = data_of({"SPY": flat(TUE, 500.0)})
        paper.snapshot(st, ny(*TUE, 10), data)
        paper.snapshot(st, ny(*TUE, 11), data)
        self.assertEqual(len(st["bench_eq"]), 2)
        st["pf"]["strong"]["eq"] = [100, 110, 99]
        st["mood_line"] = "🌡️ Market mood: <b>⚪ neutral</b> (+0)"
        r = paper.report(st, ny(*TUE, 16, 30), data, "t")
        self.assertIn("💪 Strong calls: <b>€100.00</b>", r)
        self.assertIn("📉 worst drop -10.0%", r)
        self.assertIn("📉 worst drop 0.0% — the number to beat", r)
        self.assertIn("🌡️ Market mood", r)
        self.assertIn("🌡️ Mood-filtered", r)
        self.assertNotIn("max drawdown", r)
        a = paper.account(st, "strong", ny(*TUE, 16, 30), data)
        self.assertIn("(±0.0%) · 📉 worst drop -10.0%", a)


class DailyCheck(unittest.TestCase):
    def test_extra_lines_added_and_errors_ignored(self):
        sent = []
        hb = extras.Heartbeat("github", lambda *a: {}, lambda t, *a: sent.append(t) or True,
                              daily_extra=lambda st: ["🌡️ Market mood: x", None])
        state = {}
        with unittest.mock.patch.object(extras, "local_now", return_value=extras.local_now().replace(hour=23, minute=59)):
            hb._daily(state)
        self.assertIn("🌡️ Market mood: x", sent[0])
        sent.clear()

        def boom(st):
            raise ValueError
        hb2 = extras.Heartbeat("github", lambda *a: {}, lambda t, *a: sent.append(t) or True, daily_extra=boom)
        with unittest.mock.patch.object(extras, "local_now", return_value=extras.local_now().replace(hour=23, minute=59)):
            hb2._daily({})
        self.assertTrue(sent and "Daily check" in sent[0])


import unittest.mock  # noqa: E402
