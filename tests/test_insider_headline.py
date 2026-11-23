"""2026-11-23: '🚨 Klarna CFO Niclas Neglen Purchases 22,102 Shares' was a share GRANT (Form 4 code A).
Insider-buy headlines are checked against the Form 4s the bot read: grant -> quiet + 🧾 note, real buy -> confirmed."""
import time
import unittest

import news


def ins(owner, tx):
    return {"type": "4", "owners": [owner], "role": "Chief Financial Officer", "tx": tx}


def tx(code, shares, price, deriv=False):
    return {"code": code, "shares": shares, "price": price, "value": shares * price, "deriv": deriv}


def headline(title, t="KLAR"):
    return {"kind": "news", "ticker": t, "title": title, "source": "Kalkine Media", "link": "https://x"}


class InsiderHeadline(unittest.TestCase):
    def setUp(self):
        self.ns = {}
        news.f4_record(self.ns, "KLAR", ins("Neglen Niclas", [tx("M", 198, 0), tx("A", 22102, 0)]))

    def test_grant_called_purchase_is_flagged(self):
        it = headline("Klarna CFO Niclas Neglen Purchases 22,102 Shares on November 16, 2026")
        self.assertEqual(news.f4_check(self.ns, it), ("grant", "AM"))

    def test_real_buy_is_confirmed(self):
        news.f4_record(self.ns, "PAM", ins("Mariani Gustavo", [tx("P", 12832, 62.41)]))
        it = headline("Pampa Energia CEO Gustavo Mariani Acquires 12,832 Shares", "PAM")
        kind, usd = news.f4_check(self.ns, it)
        self.assertEqual(kind, "buy")
        self.assertAlmostEqual(usd, 800845, delta=2)

    def test_other_person_or_ticker_or_headline_not_matched(self):
        self.assertIsNone(news.f4_check(self.ns, headline("Klarna CEO Sebastian Siemiatkowski buys 5,000 shares")))
        self.assertIsNone(news.f4_check(self.ns, headline("Neglen buys shares", "ZIM")))
        self.assertIsNone(news.f4_check(self.ns, headline("Klarna expands Visa partnership, Neglen says")))

    def test_old_filing_not_used(self):
        self.ns["f4"][0][1] = int(time.time()) - (news.F4_DAYS + 1) * 86400
        self.assertIsNone(news.f4_check(self.ns, headline("Klarna CFO Niclas Neglen Purchases 22,102 Shares")))

    def test_message_quiet_with_note(self):
        w = news.NewsWatcher.__new__(news.NewsWatcher)
        it = headline("Klarna CFO Niclas Neglen Purchases 22,102 Shares on November 16, 2026")
        it["f4"] = news.f4_check(self.ns, it)
        msg = w._format(it)
        self.assertTrue(msg.startswith("📰"), msg)
        self.assertIn("🧾 SEC filing (Form 4) shows a share <b>grant</b>", msg)

    def test_without_check_cfo_headline_stays_loud(self):
        w = news.NewsWatcher.__new__(news.NewsWatcher)
        msg = w._format(headline("Klarna CFO Niclas Neglen steps down"))
        self.assertTrue(msg.startswith("🚨"), msg)


if __name__ == "__main__":
    unittest.main()
