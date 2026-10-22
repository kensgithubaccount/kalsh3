import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal
from calibrate import load, analyze
from capture import write_snapshot
import json

class WorkflowTests(unittest.TestCase):
    def test_no_history_no_probability(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prices.csv"
            p.write_text("date,national_regular_usd,source_url,source_sha256,acquired_at_utc\n"
                         "2026-10-20,4.2786,https://gasprices.aaa.com/,hash,2026-10-20T12:00:00Z\n"
                         "2026-10-21,4.2644,https://gasprices.aaa.com/,hash,2026-10-21T12:00:00Z\n")
            self.assertEqual(analyze(load(p))["status"], "INSUFFICIENT_HISTORY")
    def test_reject_gaps(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prices.csv"
            p.write_text("date,national_regular_usd,source_url,source_sha256,acquired_at_utc\n"
                         "2026-10-19,4.2,url,hash,time\n2026-10-21,4.3,url,hash,time\n")
            with self.assertRaises(ValueError):
                load(p)
    def test_snapshot_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"artifacts": [], "research_only": True, "production_influence": False}
            p = write_snapshot(d, [("aaa.html", {"sha256": "x"}, b"hello")], m)
            self.assertEqual((p / "aaa.html").read_bytes(), b"hello")
            self.assertFalse(json.loads((p / "manifest.json").read_text())["production_influence"])
    def test_walk_forward(self):
        rows = [(date(2026,1,1)+timedelta(days=i), Decimal("4.0")+Decimal(i)/1000, {}) for i in range(8)]
        result = analyze(rows, min_train=3)
        self.assertEqual(result["walk_forward_test_days"], 4)
        self.assertFalse(result["production_authority"])

if __name__ == "__main__":
    unittest.main()
