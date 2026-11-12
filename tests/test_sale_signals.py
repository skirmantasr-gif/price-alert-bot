"""📣 Public sale signals (buyout channel): hourly all-US scan. No network: Google, AI and Yahoo are mocked."""
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

import buyout

NOW = datetime.now(timezone.utc)


def head(title):
    return {"title": title, "source": "Wire", "link": "https://x/" + str(abs(hash(title))), "date": NOW, "desc": ""}


HEADS = [head("Zentrox Inc. launches strategic review including a potential sale"),
         head("Judge rejects bid to block state rule"),
         head("Acmeco rejects takeover offer from Bigco"),
         head("Rumour: Qwerty weighing sale, people familiar say"),
         head("Indra Group explores sale of US unit")]
ANSWER = {"items": [
    {"id": 0, "company": "Zentrox Inc.", "ticker": "ZNTX", "type": "strategic_review"},
    {"id": 1, "company": "", "ticker": "", "type": "other"},
    {"id": 2, "company": "Acmeco", "ticker": "ACMO", "type": "offer_rejected"},
    {"id": 3, "company": "Qwerty", "ticker": "QWRT", "type": "rumour"},
    {"id": 4, "company": "Indra Group", "ticker": "IDR", "type": "strategic_review"},
    {"id": 99, "company": "Ghost", "ticker": "GHST", "type": "strategic_review"},     # id out of range: ignored
]}
NAMES = {"ZNTX": "Zentrox, Inc.", "ACMO": "Acmeco Holdings Corp", "QWRT": "Qwerty Inc", "IDR": "Idaho Strategic Resources"}
CAPS = {"ZNTX": 800e6, "ACMO": 45e9}   # Acmeco too big for a buyout signal


def row(t):
    return {"t": t, "score": 4, "hits": [], "name": NAMES[t], "price": 10.0, "mcap": CAPS.get(t, 500e6),
            "off_high": -0.4, "avg_vol": 1e5}


def run_job(seen=()):
    client = mock.Mock()
    client.messages.create.return_value = mock.Mock(stop_reason="end_turn",
                                                    content=[mock.Mock(type="text", text=json.dumps(ANSWER))])
    client.beta.messages.create.return_value = client.messages.create.return_value
    with mock.patch.dict("sys.modules", {"anthropic": mock.Mock(Anthropic=lambda **k: client)}), \
            mock.patch.object(buyout.impact, "ai_enabled", lambda: True), \
            mock.patch.object(buyout, "SIGNAL_QUERIES", ["q"]), \
            mock.patch.object(buyout, "google", lambda q: HEADS), \
            mock.patch.object(buyout.time, "sleep", lambda s: None), \
            mock.patch.object(buyout, "yahoo_find", lambda c, h: (h, NAMES[h]) if h in NAMES else None), \
            mock.patch.object(buyout, "score", row):
        w = buyout.BuyoutWatch(lambda *a: True)
        return w._signals_job(set(seen))


class SaleSignals(unittest.TestCase):
    def test_only_public_signals_from_matching_companies(self):
        res = run_job()
        self.assertEqual(res["n"], 5)
        # rumour, 'other', too big (ACMO) and wrong company behind the ticker (IDR) are all left out
        self.assertEqual([x["t"] for x in res["hits"]], ["ZNTX"])
        self.assertEqual(res["hits"][0]["type"], "strategic_review")

    def test_seen_headlines_not_sent_to_ai_again(self):
        seen = {buyout._hash("sig:" + h["title"].lower()) for h in HEADS}
        res = run_job(seen)
        self.assertEqual(res["n"], 0)
        self.assertEqual(res["hits"], [])

    def finish(self, bo, res, first):
        sent = []
        w = buyout.BuyoutWatch(lambda text, silent: sent.append((text, silent)) or True)
        w._finish_signals(bo, res, first)
        return sent

    def test_first_scan_silent_then_loud_once(self):
        res = run_job()
        bo = {}
        self.assertEqual(self.finish(bo, res, first=True), [])
        self.assertIn("ZNTX", bo["cands"])                      # still watched
        bo2 = {}
        sent = self.finish(bo2, res, first=False)
        self.assertEqual(len(sent), 1)
        text, silent = sent[0]
        self.assertIn("$ZNTX</b> PUBLIC SALE SIGNAL", text)
        self.assertIsNone(silent)                               # loud
        self.assertIn("value 800M", text)
        self.assertEqual(self.finish(bo2, res, first=False), [])   # same stock + kind within 14 days: quiet

    def test_name_check(self):
        self.assertTrue(buyout.same_company("Zentrox Inc.", "Zentrox, Inc."))
        self.assertFalse(buyout.same_company("Indra Group", "Idaho Strategic Resources"))
        self.assertFalse(buyout.same_company("The Group Inc", "Holdings Corp"))


if __name__ == "__main__":
    unittest.main()
