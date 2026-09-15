from __future__ import annotations

import base64
import hashlib
import json
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
