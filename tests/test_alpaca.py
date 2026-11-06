"""⚡ Alpaca news feed: parsing, shared cache, start-up status line."""
import unittest
import urllib.error
from unittest import mock

import alpaca

ITEM = {"id": 7, "headline": " Zentrox Wins Award ", "summary": "S", "url": "https://www.benzinga.com/x",
        "created_at": "2026-11-06T16:30:00Z", "symbols": ["zntx"], "source": "benzinga"}


class Alpaca(unittest.TestCase):
    def setUp(self):
        alpaca._items.clear()
        alpaca._last = float("-inf")

    def tearDown(self):
        alpaca._items.clear()

    def test_parse(self):
        it = alpaca._parse(ITEM)
        self.assertEqual(it["title"], "Zentrox Wins Award")
        self.assertEqual(it["symbols"], ["ZNTX"])
        self.assertEqual(it["source"], "Benzinga")
        self.assertEqual(it["date"].hour, 16)

    def test_off_without_keys(self):
        with mock.patch.object(alpaca, "ON", False):
            self.assertEqual(alpaca.recent(), [])
            self.assertTrue(alpaca.selftest().startswith("OFF"))

    def test_one_request_per_poll_window(self):
        calls = []
        with mock.patch.object(alpaca, "ON", True), \
                mock.patch.object(alpaca, "_get", lambda p: calls.append(p) or {"news": [ITEM]}):
            alpaca.recent()
            alpaca.recent()
        self.assertEqual(len(calls), 1)
        self.assertNotIn("symbols", calls[0])          # watchlist never sent to Alpaca

    def test_bad_keys_reported(self):
        def denied(p):
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch.object(alpaca, "ON", True), mock.patch.object(alpaca, "_get", denied):
            self.assertIn("FAILED", alpaca.selftest())
            self.assertIn("401", alpaca.selftest())

    def test_ok_status(self):
        with mock.patch.object(alpaca, "ON", True), mock.patch.object(alpaca, "_get", lambda p: {"news": [ITEM]}):
            self.assertTrue(alpaca.selftest().startswith("ON"))

    def test_cache_is_capped(self):
        many = {"news": [{**ITEM, "id": i, "created_at": f"2026-11-06T{i % 24:02d}:00:00Z"} for i in range(500)]}
        with mock.patch.object(alpaca, "ON", True), mock.patch.object(alpaca, "_get", lambda p: many):
            self.assertEqual(len(alpaca.recent()), alpaca.KEEP)


if __name__ == "__main__":
    unittest.main()
