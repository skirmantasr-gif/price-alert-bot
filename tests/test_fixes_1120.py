"""2026-11-20: CBOE needs a Referer; SEC insider feed retries a broken (unparsable) page."""
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import insiders
import options


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCboeReferer(unittest.TestCase):
    def test_referer_sent(self):
        seen = {}

        def fake(req, timeout=None):
            seen["referer"] = req.get_header("Referer")
            return _Resp(b"{}")

        with mock.patch.object(options.urllib.request, "urlopen", fake):
            options._get(options.URL.format("ORCL"))
        self.assertEqual(seen["referer"], "https://www.cboe.com/")


class TestInsiderParseRetry(unittest.TestCase):
    def test_parse_error_retried_then_ok(self):
        calls = []

        def fake(start):
            calls.append(start)
            if len(calls) == 1:
                raise ET.ParseError("cut-off page")
            return [("acc", "1")]

        with mock.patch.object(insiders, "feed_page", fake), mock.patch.object(insiders.time, "sleep"):
            self.assertEqual(insiders.feed_page_retry(0), [("acc", "1")])
        self.assertEqual(len(calls), 2)

    def test_parse_error_raises_after_retries(self):
        def fake(start):
            raise ET.ParseError("bad")

        with mock.patch.object(insiders, "feed_page", fake), mock.patch.object(insiders.time, "sleep"):
            with self.assertRaises(ET.ParseError):
                insiders.feed_page_retry(0)


class TestAdrValue(unittest.TestCase):
    ROWS = [["2026-11-17", "Burns Ursula M", "Director", 500000, False, "a1", 4000],
            ["2026-11-20", "Lu Lee-Chung", "VP", 303000, False, "a2", 2500]]

    def test_big_yahoo_value_not_replaced_by_sec(self):
        with mock.patch.object(insiders, "SEC_CONTACT", "x"), \
                mock.patch("movers.market_cap", return_value=1.8e12), \
                mock.patch.object(insiders, "sec_shares", return_value=25.9e9) as sh:
            self.assertEqual(insiders.market_cap("TSM", 352.09, "1046179"), 1.8e12)
        sh.assert_not_called()

    def test_small_wrong_yahoo_value_still_fixed(self):
        with mock.patch.object(insiders, "SEC_CONTACT", "x"), \
                mock.patch("movers.market_cap", return_value=2.1e6), \
                mock.patch.object(insiders, "sec_shares", return_value=9.7e6):
            self.assertAlmostEqual(insiders.market_cap("NXGL", 3.88, "1"), 9.7e6 * 3.88)

    def test_far_apart_average_not_compared(self):
        text, _ = insiders.message("TSM", "TSMC", 2, 803000, self.ROWS, 1.8e12, 352.09, 120.66)
        self.assertIn("price now $352.09", text)
        self.assertNotIn("vs their average", text)
        self.assertIn("$1800.0B", text)

    def test_normal_average_still_compared(self):
        text, _ = insiders.message("PAL", "Proficient", 2, 415000, self.ROWS, 69.9e6, 2.82, 2.65)
        self.assertIn("+6% vs their average $2.65", text)


import buyout


class TestBoNoise1120(unittest.TestCase):
    NOISE = [
        ("Surgery Partners, Inc. Announces Increased Consent Fee in Consent Solicitation Relating to Surgery "
         "Center Holdings, Inc.\u2019s 7.250% Senior Notes due 2032", "SGRY", "Surgery Partners, Inc."),
        ("Velo3D (VELO) CEO Arun Jeldi reports a Nov. 15 stock grant conversion and 3,145 shares acquired.",
         "VELO", "Velo3D, Inc."),
        ("EVgo (EVGO) CEO's stock awards vest; shares withheld using a $1.75 closing price.", "EVGO", "EVgo Inc."),
        ("Stockholders could resell shares tied to convertible notes, with no proceeds for FuboTV (FUBO).",
         "FUBO", "FuboTV Inc."),
        ("Tilray Brands shareholders re-elect directors, approve auditor, reject board changes By Investing.com",
         "TLRY", "Tilray Brands, Inc."),
        ("The CFO doubles as president at T1 Energy (TE) as its COO moves to chief commercial officer.",
         "TE", "T1 Energy Inc."),
        ("INVESTOR ALERT: Cardinal Infrastructure Group Inc. (NASDAQ: CDNL) Faces Investigation Following Severe "
         "Margin Collapse, Q3 Net Loss, and Full-Year Guidance Withdrawal - Hagens Berman", "CDNL",
         "Cardinal Infrastructure Group Inc."),
    ]
    REAL = [
        ("LCII: Merger approved preliminarily; advisory compensation proposal failed", "LCII", "LCI Industries"),
        ("Wendy's Bids $46M for 163 Restaurants in Meritage Bankruptcy", "WEN", "The Wendy's Company"),
        ("Velo3D Announces $20 Million Registered Direct Offering", "VELO", "Velo3D, Inc."),
        ("Tilray Brands Names New CEO", "TLRY", "Tilray Brands, Inc."),
    ]

    def test_noise_not_major(self):
        for title, tk, name in self.NOISE:
            self.assertFalse(buyout.major(title, tk, name), title)

    def test_real_still_major(self):
        for title, tk, name in self.REAL:
            self.assertTrue(buyout.major(title, tk, name), title)


if __name__ == "__main__":
    unittest.main()
