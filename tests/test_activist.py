import os
import unittest
from datetime import datetime, timezone

import activist
import news

FIX = open(os.path.join(os.path.dirname(__file__), "fixture_13d_saba.xml")).read()


class Parse(unittest.TestCase):
    def test_saba_dxyz(self):
        d = activist.parse(FIX)
        self.assertEqual(d["filers"][0], "Saba Capital Management, L.P.")
        self.assertIn("Boaz R. Weinstein", d["filers"])
        self.assertAlmostEqual(d["pct"], 7.58)
        self.assertEqual(d["paid"], 96227048)
        self.assertEqual(d["issuer"], "Destiny Tech100 Inc.")
        self.assertIn("undervalued", d["purpose"])

    def test_known(self):
        self.assertEqual(activist.known(["Saba Capital Management, L.P."]), "Saba Capital")
        self.assertEqual(activist.known(["Boaz R. Weinstein"]), "Saba Capital")
        self.assertEqual(activist.known(["Concentra Biosciences, LLC", "Tang Capital Partners"]), "Tang Capital / Concentra")
        self.assertEqual(activist.known(["Kaye Jack Harry"]), "")
        self.assertEqual(activist.known(["Mithaq Capital SPC"]), "")

    def test_keywords(self):
        kw = activist.keywords(activist.parse(FIX)["purpose"])
        for k in ("buyback", "tender offer", "liquidation", "board seats", "proxy fight", "new manager"):
            self.assertIn(k, kw)


class Details(unittest.TestCase):
    def setUp(self):
        activist._cache.clear()

    def test_details_and_lines(self):
        it = {"form": "SCHEDULE 13D", "acc": "0001062993-26-006212", "link": "https://x/primary_doc.xml"}
        d = activist.details(it, ask=lambda p, i: {"wants": "a tender or liquidation to close the NAV discount",
                                                    "concrete": False}, fetch=lambda u: FIX)
        self.assertEqual(d["who"], "Saba Capital")
        text = "\n".join(activist.lines(d))
        self.assertIn("🦈", text)
        self.assertIn("Known activist: Saba Capital", text)
        self.assertIn("7.58% stake", text)
        self.assertIn("paid ~$96M", text)
        self.assertIn("standard", text)          # not concrete

    def test_ai_failure_falls_back_to_keywords(self):
        def boom(p, i):
            raise RuntimeError("x")
        it = {"form": "SCHEDULE 13D", "acc": "a1", "link": "https://x/primary_doc.xml"}
        d = activist.details(it, ask=boom, fetch=lambda u: FIX)
        self.assertTrue(any("Mentions:" in x and "liquidation" in x for x in activist.lines(d)))

    def test_not_13d_or_html(self):
        self.assertIsNone(activist.details({"form": "8-K", "link": "https://x/a.xml"}))
        self.assertIsNone(activist.details({"form": "SC 13D", "acc": "b", "link": "https://x/a.htm"}))

    def test_news_format_includes_activist(self):
        it = {"kind": "sec", "ticker": "DXYZ", "form": "SCHEDULE 13D", "items": [], "link": "https://x/primary_doc.xml",
              "date": datetime.now(timezone.utc), "desc": "",
              "activist": {"filers": ["Saba Capital Management, L.P."], "who": "Saba Capital", "pct": 7.58,
                           "paid": 96e6, "wants": "tender offer", "concrete": True, "kw": []}}
        nw = news.NewsWatcher.__new__(news.NewsWatcher)
        msg = nw._format(it)
        self.assertTrue(msg.startswith("🚨"))
        self.assertIn("Known activist: Saba Capital", msg)
        self.assertIn("a concrete demand", msg)


class Watch(unittest.TestCase):
    HITS = [("0001-26-1:primary_doc.xml", "SCHEDULE 13D",
             ["Destiny Tech100 Inc.  (DXYZ)  (CIK 0001843974)", "Saba Capital Management, L.P.  (CIK 0001510281)"], "2026-11-20"),
            ("0001-26-2:primary_doc.xml", "SCHEDULE 13D",
             ["Foo Bio Inc.  (FOOB)  (CIK 0000000123)", "Tang Capital Partners LP  (CIK 0000000999)"], "2026-11-20"),
            ("0001-26-3:primary_doc.xml", "SCHEDULE 13D/A",
             ["Bar Inc.  (BARR)  (CIK 0000000124)", "Some Person  (CIK 0000000998)"], "2026-11-20")]

    def test_first_run_silent_then_posts_known_non_watchlist(self):
        sent = []
        hits = list(self.HITS[:1])
        w = activist.ActivistWatch(lambda t, s: sent.append((t, s)) or True, ["DXYZ"], find=lambda: hits,
                                   info=lambda it: {"filers": ["Tang Capital Partners LP"], "who": "", "pct": 9.9})
        st = {}
        w.check(st)
        self.assertEqual(sent, [])
        self.assertTrue(st["_activist"]["init"])
        hits[:] = self.HITS
        w.last = float("-inf")
        w.check(st)
        self.assertEqual(len(sent), 1)           # DXYZ = watchlist (news.py), BARR = unknown filer
        text, silent = sent[0]
        self.assertIn("$FOOB", text)
        self.assertIn("Tang Capital / Concentra", text)
        self.assertIn("9.90% stake", text)
        self.assertTrue(silent)
        w.last = float("-inf")
        w.check(st)
        self.assertEqual(len(sent), 1)           # no repeats

    def test_subject(self):
        self.assertEqual(activist.subject(self.HITS[0][2]), ("DXYZ", "Destiny Tech100 Inc.", 1843974))
        self.assertIsNone(activist.subject(["Person  (CIK 0000000001)"]))


if __name__ == "__main__":
    unittest.main()
