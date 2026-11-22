"""2026-11-22: ⚠️/✅ Google notices came 3–4× a day although they say 'max one per day'."""
import unittest
import urllib.request
from unittest import mock

import netgate
from tests.test_netgate import G, Fake, http_error


class OncePerDay(unittest.TestCase):
    def setUp(self):
        netgate._reset()
        self.addCleanup(netgate._reset)

    def open_at(self, result, at):
        with mock.patch.object(netgate.urllib.request, "urlopen", Fake(result)), \
                mock.patch.object(netgate.time, "time", return_value=at):
            try:
                netgate.urlopen(urllib.request.Request(G), timeout=5)
            except netgate.Blocked:
                pass

    def notices_at(self, at, day):
        with mock.patch.object(netgate.time, "time", return_value=at), \
                mock.patch.object(netgate, "_today", return_value=day):
            return netgate.notices()

    def cycle(self, t, day):
        """block → 1 h+ later notices → recovery → notices"""
        self.open_at(http_error(429), t)
        a = self.notices_at(t + 3700, day)
        self.open_at(b"<rss/>", t + 4 * 3600)
        return a + self.notices_at(t + 4 * 3600 + 1, day)

    def test_second_block_same_day_is_quiet(self):
        t = 1_700_000_000
        first = self.cycle(t, "2026-11-21")
        self.assertEqual(len(first), 2)                       # ⚠️ + ✅
        self.assertEqual(self.cycle(t + 5 * 3600, "2026-11-21"), [])
        self.assertEqual(self.cycle(t + 10 * 3600, "2026-11-21"), [])
        self.assertEqual(len(self.cycle(t + 30 * 3600, "2026-11-22")), 2)   # next day: told again

    def test_survives_restart(self):
        t = 1_700_000_000
        self.cycle(t, "2026-11-21")
        st = {}
        netgate.attach(st)
        self.assertEqual(st["_net_told"], {"news.google.com": "2026-11-21"})
        netgate._reset()
        netgate.attach(st)                                     # restart: memory back
        self.assertEqual(self.cycle(t + 5 * 3600, "2026-11-21"), [])


if __name__ == "__main__":
    unittest.main()
