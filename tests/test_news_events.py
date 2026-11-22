"""AI news calls need a named event (2026-11-22, after the 🎓 review: 36% right)."""
import unittest

import impact


def it(title, desc="", contract=None):
    return {"title": title, "desc": desc, "contract": contract}


class NeedsEvent(unittest.TestCase):
    def test_kept(self):
        for h in ["US FDA declines to approve Capricor's Duchenne treatment citing unreliable trial data",
                  "SELLAS reports positive Phase 3 REGAL topline results",
                  "Savara prices $100 million public offering",
                  "Verlinvest commences tender offer for Oatly",
                  "Coherus Q3 revenue misses, cuts guidance"]:
            self.assertEqual(impact.needs_event(it(h), "down", "medium"), ("down", ""), h)

    def test_neutralised(self):
        cases = [("Klarna Group (NYSE:KLAR) Stock Rating Lowered by Wall Street Zen", "medium", "analyst rating change"),
                 ("Jefferies upgrades Criteo to Buy, price target $40", "high", "analyst rating change"),
                 ("Amdocs (DOX) Is Back In Focus, What Is Driving Attention Now?", "medium", "no named event"),
                 ("Capricor shares jump on FDA approval", "low", "low confidence")]
        for h, c, why in cases:
            self.assertEqual(impact.needs_event(it(h), "up", c), ("neutral", why), h)

    def test_contract_and_neutral_untouched(self):
        self.assertEqual(impact.needs_event(it("Nikon gets Pentagon money", contract=23.5e6), "up", "medium"), ("up", ""))
        self.assertEqual(impact.needs_event(it("Anything"), "neutral", "low"), ("neutral", ""))

    def test_event_in_snippet_counts(self):
        self.assertEqual(impact.needs_event(it("Capricor Therapeutics Provides Regulatory Update on Deramiocel BLA",
                                               "Company receives Complete Response Letter from FDA"), "down", "high"),
                         ("down", ""))


if __name__ == "__main__":
    unittest.main()
