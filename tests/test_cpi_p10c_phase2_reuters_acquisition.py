"""Fail-closed tests for the CPI-E1-P10C Phase 2 Reuters acquisition bundle.

Re-runs the read-only validator's checks as pytest assertions so drift in the
committed evidence (digest mismatch, count drift, event-set drift, precision
promotion, or a PASS row losing its cutoff/corroboration guarantees) fails CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/reviews/artifacts/cpi-p10c-reuters-phase2"
FREEZE = ROOT / "docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json"


def _load(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text())
    return result


def test_validator_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_cpi_p10c_phase2_reuters_acquisition.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_coverage_reconciles_to_42() -> None:
    coverage = _load(BUNDLE / "coverage.json")
    events = coverage["events"]
    assert len(events) == 42
    assert len({e["event_ticker"] for e in events}) == 42

    freeze = _load(FREEZE)
    freeze_tickers = {e["event_ticker"] for e in freeze["events"]}
    assert {e["event_ticker"] for e in events} == freeze_tickers

    arith = coverage["coverage_arithmetic"]
    pass_count = sum(1 for e in events if e["terminal_state"] == "PASS")
    unknown_count = sum(1 for e in events if e["terminal_state"] == "UNKNOWN")
    failure_count = sum(1 for e in events if e["terminal_state"] == "ACQUISITION_FAILURE")
    assert pass_count == arith["positively_proven_observations"] == 5
    assert unknown_count == arith["searched_no_qualifying_observation_found"] == 37
    assert failure_count == arith["acquisition_authority_failures"] == 0
    assert pass_count + unknown_count + failure_count == 42


def test_no_kalshi_scoring_or_production_influence() -> None:
    coverage = _load(BUNDLE / "coverage.json")
    assert coverage["production_influence"] == 0
    assert coverage["kalshi_scoring_performed"] is False
    assert coverage["edge_pnl_fees_computed"] is False
    assert coverage["cutoff_semantics"] == "per_sibling_market"
    assert coverage["research_only"] is True


def test_pass_receipts_precede_frozen_cutoffs_with_exact_precision() -> None:
    freeze = _load(FREEZE)
    expected_values = {"CPI-23AUG": Decimal("0.6"), "CPI-24JAN": Decimal("0.2")}

    for ticker, expected in expected_values.items():
        receipt = _load(BUNDLE / ticker / "receipt.json")
        value = Decimal(receipt["value"])
        assert value == expected
        assert str(value) == str(expected)  # no precision promotion

        event = next(e for e in freeze["events"] if e["event_ticker"] == ticker)
        from datetime import datetime

        published_at = datetime.fromisoformat(receipt["published_at"].replace("Z", "+00:00"))
        for sibling in event["accepted_siblings"]:
            cutoff = datetime.fromisoformat(sibling["sibling_cutoff"])
            assert published_at < cutoff

        hosts = {f["host"] for f in receipt["fetches"] if f.get("http_status") == 200}
        assert len(hosts) >= 2
        assert receipt["retrospective_language_found"] is False
        assert receipt["vintage_status"] == "PASS"


def test_manifest_artifact_hashes_match_disk() -> None:
    import hashlib

    manifest = _load(BUNDLE / "manifest.json")
    for entry in manifest["committed_artifacts"]:
        path = ROOT / entry["path"]
        assert path.is_file(), f"missing {entry['path']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], f"hash mismatch for {entry['path']}"
