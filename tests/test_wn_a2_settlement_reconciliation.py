from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_attempt_store import WnA1RunStart, open_isolated_attempt_store
from services.forecasting.wn_a1_current_daily_high_authority import POLICY_IDENTITY
from services.forecasting.wn_a2_settlement_reconciliation import (
    OutcomeStore,
    ReconciliationError,
    ReconciliationState,
    _reconcile,
)
from tests.test_wn_a1_attempt_store import _attempt, _hot_evidence

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _market(
    *, status: str = "finalized", result: str = "no", value: str = "72"
) -> dict[str, object]:
    return {
        "ticker": "KXHIGHCHI-26SEP15-T87",
        "event_ticker": "KXHIGHCHI-26SEP15",
        "market_type": "binary",
        "status": status,
        "rules_primary": (
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            "greater than 87° fahrenheit according to The Weather Company, then the market "
            "resolves to Yes."
        ),
        "price_level_structure": "integer",
        "result": result,
        "settlement_value_dollars": value,
        "settlement_ts": "2026-09-20T10:00:00Z",
        "strike_type": "greater",
        "floor_strike": "87",
        "volume_fp": "0",
        "open_interest_fp": "0",
    }


def _ready(tmp_path, *, market: dict[str, object] | None = None):
    attempts = open_isolated_attempt_store(tmp_path / "a1")
    attempt = replace(
        _attempt(
            evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
            evidence=_hot_evidence(),
            decision_state="TOO UNCERTAIN",
            side="YES",
        ),
        contract_policy_identity=POLICY_IDENTITY,
    )
    attempts.register_run_start(
        WnA1RunStart(
            attempt.run_id,
            attempt.target_local_date,
            attempt.event_ticker,
            "source",
            NOW - timedelta(days=1),
            Decimal("41.8"),
            Decimal("-87.75"),
            "policy",
            NOW - timedelta(days=1),
        )
    )
    attempts.append(attempt)
    raw = market or _market()
    body = json.dumps({"market": raw}, sort_keys=True, separators=(",", ":")).encode()
    envelope = {
        "classification": "SUCCESS",
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode(),  # pragma: allowlist secret
        "payload": {"market": raw},
    }
    return attempts, OutcomeStore(tmp_path / "a2.sqlite3"), attempt, envelope, body


def test_finalized_exact_market_is_reconciled_and_reopened(tmp_path) -> None:
    attempts, outcomes, attempt, envelope, body = _ready(tmp_path)
    path = "/trade-api/v2/markets/KXHIGHCHI-26SEP15-T87"  # pragma: allowlist secret
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _a: (envelope, body, path),
        observed_clock=lambda: NOW,
    )
    assert result.state is ReconciliationState.SETTLED_MATCHED
    assert result.result == "no"
    assert result.settlement_value_dollars == Decimal("72")
    with pytest.raises(ReconciliationError, match="duplicate reconciliation"):
        _reconcile(
            attempt.attempt_id,
            attempts=attempts,
            outcomes=outcomes,
            acquire=lambda _a: (envelope, body, "path"),
            observed_clock=lambda: NOW,
        )


def test_nonfinal_and_predicate_conflict_fail_closed(tmp_path) -> None:
    attempts, outcomes, attempt, envelope, body = _ready(tmp_path, market=_market(status="closed"))
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _a: (envelope, body, "path"),
        observed_clock=lambda: NOW,
    )
    assert result.state is ReconciliationState.NOT_FINAL

    attempts, outcomes, attempt, envelope, body = _ready(
        tmp_path / "conflict", market=_market(result="yes")
    )
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _a: (envelope, body, "path"),
        observed_clock=lambda: NOW,
    )
    assert result.state is ReconciliationState.CONFLICT


def test_raw_body_mutation_cannot_be_positive(tmp_path) -> None:
    attempts, outcomes, attempt, envelope, body = _ready(tmp_path)
    mutated = body.replace(b"72", b"73")
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _a: (envelope, mutated, "path"),
        observed_clock=lambda: NOW,
    )
    assert result.state is ReconciliationState.EVIDENCE_INVALID


def test_load_result_replays_typed_records_in_a_fresh_process(tmp_path) -> None:
    script = r"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from tests.test_wn_a2_settlement_reconciliation import NOW, _ready
from services.forecasting.wn_a2_settlement_reconciliation import _reconcile

root = Path(sys.argv[1])
attempts, outcomes, attempt, envelope, body = _ready(root)
result = _reconcile(
    attempt.attempt_id,
    attempts=attempts,
    outcomes=outcomes,
    acquire=lambda _: (envelope, body, "path"),
    observed_clock=lambda: NOW,
)
with sqlite3.connect(root / "a1" / "attempts.sqlite3") as db:
    attempt_payload = db.execute(
        "SELECT payload FROM wn_a1_attempts WHERE attempt_id=?", (attempt.attempt_id,)
    ).fetchone()[0].encode()
files = {"attempt_payload": hashlib.sha256(attempt_payload).hexdigest()}
(root / "metadata.json").write_text(json.dumps({
    "attempt_id": attempt.attempt_id,
    "result_id": result.result_id,
    "evidence_id": result.evidence_id,
    "state": result.state.value,
    "value": str(result.settlement_value_dollars),
    "settlement_ts": result.settlement_ts.isoformat(),
    "observed_at": result.outcome_observed_at.isoformat(),
    "run_id": result.run_id,
    "policy": result.reconciliation_policy_version,
    "a1_files": files,
}), encoding="utf-8")
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True)
    replay = r"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from services.forecasting.wn_a1_attempt_store import open_isolated_attempt_store
from services.forecasting.wn_a2_settlement_reconciliation import OutcomeStore

root = Path(sys.argv[1])
expected = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
with sqlite3.connect(root / "a1" / "attempts.sqlite3") as db:
    attempt_payload = db.execute(
        "SELECT payload FROM wn_a1_attempts WHERE attempt_id=?", (expected["attempt_id"],)
    ).fetchone()[0].encode()
actual_files = {"attempt_payload": hashlib.sha256(attempt_payload).hexdigest()}
assert actual_files == expected["a1_files"]
result, evidence = OutcomeStore(root / "a2.sqlite3").load_result(expected["attempt_id"])
assert evidence is not None
assert result.result_id == expected["result_id"]
assert evidence.evidence_id == expected["evidence_id"]
assert result.state.value == expected["state"]
assert str(result.settlement_value_dollars) == expected["value"]
assert result.settlement_ts.isoformat() == expected["settlement_ts"]
assert result.outcome_observed_at.isoformat() == expected["observed_at"]
assert result.attempt_id == evidence.attempt_id == expected["attempt_id"]
assert result.run_id == evidence.run_id == expected["run_id"]
assert result.reconciliation_policy_version == expected["policy"]
attempts = open_isolated_attempt_store(root / "a1")
assert attempts.get(expected["attempt_id"]).attempt_id == expected["attempt_id"]
"""
    subprocess.run([sys.executable, "-c", replay, str(tmp_path)], check=True)


def _tamper_outcome_db(path, statement: str, parameters: tuple[object, ...]) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("DROP TRIGGER starts_no_update; DROP TRIGGER outcomes_no_update;")
        db.execute(statement, parameters)


def _stored_evidence_payload(path, evidence_id: str) -> dict[str, object]:
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT payload FROM evidence WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        ("evidence_payload", "evidence identity mismatch"),
        ("raw_body", "raw body hash mismatch"),
        ("evidence_attempt", "cross-attempt evidence"),
        ("result_run", "outcome identity"),
        ("relationship", "result/evidence relationship"),
        ("policy", "unsupported persisted evidence policy"),
    ],
)
def test_load_result_rejects_persistence_tampering(tmp_path, tamper: str, match: str) -> None:
    attempts, outcomes, attempt, envelope, body = _ready(tmp_path)
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _: (envelope, body, "path"),
        observed_clock=lambda: NOW,
    )
    assert result.evidence_id is not None
    if tamper == "evidence_payload":
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE evidence SET payload=? WHERE evidence_id=?",
            (
                json.dumps(
                    {
                        **_stored_evidence_payload(outcomes.path, result.evidence_id),
                        "request_path": "tampered",
                    }
                ),
                result.evidence_id,
            ),
        )
    elif tamper == "raw_body":
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE evidence SET raw_body_b64=? WHERE evidence_id=?",
            (base64.b64encode(b"replacement").decode(), result.evidence_id),
        )
    elif tamper == "evidence_attempt":
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE evidence SET attempt_id=? WHERE evidence_id=?",
            ("different-attempt", result.evidence_id),
        )
    elif tamper == "result_run":
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE outcomes SET payload=? WHERE result_id=?",
            (json.dumps({**result._hash_material(), "run_id": "different-run"}), result.result_id),
        )
    elif tamper == "relationship":
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE outcomes SET evidence_id=? WHERE result_id=?",
            (None, result.result_id),
        )
    else:
        _tamper_outcome_db(
            outcomes.path,
            "UPDATE evidence SET payload=? WHERE evidence_id=?",
            (
                json.dumps(
                    {
                        **_stored_evidence_payload(outcomes.path, result.evidence_id),
                        "reconciliation_policy_version": "unsupported",
                    }
                ),
                result.evidence_id,
            ),
        )
    with pytest.raises(ReconciliationError, match=match):
        outcomes.load_result(attempt.attempt_id)
