"""2026-11-27: PYPD approved Nov 25 ("PolyPid Announces FDA Approval of BACFENSSI®") but the bot missed it and
posted 📈 market-implied odds for the Nov 28 date from a post-approval snapshot."""
import unittest
from datetime import date, datetime

import catalysts
import marketodds
import options

TITLE = ("PolyPid Announces FDA Approval of BACFENSSI® (D-PLEX₁₀₀) to Reduce the Occurrence of Surgical Site "
         "Infections in Patients Undergoing Colorectal Surgery")


def _state():
    return {"_fdacal": {"ev": {"PYPD:2026-11-28": {"t": "PYPD", "name": "PolyPid Ltd.", "date": "2026-11-28"}},
                        "docs": []}}


class AnnouncesApproval(unittest.TestCase):
    def test_marks_decided_approved(self):
        s = _state()
        self.assertTrue(catalysts.mark_decided(s, "PYPD", TITLE, date(2026, 11, 25)))
        d = s["_fdacal"]["decided"]["PYPD:2026-11-28"]
        self.assertTrue(d["approved"])
        self.assertEqual(d["at"], "2026-11-25")

    def test_other_phrasings(self):
        for t in ("XYZ Announced U.S. FDA Approval of Drug", "XYZ Achieves FDA Accelerated Approval for Drug",
                  "XYZ Announces Receipt of FDA Approval"):
            self.assertTrue(catalysts.DECIDED_RE.search(t), t)
        self.assertFalse(catalysts.DECIDED_RE.search("XYZ Announces FDA Acceptance of NDA"))
        self.assertFalse(catalysts.DECIDED_RE.search("XYZ announces plans to seek FDA approval"))


class NoOddsAfterDecision(unittest.TestCase):
    def test_scored_only_with_pre_decision_snapshot(self):
        s = _state()
        catalysts.mark_decided(s, "PYPD", TITLE, date(2026, 11, 25))
        snap = lambda px: {"px": px, "exps": [["2026-12-18", px, 38.0]]}
        s["_opth"] = {"PYPD:2026-11-28": {"t": "PYPD", "event": "2026-11-28", "kind": "fda",
                                          "snaps": {"2026-11-24": snap(4.0), "2026-11-26": snap(5.91)}}}
        sent = []
        m = marketodds.MarketOdds(lambda txt, quiet: sent.append(txt), ["PYPD"], cash=lambda t: 1.0, preds=lambda: [],
                                  now=lambda: datetime(2026, 11, 27, 12), cap=lambda t: 3e8)
        self.assertTrue(m.check(s))
        r = s["_mkt"]["PYPD:2026-11-28"]
        self.assertEqual(r["made"], "2026-11-24")        # snapshot from before the approval
        self.assertTrue(r["outcome"])
        self.assertFalse(any("Market-implied odds" in x for x in sent))
        self.assertTrue(any("Market odds scored" in x for x in sent))


class StaleOptionsFile(unittest.TestCase):
    CHAIN = {"timestamp": "2026-11-23 15:31:28",
             "data": {"current_price": 5.91, "iv30": 145.0,
                      "options": [{"option": "PYPD261218C00005000", "open_interest": 10, "volume": 0, "bid": 1, "ask": 2},
                                  {"option": "PYPD261218P00005000", "open_interest": 10, "volume": 0, "bid": 1, "ask": 2}]}}

    def _load(self, min_day):
        import json
        from unittest import mock
        with mock.patch.object(options, "_get", lambda url, timeout=20: json.dumps(self.CHAIN).encode()):
            return options.load("PYPD", min_day)

    def test_old_file_rejected(self):
        with self.assertRaises(options.Stale):
            self._load(date(2026, 11, 24))
        self.assertEqual(self._load(date(2026, 11, 23))[0], 5.91)
        self.assertEqual(self._load(None)[0], 5.91)

    def test_prev_trading_day(self):
        self.assertEqual(options.prev_trading_day(date(2026, 11, 27)), date(2026, 11, 25))   # skips Thanksgiving
        self.assertEqual(options.prev_trading_day(date(2026, 11, 30)), date(2026, 11, 27))   # skips the weekend

    def test_history_retries_after_stale(self):
        from zoneinfo import ZoneInfo
        calls = []

        def fetch(sym):
            calls.append(sym)
            if len(calls) == 1:
                raise options.Stale("2026-11-23T15:31:28")
            return 5.0, 100, [(date(2026, 12, 18), "C", 5.0, 10, 1, 1, 2), (date(2026, 12, 18), "P", 5.0, 10, 1, 1, 2)]
        now = datetime(2026, 11, 24, 16, 0, tzinfo=ZoneInfo("America/New_York"))
        w = options.HistoryWatch([], fetch=fetch, now=lambda: now)
        s = {"_fdacal": {"ev": {"PYPD:2026-11-28": {"t": "PYPD", "date": "2026-11-28"}}}}
        w.check(s)
        self.assertEqual(s["_opth"]["PYPD:2026-11-28"]["snaps"], {})      # nothing saved from the old file
        self.assertFalse(w.check(s))                                         # waits 30 min
        w.retry_at = float("-inf")
        self.assertTrue(w.check(s))
        self.assertIn("2026-11-24", s["_opth"]["PYPD:2026-11-28"]["snaps"])


if __name__ == "__main__":
    unittest.main()
