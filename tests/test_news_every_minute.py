"""2026-11-17: news every minute; Google contract search off; Google remembers its last pause after recovery."""
import io
import unittest
import urllib.error
import urllib.request
from unittest import mock

import bot
import netgate
import news

G = "https://news.google.com/rss/search?q=x"


def http_error(code):
    return urllib.error.HTTPError("u", code, "no", {}, io.BytesIO(b""))


def open_at(result, at):
    def fake(req, timeout=None):
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(result)
    with mock.patch.object(netgate.urllib.request, "urlopen", fake), \
            mock.patch.object(netgate.time, "time", return_value=at):
        return netgate.urlopen(urllib.request.Request(G), timeout=5)


class Intervals(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(bot.NEWS_SECONDS, 60)
        self.assertEqual(news.SA_SECONDS, 60)
        self.assertGreaterEqual(news.GOOGLE_SECONDS, 900)
        self.assertFalse(news.GOOGLE_CONTRACTS)

    def test_no_google_contract_search(self):
        calls = []
        w = news.NewsWatcher(["AAA"], lambda *a: True)
        w.last_google = w.last_sa = w.last_contract_search = w.last_calendar = float("-inf")
        patches = [mock.patch.object(news, n, lambda *a, n=n: calls.append(n) or []) for n in
                   ("google_news", "seekingalpha_news", "google_contracts", "google_calendar", "fda_news",
                    "prnewswire_news", "yahoo_news")]
        patches.append(mock.patch.object(news.time, "sleep"))
        for p in patches:
            p.start()
        try:
            w.check({})
        finally:
            for p in patches:
                p.stop()
        self.assertIn("google_news", calls)
        self.assertNotIn("google_contracts", calls)


class RememberPause(unittest.TestCase):
    def setUp(self):
        netgate._reset()
        self.addCleanup(netgate._reset)

    def test_block_soon_after_recovery_waits_longer(self):
        t0 = 1_000_000
        with self.assertRaises(netgate.Blocked):
            open_at(http_error(429), t0)                      # 30 min
        with mock.patch.object(netgate.time, "time", return_value=t0 + 1801):
            open_at(b"<rss/>", t0 + 1801)                     # works again
        with self.assertRaises(netgate.Blocked):
            open_at(http_error(429), t0 + 3600)               # blocked again 20 min later
        self.assertEqual(netgate._st["news.google.com"]["delay"], 3600)

    def test_forgotten_after_quiet_hours(self):
        t0 = 1_000_000
        with self.assertRaises(netgate.Blocked):
            open_at(http_error(429), t0)
        with mock.patch.object(netgate.time, "time", return_value=t0 + 1801):
            open_at(b"<rss/>", t0 + 1801)
        with self.assertRaises(netgate.Blocked):
            open_at(http_error(429), t0 + 1801 + netgate.MEMORY + 10)
        self.assertEqual(netgate._st["news.google.com"]["delay"], 1800)


if __name__ == "__main__":
    unittest.main()
