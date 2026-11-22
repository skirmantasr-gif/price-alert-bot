"""X posts from the owner's Mac reader (2026-11-22)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import news
import watchlist


def line(t="CAPR", user="biotechnews", fol=25000, mins=5, text="$CAPR halted, FDA letter out?"):
    d = (datetime.now(timezone.utc) - timedelta(minutes=mins)).strftime("%a %b %d %H:%M:%S +0000 %Y")
    return json.dumps({"ticker": t, "id": "1", "user": user, "followers": fol, "text": text, "created": d,
                       "link": f"https://x.com/{user}/status/1"}) + "\n"


class XInbox(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.remove, self.path)
        p = mock.patch.object(watchlist, "TICKERS", ["CAPR", "SLS"])
        p.start()
        self.addCleanup(p.stop)

    def write(self, s, mode="a"):
        with open(self.path, mode) as f:
            f.write(s)

    def test_reads_new_lines_once(self):
        ns = {}
        self.write(line() + line("SLS", text="SELLAS files BLA"))
        items = news.x_news(ns, self.path)
        self.assertEqual([i["ticker"] for i in items], ["CAPR", "SLS"])
        self.assertEqual(items[0]["source"], "X @biotechnews (25k)")
        self.assertTrue(items[0]["x"])
        self.assertEqual(news.x_news(ns, self.path), [])              # nothing new
        self.write(line(text="second"))
        self.assertEqual([i["title"] for i in news.x_news(ns, self.path)], ["second"])

    def test_half_line_waits_and_unknown_ticker_skipped(self):
        ns = {}
        full = line()
        self.write(full + full[:20])
        self.assertEqual(len(news.x_news(ns, self.path)), 1)
        self.write(full[20:] + line("ZZZZ"))
        self.assertEqual(len(news.x_news(ns, self.path)), 1)          # the finished line; ZZZZ not on watchlist

    def test_truncated_file_restarts(self):
        ns = {"x_off": 10 ** 6}
        self.write(line())
        self.assertEqual(len(news.x_news(ns, self.path)), 1)

    def test_missing_file(self):
        self.assertEqual(news.x_news({}, "/nonexistent/x.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
