from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from services.perps_shadow_research.phase0_prospective import (
    PROTOCOL_SHA256,
    TICKER,
    CollectionStatus,
    ProspectiveProtocolError,
)
from services.perps_shadow_research.phase0_reconcile import reconcile_root


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True))


def common(session_id: str) -> dict[str, object]:
    return {
        "protocol_sha256": PROTOCOL_SHA256,
        "session_id": session_id,
        "ticker": TICKER,
        "production_influence": "0",
    }


def test_reconciliation_accounts_for_captured_failed_and_missing(tmp_path: Path) -> None:
    captured = tmp_path / "P0-S01"
    captured.mkdir()
    write_json(captured / "claim.json", {"record_type": "claim", **common("P0-S01")})
    db = captured / "evidence.sqlite3"
    with sqlite3.connect(db) as handle:
        handle.execute("CREATE TABLE x(id INTEGER PRIMARY KEY)")
    import hashlib

    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    write_json(
        captured / "result.json",
        {
            "record_type": "PERPS-REFERENCE-LAG-P0-RESULT-v1",
            "evidence_db_sha256": db_sha,
            **common("P0-S01"),
        },
    )

    failed = tmp_path / "P0-S02"
    failed.mkdir()
    write_json(failed / "claim.json", {"record_type": "claim", **common("P0-S02")})
    write_json(
        failed / "failure.json",
        {
            "record_type": "PERPS-REFERENCE-LAG-P0-FAILURE-v1",
            **common("P0-S02"),
        },
    )

    incomplete = tmp_path / "P0-S03"
    incomplete.mkdir()
    write_json(incomplete / "claim.json", {"record_type": "claim", **common("P0-S03")})

    report = reconcile_root(tmp_path)
    by_id = {item.session_id: item for item in report.sessions}
    assert by_id["P0-S01"].status is CollectionStatus.CAPTURED
    assert by_id["P0-S02"].status is CollectionStatus.FAILED
    assert by_id["P0-S03"].status is CollectionStatus.FAILED
    assert by_id["P0-S04"].status is CollectionStatus.MISSING
    assert report.captured == 1
    assert report.failed == 2
    assert report.missing == 5


def test_reconciliation_detects_tampered_database(tmp_path: Path) -> None:
    session = tmp_path / "P0-S01"
    session.mkdir()
    write_json(session / "claim.json", {"record_type": "claim", **common("P0-S01")})
    db = session / "evidence.sqlite3"
    db.write_bytes(b"tampered")
    write_json(
        session / "result.json",
        {
            "record_type": "PERPS-REFERENCE-LAG-P0-RESULT-v1",
            "evidence_db_sha256": "0" * 64,
            **common("P0-S01"),
        },
    )
    with pytest.raises(ProspectiveProtocolError, match="hash mismatch"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_result_failure_conflict(tmp_path: Path) -> None:
    session = tmp_path / "P0-S01"
    session.mkdir()
    write_json(session / "claim.json", {"record_type": "claim", **common("P0-S01")})
    write_json(session / "result.json", {"record_type": "x", **common("P0-S01")})
    write_json(session / "failure.json", {"record_type": "x", **common("P0-S01")})
    with pytest.raises(ProspectiveProtocolError, match="both result and failure"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_unknown_session_directory(tmp_path: Path) -> None:
    (tmp_path / "P0-S99").mkdir()
    with pytest.raises(ProspectiveProtocolError, match="unexpected"):
        reconcile_root(tmp_path)
