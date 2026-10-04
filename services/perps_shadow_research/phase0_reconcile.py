"""Read-only reconciliation for the frozen Perps Phase-0 prospective sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .phase0_prospective import (
    PROTOCOL_SHA256,
    TICKER,
    CollectionStatus,
    ProspectiveProtocolError,
    expected_session_ids,
)


@dataclass(frozen=True, slots=True)
class ReconciledSession:
    session_id: str
    status: CollectionStatus
    reason: str
    evidence_db_sha256: str | None


@dataclass(frozen=True, slots=True)
class Reconciliation:
    sessions: tuple[ReconciledSession, ...]

    @property
    def captured(self) -> int:
        return sum(item.status is CollectionStatus.CAPTURED for item in self.sessions)

    @property
    def failed(self) -> int:
        return sum(item.status is CollectionStatus.FAILED for item in self.sessions)

    @property
    def missing(self) -> int:
        return sum(item.status is CollectionStatus.MISSING for item in self.sessions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "PERPS-REFERENCE-LAG-P0-RECONCILIATION-v1",
            "protocol_sha256": PROTOCOL_SHA256,
            "ticker": TICKER,
            "captured_sessions": self.captured,
            "failed_sessions": self.failed,
            "missing_sessions": self.missing,
            "sessions": [
                {
                    "session_id": item.session_id,
                    "status": item.status.value,
                    "reason": item.reason,
                    "evidence_db_sha256": item.evidence_db_sha256,
                }
                for item in self.sessions
            ],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProspectiveProtocolError(f"invalid prospective artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise ProspectiveProtocolError(f"prospective artifact must be an object: {path.name}")
    return value


def _validate_common(payload: dict[str, Any], session_id: str) -> None:
    if payload.get("protocol_sha256") != PROTOCOL_SHA256:
        raise ProspectiveProtocolError("prospective artifact protocol hash mismatch")
    if payload.get("session_id") != session_id:
        raise ProspectiveProtocolError("prospective artifact session id mismatch")
    if payload.get("ticker") != TICKER:
        raise ProspectiveProtocolError("prospective artifact ticker mismatch")
    if payload.get("production_influence") != "0":
        raise ProspectiveProtocolError("prospective artifact production influence is not zero")


def reconcile_session(root: Path, session_id: str) -> ReconciledSession:
    directory = root / session_id
    if not directory.exists():
        return ReconciledSession(session_id, CollectionStatus.MISSING, "NO_SESSION_DIRECTORY", None)
    if not directory.is_dir():
        raise ProspectiveProtocolError("prospective session path is not a directory")

    claim = directory / "claim.json"
    result = directory / "result.json"
    failure = directory / "failure.json"
    evidence = directory / "evidence.sqlite3"

    if not claim.is_file():
        return ReconciledSession(session_id, CollectionStatus.FAILED, "CLAIM_MISSING", None)

    claim_payload = _json(claim)
    _validate_common(claim_payload, session_id)

    if result.exists() and failure.exists():
        raise ProspectiveProtocolError("prospective session has both result and failure receipts")

    if result.is_file():
        payload = _json(result)
        _validate_common(payload, session_id)
        if payload.get("record_type") != "PERPS-REFERENCE-LAG-P0-RESULT-v1":
            raise ProspectiveProtocolError("unexpected prospective result record type")
        expected_sha = payload.get("evidence_db_sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ProspectiveProtocolError("prospective result has invalid evidence DB hash")
        if not evidence.is_file() or _sha256(evidence) != expected_sha:
            raise ProspectiveProtocolError("prospective evidence DB hash mismatch")
        return ReconciledSession(session_id, CollectionStatus.CAPTURED, "SEALED_RESULT", expected_sha)

    if failure.is_file():
        payload = _json(failure)
        _validate_common(payload, session_id)
        if payload.get("record_type") != "PERPS-REFERENCE-LAG-P0-FAILURE-v1":
            raise ProspectiveProtocolError("unexpected prospective failure record type")
        expected_sha = payload.get("evidence_db_sha256")
        if expected_sha is not None:
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                raise ProspectiveProtocolError("prospective failure has invalid evidence DB hash")
            if not evidence.is_file() or _sha256(evidence) != expected_sha:
                raise ProspectiveProtocolError("failed-session evidence DB hash mismatch")
        return ReconciledSession(session_id, CollectionStatus.FAILED, "FAILURE_RECEIPT", expected_sha)

    return ReconciledSession(session_id, CollectionStatus.FAILED, "CLAIMED_WITHOUT_RECEIPT", None)


def reconcile_root(root: Path) -> Reconciliation:
    expected = set(expected_session_ids())
    if root.exists():
        unexpected = sorted(
            item.name
            for item in root.iterdir()
            if item.name.startswith("P0-") and item.name not in expected
        )
        if unexpected:
            raise ProspectiveProtocolError("unexpected prospective session directories exist")
    return Reconciliation(tuple(reconcile_session(root, session_id) for session_id in expected_session_ids()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile frozen Perps Phase-0 sessions")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = reconcile_root(args.root)
    except ProspectiveProtocolError as exc:
        print(f"INVALID: {exc}")
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
