"""🔄 Buying / selling pressure estimate in volume alerts (1-minute bars)."""
import unittest

import extras


def chart(closes, vols):
    t0 = 1_000_000
    return [t0 + 60 * i for i in range(len(closes))], {"close": closes, "high": closes, "low": closes, "volume": vols}


class Flow(unittest.TestCase):
    def test_selling_pressure(self):
        st, q = chart([10, 9.9, 9.8, 9.85, 9.7, 9.6], [100, 500, 500, 100, 500, 500])
        f = extras.flow(st, q, 0, 2e9)
        self.assertAlmostEqual(f["up"], 100 / 2100)        # only the 9.8 -> 9.85 minute rose
        line = extras.flow_line(st, q, 0, 2e9, 9.6)
        self.assertIn("selling</b> pressure", line)
        self.assertIn("below today's average", line)

    def test_unchanged_minutes_count_half(self):
        st, q = chart([10, 10, 10, 10, 10, 10], [100] * 6)
        self.assertAlmostEqual(extras.flow(st, q, 0, 2e9)["up"], 0.5)
        self.assertIn("balanced", extras.flow_line(st, q, 0, 2e9, 10))

    def test_no_volume_no_line(self):
        st, q = chart([10, 10.1, 10.2, 10.3, 10.4, 10.5], [0] * 6)   # Yahoo pre-market: no volume
        self.assertIsNone(extras.flow(st, q, 0, 2e9))
        self.assertEqual(extras.flow_line(st, q, 0, 2e9, 10.5), "")

    def test_last_30_minutes_shown_when_different(self):
        closes = [10 + 0.01 * i for i in range(40)] + [10.4 - 0.01 * i for i in range(30)]
        st, q = chart(closes, [100] * 70)
        self.assertIn("last 30 min: 3% rising", extras.flow_line(st, q, 0, 2e9, closes[-1]))


if __name__ == "__main__":
    unittest.main()
