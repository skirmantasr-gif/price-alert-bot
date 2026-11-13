import time, unittest
import news


class SourceWarningOnce(unittest.TestCase):
    def test_one_warning_per_source_not_per_ticker(self):
        sent = []
        w = news.NewsWatcher(["AAA", "BBB", "CCC"], sent.append)

        def boom(_t):
            raise OSError("blocked")
        for t in w.tickers:
            w.fail[f"Google:{t}"] = time.time() - 4000
            w.fail[f"Google contracts:{t}"] = time.time() - 4000
        for _ in range(3):
            for t in w.tickers:
                w._fetch(f"Google:{t}", boom, t)
                w._fetch(f"Google contracts:{t}", boom, t)
        self.assertEqual(len(sent), 2)  # one for Google, one for Google contracts

    def test_rewarns_after_full_recovery(self):
        sent = []
        w = news.NewsWatcher(["AAA"], sent.append)
        w.fail["Google:AAA"] = time.time() - 4000
        w._fetch("Google:AAA", lambda t: (_ for _ in ()).throw(OSError()), "AAA")
        w._fetch("Google:AAA", lambda t: [], "AAA")          # recovers
        w.fail["Google:AAA"] = time.time() - 4000
        w._fetch("Google:AAA", lambda t: (_ for _ in ()).throw(OSError()), "AAA")
        self.assertEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
