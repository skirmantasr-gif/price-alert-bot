import unittest

import bot
import chatnotes

LONG = ("SLS: the offering closed today and the underwriters exercised the full option. Total 13,269,231 shares "
        "at $13, $172.5M gross. Cash is now roughly $296M and the ATM is locked until mid January.")
GOOD = {"worth": True, "tickers": ["SLS"], "claim": "Full over-allotment shows strong demand",
        "checks": [{"point": "13.27M shares at $13", "verdict": "correct", "why": "matches the 424B5"},
                   {"point": "Specialist funds bought", "verdict": "unverified", "why": "boilerplate <b>PR</b> wording"}],
        "left_out": "Stock fell 29% from the spike", "lean": "down", "confidence": "low", "rating": 14}


class Filter(unittest.TestCase):
    def setUp(self):
        chatnotes._queue.clear()

    def test_wanted(self):
        self.assertTrue(chatnotes.wanted(LONG, "Marius", "admin"))
        self.assertFalse(chatnotes.wanted(LONG, "Someone", "member"))
        self.assertFalse(chatnotes.wanted("SLS up today!", "Marius", "admin"))
        old = chatnotes.NAMES
        chatnotes.NAMES = ["guest analyst"]
        try:
            self.assertTrue(chatnotes.wanted(LONG, "Guest Analyst", "member"))
        finally:
            chatnotes.NAMES = old

    def test_offer_dedupes(self):
        chatnotes.offer("p1", LONG, "Marius", "admin", "https://x/p1")
        chatnotes.offer("p1", LONG, "Marius", "admin", "https://x/p1")
        chatnotes.offer("p2", "short", "Marius", "admin", "https://x/p2")
        self.assertEqual([q[0] for q in chatnotes._queue], ["p1"])


class Flow(unittest.TestCase):
    def setUp(self):
        chatnotes._queue.clear()

    def run_once(self, cn, st):
        cn.check(st)
        if cn.thread:
            cn.thread.join(5)
        cn.check(st)

    def test_worth_posts_to_main_quietly(self):
        sent, asked = [], []
        cn = chatnotes.ChatNotes(lambda t, s: sent.append((t, s)) or True, lambda txt: ["SLS"],
                                 lambda t: {"price": 13.66}, ask=lambda b, f: asked.append(f) or GOOD)
        chatnotes.offer("p1", LONG, "Marius", "admin", "https://substack.com/chat/1")
        st = {}
        self.run_once(cn, st)
        self.assertEqual(asked, [{"SLS": {"price": 13.66}}])
        text, silent = sent[0]
        self.assertTrue(silent)
        self.assertIn("Marius's post checked", text)
        self.assertIn("✅ 13.27M shares", text)
        self.assertIn("❓ Specialist", text)
        self.assertNotIn("<b>PR", text)                      # tags in the AI text are removed
        self.assertNotRegex(text, r"&(?!amp;|lt;|gt;|quot;|#)")   # Telegram HTML: no bare "&"
        self.assertIn("10/10", text)                         # rating clamped
        self.assertIn("facts lean DOWN", text)
        self.assertIn("Open the post", text)
        self.assertIn("p1", st["_chatnotes"]["done"])

    def test_not_worth_and_no_ticker_and_cap(self):
        sent = []
        cn = chatnotes.ChatNotes(lambda t, s: sent.append(t) or True, lambda txt: ["SLS"], lambda t: {},
                                 ask=lambda b, f: dict(GOOD, worth=False))
        chatnotes.offer("p1", LONG, "Marius", "admin", "x")
        st = {}
        self.run_once(cn, st)
        self.assertEqual(sent, [])
        asked = []
        cn2 = chatnotes.ChatNotes(lambda t, s: True, lambda txt: [], lambda t: {}, ask=lambda b, f: asked.append(1))
        chatnotes.offer("p2", LONG, "Marius", "admin", "x")
        self.run_once(cn2, st)
        self.assertEqual(asked, [])                          # no real ticker: no AI call
        st["_chatnotes"]["n"] = chatnotes.MAX_PER_DAY
        cn3 = chatnotes.ChatNotes(lambda t, s: True, lambda txt: ["SLS"], lambda t: {}, ask=lambda b, f: asked.append(1),
                                  now=lambda: st["_chatnotes"]["day"])
        chatnotes.offer("p3", LONG, "Marius", "admin", "x")
        self.run_once(cn3, st)
        self.assertEqual(asked, [])                          # daily cap

    def test_ai_failure_is_quiet(self):
        sent = []

        def boom(b, f):
            raise RuntimeError("x")
        cn = chatnotes.ChatNotes(lambda t, s: sent.append(t) or True, lambda txt: ["SLS"], lambda t: {}, ask=boom)
        chatnotes.offer("p1", LONG, "Marius", "admin", "x")
        st = {}
        self.run_once(cn, st)
        self.assertEqual(sent, [])
        self.assertIn("p1", st["_chatnotes"]["done"])


class Text(unittest.TestCase):
    def test_clean_cuts_at_a_word_with_ellipsis(self):
        out = chatnotes._clean("one two three four five six", 15)
        self.assertEqual(out, "one two three…")
        self.assertEqual(chatnotes._clean("short", 15), "short")

    def test_conference_acronyms_are_not_tickers(self):
        import extras
        for w in ("ASH", "ASCO", "BLA", "NDA", "CRL", "DMD"):
            self.assertIn(w, extras.STOP)


class Tickers(unittest.TestCase):
    def test_chat_tickers_checks_prices(self):
        old = bot.fetch_price
        bot._ticker_cache.clear()

        def fp(t):
            if t not in ("SLS", "CAPR"):
                raise ValueError("no such ticker")
            return 10.0, 9.0, "regular"
        bot.fetch_price = fp
        try:
            self.assertEqual(bot.chat_tickers("$SLS and CAPR beat; BLA, ASH and HOLD news"), ["SLS", "CAPR"])
            self.assertEqual(bot.chat_tickers("Presenting at ASH next month"), [])
        finally:
            bot.fetch_price = old
            bot._ticker_cache.clear()


if __name__ == "__main__":
    unittest.main()
