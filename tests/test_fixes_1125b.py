"""2026-11-25 Telegram proofread, part 2: buyout post-mortem only for targets, BO dedupe + late label,
no-ticker insider clusters, USAspending one-word names."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import buyout
import contracts
import insiders
import movers


class PostMortemTarget(unittest.TestCase):
    def test_roles(self):
        self.assertTrue(movers.is_buyout_target({"cause": "buyout", "deal_role": "target"}))
        self.assertFalse(movers.is_buyout_target({"cause": "buyout", "deal_role": "acquirer"}))
        self.assertFalse(movers.is_buyout_target({"cause": "buyout", "deal_role": "other"}))
        self.assertTrue(movers.is_buyout_target({"cause": "buyout"}))          # older answers
        self.assertFalse(movers.is_buyout_target({"cause": "earnings", "deal_role": ""}))
        self.assertFalse(movers.is_buyout_target(None))

    def test_schema_requires_role(self):
        item = movers.SCHEMA["properties"]["items"]["items"]
        self.assertIn("deal_role", item["required"])


def head(title, hours_ago=0.2):
    return {"title": title, "desc": "", "link": "https://example.com/x", "source": "Wire",
            "date": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


class BoNews(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.w = buyout.BuyoutWatch(lambda t, q: self.sent.append(t))
        self.seen = set()

    def new(self, key, src):
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def post(self, bo, heads):
        with mock.patch.object(buyout.extras, "remember", lambda *a: None):
            self.w._post_heads(bo, "ZNTX", "Zentex Media", heads, self.new)

    def test_amounts_match(self):
        self.assertTrue(buyout.same_event(["$37m", "island", "mackinac"], ["$37", "island", "mackinac"]))

    def test_takeover_repeat_skipped(self):
        bo = {}
        self.post(bo, [head("Zentex Media rejects Mackinac Island ferries owner's $37M takeover bid")])
        self.post(bo, [head("Zentex Media rejects Mackinac Island ferry owner's $37M takeover bid")])
        self.assertEqual(len(self.sent), 1)

    def test_late_label(self):
        self.post({}, [head("Zentex Media rejects $37M takeover bid from rival", hours_ago=20)])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("⏰ Published 20h ago", self.sent[0])


class NoTicker(unittest.TestCase):
    def test_real_ticker(self):
        for t in ("NONE", "N/A", "", "NA"):
            self.assertFalse(insiders.real_ticker(t))
        for t in ("ZNTX", "BRK.B", "AAA"):
            self.assertTrue(insiders.real_ticker(t))


class GovNames(unittest.TestCase):
    def test_one_word_needs_whole_name(self):
        self.assertFalse(contracts._gov_name_match("EMERGENT LLC", "Emergent BioSolutions Inc."))
        self.assertTrue(contracts._gov_name_match("BOEING COMPANY, THE", "The Boeing Company"))
        self.assertTrue(contracts._gov_name_match("LOCKHEED MARTIN CORP", "Lockheed Martin Corporation"))


if __name__ == "__main__":
    unittest.main()
