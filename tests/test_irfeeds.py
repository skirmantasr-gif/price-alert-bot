"""Company investor-site feeds (watchlist "ir", 2026-11-22)."""
import unittest
from datetime import date
from unittest import mock

import news
import watchlist

FEED = b"""<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel>
<title>Capricor Therapeutics, Inc. (CAPR) Press Releases</title>
<item><title><![CDATA[Capricor Therapeutics Provides Regulatory Update on Deramiocel BLA for Duchenne Muscular Dystrophy]]></title>
<link>https://www.capricor.com/investors/news-events/press-releases/detail/400</link>
<description><![CDATA[Company receives Complete Response Letter from FDA for Deramiocel BLA]]></description>
<pubDate>Fri, 20 Nov 26 19:55:00 -0500</pubDate></item>
</channel></rss>"""


class IrFeed(unittest.TestCase):
    def test_parse_company_feed(self):
        with mock.patch.dict(watchlist.IR_FEEDS, {"CAPR": "https://www.capricor.com/x/rss"}), \
                mock.patch.object(news, "_get", return_value=FEED):
            items = news.ir_news("CAPR")
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["source"], "Company site")
        self.assertIn("Regulatory Update", it["title"])
        self.assertIn("Complete Response Letter", it["desc"])
        self.assertEqual(it["date"].isoformat(), "2026-11-20T19:55:00-05:00")

    def test_hot_window(self):
        st = {"_cal": [["CAPR", "2026-11-22", "fda", "FDA decision date (PDUFA)"]], "_fdacal": {"ev": {}}}
        self.assertTrue(news.ir_hot(st, "CAPR", date(2026, 11, 20)))
        self.assertTrue(news.ir_hot(st, "CAPR", date(2026, 11, 25)))
        self.assertFalse(news.ir_hot(st, "CAPR", date(2026, 11, 10)))
        self.assertFalse(news.ir_hot(st, "SLS", date(2026, 11, 22)))


if __name__ == "__main__":
    unittest.main()
