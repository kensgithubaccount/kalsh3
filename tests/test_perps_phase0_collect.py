from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.perps_shadow_research.phase0_collect import (
    ProspectiveCollectionError,
    SessionPaths,
    _write_json_exclusive,
    claim_session,
    seal_evidence_db,
    validate_invocation_time,
)
from services.perps_shadow_research.phase0_prospective import PROTOCOL_SHA256, scheduled_at


def test_invocation_time_is_frozen_and_late_bounded() -> None:
    scheduled = scheduled_at("P0-S01")
    assert validate_invocation_time("P0-S01", scheduled) == scheduled
    assert validate_invocation_time("P0-S01", scheduled + timedelta(seconds=299)) == scheduled
    with pytest.raises(ProspectiveCollectionError, match="before"):
        validate_invocation_time("P0-S01", scheduled - timedelta(microseconds=1))
    with pytest.raises(ProspectiveCollectionError, match="after"):
        validate_invocation_time("P0-S01", scheduled + timedelta(seconds=300))


def test_claim_is_exclusive_and_binds_protocol(tmp_path: Path) -> None:
    paths = SessionPaths(tmp_path, "P0-S01")
    now = scheduled_at("P0-S01")
    claim_session(paths, now=now)
    payload = json.loads(paths.claim.read_text())
    assert payload["protocol_sha256"] == PROTOCOL_SHA256
    assert payload["session_id"] == "P0-S01"
    assert payload["ticker"] == "BTC-PERP"
    assert payload["no_retry"] is True
    with pytest.raises(ProspectiveCollectionError, match="already claimed"):
        claim_session(paths, now=now)


def test_exclusive_receipt_writer_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    _write_json_exclusive(path, {"x": 1})
    with pytest.raises(FileExistsError):
        _write_json_exclusive(path, {"x": 2})


def test_sqlite_seal_checks_integrity_and_hashes_file(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE x(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO x(value) VALUES ('a')")
    first = seal_evidence_db(path)
    second = seal_evidence_db(path)
    assert len(first) == 64
    assert first == second
    assert not Path(str(path) + "-wal").exists() or Path(str(path) + "-wal").stat().st_size == 0


def test_naive_invocation_time_fails_closed() -> None:
    with pytest.raises(ProspectiveCollectionError, match="timezone-aware"):
        validate_invocation_time("P0-S01", datetime(2026, 10, 5, 12, 0))
