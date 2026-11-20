"""💊📰 news for FDA-calendar stocks off the watchlist (2026-11-20, after SVRA)."""
import unittest
from datetime import datetime, timedelta, timezone

import catalysts as c

NOW = datetime(2026, 11, 20, 16, 0)
RECENT = datetime.now(timezone.utc) - timedelta(hours=1)


def state():
    return {"_fdacal": {"ev": {"SVRA:2026-11-22": {"t": "SVRA", "date": "2026-11-22", "name": "Savara Inc"},
                               "CAPR:2026-11-22": {"t": "CAPR", "date": "2026-11-22", "name": "Capricor"},
                               "FAR:2027-06-01": {"t": "FAR", "date": "2027-06-01", "name": "Far Away"}}}}


class FdaNews(unittest.TestCase):
    def make(self, heads, filings=()):
        sent = []
        w = c.FdaNewsWatch(lambda t, s: sent.append((t, s)), ["CAPR"],
                           headlines=lambda t: heads.get(t, []), filings_of=lambda t: list(filings),
                           summarize=lambda f, n: {"summary": "FDA will not meet the date.", "dilution": "no", "why": ""})
        return w, sent

    def test_only_off_watchlist_and_in_window(self):
        w, _ = self.make({})
        self.assertEqual(sorted(w.tickers(state(), NOW.date())), ["SVRA"])

    def test_first_look_silent_then_new_fda_headline_loud(self):
        heads = {"SVRA": [{"title": "Savara presents data at CHEST", "link": "https://x/1", "date": RECENT},
                          {"title": "Savara PDUFA date approaching", "link": "https://x/2", "date": RECENT}]}
        st = state()
        w, sent = self.make(heads)
        w.check(st, now=NOW)
        self.assertEqual(sent, [])
        heads["SVRA"].append({"title": "Savara Informed That FDA Will Not Take Action on the Molgramostim BLA by PDUFA Date",
                              "link": "https://x/3", "date": RECENT})
        w.check(st, now=NOW)
        self.assertEqual(len(sent), 1)
        text, silent = sent[0]
        self.assertFalse(silent)
        self.assertIn("💊🚨 <b>$SVRA</b> FDA news (FDA date Sun Nov 22)", text)
        w.check(st, now=NOW)
        self.assertEqual(len(sent), 1)                                    # once

    def test_non_fda_headline_ignored(self):
        heads = {"SVRA": []}
        st = state()
        w, sent = self.make(heads)
        w.check(st, now=NOW)
        heads["SVRA"].append({"title": "Savara to present at investor conference", "link": "https://x", "date": RECENT})
        w.check(st, now=NOW)
        self.assertEqual(sent, [])

    def test_new_8k_posted_with_summary(self):
        fl = [{"acc": "a1", "form": "8-K", "link": "https://sec/a1"}]
        st = state()
        w, sent = self.make({}, fl)
        w.check(st, now=NOW)
        self.assertEqual(sent, [])
        fl.insert(0, {"acc": "a2", "form": "8-K", "link": "https://sec/a2"})
        w.check(st, now=NOW)
        self.assertEqual(len(sent), 1)
        self.assertIn("💊📄 <b>$SVRA</b> SEC filing 8-K", sent[0][0])
        self.assertIn("🤖 FDA will not meet the date.", sent[0][0])
        self.assertTrue(sent[0][1])


if __name__ == "__main__":
    unittest.main()
