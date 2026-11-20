import unittest
from datetime import date

import catalysts
import funds

DXYZ_TEXT = ("In connection with our regular net asset value determination process, as provided in our valuation "
             "policies and procedures, our net asset value as of September 30, 2026, is $38.32 per share of our "
             "common stock.")


class Funds(unittest.TestCase):
    def setUp(self):
        funds._cache.clear()
        self.old = funds.SEC_CONTACT
        funds.SEC_CONTACT = "test"

    def tearDown(self):
        funds.SEC_CONTACT = self.old

    def test_find_nav(self):
        self.assertEqual(funds.find_nav(DXYZ_TEXT), (38.32, "2026-09-30"))
        self.assertEqual(funds.find_nav("Our NAV per share was $12.05 at quarter end"), (12.05, ""))
        self.assertIsNone(funds.find_nav("net asset value determination process"))

    def test_nav_only_for_funds(self):
        fl = [{"form": "424B3", "doc": "a.htm", "link": "https://x/a.htm"}, {"form": "NPORT-P", "doc": "p.xml"}]
        self.assertEqual(funds.nav("DXYZ", filings_of=lambda t: fl, text_of=lambda u: DXYZ_TEXT), (38.32, "2026-09-30"))
        self.assertIsNone(funds.nav("CAPR", filings_of=lambda t: [{"form": "424B3", "doc": "a.htm", "link": "x"}],
                                    text_of=lambda u: DXYZ_TEXT))   # an operating company's prospectus: no NAV line

    def test_line(self):
        ln = funds.line("DXYZ", 24.74, nav_of=lambda t: (38.32, "2026-09-30"))
        self.assertIn("Below its holdings value", ln)
        self.assertIn("35% discount", ln)
        self.assertIn("Sep 30", ln)
        self.assertIn("premium", funds.line("X", 12.0, nav_of=lambda t: (10.0, "")))
        self.assertEqual(funds.line("X", None, nav_of=lambda t: (10.0, "")), "")
        self.assertEqual(funds.line("X", 5.0, nav_of=lambda t: None), "")


class Holidays(unittest.TestCase):
    OLD = {  # the hand-typed list this replaced
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
        "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
        "2027-11-25", "2027-12-24"}

    def test_matches_old_list(self):
        self.assertEqual(catalysts.nyse_holidays(2026) | catalysts.nyse_holidays(2027), self.OLD)

    def test_later_years(self):
        h28 = catalysts.nyse_holidays(2028)
        self.assertNotIn("2027-12-31", h28 | catalysts.nyse_holidays(2027))  # New Year on Saturday: no closure
        self.assertIn("2028-04-14", h28)       # Good Friday 2028
        self.assertIn("2028-06-19", h28)
        self.assertIn("2028-11-23", h28)       # Thanksgiving
        self.assertIn("2034-01-02", catalysts.nyse_holidays(2034))   # New Year on Sunday -> Monday
        self.assertFalse(catalysts.trading_day(date(2030, 12, 25)))
        self.assertTrue(catalysts.trading_day(date(2030, 12, 24)))


class Missed(unittest.TestCase):
    def state(self):
        return {"_cal": [["SVRA", "2026-11-22", "fda", "PDUFA"]],
                "_fdacal": {"ev": {"SVRA:2026-11-22": {"t": "SVRA", "name": "Savara", "date": "2026-11-22",
                                                       "filed": "2026-11-01", "snip": "PDUFA Nov 22"},
                                   "CAPR:2026-11-22": {"t": "CAPR", "name": "Capricor", "date": "2026-11-22",
                                                       "filed": "2026-11-01", "snip": "PDUFA Nov 22"}}, "docs": []}}

    def test_mark_and_effects(self):
        st = self.state()
        today = date(2026, 11, 19)
        self.assertFalse(catalysts.mark_missed(st, "SVRA", "Savara shares fall on FDA review", today))
        self.assertTrue(catalysts.mark_missed(
            st, "SVRA", "Savara says FDA will not meet the PDUFA date for MOLBREEVI", today))
        self.assertIn("SVRA:2026-11-22", st["_fdacal"]["missed"])
        self.assertFalse(catalysts.mark_missed(
            st, "SVRA", "Savara says FDA will not meet the PDUFA date for MOLBREEVI", today))   # once
        # ⏰ weekend warning on Fri Nov 20 only for CAPR now
        self.assertEqual(catalysts.closed_day_events(st, date(2026, 11, 20)), [("CAPR", "2026-11-22")])
        # 💊 list shows the miss
        cal = catalysts.FdaCalendar.__new__(catalysts.FdaCalendar)
        cal.watch = set()
        import movers
        import options
        old = movers.market_cap, options.get
        movers.market_cap, options.get = (lambda t: None), (lambda t: None)
        try:
            txt = cal.list_message(st["_fdacal"]["ev"], today, st["_fdacal"]["missed"])
        finally:
            movers.market_cap, options.get = old
        svra = [ln for ln in txt.split("\n") if "SVRA" in ln][0]
        self.assertIn("MISS", svra)
        self.assertIn("will not meet", txt)

    def test_real_svra_headlines(self):
        for h in ("Savara shares fall 20% as FDA says it will miss Molgramostim review deadline",
                  "Savara drops after FDA says it will not meet molgramostim BLA action date",
                  "Savara Shares Fall 18% After Company Says FDA Will Miss Molgramostim Action Date"):
            st = self.state()
            self.assertTrue(catalysts.mark_missed(st, "SVRA", h, date(2026, 11, 19)), h)
        for h in ("SVRA Stock Hits 52-Week Low After FDA Delays Decision On Lung Disease Drug Application",
                  "Savara misses revenue estimates; FDA decision due Sunday",
                  "Why is Savara stock plunging today?"):
            self.assertFalse(catalysts.mark_missed(self.state(), "SVRA", h, date(2026, 11, 19)), h)

    def test_other_wording_and_window(self):
        st = self.state()
        self.assertTrue(catalysts.mark_missed(st, "CAPR", "Capricor: FDA missed its PDUFA goal date", date(2026, 11, 24)))
        st = self.state()
        self.assertFalse(catalysts.mark_missed(st, "CAPR", "FDA will not meet PDUFA", date(2026, 12, 20)))  # too late

    def test_result_kind(self):
        st = self.state()
        catalysts.mark_missed(st, "SVRA", "FDA will not meet the PDUFA date", date(2026, 11, 19))
        st["_opth"] = {"SVRA:2026-11-22": {"t": "SVRA", "event": "2026-11-22", "kind": "fda", "snaps": []}}
        bars = [(f"2026-11-{d:02d}", 10, 10 + (d == 23) * -2, 1000 * (3 if d == 23 else 1)) for d in range(2, 25)
                if date(2026, 11, d).weekday() < 5]
        sent = []
        rw = catalysts.ResultWatch(lambda t, s: sent.append(t), lambda t, s: sent.append(t), ["SVRA"],
                                   bars=lambda t: bars)
        rw.check(st, now=__import__("datetime").datetime(2026, 11, 24, 12))
        self.assertEqual(len(sent), 1)
        self.assertIn("FDA missed it", sent[0])
        self.assertEqual(st["_evres"]["rows"][-1]["kind"], "fda_missed")


if __name__ == "__main__":
    unittest.main()
