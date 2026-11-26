"""2026-11-26 check (Thanksgiving): no stale options / pre-market posts on NYSE holidays; one 🎯 post per bidder story."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import buyout
import catalysts
import marketodds
import predict
import movers
import options

NY = ZoneInfo("America/New_York")
THANKSGIVING = datetime(2026, 11, 26, tzinfo=NY)
WEDNESDAY = datetime(2026, 11, 25, tzinfo=NY)


class HolidayGuards(unittest.TestCase):
    def test_trading_day(self):
        self.assertFalse(options.trading_day(date(2026, 11, 26)))   # Thanksgiving
        self.assertTrue(options.trading_day(date(2026, 11, 27)))    # early close, still a trading day
        self.assertFalse(options.trading_day(date(2026, 11, 28)))   # Saturday

    def test_options_history_skips_holiday(self):
        calls = []
        w = options.HistoryWatch(["ZNTX"], fetch=lambda s: calls.append(s), now=lambda: THANKSGIVING.replace(hour=16))
        self.assertFalse(w.check({}))
        self.assertEqual(calls, [])

    def _movers_at(self, when):
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz) if tz else when
        w = movers.MoversWatch(lambda *a: True, ())
        started = []
        w._start = lambda job, fn, day: started.append(job)
        with mock.patch.object(movers, "datetime", FakeDT):
            w.check({})
        return started

    def test_no_premarket_report_on_thanksgiving(self):
        self.assertEqual(self._movers_at(THANKSGIVING.replace(hour=8, minute=1)), [])

    def test_premarket_report_on_trading_day(self):
        self.assertEqual(self._movers_at(WEDNESDAY.replace(hour=8, minute=1)), ["pre"])


def head(title, hours_ago=0.2):
    return {"title": title, "desc": "", "link": "https://example.com/x", "source": "Wire",
            "date": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


class SameBidder(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.w = buyout.BuyoutWatch(lambda t, q: self.sent.append(t))
        self.seen = set()

    def new(self, key, src):
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def post(self, bo, title):
        with mock.patch.object(buyout.extras, "remember", lambda *a: None):
            self.w._post_heads(bo, "ZNTX", "Zentex Holdings", [head(title)], self.new)

    def test_one_story_many_wordings(self):
        bo = {}
        for t in ["Targa Resources in Talks to Acquire Zentex Holdings Inc.",
                  "Targa in talks to buy Zentex Holdings, Bloomberg says",
                  "Targa is in talks to buy Permian pipeline operator Zentex"]:
            self.post(bo, t)
        self.assertEqual(len(self.sent), 1, self.sent)

    def test_other_bidder_and_raised_offer_still_posted(self):
        bo = {}
        self.post(bo, "Targa Resources in Talks to Acquire Zentex Holdings")
        self.post(bo, "Oneok in talks to acquire Zentex Holdings")             # different bidder
        self.post(bo, "Targa agrees to acquire Zentex Holdings for $4.4B")     # price is new
        self.assertEqual(len(self.sent), 3, self.sent)

    def test_same_deal_rules(self):
        self.assertTrue(buyout.same_deal({"targa", "permian"}, {"targa", "resources", "talks", "acquire"}))
        self.assertFalse(buyout.same_deal({"talks", "acquire", "bloomberg"}, {"oneok", "talks", "acquire"}))
        self.assertFalse(buyout.same_deal({"targa", "$4.4b"}, {"targa", "talks"}))
        self.assertTrue(buyout.same_deal({"targa", "$4.4b"}, {"targa", "$4.4"}))


class FdaForecasts(unittest.TestCase):
    STATE = {"_fdacal": {"decided": {"ZNTX:2026-11-27": {"approved": True, "at": "2026-11-23"},
                                     "BBBX:2026-11-22": {"approved": False, "at": "2026-11-22"}},
                         "missed": {"OTHR:2026-11-30": {"at": "2026-11-26"}}}}

    def test_resolved_from_fda_marks(self):
        now = datetime(2026, 11, 26, 12)
        r = lambda key: predict.resolve({"id": "x", "p": 0.8, "fda": key, "rule": {"type": "manual"}}, now,
                                        state=self.STATE)
        self.assertTrue(r("ZNTX:2026-11-27")[0])
        self.assertFalse(r("BBBX:2026-11-22")[0])
        self.assertFalse(r("OTHR:2026-11-30")[0])
        self.assertIsNone(r("AAA:2026-12-10"))
        # a hand-written outcome still wins
        self.assertTrue(predict.resolve({"fda": "OTHR:2026-11-30", "outcome": True}, now, state=self.STATE)[0])

    def test_watch_posts_auto_result(self):
        sent = []
        w = predict.PredictionWatch(lambda t, q: sent.append(t) or True, now=lambda: datetime(2026, 11, 26, 12))
        st = dict(self.STATE, _pred={"_init": True})
        with mock.patch.object(predict, "load", lambda f=None: [
                {"id": "zntx", "p": 0.8, "claim": "ZNTX approved by Nov 27", "made": "2026-11-20",
                 "fda": "ZNTX:2026-11-27", "rule": {"type": "manual"}}]):
            self.assertTrue(w.check(st))
        self.assertEqual(len(sent), 1)
        self.assertIn("it happened", sent[0])

    def test_extension_marks_date_missed(self):
        for t in ["ZNTX Down as FDA Extends Review Timeline for Zentamab BLA",
                  "Zentex says PDUFA date extended by three months"]:
            st = {"_cal": [["ZNTX", "2026-11-30", "fda", ""]]}
            self.assertTrue(catalysts.mark_missed(st, "ZNTX", t, date(2026, 11, 26)), t)
        self.assertFalse(catalysts.mark_missed({"_cal": [["ZNTX", "2026-11-30", "fda", ""]]}, "ZNTX",
                                               "Zentex extends cash runway into 2028 after FDA meeting",
                                               date(2026, 11, 26)))

    def test_claude_odds_in_market_post(self):
        preds = [{"fda": "ZNTX:2026-11-27", "p": 0.8, "made": "2026-11-20"}]
        line = marketodds.claude_line(preds, "ZNTX:2026-11-27", 0.55)
        self.assertIn("Claude said 80%", line)
        self.assertIn("+25 pts", line)
        self.assertIn("more hopeful", line)
        self.assertEqual(marketodds.claude_line(preds, "AAA:2026-12-01", 0.5), "")


if __name__ == "__main__":
    unittest.main()
