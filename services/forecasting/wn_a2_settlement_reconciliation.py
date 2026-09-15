# ruff: noqa: E501
"""WN-A2: append-only, research-only reconciliation of a persisted WN-A1 attempt.

Kalshi's exact finalized market response is the sole settlement authority.  The Weather
Company route is intentionally not acquired here: it is optional corroboration and cannot
replace a Kalshi result.  Forecast evidence and this lane have no production influence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from services.market_universe.domain import Market, MarketStatus, stable_hash
from services.market_universe.public_read import BASE, PublicReadFailure, get, get_market_with_body

from .wn_a1_attempt_store import WnA1Attempt, WnA1AttemptStore, open_default_attempt_store
from .wn_a1_current_daily_high_authority import POLICY_IDENTITY, SERIES_TICKER, SETTLEMENT_SOURCE
from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

POLICY_VERSION = "wn-a2-kalshi-canonical-settlement-reconciliation-v1"
DEFAULT_DB = Path(__file__).resolve().parents[2] / ".kalsh3-wn-a2-research" / "outcomes.sqlite3"


class ReconciliationState(StrEnum):
    SETTLED_MATCHED = "SETTLED_MATCHED"
    NOT_FINAL = "NOT_FINAL"
    CONFLICT = "CONFLICT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"


class ReconciliationError(WnA1Error):
    """Fail-closed WN-A2 persistence or authority error."""


class _NotFinal(ReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class KalshiSettlementEvidence:
    attempt_id: str
    run_id: str
    market_ticker: str
    event_ticker: str
    target_local_date: str
    request_path: str
    http_method: str
    raw_body_sha256: str
    observed_at: datetime
    market_status: str
    result: str
    settlement_value_dollars: Decimal
    settlement_ts: datetime
    rules_source_identity: str
    reconciliation_policy_version: str = POLICY_VERSION

    @property
    def evidence_id(self) -> str:
        return stable_hash(("wn-a2-kalshi-settlement", self._hash_material()))

    def _hash_material(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "market_ticker": self.market_ticker,
            "event_ticker": self.event_ticker,
            "target_local_date": self.target_local_date,
            "request_path": self.request_path,
            "http_method": self.http_method,
            "raw_body_sha256": self.raw_body_sha256,
            "observed_at": self.observed_at.isoformat(),
            "market_status": self.market_status,
            "result": self.result,
            "settlement_value_dollars": str(self.settlement_value_dollars),
            "settlement_ts": self.settlement_ts.isoformat(),
            "rules_source_identity": self.rules_source_identity,
            "reconciliation_policy_version": self.reconciliation_policy_version,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    attempt_id: str
    run_id: str
    state: ReconciliationState
    evidence_id: str | None
    result: str | None
    settlement_value_dollars: Decimal | None
    settlement_ts: datetime | None
    outcome_observed_at: datetime | None
    reconciliation_policy_version: str = POLICY_VERSION
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.research_only is not True or self.production_influence != 0:
            raise ReconciliationError("WN-A2 must remain research-only, zero influence")

    @property
    def result_id(self) -> str:
        return stable_hash({"state": self.state.value, "result": self._hash_material()})

    def _hash_material(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "state": self.state.value,
            "evidence_id": self.evidence_id,
            "result": self.result,
            "value": None
            if self.settlement_value_dollars is None
            else str(self.settlement_value_dollars),
            "settlement_ts": None if self.settlement_ts is None else self.settlement_ts.isoformat(),
            "observed_at": None
            if self.outcome_observed_at is None
            else self.outcome_observed_at.isoformat(),
            "policy": self.reconciliation_policy_version,
        }


class OutcomeStore:
    """Fixed-location append-only START/evidence/result journal."""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS starts (attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outcomes (result_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, evidence_id TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence (evidence_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, raw_body_b64 TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS starts_no_update BEFORE UPDATE ON starts BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS starts_no_delete BEFORE DELETE ON starts BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes BEGIN SELECT RAISE(ABORT,'append only'); END;
            """)

    def register(self, attempt: WnA1Attempt) -> None:
        payload = json.dumps(
            {"attempt_id": attempt.attempt_id, "run_id": attempt.run_id, "policy": POLICY_VERSION},
            sort_keys=True,
        )
        try:
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT INTO starts VALUES (?,?,?)",
                    (attempt.attempt_id, attempt.run_id, payload),
                )
        except sqlite3.IntegrityError as exc:
            raise ReconciliationError("duplicate reconciliation registration rejected") from exc

    def append(
        self,
        result: ReconciliationResult,
        evidence: KalshiSettlementEvidence | None = None,
        raw_body: bytes | None = None,
    ) -> None:
        if evidence is not None and evidence.attempt_id != result.attempt_id:
            raise ReconciliationError("cross-attempt outcome pairing rejected")
        try:
            with sqlite3.connect(self.path) as db:
                if evidence is not None and raw_body is not None:
                    db.execute(
                        "INSERT INTO evidence VALUES (?,?,?,?)",
                        (
                            evidence.evidence_id,
                            evidence.attempt_id,
                            base64.b64encode(raw_body).decode("ascii"),
                            json.dumps(evidence._hash_material(), sort_keys=True),
                        ),
                    )
                db.execute(
                    "INSERT INTO outcomes VALUES (?,?,?,?)",
                    (
                        result.result_id,
                        result.attempt_id,
                        result.evidence_id,
                        json.dumps(result._hash_material(), sort_keys=True),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ReconciliationError("duplicate exact reconciliation rejected") from exc


def _payload_from_envelope(envelope: dict[str, object], body: bytes) -> dict[str, Any]:
    if envelope.get("classification") != "SUCCESS" or envelope.get("status") != 200:
        raise PublicReadFailure("Kalshi response unavailable")
    stamped = envelope.get("body_sha256")
    if not isinstance(stamped, str) or hashlib.sha256(body).hexdigest() != stamped:
        raise ReconciliationError("raw Kalshi body hash mismatch")
    if envelope.get("raw_body_b64") != base64.b64encode(body).decode("ascii"):
        raise ReconciliationError("raw Kalshi body provenance mismatch")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("market"), dict):
        raise ReconciliationError("Kalshi market payload malformed")
    return cast(dict[str, Any], payload["market"])


def _predicate(attempt: WnA1Attempt, value: Decimal) -> bool:
    if attempt.contract_comparator == "GT":
        return value > attempt.contract_lower
    if attempt.contract_comparator == "LT":
        return value < attempt.contract_lower
    if attempt.contract_comparator == "RANGE":
        return (
            attempt.contract_upper is not None
            and attempt.contract_lower <= value < attempt.contract_upper
        )
    raise ReconciliationError("unsupported persisted contract comparator")


def _public_acquire(attempt: WnA1Attempt) -> tuple[dict[str, object], bytes, str]:
    """Route from Kalshi's public cutoff, never from a caller age/transport flag."""
    cutoff_envelope = get(f"{BASE}/historical/cutoff")
    cutoff_payload = cutoff_envelope.get("payload")
    if not isinstance(cutoff_payload, dict) or not isinstance(
        cutoff_payload.get("market_settled_ts"), str
    ):
        raise PublicReadFailure("historical cutoff unavailable")
    cutoff = datetime.fromisoformat(
        cutoff_payload["market_settled_ts"].replace("Z", "+00:00")
    ).astimezone(UTC)
    local_end = datetime.combine(
        date.fromisoformat(attempt.target_local_date),
        datetime.max.time(),
        tzinfo=ZoneInfo("America/Chicago"),
    ).astimezone(UTC)
    if local_end <= cutoff:
        path = f"{BASE}/historical/markets/{attempt.market_ticker}"
        envelope = get(path)
        raw_body_b64 = envelope.get("raw_body_b64")
        if not isinstance(raw_body_b64, str):
            raise PublicReadFailure("historical response has no raw body")
        return envelope, base64.b64decode(raw_body_b64), path
    envelope, body = get_market_with_body(attempt.market_ticker)
    return envelope, body, f"{BASE}/markets/{attempt.market_ticker}"


def _check_market(
    attempt: WnA1Attempt, raw: dict[str, Any], observed: datetime, path: str, body: bytes
) -> KalshiSettlementEvidence:
    market = Market.parse(raw)
    if (market.ticker, market.event_ticker) != (attempt.market_ticker, attempt.event_ticker):
        raise ReconciliationError("exact market/event mismatch")
    if (
        attempt.series_ticker != SERIES_TICKER
        or attempt.contract_policy_identity != POLICY_IDENTITY
    ):
        raise ReconciliationError("persisted WN-A1 policy/series is not canonical")
    if market.status is not MarketStatus.FINALIZED:
        raise _NotFinal("market is not finalized")
    result = raw.get("result")
    payout_raw = raw.get("settlement_value_dollars")
    value_raw = raw.get("expiration_value", payout_raw)
    ts_raw = raw.get("settlement_ts")
    if (
        result not in {"yes", "no"}
        or not isinstance(payout_raw, str)
        or not isinstance(value_raw, (int, str))
        or isinstance(value_raw, bool)
        or not isinstance(ts_raw, str)
    ):
        raise ReconciliationError("finalized market lacks required settlement fields")
    value = Decimal(value_raw)
    settlement_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(UTC)
    if settlement_ts < attempt.evaluated_at or settlement_ts > observed:
        raise ReconciliationError("settlement chronology is invalid")
    if (result == "yes") != _predicate(attempt, value):
        raise ReconciliationError("RECONCILIATION_CONFLICT")
    # Re-check the frozen source semantics without using the mutable lifecycle status.
    if "CLIMDW" not in str(raw.get("rules_primary")) or SETTLEMENT_SOURCE not in str(
        raw.get("rules_primary")
    ):
        raise ReconciliationError("settlement rules/source are incompatible")
    return KalshiSettlementEvidence(
        attempt.attempt_id,
        attempt.run_id,
        market.ticker,
        market.event_ticker,
        attempt.target_local_date,
        path,
        "GET",
        hashlib.sha256(body).hexdigest(),
        observed,
        market.status.value,
        result,
        value,
        settlement_ts,
        stable_hash({"rules_hash": market.rules_hash, "settlement_source": SETTLEMENT_SOURCE}),
    )


def _reconcile(
    attempt_id: str,
    *,
    attempts: WnA1AttemptStore,
    outcomes: OutcomeStore,
    acquire: Callable[[WnA1Attempt], tuple[dict[str, object], bytes, str]] = _public_acquire,
    observed_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReconciliationResult:
    attempt = attempts.get(attempt_id)
    if attempts.get_run_start(attempt.run_id).run_id != attempt.run_id:
        raise ReconciliationError("attempt/run relationship invalid")
    outcomes.register(attempt)
    observed = observed_clock().astimezone(UTC)
    try:
        envelope, body, path = acquire(attempt)
        raw = _payload_from_envelope(envelope, body)
        evidence = _check_market(attempt, raw, observed, path, body)
        result = ReconciliationResult(
            attempt.attempt_id,
            attempt.run_id,
            ReconciliationState.SETTLED_MATCHED,
            evidence.evidence_id,
            evidence.result,
            evidence.settlement_value_dollars,
            evidence.settlement_ts,
            evidence.observed_at,
        )
    except PublicReadFailure:
        result = ReconciliationResult(
            attempt.attempt_id,
            attempt.run_id,
            ReconciliationState.SOURCE_UNAVAILABLE,
            None,
            None,
            None,
            None,
            observed,
        )
    except ReconciliationError as exc:
        state = (
            ReconciliationState.CONFLICT
            if "CONFLICT" in str(exc)
            else ReconciliationState.NOT_FINAL
            if isinstance(exc, _NotFinal)
            else ReconciliationState.EVIDENCE_INVALID
        )
        result = ReconciliationResult(
            attempt.attempt_id, attempt.run_id, state, None, None, None, None, observed
        )
    outcomes.append(
        result,
        evidence if result.state is ReconciliationState.SETTLED_MATCHED else None,
        body if result.state is ReconciliationState.SETTLED_MATCHED else None,
    )
    return result


def reconcile_canonical(attempt_id: str) -> ReconciliationResult:
    """Canonical public entrypoint: the immutable persisted attempt ID is the only input."""
    return _reconcile(attempt_id, attempts=open_default_attempt_store(), outcomes=OutcomeStore())
