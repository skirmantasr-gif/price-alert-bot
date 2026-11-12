"""🧾 Insider tax check: is a Form 4 sale about taxes rather than a view on the stock?"""
import unittest

import impact
import news


def tx(code, sh, d, deriv=False):
    return {"code": code, "deriv": deriv, "security": "", "shares": sh, "price": 10.0, "value": sh * 10.0,
            "after": None, "date": d}


class TaxCheck(unittest.TestCase):
    def test_withholding(self):
        self.assertEqual(news.tax_check([tx("A", 1000, "2026-11-10"), tx("F", 350, "2026-11-10")], "", False)[0],
                         "withholding")

    def test_sell_to_cover_by_footnote(self):
        notes = "The shares were sold to cover tax withholding obligations in connection with the vesting of RSUs."
        self.assertEqual(news.tax_check([tx("S", 5000, "2026-11-10")], notes, False)[0], "sell_to_cover")

    def test_sell_to_cover_by_same_day_partial_sale(self):
        self.assertEqual(news.tax_check([tx("A", 1000, "2026-11-10"), tx("S", 400, "2026-11-10")], "", False)[0],
                         "sell_to_cover")

    def test_full_same_day_sale_is_cashing_out_not_tax(self):
        self.assertEqual(news.tax_check([tx("M", 1000, "2026-11-10"), tx("S", 1000, "2026-11-10")], "", False)[0], "")

    def test_plain_sale_and_december(self):
        self.assertEqual(news.tax_check([tx("S", 5000, "2026-11-10")], "", False), ("", ""))
        self.assertEqual(news.tax_check([tx("S", 5000, "2026-12-15")], "", False)[0], "year_end")
        self.assertEqual(news.tax_check([tx("S", 5000, "2026-12-15")], "", True)[0], "")   # pre-planned: not year-end

    def test_buy_never_tax(self):
        self.assertEqual(news.tax_check([tx("P", 5000, "2026-11-10")], "", False), ("", ""))


class Effects(unittest.TestCase):
    def ins(self, tax):
        return {"type": "4", "owners": ["Smith John"], "role": "Chief Executive Officer", "plan": False,
                "tx": [tx("S", 200000, "2026-11-10")], "holds": 0, "tax": tax,
                "tax_text": "the filing says the sale paid taxes on vesting / exercised stock" if tax else ""}

    def test_tax_sale_neutral_and_quiet(self):
        it = {"form": "4", "ticker": "ZNTX", "insider": self.ins("sell_to_cover")}
        self.assertEqual(impact.rule_sec(it)[0], "neutral")
        lines = news.format_insider(it, it["insider"])
        self.assertTrue(lines[0].startswith("⚪👤 INSIDER SALE (tax)"))
        self.assertIn("🧾 Tax check", lines[-1])

    def test_real_sale_still_counts(self):
        it = {"form": "4", "ticker": "ZNTX", "insider": self.ins("")}
        self.assertEqual(impact.rule_sec(it)[0], "down")          # $2M sale, no tax link
        self.assertTrue(news.format_insider(it, it["insider"])[0].startswith("🔴👤 INSIDER SALE"))


if __name__ == "__main__":
    unittest.main()
