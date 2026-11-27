"""2026-11-27: PYPD approved Nov 25 ("PolyPid Announces FDA Approval of BACFENSSI®") but the bot missed it and
posted 📈 market-implied odds for the Nov 28 date from a post-approval snapshot."""
import unittest
from datetime import date, datetime

import catalysts
import marketodds

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


if __name__ == "__main__":
    unittest.main()
