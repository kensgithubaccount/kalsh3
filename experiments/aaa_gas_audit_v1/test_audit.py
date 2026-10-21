import unittest
from audit import validate, evaluate

BASE = dict(record_id="a", market_ticker="KXAAAGASD-26OCT22-4.2650",
            side="NO", threshold="4.2650", observation_date="2026-10-22",
            recommendation_date="2026-10-21", production_influence=False,
            research_only=True, settlement_value=None)

class AuditTests(unittest.TestCase):
    def test_unresolved(self):
        self.assertEqual(evaluate(validate(dict(BASE))), "UNRESOLVED")
    def test_strict_boundary(self):
        r = dict(BASE, settlement_value="4.2650", settlement_source_receipt="verified")
        self.assertEqual(evaluate(validate(r)), "WIN")
        r["settlement_value"] = "4.2651"
        self.assertEqual(evaluate(validate(r)), "LOSS")
    def test_reject_fill_without_receipt(self):
        with self.assertRaises(ValueError):
            validate(dict(BASE, fill_status="filled"))
    def test_reject_production(self):
        with self.assertRaises(ValueError):
            validate(dict(BASE, production_influence=True))
    def test_reject_retrospective(self):
        with self.assertRaises(ValueError):
            validate(dict(BASE, recommendation_date="2026-10-23"))

if __name__ == "__main__":
    unittest.main()
