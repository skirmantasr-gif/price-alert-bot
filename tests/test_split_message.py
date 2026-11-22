"""Long Telegram messages are split, not lost (2026-11-22)."""
import re
import unittest
from unittest import mock

import bot


def balanced(part):
    for t in ("b", "i", "pre", "code"):
        if len(re.findall(f"<{t}[ >]", part)) != part.count(f"</{t}>"):
            return False
    return True


class Split(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(bot.split_message("hi <b>x</b>"), ["hi <b>x</b>"])

    def test_long_cut_at_lines_and_tags_balanced(self):
        lines = ["<b>📅 Weekly review</b>"] + [f"• <b>$T{i}</b> line {i} " + "x" * 80 for i in range(120)]
        text = "\n".join(lines)
        parts = bot.split_message(text, limit=1000)
        self.assertGreater(len(parts), 5)
        self.assertTrue(all(len(p) <= 1000 for p in parts))
        self.assertTrue(all(balanced(p) for p in parts))
        self.assertEqual("\n".join(parts).count("line "), 120)       # nothing lost

    def test_pre_block_reopened(self):
        text = "<b>Buyout watch</b>\n<pre>" + "\n".join(f"ROW{i:03d} " + "y" * 60 for i in range(60)) + "</pre>\nend"
        parts = bot.split_message(text, limit=800)
        self.assertTrue(all(balanced(p) for p in parts))
        self.assertTrue(parts[1].startswith("<pre>"))
        self.assertTrue(parts[-1].rstrip().endswith("end"))

    def test_send_parts_second_quiet(self):
        calls = []
        with mock.patch.object(bot, "telegram_call", lambda m, p, c=None: calls.append(p) or True), \
                mock.patch.object(bot, "TG_LIMIT", 500):
            text = "\n".join("line " + "z" * 90 for _ in range(30))
            self.assertTrue(bot.send_telegram(text, False))
        self.assertGreater(len(calls), 1)
        self.assertNotIn("disable_notification", calls[0])
        self.assertTrue(all(c.get("disable_notification") == "true" for c in calls[1:]))


if __name__ == "__main__":
    unittest.main()
