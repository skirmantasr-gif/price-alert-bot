"""2026-11-15: everything on one server — heartbeat for the GitHub watchdog, own daily check, Substack forwards to
their own channel."""
import unittest
from unittest import mock

import bot
import extras
import watchdog


def late():
    return extras.local_now().replace(hour=23, minute=59)


class ServerHeartbeat(unittest.TestCase):
    def test_writes_description_watchdog_can_read_and_checks_no_peer(self):
        calls, sent = [], []
        hb = extras.Heartbeat("server", lambda m, p=None: calls.append((m, p)) or {}, lambda t, *a: sent.append(t) or True)
        hb._beat({})
        self.assertEqual([m for m, _ in calls], ["setMyDescription"])   # no getMyShortDescription (phone) any more
        desc = calls[0][1]["description"]
        age = __import__("time").time() - extras._parse_ts(desc)
        self.assertLess(age, 120)
        self.assertFalse(watchdog.should_alert(age))
        self.assertEqual(sent, [])

    def test_daily_check_shows_server_and_substack_status(self):
        for ok, want in ((True, "Substack forwarding (server): on · login OK"),
                         (False, "STOPPED — login expired"), (None, None)):
            sent = []
            hb = extras.Heartbeat("server", lambda *a: {}, lambda t, *a: sent.append(t) or True, thirdparty_ok=lambda: ok)
            with mock.patch.object(extras, "local_now", return_value=late()):
                self.assertTrue(hb._daily({}))
            self.assertIn("Price &amp; news bot (server): running", sent[0])
            self.assertNotIn("Phone", sent[0])
            if want:
                self.assertIn(want, sent[0])
            else:
                self.assertNotIn("Substack", sent[0])

    def test_daily_check_once_per_day(self):
        sent = []
        hb = extras.Heartbeat("server", lambda *a: {}, lambda t, *a: sent.append(t) or True)
        st = {}
        with mock.patch.object(extras, "local_now", return_value=late()):
            hb._daily(st)
            hb._daily(st)
        self.assertEqual(len(sent), 1)


class SubstackChannel(unittest.TestCase):
    def test_forwards_and_photos_go_to_substack_channel(self):
        calls = []
        with mock.patch.object(bot, "TP_CHAT_ID", "-100777"), \
                mock.patch.object(bot, "telegram_call", lambda m, p, chat=None: calls.append((m, chat)) or True), \
                mock.patch.object(extras, "price_context", lambda *a: ""):
            self.assertTrue(bot.send_chat_message("📢 <b>Chat</b>", "hello", "https://x/y", ["https://img/1.png"]))
        self.assertEqual(calls, [("sendMessage", "-100777"), ("sendPhoto", "-100777")])

    def test_default_channel_when_not_set(self):
        calls = []
        with mock.patch.object(bot, "TP_CHAT_ID", None), \
                mock.patch.object(bot, "telegram_call", lambda m, p, chat=None: calls.append((m, chat)) or True), \
                mock.patch.object(extras, "price_context", lambda *a: ""):
            bot.send_chat_message("h", "b", "https://x/y", [])
        self.assertEqual(calls, [("sendMessage", None)])


if __name__ == "__main__":
    unittest.main()
