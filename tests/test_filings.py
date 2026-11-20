"""🤖 SEC filing summaries (2026-11-20)."""
import json
import unittest
from unittest import mock

import filings
import news


class Wanted(unittest.TestCase):
    def test_forms(self):
        self.assertTrue(filings.wanted({"form": "8-K", "items": ["1.01", "9.01"]}))
        self.assertFalse(filings.wanted({"form": "8-K", "items": ["2.02", "9.01"]}))   # results: press release covers it
        self.assertTrue(filings.wanted({"form": "424B5", "items": []}))
        self.assertTrue(filings.wanted({"form": "S-3", "items": []}))
        self.assertFalse(filings.wanted({"form": "4", "items": []}))
        self.assertFalse(filings.wanted({"form": "10-Q", "items": []}))


class Summarize(unittest.TestCase):
    IT = {"form": "8-K", "items": ["1.01", "8.01"], "cik": 1390478, "acc": "0001-26-000123", "doc": "sls-8k.htm",
          "ticker": "SLS"}

    def run_it(self, answer, text="x" * 500):
        filings._cache.clear()
        with mock.patch.object(filings, "SEC_CONTACT", "x"), mock.patch.object(filings, "filing_text", lambda it: text), \
                mock.patch.object(filings, "_ask", lambda *a: answer), \
                mock.patch("impact.ai_enabled", lambda: True):
            return filings.summarize(dict(self.IT), "SELLAS")

    def test_offering(self):
        s = self.run_it({"summary": "Sells 11.5M shares at $13.00 <b>now</b> https://evil.example", "dilution": "yes",
                         "dilution_why": "11.5 million new shares"})
        self.assertEqual(s["dilution"], "yes")
        self.assertNotIn("http", s["summary"])
        self.assertNotIn("<b>", s["summary"])
        ls = filings.lines(s)
        self.assertTrue(ls[0].startswith("🤖 Sells 11.5M shares"))
        self.assertIn("Dilution: YES", ls[1])

    def test_short_text_no_summary(self):
        self.assertIsNone(self.run_it({"summary": "x", "dilution": "no", "dilution_why": ""}, text="tiny"))

    def test_ai_failure_none(self):
        filings._cache.clear()
        def boom(*a):
            raise RuntimeError("stop_reason=refusal")
        with mock.patch.object(filings, "SEC_CONTACT", "x"), mock.patch.object(filings, "filing_text", lambda it: "x" * 500), \
                mock.patch.object(filings, "_ask", boom), mock.patch("impact.ai_enabled", lambda: True):
            self.assertIsNone(filings.summarize(dict(self.IT)))

    def test_no_dilution_line(self):
        self.assertEqual(len(filings.lines({"summary": "Signed a lease.", "dilution": "no", "why": ""})), 1)


class Message(unittest.TestCase):
    def test_summary_in_sec_message(self):
        w = news.NewsWatcher.__new__(news.NewsWatcher)
        it = {"ticker": "SLS", "kind": "sec", "form": "8-K", "items": ["1.01", "8.01", "9.01"], "link": "https://sec/x",
              "date": None, "desc": "", "ai_sum": {"summary": "Sells 11.5M shares at $13.", "dilution": "yes",
                                                     "why": "new shares"}}
        m = w._format(it)
        self.assertIn("1.01 material agreement; 8.01 other events", m)
        self.assertIn("🤖 Sells 11.5M shares at $13.", m)
        self.assertIn("💧 <b>Dilution: YES</b>", m)


if __name__ == "__main__":
    unittest.main()
