import unittest

import insiders
import movers

FORM4 = """<ownershipDocument><issuer><issuerCik>1</issuerCik><issuerName>FLAGSTAR BANK</issuerName>
<issuerTradingSymbol>NYSE:FLG</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>Otting Joseph M</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
</reportingOwnerRelationship></reportingOwner><nonDerivativeTable></nonDerivativeTable></ownershipDocument>"""


class Fixes(unittest.TestCase):
    def test_exchange_prefix_removed(self):
        self.assertEqual(insiders.parse_form4(FORM4)["ticker"], "FLG")

    def test_megacap_glitch(self):
        self.assertTrue(movers.glitch({"cap": 967e9}, -88.8))
        self.assertFalse(movers.glitch({"cap": 967e9}, -12.0))
        self.assertFalse(movers.glitch({"cap": 400e6}, -60.0))   # small caps really do this on news
        self.assertFalse(movers.glitch({"cap": None}, 70.0))


if __name__ == "__main__":
    unittest.main()
