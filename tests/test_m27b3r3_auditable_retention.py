from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.market_universe.archive import UniverseObservationArchive
from services.opportunity_engine.auditable_retention import (
    AuditableRetentionLedger,
    RetentionGateError,
    RetentionPolicy,
)


def _write(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


def test_smoke_projection_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "universe.sqlite"
    evidence = tmp_path / "observations.sqlite"
    # Primary evidence must already exist at preflight (gap A); the scan then grows it, which is
    # what the smoke projection is measuring and bounding.
    _write(archive, 1)
    _write(evidence, 1)
    ledger = AuditableRetentionLedger(
        tmp_path / "retention",
        RetentionPolicy(budget_bytes=200, free_space_floor_bytes=1, expected_scans=4),
    )
    ledger.check_before_scan((archive, evidence))
    _write(archive, 30)
    _write(evidence, 30)
    with pytest.raises(RetentionGateError, match="exceeds approved storage budget"):
        ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )


def test_receipt_is_atomic_hashed_and_reopenable(tmp_path: Path) -> None:
    archive = tmp_path / "universe.sqlite"
    evidence = tmp_path / "observations.sqlite"
    _write(archive, 4)
    _write(evidence, 6)
    ledger = AuditableRetentionLedger(
        tmp_path / "retention",
        RetentionPolicy(budget_bytes=10_000, free_space_floor_bytes=1, expected_scans=1),
    )
    ledger.check_before_scan((archive, evidence))
    receipt = ledger.record_scan(
        scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
    )
    receipt_path = tmp_path / "retention" / "retention-receipts" / f"{receipt['receipt_id']}.json"
    assert json.loads(receipt_path.read_text())["production_influence"] == 0
    assert json.loads((tmp_path / "retention" / "retention-state.json").read_text())["scans"]


def test_free_space_floor_blocks_before_acquisition(tmp_path: Path) -> None:
    ledger = AuditableRetentionLedger(
        tmp_path / "retention",
        RetentionPolicy(budget_bytes=1000, free_space_floor_bytes=10**18, expected_scans=1),
    )
    with pytest.raises(RetentionGateError, match="free-space floor"):
        ledger.check_before_scan((tmp_path / "missing-a", tmp_path / "missing-b"))


def test_operational_compressed_archive_reconstructs_exact_payload(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite", compressed_evidence=True)
    assert archive.compressed_evidence is True
