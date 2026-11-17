"""2026-11-17: Google News HTTP 429 / FDA.gov HTTP 403 on the server → back off, one log line, one Telegram notice a day."""
import io
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import netgate
import news

G = "https://news.google.com/rss/search?q=x"
F = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"


def http_error(code):
    return urllib.error.HTTPError("u", code, "no", {}, io.BytesIO(b""))


class Fake:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url if isinstance(req, urllib.request.Request) else req)
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(r)


class Gate(unittest.TestCase):
    def setUp(self):
        netgate._reset()
        self.addCleanup(netgate._reset)

    def run_open(self, fake, url, at):
        with mock.patch.object(netgate.urllib.request, "urlopen", fake), mock.patch.object(netgate.time, "time",
                                                                                            return_value=at):
            return netgate.urlopen(urllib.request.Request(url), timeout=5)

    def test_429_pauses_then_doubles_then_recovers(self):
        fake = Fake(http_error(429))
        t0 = 1_000_000
        with self.assertRaises(netgate.Blocked):
            self.run_open(fake, G, t0)
        with self.assertRaises(netgate.Blocked):     # paused: no request sent at all
            self.run_open(fake, G, t0 + 60)
        self.assertEqual(len(fake.calls), 1)
        with self.assertRaises(netgate.Blocked):     # 30 min later: tries again, still blocked → 60 min
            self.run_open(fake, G, t0 + 1801)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(netgate._st["news.google.com"]["delay"], 3600)
        ok = Fake(b"<rss/>")
        self.assertEqual(self.run_open(ok, G, t0 + 1801 + 3601).read(), b"<rss/>")
        self.assertNotIn("news.google.com", netgate._st)

    def test_delay_capped(self):
        fake = Fake(http_error(429))
        t = 0
        for _ in range(12):
            t += 10 ** 6
            with self.assertRaises(netgate.Blocked):
                self.run_open(fake, G, t)
        self.assertEqual(netgate._st["news.google.com"]["delay"], 6 * 3600)

    def test_other_hosts_and_codes_untouched(self):
        with self.assertRaises(urllib.error.HTTPError):   # 500 is not a block: normal error, no pause
            self.run_open(Fake(http_error(500)), G, 1)
        self.assertEqual(netgate._st, {})
        with self.assertRaises(urllib.error.HTTPError):   # other site's 429: not gated
            self.run_open(Fake(http_error(429)), "https://query1.finance.yahoo.com/x", 1)
        self.assertEqual(netgate._st, {})

    def test_one_warning_per_block_not_per_ticker(self):
        fake = Fake(http_error(429))
        with self.assertLogs("bot", "WARNING") as cm:
            for i in range(16):   # 16 tickers in one round
                try:
                    self.run_open(fake, G, 5000 + i)
                except netgate.Blocked:
                    pass
        self.assertEqual(len(cm.output), 1)
        self.assertIn("pausing it for 30 min", cm.output[0])

    def test_telegram_notice_once_a_day_after_1h_and_recovery(self):
        fake = Fake(http_error(429))
        t0 = 1_700_000_000
        with mock.patch.object(netgate.time, "time", return_value=t0 + 10):
            pass
        with self.assertRaises(netgate.Blocked):
            self.run_open(fake, G, t0)
        with mock.patch.object(netgate.time, "time", return_value=t0 + 600):
            self.assertEqual(netgate.notices(), [])          # short blip: no message
        with mock.patch.object(netgate.time, "time", return_value=t0 + 3700):
            msgs = netgate.notices()
            self.assertEqual(len(msgs), 1)
            self.assertIn("Google News is blocking", msgs[0])
            self.assertEqual(netgate.notices(), [])          # not again the same day
        self.run_open(Fake(b"<rss/>"), G, t0 + 10 ** 5)
        self.assertEqual(netgate.notices(), ["✅ Google News works again — its news searches are back on."])

    def test_fda_quiet_but_in_daily_line(self):
        t0 = 1_700_000_000
        with self.assertRaises(netgate.Blocked):
            self.run_open(Fake(http_error(403)), F, t0)
        with mock.patch.object(netgate.time, "time", return_value=t0 + 10 * 3600):
            self.assertEqual(netgate.notices(), [])
            self.assertIn("FDA.gov (HTTP 403", netgate.status_line())
        self.assertEqual(netgate._st["www.fda.gov"]["delay"], 3600)

    def test_status_line_empty_when_ok(self):
        self.assertEqual(netgate.status_line(), "")

    def test_pause_survives_restart(self):
        t0 = time.time()
        with self.assertRaises(netgate.Blocked):
            self.run_open(Fake(http_error(429)), G, t0)
        state = {}
        self.assertTrue(netgate.attach(state))
        self.assertIn("news.google.com", state["_net"])
        netgate._reset()                                  # restart
        netgate.attach(state)
        fake = Fake(b"<rss/>")
        with self.assertRaises(netgate.Blocked):          # still paused, no request
            self.run_open(fake, G, t0 + 60)
        self.assertEqual(fake.calls, [])
        self.run_open(fake, G, t0 + 1801)                 # works again → removed from state
        netgate.attach(state)
        self.assertNotIn("_net", state)


class NewsWatcherQuiet(unittest.TestCase):
    def setUp(self):
        netgate._reset()
        self.addCleanup(netgate._reset)

    def test_blocked_source_no_old_hourly_warning(self):
        sent = []
        w = news.NewsWatcher(["AAA"], sent.append)

        def blocked(*a):
            raise netgate.Blocked("news.google.com", 429)
        with mock.patch.object(news.time, "time", return_value=10 ** 9):
            w._fetch("Google:AAA", blocked, "AAA")
        with mock.patch.object(news.time, "time", return_value=10 ** 9 + 7200):
            self.assertIsNone(w._fetch("Google:AAA", blocked, "AAA"))
        self.assertEqual(sent, [])        # was: "⚠️ News source Google failing for 1h+" after every restart
        self.assertEqual(w.fail, {})

    def test_google_poll_slower(self):
        self.assertGreaterEqual(news.GOOGLE_SECONDS, 900)


if __name__ == "__main__":
    unittest.main()
