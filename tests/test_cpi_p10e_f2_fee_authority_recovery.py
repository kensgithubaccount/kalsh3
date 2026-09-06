import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = ROOT / "docs/reviews/artifacts/cpi-p10e-f2-fee-authority-recovery/coverage.json"


def _coverage() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(COVERAGE.read_bytes()))


def test_f2_coverage_digest_and_hard_gate() -> None:
    coverage = _coverage()
    unsigned = dict(coverage)
    declared = unsigned.pop("coverage_digest_sha256")
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode()).hexdigest() == declared
    assert coverage["counts"] == {"pass": 0, "unknown": 2, "failure": 0}
    assert coverage["phase1_ready"] is False
    assert coverage["strategy_pnl_computed"] is False


def test_f2_covers_exactly_the_two_frozen_candidates() -> None:
    rows = _coverage()["candidate_fee_coverage"]
    actual = [
        (row["event_ticker"], row["market_ticker"], row["execution_timestamp"]) for row in rows
    ]
    assert actual == [
        ("CPI-23AUG", "CPI-23AUG-T0.6", "2023-09-13T12:25:00+00:00"),
        ("KXCPI-25JUL", "KXCPI-25JUL-T0.2", "2025-08-12T12:29:00+00:00"),
    ]
    assert all(row["side"] == "NO" for row in rows)
    assert all(row["required_class"] == "marketable crossing / taker" for row in rows)
    assert all(row["fee_status"] == "UNKNOWN" for row in rows)
