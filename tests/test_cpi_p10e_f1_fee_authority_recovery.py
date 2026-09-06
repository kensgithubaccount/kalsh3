import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = ROOT / "docs/reviews/artifacts/cpi-p10e-f1-fee-authority-recovery/coverage.json"


def _coverage() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(COVERAGE.read_bytes()))


def test_coverage_digest_is_deterministic_and_no_economics_were_run() -> None:
    coverage = _coverage()
    declared = coverage["coverage_digest_sha256"]
    unsigned = dict(coverage)
    del unsigned["coverage_digest_sha256"]
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode()).hexdigest() == declared
    assert coverage["completeness_gate"] == {
        "pass_count": 0,
        "unknown_count": 2,
        "failure_count": 0,
        "phase1_ready": False,
        "classification": "BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE",
        "strategy_pnl_computed": False,
        "outcomes_used_for_fee_selection": False,
    }


def test_exactly_the_two_frozen_disagreement_candidates_are_covered() -> None:
    rows = _coverage()["candidate_fee_coverage"]
    actual = [
        (row["event_ticker"], row["market_ticker"], row["execution_timestamp"])
        for row in rows
    ]
    assert actual == [
        ("CPI-23AUG", "CPI-23AUG-T0.6", "2023-09-13T12:25:00+00:00"),
        ("KXCPI-25JUL", "KXCPI-25JUL-T0.2", "2025-08-12T12:29:00+00:00"),
    ]
    assert all(row["reuters_side"] == "NO" for row in rows)
    assert all(row["crossing_status"] == "FROZEN MARKETABLE CROSSING / TAKER CLASS" for row in rows)
    assert all(row["fee_status"] == "UNKNOWN" for row in rows)
