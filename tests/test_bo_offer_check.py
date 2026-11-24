"""Buyout channel improvements 2026-11-24 (OTLY case): offer vs market price, 🔀 control change signal, falling-knife
filter for automatic picks. No network: everything mocked."""
import unittest
from unittest import mock

import buyout


class OfferView(unittest.TestCase):
    def test_discount_tender_is_control_change(self):
        kind, line = buyout.offer_view("unsolicited_offer", "Bigco launches tender offer for Zentrox at 42% discount",
                                       4.13, 7.17)
        self.assertEqual(kind, "control_change")
        self.assertIn("42% BELOW the market", line)

    def test_mandatory_bid_without_price(self):
        kind, line = buyout.offer_view("unsolicited_offer", "Bigco makes mandatory offer for Zentrox shares", 0, 7.0)
        self.assertEqual(kind, "control_change")
        self.assertIn("not a buyout premium", line)

    def test_premium_offer(self):
        kind, line = buyout.offer_view("unsolicited_offer", "Zentrox receives $37 per share proposal", 37, 29.0)
        self.assertEqual(kind, "unsolicited_offer")
        self.assertIn("+28% vs market", line)

    def test_market_above_rejected_bid_stays_offer(self):
        kind, line = buyout.offer_view("offer_rejected", "Zentrox rejects $7.35 per share bid", 7.35, 8.54)
        self.assertEqual(kind, "offer_rejected")
        self.assertIn("already 14% above", line)

    def test_other_kinds_untouched(self):
        self.assertEqual(buyout.offer_view("strategic_review", "Zentrox explores sale", 0, 5.0), ("strategic_review", ""))

    def test_offer_price_from_ai_or_headline(self):
        self.assertEqual(buyout.offer_price("anything", 4.13), 4.13)
        self.assertEqual(buyout.offer_price("Bigco offers $12.50 per share for Zentrox", 0), 12.5)
        self.assertEqual(buyout.offer_price("Bigco offers $4.13/ADS", None), 4.13)
        self.assertEqual(buyout.offer_price("Zentrox explores sale", "x"), 0.0)


class ControlChangePost(unittest.TestCase):
    def test_message(self):
        sent = []
        w = buyout.BuyoutWatch(lambda text, silent: sent.append(text) or True)
        row = {"t": "ZNTX", "score": 5, "hits": [], "name": "Zentrox", "price": 6.93, "mcap": 400e6,
               "off_high": -0.5, "avg_vol": 177e3}
        kind, line = buyout.offer_view("unsolicited_offer", "Bigco tender offer for Zentrox at 42% discount", 4.13, 6.93)
        res = {"seen": [], "n": 1, "hits": [{"t": "ZNTX", "name": "Zentrox", "type": kind, "title": "t", "source": "Wire",
                                             "link": "https://x/1", "date": None, "row": row, "offer": line}]}
        bo = {}
        with mock.patch.dict("sys.modules", {"context": mock.Mock(lines=lambda *a, **k: [])}):
            w._finish_signals(bo, res, first=False, state={})
        self.assertEqual(len(sent), 1)
        self.assertIn("CONTROL CHANGE", sent[0])
        self.assertIn("BELOW the market", sent[0])
        self.assertIn("Thin trading", sent[0])            # 177k x $6.93 = $1.2M/day
        self.assertEqual(bo["cands"]["ZNTX"]["signal"], "control_change")
        self.assertIn("BELOW", bo["cands"]["ZNTX"]["offer"])


def scored_row(t, price, r20, auto=True):
    return {"t": t, "score": 6, "hits": ["commercial", "cheap", "stress", "smallcap", "depressed", "spikes"],
            "price": price, "boost": 0, "boosts": [], "mcap": 500e6, "ttm": 400e6, "yoy": 0.2, "ev_s": 1.0,
            "debt": 1, "cash": 0, "dil": 0, "off_high": -0.5, "spikes": 1, "avg_vol": 1e6, "name": t,
            "sector": "Industrials", "r20": r20, "auto": auto}


class FallingKnives(unittest.TestCase):
    def screen(self, bo, rows):
        w = buyout.BuyoutWatch(lambda *a: True)
        w.result = (rows, [], 0, {})
        with mock.patch.object(buyout, "TABLE_WEEKDAY", 9):   # no weekly table
            w._finish_screen(bo, first=False)
        return bo

    def test_free_fall_not_added(self):
        bo = self.screen({}, [scored_row("AAA", 5.0, -0.30), scored_row("BBB", 5.0, -0.05)])
        self.assertNotIn("AAA", bo["cands"])
        self.assertIn("BBB", bo["cands"])

    def test_auto_pick_dropped_after_30pct_fall_and_cooled(self):
        bo = {"cands": {"AAA": {"since": "2026-11-01", "price": 10.0, "auto": True}}}
        self.screen(bo, [scored_row("AAA", 6.5, -0.05)])
        self.assertNotIn("AAA", bo["cands"])
        self.assertIn("AAA", bo["cool"])
        self.screen(bo, [scored_row("AAA", 6.5, 0.02)])   # stabilised next day: still cooling off
        self.assertNotIn("AAA", bo["cands"])

    def test_signal_candidate_not_dropped_for_falling(self):
        bo = {"cands": {"AAA": {"since": buyout.extras.local_now().strftime("%Y-%m-%d"), "price": 10.0,
                                "signal": "offer_rejected"}}}
        self.screen(bo, [scored_row("AAA", 6.0, -0.4, auto=False)])
        self.assertIn("AAA", bo["cands"])


if __name__ == "__main__":
    unittest.main()


class RumourWithSaleHistory(unittest.TestCase):
    """BILL Nov 2026: activists + sale reports all year, fingerprint 2/6; a fresh take-private report came true."""
    def run_one(self, kind, old, score=2, cap=5e9):
        import json as _json
        heads = [{"title": "Bigco said to near deal for Zentrox", "source": "Wire", "link": "https://x/z",
                  "date": buyout.datetime.now(buyout.timezone.utc), "desc": ""}]
        answer = {"items": [{"id": 0, "company": "Zentrox Inc.", "ticker": "ZNTX", "type": kind, "offer_price": 0}]}
        client = mock.Mock()
        client.messages.create.return_value = mock.Mock(stop_reason="end_turn",
                                                        content=[mock.Mock(type="text", text=_json.dumps(answer))])
        client.beta.messages.create.return_value = client.messages.create.return_value
        row = {"t": "ZNTX", "score": score, "hits": [], "name": "Zentrox, Inc.", "price": 10.0, "mcap": cap,
               "off_high": 0.0, "avg_vol": 5e6}
        with mock.patch.dict("sys.modules", {"anthropic": mock.Mock(Anthropic=lambda **k: client)}), \
                mock.patch.object(buyout.impact, "ai_enabled", lambda: True), \
                mock.patch.object(buyout, "SIGNAL_QUERIES", ["q"]), \
                mock.patch.object(buyout, "alpaca_heads", lambda h: []), \
                mock.patch.object(buyout, "google", lambda q: heads), \
                mock.patch.object(buyout.time, "sleep", lambda s: None), \
                mock.patch.object(buyout, "yahoo_find", lambda c, h: ("ZNTX", "Zentrox, Inc.")), \
                mock.patch.object(buyout, "score", lambda t: row), \
                mock.patch.object(buyout.BuyoutWatch, "_old_news", staticmethod(lambda *a: old)):
            return buyout.BuyoutWatch(lambda *a: True)._signals_job(set())["hits"]

    def test_low_score_rumour_with_history_posted(self):
        hits = self.run_one("rumour", "old story")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["hist"])

    def test_low_score_rumour_without_history_skipped(self):
        self.assertEqual(self.run_one("rumour", ""), [])

    def test_old_story_still_skips_other_signals(self):
        self.assertEqual(self.run_one("strategic_review", "old story"), [])

    def test_moved_stock_still_skipped(self):
        self.assertEqual(self.run_one("rumour", "stock already moved"), [])

    def test_microcap_signal_now_allowed(self):
        self.assertEqual(len(self.run_one("strategic_review", "", cap=30e6)), 1)
        self.assertEqual(self.run_one("strategic_review", "", cap=10e6), [])

    def test_history_post_is_loud_with_line(self):
        sent = []
        w = buyout.BuyoutWatch(lambda text, silent: sent.append((text, silent)) or True)
        hit = self.run_one("rumour", "old story")[0]
        with mock.patch.dict("sys.modules", {"context": mock.Mock(lines=lambda *a, **k: [])}):
            w._finish_signals({}, {"seen": [], "n": 1, "hits": [hit]}, first=False)
        self.assertIn("Earlier sale / takeover headlines", sent[0][0])
        self.assertIsNone(sent[0][1])    # loud
