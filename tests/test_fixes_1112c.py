"""2026-11-12 (c): 📣 sale signals skip news the market already knows (SGRY / LKQ cases); compact state."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import buyout

NOW = datetime.now(timezone.utc)


def bars(jump_at=None, n=60, jump=1.14, vol=17e6):
    out, px = [], 20.0
    for i in range(n):
        v = 1e6
        if i == jump_at:
            px, v = px * jump, vol
        out.append((i, px, v))
    return out, {}


def news(*ages_days, title="Zentrox explores a sale of the company"):
    return [{"title": title, "date": NOW - timedelta(days=a), "source": "x", "link": "", "desc": ""} for a in ages_days]


class OldNews(unittest.TestCase):
    def check(self, chart, items, title):
        with mock.patch.object(buyout, "daily_chart", lambda t: chart), mock.patch.object(buyout, "google", lambda q: items):
            return buyout.BuyoutWatch._old_news("ZNTX", "Zentrox, Inc.", "Zentrox", {"title": title})

    def test_stock_already_jumped(self):          # SGRY: raised offer Nov 9 (+14% on big volume), recap Nov 12
        self.assertEqual(self.check(bars(jump_at=57), [], "Buyer bumps up offer for Zentrox"), "stock already moved")

    def test_big_earlier_spike_does_not_hide_the_next(self):
        chart = bars(jump_at=57)[0]
        chart[45] = (45, chart[45][1], 60e6)      # the original deal day: huge volume inside the 20-day window
        self.assertEqual(self.check((chart, {}), [], "Buyer bumps up offer for Zentrox"), "stock already moved")

    def test_old_story(self):                     # LKQ: review announced 10 months ago, republished now
        self.assertEqual(self.check(bars(), news(0, 290), "Zentrox launches strategic review"), "old story")

    def test_new_development_not_skipped(self):   # a raised / rival bid is new even in an old story
        self.assertEqual(self.check(bars(), news(0, 290), "Rival bidder raises offer for Zentrox"), "")

    def test_fresh_story_posted(self):
        self.assertEqual(self.check(bars(), news(0, 1), "Zentrox explores strategic alternatives"), "")

    def test_ancient_or_unrelated_coverage_ignored(self):   # 2012 "takeover lunch", other companies
        items = news(0, 4000) + news(100, title="Acme explores a sale")
        self.assertEqual(self.check(bars(), items, "Zentrox explores strategic alternatives"), "")

    def test_prompt_treats_raised_offer_in_agreed_deal_as_agreed(self):
        self.assertIn("raised, amended or sweetened offer", buyout.SIGNAL_SYSTEM)


class CompactState(unittest.TestCase):
    def test_saved_without_padding(self):
        import bot
        with mock.patch.object(bot, "STATE_FILE", "/tmp/state-test.json"):
            bot.save_state({"_news": {"a": [1, 2]}})
        self.assertEqual(open("/tmp/state-test.json").read(), json.dumps({"_news": {"a": [1, 2]}}, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
