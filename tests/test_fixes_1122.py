"""2026-11-22 check: CAPR CRL night spam (~40 price alerts), stale 'TODAY' FDA dates, slow FDA-decision alert."""
import unittest
from datetime import date, datetime, timedelta, timezone

import bot
import catalysts
import extras

PREV = 7.53
# (LT time, price) of the CAPR alerts sent Nov 21 01:17–02:59 LT, as they appeared in Telegram
CAPR_NIGHT = [("01:17", 4.50), ("01:19", 5.02), ("01:20", 3.58), ("01:22", 3.37), ("01:24", 5.00), ("01:25", 5.53),
              ("01:27", 4.50), ("01:29", 2.71), ("01:30", 2.20), ("01:32", 2.38), ("01:33", 1.76), ("01:35", 2.11),
              ("01:39", 2.20), ("01:41", 2.11), ("01:42", 1.90), ("01:48", 1.98), ("01:50", 2.15), ("01:54", 1.99),
              ("01:57", 1.91), ("01:58", 1.76), ("02:01", 1.60), ("02:04", 1.38), ("02:06", 1.44), ("02:07", 1.50),
              ("02:09", 1.42), ("02:10", 1.52), ("02:13", 1.45), ("02:19", 1.54), ("02:23", 1.47), ("02:29", 1.58),
              ("02:31", 1.66), ("02:38", 1.75), ("02:46", 1.67), ("02:57", 1.59), ("02:59", 1.67)]


def _ts(hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc).timestamp() + ((h + 24 - 1) % 24) * 3600 + m * 60


class BigDayDamping(unittest.TestCase):
    def replay(self):
        ref, ref_time, sent = 7.79, datetime.fromtimestamp(_ts("00:21"), timezone.utc).isoformat(), []
        for hm, p in CAPR_NIGHT:
            change, day = p / ref - 1, p / PREV - 1
            if abs(change) >= 0.04 and not bot.big_day_hold(change, day, 0.04, ref_time, now=_ts(hm)):
                sent.append(hm)
                ref, ref_time = p, datetime.fromtimestamp(_ts(hm), timezone.utc).isoformat()
        return sent

    def test_capr_night_far_fewer_alerts(self):
        sent = self.replay()
        self.assertEqual(sent[0], "01:17")           # the crash itself is still reported at once
        self.assertLessEqual(len(sent), 12, sent)    # was 35 in this list (≈40 in all)
        self.assertGreaterEqual(len(sent), 4, sent)  # the big legs (−40%, −64%, −77%) still come through

    def test_normal_day_untouched(self):
        self.assertFalse(bot.big_day_hold(0.03, 0.05, 0.02, datetime.now(timezone.utc).isoformat()))
        self.assertFalse(bot.big_day_hold(-0.05, None, 0.04, None))

    def test_big_day_small_step_waits(self):
        now = time_now = datetime.now(timezone.utc)
        self.assertTrue(bot.big_day_hold(0.06, -0.7, 0.04, now.isoformat(), now=time_now.timestamp()))
        # 10%+ but only 2 min after the last alert: wait …
        self.assertTrue(bot.big_day_hold(0.12, -0.7, 0.04, now.isoformat(), now=now.timestamp() + 120))
        # … unless it is a new 25%+ leg
        self.assertFalse(bot.big_day_hold(-0.30, -0.7, 0.04, now.isoformat(), now=now.timestamp() + 120))
        # 10%+ and 15 min later: alert
        self.assertFalse(bot.big_day_hold(0.12, -0.7, 0.04, now.isoformat(), now=now.timestamp() + 901))


def _state(t="CAPR", iso="2026-11-22"):
    return {"_cal": [[t, iso, "fda", "FDA decision date (PDUFA)"]], "_fdacal": {"ev": {}, "docs": []}}


class Decided(unittest.TestCase):
    TODAY = date(2026, 11, 21)

    def test_real_decision_headlines(self):
        for h in ["US FDA declines to approve Capricor's Duchenne treatment citing unreliable trial data; shares slump",
                  "Capricor receives Complete Response Letter from FDA for Deramiocel BLA",
                  "FDA rejects Capricor's cell therapy again",
                  "Capricor wins FDA approval for deramiocel",
                  "FDA approves Capricor's deramiocel for Duchenne"]:
            st = _state()
            self.assertTrue(catalysts.mark_decided(st, "CAPR", h, self.TODAY, ["Capricor"]), h)
            self.assertIn("CAPR:2026-11-22", catalysts.decided_keys(st))

    def test_not_decisions(self):
        for h in ["Will the FDA approve Capricor's deramiocel this weekend?",
                  "Capricor faces FDA decision on deramiocel",
                  "CAPR Stock Gains Ahead Of Weekend FDA Deramiocel Decision After Rough Week",
                  "SMMT Stock Plunges Overnight On FDA Rejection — CAPR Holders Feel The Jitters",
                  "FDA rejects Summit's ivonescimab",                       # other company
                  "Capricor could win FDA approval, analysts say",
                  "Capricor Therapeutics Provides Regulatory Update on Deramiocel BLA"]:
            st = _state()
            self.assertFalse(catalysts.mark_decided(st, "CAPR", h, self.TODAY, ["Capricor"]), h)

    def test_far_dates_untouched(self):
        st = _state(iso="2027-03-31")
        self.assertFalse(catalysts.mark_decided(st, "CAPR", "FDA approves Capricor's deramiocel", self.TODAY))

    def test_mark_news_both(self):
        st = _state("SVRA")
        self.assertTrue(catalysts.mark_news(st, "SVRA", "Savara says US FDA will not meet PDUFA target action date",
                                            self.TODAY, ["Savara"]))
        self.assertIn("SVRA:2026-11-22", catalysts.missed_keys(st))


class DailyCheckStatus(unittest.TestCase):
    def test_labels(self):
        st = _state()
        e = st["_cal"][0]
        self.assertEqual(extras._fda_status(st, e), "")
        catalysts.mark_missed(st, "CAPR", "FDA will not meet the PDUFA date for Capricor", date(2026, 11, 21))
        self.assertIn("will miss", extras._fda_status(st, e))
        catalysts.mark_decided(st, "CAPR", "FDA declines to approve Capricor's therapy", date(2026, 11, 21), ["Capricor"])
        self.assertIn("already decided", extras._fda_status(st, e))
        self.assertEqual(extras._fda_status(st, ["BZUN", "2026-11-24", "earnings", "earnings"]), "")

    def test_list_message_decided(self):
        cal = catalysts.FdaCalendar.__new__(catalysts.FdaCalendar)
        cal.watch = set()
        ev = {"k": {"t": "CAPR", "date": "2026-11-22"}}
        txt = cal.list_message(ev, date(2026, 11, 21), {}, {"CAPR:2026-11-22": {"title": "FDA declines to approve X"}})
        self.assertIn("already decided", txt)


class Shock(unittest.TestCase):
    def test_once_per_date(self):
        st = _state()
        today = date(2026, 11, 20)
        m = catalysts.shock_check(st, "CAPR", -0.40, "after-hours", today)
        self.assertIn("💊⚡", m)
        self.assertIn("REJECTED", m)
        self.assertIn("in 2 days", m)
        self.assertEqual(catalysts.shock_check(st, "CAPR", -0.60, "after-hours", today), "")   # no repeat
        self.assertIn("decision probably out", extras._fda_status(st, st["_cal"][0]))

    def test_small_move_far_date_or_decided(self):
        st = _state()
        self.assertEqual(catalysts.shock_check(st, "CAPR", -0.08, "regular", date(2026, 11, 20)), "")
        self.assertEqual(catalysts.shock_check(st, "CAPR", -0.40, "regular", date(2026, 11, 10)), "")
        st["_fdacal"]["ev"]["x"] = {"t": "CAPR", "name": "Capricor Therapeutics, Inc.", "date": "2026-11-22"}
        self.assertTrue(catalysts.mark_decided(st, "CAPR", "FDA declines to approve Capricor's therapy", date(2026, 11, 20)))
        self.assertEqual(catalysts.shock_check(st, "CAPR", -0.40, "regular", date(2026, 11, 20)), "")

    def test_up_move(self):
        st = _state("SVRA")
        self.assertIn("APPROVED", catalysts.shock_check(st, "SVRA", 0.35, "pre-market", date(2026, 11, 22)))


if __name__ == "__main__":
    unittest.main()
