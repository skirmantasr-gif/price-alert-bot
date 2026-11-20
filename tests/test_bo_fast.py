"""⚡ Alpaca in the buyout watch (2026-11-20): candidate news every minute, early 📣 scans, rumours."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import buyout

NOW = datetime.now(timezone.utc)


def item(i, title, syms, hours=1):
    return {"id": i, "title": title, "desc": "", "link": f"https://x/{i}", "date": NOW - timedelta(hours=hours),
            "symbols": syms, "source": "Benzinga"}


class FakeAlpaca:
    def __init__(self, items):
        self.items = items

    def recent(self):
        return self.items


def watch(items):
    sent = []
    w = buyout.BuyoutWatch(lambda text, silent=None: sent.append(text) or True)
    return w, sent, mock.patch.dict("sys.modules", {"alpaca": FakeAlpaca(items)})


class AlpacaHeads(unittest.TestCase):
    def test_convert_and_age_cut(self):
        with mock.patch.dict("sys.modules", {"alpaca": FakeAlpaca([item(1, "New", ["ZNTX"]),
                                                                    item(2, "Old", ["ZNTX"], hours=80)])}):
            heads = buyout.alpaca_heads(48)
        self.assertEqual([h["title"] for h in heads], ["New"])
        self.assertEqual(heads[0]["symbols"], ["ZNTX"])

    def test_no_alpaca_no_heads(self):
        with mock.patch.dict("sys.modules", {"alpaca": None}):
            self.assertEqual(buyout.alpaca_heads(), [])

    def test_takeoverish(self):
        self.assertTrue(buyout.takeoverish("Zentrox explores strategic alternatives including a sale"))
        self.assertTrue(buyout.takeoverish("Bon Secours Reportedly Nears $6 Billion Acquisition Talks With Surgery Partners"))
        self.assertFalse(buyout.takeoverish("Zentrox reports third-quarter results"))
        self.assertFalse(buyout.takeoverish("SHAREHOLDER ALERT: law firm investigates Zentrox takeover"))


class FastCandidates(unittest.TestCase):
    BO = lambda self: {"cands": {"ZNTX": {"name": "Zentrox, Inc.", "since": "2026-11-01", "price": 10}},
                       "init": ["news:ZNTX", "sec:ZNTX"], "seen": {}}

    def test_first_look_silent_then_new_headline_posted(self):
        bo = self.BO()
        w, sent, p = watch([item(1, "Zentrox Inc. receives takeover approach from a rival", ["ZNTX"])])
        with p:
            w._fast(bo)
        self.assertEqual(sent, [])                       # backlog of a new source: learned silently
        self.assertIn("alp:ZNTX", bo["init"])
        w2, sent2, p2 = watch([item(1, "Zentrox Inc. receives takeover approach from a rival", ["ZNTX"]),
                               item(2, "Zentrox Inc. confirms buyout talks with Acmeco", ["ZNTX"])])
        with p2:
            self.assertTrue(w2._fast(bo))
        self.assertEqual(len(sent2), 1)
        self.assertIn("🎯 <b>$ZNTX</b> takeover-type news", sent2[0])
        self.assertIn("confirms buyout talks", sent2[0])

    def test_other_symbols_ignored(self):
        bo = self.BO()
        bo["init"].append("alp:ZNTX")
        w, sent, p = watch([item(3, "Acmeco agrees takeover of Widgets", ["ACMO"])])
        with p:
            w._fast(bo)
        self.assertEqual(sent, [])

    def test_first_run_marks_backlog_as_seen_for_signals(self):
        bo = {}
        w, _, p = watch([item(4, "Qwerty explores a sale of the company", ["QWRT"]),
                         item(5, "Qwerty quarterly results", ["QWRT"])])
        with p:
            w._fast(bo)
        self.assertTrue(bo["alp_init"])
        self.assertEqual(len(bo["sigseen"]), 1)          # only the takeover-type headline
        self.assertIn("Qwerty explores a sale of the company", w.rumours)


class EarlySignals(unittest.TestCase):
    def test_new_takeover_headline_starts_alpaca_only_scan(self):
        bo = {"sig_init": True, "alp_init": True, "sigseen": {}, "report_day": "2099-01-01", "study_v": buyout.STUDY_V}
        w, _, p = watch([item(6, "Zentrox explores strategic alternatives", ["ZNTX"])])
        w.last_sig = buyout.time.monotonic()             # the hourly scan just ran
        started = []
        with p, mock.patch.object(buyout, "SIGNALS_ON", True), \
                mock.patch.object(buyout.BuyoutWatch, "_start_signals", lambda self, bo, google_too: started.append(google_too)):
            w.check({"_bo": bo})
        self.assertEqual(started, [False])

    def test_nothing_new_no_scan(self):
        bo = {"sig_init": True, "alp_init": True, "report_day": "2099-01-01", "study_v": buyout.STUDY_V,
              "sigseen": {buyout._hash("sig:zentrox explores strategic alternatives"): "2026-11-20"}}
        w, _, p = watch([item(6, "Zentrox explores strategic alternatives", ["ZNTX"])])
        w.last_sig = buyout.time.monotonic()
        started = []
        with p, mock.patch.object(buyout, "SIGNALS_ON", True), \
                mock.patch.object(buyout.BuyoutWatch, "_start_signals", lambda self, bo, google_too: started.append(google_too)):
            w.check({"_bo": bo})
        self.assertEqual(started, [])


class Rumours(unittest.TestCase):
    def run_job(self, score):
        heads = [item(7, "Acmeco weighing takeover bid for Zentrox, people familiar say", ["ZNTX"])]
        answer = {"items": [{"id": 0, "company": "Zentrox", "ticker": "ZNTX", "type": "rumour"}]}
        client = mock.Mock()
        client.messages.create.return_value = mock.Mock(stop_reason="end_turn",
                                                        content=[mock.Mock(type="text", text=json.dumps(answer))])
        client.beta.messages.create.return_value = client.messages.create.return_value
        row = {"t": "ZNTX", "score": score, "hits": [], "name": "Zentrox, Inc.", "price": 10.0, "mcap": 800e6,
               "off_high": -0.4, "avg_vol": 1e5}
        with mock.patch.dict("sys.modules", {"anthropic": mock.Mock(Anthropic=lambda **k: client),
                                             "alpaca": FakeAlpaca(heads)}), \
                mock.patch.object(buyout.impact, "ai_enabled", lambda: True), \
                mock.patch.object(buyout, "yahoo_find", lambda c, h: ("ZNTX", "Zentrox, Inc.")), \
                mock.patch.object(buyout, "score", lambda t: row), \
                mock.patch.object(buyout.BuyoutWatch, "_old_news", staticmethod(lambda *a: "")):
            w = buyout.BuyoutWatch(lambda *a: True)
            return w._signals_job(set(), google_too=False)

    def test_rumour_kept_for_fingerprint_stock(self):
        res = self.run_job(buyout.MIN_SCORE - 1)
        self.assertEqual([x["t"] for x in res["hits"]], ["ZNTX"])

    def test_rumour_dropped_for_poor_fit(self):
        self.assertEqual(self.run_job(1)["hits"], [])

    def test_rumour_post_wording_and_quiet(self):
        sent = []
        w = buyout.BuyoutWatch(lambda text, silent=None: sent.append((text, silent)) or True)
        res = {"seen": [], "n": 1, "hits": [{"t": "ZNTX", "name": "Zentrox, Inc.", "type": "rumour", "title": "T",
                                             "source": "Benzinga", "link": "https://x", "date": None, "row": None}]}
        with mock.patch.dict("sys.modules", {"context": mock.Mock(lines=lambda *a, **k: [])}):
            w._finish_signals({"sig_init": True}, res, first=False)
        self.assertIn("TAKEOVER RUMOUR", sent[0][0])
        self.assertTrue(sent[0][1])


if __name__ == "__main__":
    unittest.main()
