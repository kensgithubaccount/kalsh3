from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.perps_shadow_research.phase0_prospective import (
    PROTOCOL_SHA256,
    SESSION_DURATION_SECONDS,
    TICKER,
    CollectionStatus,
    ProspectiveProtocolError,
    scheduled_at,
)
from services.perps_shadow_research.phase0_reconcile import reconcile_root

METADATA_HASH = "a" * 64
CONTRACT_HASH = "b" * 64


def iso_z(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True))


def common(session_id: str) -> dict[str, object]:
    return {
        "protocol_sha256": PROTOCOL_SHA256,
        "session_id": session_id,
        "ticker": TICKER,
        "production_influence": "0",
    }


def claim_payload(session_id: str) -> dict[str, object]:
    when = scheduled_at(session_id)
    return {
        "record_type": "PERPS-REFERENCE-LAG-P0-CLAIM-v1",
        "scheduled_at_utc": iso_z(when),
        "claimed_at_utc": iso_z(when),
        "session_duration_seconds": SESSION_DURATION_SECONDS,
        "no_retry": True,
        "no_backfill": True,
        **common(session_id),
    }


def failure_payload(session_id: str) -> dict[str, object]:
    when = scheduled_at(session_id)
    return {
        "record_type": "PERPS-REFERENCE-LAG-P0-FAILURE-v1",
        "failed_at_utc": iso_z(when + timedelta(seconds=2)),
        "failure_type": "ProspectiveCollectionError",
        "no_retry": True,
        "no_backfill": True,
        **common(session_id),
    }


def create_minimal_evidence_db(path: Path, *, ticker: str = TICKER) -> str:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE perps_market_metadata (
                ticker TEXT NOT NULL,
                market_metadata_hash TEXT NOT NULL,
                perps_contract_hash TEXT NOT NULL,
                production_influence TEXT NOT NULL
            );
            CREATE TABLE perps_book_evidence (
                ticker TEXT NOT NULL,
                market_metadata_hash TEXT NOT NULL,
                production_influence TEXT NOT NULL
            );
            CREATE TABLE perps_market_state (
                ticker TEXT NOT NULL,
                market_metadata_hash TEXT NOT NULL,
                production_influence TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO perps_market_metadata VALUES (?,?,?,?)",
            (ticker, METADATA_HASH, CONTRACT_HASH, "0"),
        )
        db.execute(
            "INSERT INTO perps_book_evidence VALUES (?,?,?)",
            (ticker, METADATA_HASH, "0"),
        )
        db.execute(
            "INSERT INTO perps_market_state VALUES (?,?,?)",
            (ticker, METADATA_HASH, "0"),
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_payload(session_id: str, db_sha: str) -> dict[str, object]:
    claimed = scheduled_at(session_id)
    return {
        "record_type": "PERPS-REFERENCE-LAG-P0-RESULT-v1",
        "scheduled_at_utc": iso_z(claimed),
        "claimed_at_utc": iso_z(claimed),
        "collection_started_at_utc": iso_z(claimed + timedelta(seconds=1)),
        "collection_ended_at_utc": iso_z(claimed + timedelta(seconds=61)),
        "session_duration_seconds": SESSION_DURATION_SECONDS,
        "market_metadata_hash": METADATA_HASH,
        "perps_contract_hash": CONTRACT_HASH,
        "book_rows": 1,
        "market_state_rows": 1,
        "evidence_db_sha256": db_sha,
        "no_trade": True,
        **common(session_id),
    }


def test_reconciliation_accounts_for_captured_failed_and_missing(tmp_path: Path) -> None:
    captured = tmp_path / "P0-S01"
    captured.mkdir()
    write_json(captured / "claim.json", claim_payload("P0-S01"))
    db_sha = create_minimal_evidence_db(captured / "evidence.sqlite3")
    write_json(captured / "result.json", result_payload("P0-S01", db_sha))

    failed = tmp_path / "P0-S02"
    failed.mkdir()
    write_json(failed / "claim.json", claim_payload("P0-S02"))
    write_json(failed / "failure.json", failure_payload("P0-S02"))

    incomplete = tmp_path / "P0-S03"
    incomplete.mkdir()
    write_json(incomplete / "claim.json", claim_payload("P0-S03"))

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
    write_json(session / "claim.json", claim_payload("P0-S01"))
    db = session / "evidence.sqlite3"
    db_sha = create_minimal_evidence_db(db)
    write_json(session / "result.json", result_payload("P0-S01", db_sha))
    with db.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ProspectiveProtocolError, match="hash mismatch"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_result_failure_conflict(tmp_path: Path) -> None:
    session = tmp_path / "P0-S01"
    session.mkdir()
    write_json(session / "claim.json", claim_payload("P0-S01"))
    write_json(session / "result.json", {"record_type": "x"})
    write_json(session / "failure.json", {"record_type": "x"})
    with pytest.raises(ProspectiveProtocolError, match="both result and failure"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_claim_outside_frozen_window(tmp_path: Path) -> None:
    session = tmp_path / "P0-S01"
    session.mkdir()
    payload = claim_payload("P0-S01")
    payload["claimed_at_utc"] = iso_z(scheduled_at("P0-S01") + timedelta(minutes=5))
    write_json(session / "claim.json", payload)
    with pytest.raises(ProspectiveProtocolError, match="outside frozen window"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_internal_db_identity_mismatch(tmp_path: Path) -> None:
    session = tmp_path / "P0-S01"
    session.mkdir()
    write_json(session / "claim.json", claim_payload("P0-S01"))
    db = session / "evidence.sqlite3"
    db_sha = create_minimal_evidence_db(db, ticker="WRONG")
    write_json(session / "result.json", result_payload("P0-S01", db_sha))
    with pytest.raises(ProspectiveProtocolError, match="metadata identity mismatch"):
        reconcile_root(tmp_path)


def test_reconciliation_rejects_unknown_session_directory(tmp_path: Path) -> None:
    (tmp_path / "P0-S99").mkdir()
    with pytest.raises(ProspectiveProtocolError, match="unexpected"):
        reconcile_root(tmp_path)
