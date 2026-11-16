"""2026-11-16 check: Telegram read-timeout duplicates, SPAC name vs ticker in 📰 major news, stuck IBKR worker."""
import queue
import sys
import threading
import time
import unittest
import urllib.error
from unittest import mock

import bot
import buyout
import ibkr
from tests.test_ibkr import FakeIB, fake_lib


class TelegramTimeout(unittest.TestCase):
    def call(self, side_effects):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            e = side_effects[min(len(calls), len(side_effects)) - 1]
            raise e

        with mock.patch.object(bot.urllib.request, "urlopen", fake_urlopen), mock.patch.object(bot.time, "sleep"):
            r = bot.telegram_call("sendMessage", {"text": "x"})
        return r, len(calls)

    def test_read_timeout_not_resent(self):
        r, n = self.call([TimeoutError()])
        self.assertEqual(n, 1)          # was 4 tries + the next news poll = 5 copies
        self.assertTrue(r)              # counts as sent, so news.py doesn't retry either

    def test_connection_failure_still_retried(self):
        r, n = self.call([urllib.error.URLError("refused")])
        self.assertEqual(n, 4)
        self.assertFalse(r)


class SpacNotTicker(unittest.TestCase):
    N = "The Wendy's Company"

    def test_wen_acquisition_is_not_wendys(self):
        for t in ("Form 425 WEN Acquisition Corp Filed by: WEN Acquisition Corp",
                  "WEN Acquisition to Take Our Next Energy Public in $1 Billion Battery Deal",
                  "A $35M cash minimum is a condition for WEN Acquisition (WENN)s planned Our Next Energy merger."):
            self.assertFalse(buyout.in_title(t, "WEN", self.N), t)
            self.assertFalse(buyout.major(t, "WEN", self.N), t)

    def test_real_company_still_matches(self):
        self.assertTrue(buyout.in_title("WEN stock jumps as board explores sale", "WEN", self.N))
        self.assertTrue(buyout.in_title("$WEN Acquisition talk heats up", "WEN", self.N))
        self.assertTrue(buyout.in_title("OMC Group to merge with rival", "OMC", "Omnicom Group Inc."))
        self.assertTrue(buyout.major("Wendy's announces $300 million offering", "WEN", self.N))


class StuckWorker(unittest.TestCase):
    def test_request_timeout_set(self):
        fake = FakeIB()
        lib = fake_lib()
        lib.IB = lambda: fake
        with mock.patch.dict(sys.modules, {"ib_async": lib}):
            ibkr.Mirror(lambda *a: True)._connect()
        self.assertEqual(fake.RequestTimeout, ibkr.REQUEST_TIMEOUT)

    def test_watchdog_restarts_and_retries_once(self):
        sent = []
        m = ibkr.Mirror(lambda t, s=True: sent.append(t) or True)
        m._start = lambda: None
        old = m.q
        old.put({"a": "buy", "tk": "BBB", "p": 5})
        m.job, m.started = {"a": "buy", "tk": "AAA", "p": 10}, time.time() - ibkr.JOB_LIMIT - 1
        self.assertFalse(m._watchdog(now=m.started + 10))
        self.assertTrue(m._watchdog())
        self.assertIsNot(m.q, old)
        jobs = [m.q.get_nowait() for _ in range(m.q.qsize())]
        self.assertEqual([(j["tk"], j.get("retry")) for j in jobs], [("AAA", True), ("BBB", None)])
        self.assertEqual(m.gen, 1)
        self.assertTrue(any("hung" in x and "AAA" in x for x in sent))
        # a retry that hangs again is not retried a third time
        m.job, m.started = jobs[0], time.time() - ibkr.JOB_LIMIT - 1
        m._watchdog()
        self.assertEqual(m.q.qsize(), 0)

    def test_stale_worker_does_not_order(self):
        ib = FakeIB()
        m = ibkr.Mirror(lambda *a: True)
        m.gen = 2
        with mock.patch.dict(sys.modules, {"ib_async": fake_lib()}), mock.patch.object(m, "_connect", lambda: ib):
            m._do({"a": "buy", "tk": "AAA", "p": 10.0, "eur": 10, "gen": 1})
        self.assertEqual(ib.placed, [])
        self.assertTrue(ib.disconnected)

    def test_worker_runs_jobs_and_clears_current(self):
        m = ibkr.Mirror(lambda *a: True)
        done = threading.Event()
        m._do = lambda job: done.set()
        m.q.put({"a": "sync"})
        m._start()
        self.assertTrue(done.wait(2))
        time.sleep(0.05)
        self.assertIsNone(m.job)


if __name__ == "__main__":
    unittest.main()
