import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

import bot


class StopSave(unittest.TestCase):
    def test_handler_saves_then_exits(self):
        saved = []
        h = bot.stop_handler({"a": 1}, save=lambda st: saved.append(dict(st)))
        with self.assertRaises(SystemExit):
            h(signal.SIGTERM, None)
        self.assertEqual(saved, [{"a": 1}])

    def test_retries_when_state_changes_during_write(self):
        calls = []

        def flaky(st):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("dictionary changed size during iteration")
        with self.assertRaises(SystemExit):
            bot.stop_handler({}, save=flaky)(signal.SIGTERM, None)
        self.assertEqual(len(calls), 3)

    def test_other_errors_still_exit(self):
        def bad(st):
            raise OSError("disk full")
        with self.assertRaises(SystemExit):
            bot.stop_handler({}, save=bad)(signal.SIGTERM, None)

    @unittest.skipIf(os.name != "posix", "needs POSIX signals")
    def test_real_sigterm_in_a_child_process(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "saved.json")
            code = textwrap.dedent(f"""
                import json, os, signal, sys, time
                sys.path.insert(0, {here!r})
                import bot
                st = {{"sent": "statement"}}
                signal.signal(signal.SIGTERM, bot.stop_handler(st, save=lambda s: json.dump(s, open({out!r}, "w"))))
                print("ready", flush=True)
                time.sleep(30)
            """)
            p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                                 env=dict(os.environ, TELEGRAM_BOT_TOKEN="", WATCHLIST=""))
            self.assertEqual(p.stdout.readline().strip(), "ready")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(10), 0)
            self.assertEqual(open(out).read(), '{"sent": "statement"}')


if __name__ == "__main__":
    unittest.main()
