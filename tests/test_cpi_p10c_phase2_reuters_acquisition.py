"""Fail-closed tests for the CPI-E1-P10C Phase 2 Reuters acquisition bundle.

Re-runs the read-only validator's checks as pytest assertions so drift in the
committed evidence (digest mismatch, count drift, event-set drift, precision
promotion, a resurrected event-level decision_cutoff, or per-sibling
eligibility drifting from the frozen manifest) fails CI.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/reviews/artifacts/cpi-p10c-reuters-phase2"
FREEZE = ROOT / "docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json"

# New Phase 2 PASS events only (P10B's reused PASS events are separate,
# already-reviewed evidence from a merged PR and out of scope for the
# per-sibling-eligibility repair).
NEW_PASS_EVENTS = {"CPI-23AUG": Decimal("0.6")}


def _load(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text())
    return result


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    assert pass_count == arith["positively_proven_observations"] == 4
    assert unknown_count == arith["searched_no_qualifying_observation_found"] == 38
    assert failure_count == arith["acquisition_authority_failures"] == 0
    assert pass_count + unknown_count + failure_count == 42


def test_cpi_24jan_is_not_pass() -> None:
    """R1 repair: CPI-24JAN's admitted candidate never states its reference
    month in body text/dateline (only release-schedule inference + a
    companion article did) -- reclassified from PASS to UNKNOWN, and its
    receipt/extract removed entirely."""
    coverage = _load(BUNDLE / "coverage.json")
    row = next(e for e in coverage["events"] if e["event_ticker"] == "CPI-24JAN")
    assert row["terminal_state"] == "UNKNOWN"
    assert not (BUNDLE / "CPI-24JAN").exists()


def test_no_kalshi_scoring_or_production_influence() -> None:
    coverage = _load(BUNDLE / "coverage.json")
    assert coverage["production_influence"] == 0
    assert coverage["kalshi_scoring_performed"] is False
    assert coverage["edge_pnl_fees_computed"] is False
    assert coverage["cutoff_semantics"] == "per_sibling_market"
    assert coverage["research_only"] is True


def test_new_pass_receipts_have_no_event_level_cutoff() -> None:
    for ticker in NEW_PASS_EVENTS:
        receipt = _load(BUNDLE / ticker / "receipt.json")
        assert "decision_cutoff" not in receipt
        assert "temporal_comparison" not in receipt
        assert "lead_time" not in receipt


def test_new_pass_receipts_sibling_eligibility_matches_frozen_manifest_exactly() -> None:
    freeze = _load(FREEZE)

    for ticker, expected_value in NEW_PASS_EVENTS.items():
        receipt = _load(BUNDLE / ticker / "receipt.json")

        value = Decimal(receipt["value"])
        assert value == expected_value
        assert str(value) == str(expected_value)  # no precision promotion
        assert receipt["vintage_status"] == "PASS"
        assert receipt["retrospective_language_found"] is False

        event = next(e for e in freeze["events"] if e["event_ticker"] == ticker)
        frozen_siblings = event["accepted_siblings"]
        frozen_tickers = [s["market_ticker"] for s in frozen_siblings]
        frozen_cutoffs = {s["market_ticker"]: s["sibling_cutoff"] for s in frozen_siblings}

        eligibility = receipt["sibling_temporal_eligibility"]
        eligibility_tickers = [r["market_ticker"] for r in eligibility]

        # one record per frozen accepted sibling, no collapsing repeats
        assert len(eligibility) == len(frozen_siblings)
        assert sorted(eligibility_tickers) == sorted(frozen_tickers)

        published_at = _parse_ts(receipt["published_at"])
        for record in eligibility:
            assert record["sibling_cutoff"] == frozen_cutoffs[record["market_ticker"]]
            recomputed = published_at < _parse_ts(record["sibling_cutoff"])
            assert record["available_before_cutoff"] == recomputed

        hosts = {f["host"] for f in receipt["fetches"] if f.get("http_status") == 200}
        assert len(hosts) >= 2


def test_manifest_artifact_hashes_match_disk() -> None:
    manifest = _load(BUNDLE / "manifest.json")
    for entry in manifest["committed_artifacts"]:
        path = ROOT / entry["path"]
        assert path.is_file(), f"missing {entry['path']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], f"hash mismatch for {entry['path']}"

    # the manifest must not still list the removed CPI-24JAN artifacts
    manifest_paths = {e["path"] for e in manifest["committed_artifacts"]}
    assert not any("CPI-24JAN" in p for p in manifest_paths)
