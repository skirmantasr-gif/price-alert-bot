"""Every module loads without secrets or internet; the Alpaca import is optional (old phone copies)."""
import importlib
import sys
import unittest


class Imports(unittest.TestCase):
    def test_all_modules_import(self):
        for m in ("alpaca", "bot", "buyout", "contracts", "extras", "impact", "insights", "learn", "movers", "news",
                  "paper", "watchlist"):
            with self.subTest(module=m):
                importlib.import_module(m)

    def test_works_without_alpaca_file(self):
        import contracts
        import news
        saved = sys.modules.get("alpaca")
        sys.modules["alpaca"] = None                     # makes "import alpaca" fail
        try:
            n = importlib.reload(news)
            c = importlib.reload(contracts)
            self.assertFalse(n.alpaca.ON)
            self.assertEqual(n.alpaca_news(["ZNTX"]), [])
            self.assertFalse(c.alpaca.ON)
        finally:
            sys.modules["alpaca"] = saved
            importlib.reload(news)
            importlib.reload(contracts)


if __name__ == "__main__":
    unittest.main()
