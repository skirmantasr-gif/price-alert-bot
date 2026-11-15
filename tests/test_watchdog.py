import unittest

import watchdog

M = 60


class Windows(unittest.TestCase):
    def test_quiet_while_fresh(self):
        for age in (0, 10 * M, 39 * M, None):
            self.assertFalse(watchdog.should_alert(age))

    def test_first_warning_window(self):
        self.assertTrue(watchdog.should_alert(40 * M))
        self.assertTrue(watchdog.should_alert(69 * M))
        self.assertFalse(watchdog.should_alert(71 * M))

    def test_one_alert_per_window_for_30_min_runs(self):
        # runs every 30 min at any offset: exactly one run falls in each window
        for offset in range(0, 30 * M, 5 * M):
            ages = [offset + k * 30 * M for k in range(0, 20)]
            first = [a for a in ages if watchdog.should_alert(a) and a < 6 * 3600]
            self.assertEqual(len(first), 1, offset)

    def test_reminder_every_6_hours(self):
        self.assertTrue(watchdog.should_alert(40 * M + 6 * 3600))
        self.assertFalse(watchdog.should_alert(40 * M + 3 * 3600))

    def test_message(self):
        self.assertIn("1 h 5 min", watchdog.message(65 * M))
        self.assertIn("45 min", watchdog.message(45 * M))


if __name__ == "__main__":
    unittest.main()
