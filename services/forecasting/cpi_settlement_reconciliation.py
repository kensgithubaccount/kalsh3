"""Fail-closed reconciliation of an exact CPI observation and Kalshi result.

This module is deliberately an offline evidence boundary.  It does not fetch
Kalshi data and it has no dependency on execution, credentials, risk, or model
code.  Exchange facts are reconstructed from raw JSON before they can affect a
settlement record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from services.contract_intelligence.settlement import (
    DeterminationState,
    ExchangeDetermination,
    ReconciliationStatus,
    SettlementRecord,
)
from services.contract_intelligence.specification import (
    Comparator,
    ContractSpecification,
    PayoutModel,
    SemanticStatus,
)
from services.forecasting.cpi_initial_release_value import (
    CPIBasket,
    CPIGeography,
    CPIHorizon,
    CPIInitialReleaseObservation,
    CPIPopulation,
    CPISeasonalBasis,
    CPIUnit,
    validate_cpi_initial_release_observation,
)

ZERO = Decimal("0")
ONE = Decimal("1")
SUPPORTED_COMPARATORS = frozenset(
    {Comparator.GT, Comparator.GTE, Comparator.LT, Comparator.LTE, Comparator.EQ}
)
EXPECTED_MEASURED_VALUE = (
    "CPI-U U.S. city average all items seasonally adjusted change from preceding month"
)
POLICY_VERSION = "cpi-e1-p7-settlement-reconciliation-v1"


class CPISettlementReconciliationError(ValueError):
    """The exact semantic or exchange evidence package failed closed."""


class ExpectedBinaryResult(StrEnum):
    YES = "YES"
    NO = "NO"


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CPISettlementReconciliationError(f"{name} must be timezone-aware")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise CPISettlementReconciliationError(f"{name} must be a decimal value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CPISettlementReconciliationError(f"{name} is malformed") from exc
    if not result.is_finite():
        raise CPISettlementReconciliationError(f"{name} is not finite")
    return result


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise CPISettlementReconciliationError(f"{name} is missing")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as exc:
        raise CPISettlementReconciliationError(f"{name} is malformed") from exc


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class KalshiFinalizedEvidence:
    """Raw-response-bound exchange determination.

    The public constructor is intentionally unusable for positive authority:
    ``from_raw_response`` derives every exchange fact from the raw artifact,
    and validation repeats that derivation.
    """

    raw_response: bytes
    raw_artifact_hash: str
    source_identity: str
    acquired_at: datetime
    determination: ExchangeDetermination
    research_only: bool = True
    production_influence: Decimal = ZERO

    @classmethod
    def from_raw_response(
        cls, raw_response: bytes, *, source_identity: str, acquired_at: datetime
    ) -> KalshiFinalizedEvidence:
        if type(raw_response) is not bytes or not raw_response:
            raise CPISettlementReconciliationError("non-empty raw exchange bytes are required")
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise CPISettlementReconciliationError("exchange source identity is required")
        _utc(acquired_at, "evidence acquisition timestamp")
        payload = _payload(raw_response)
        determination = _determination(payload, _hash(raw_response), acquired_at)
        result = cls(raw_response, _hash(raw_response), source_identity, acquired_at, determination)
        validate_exchange_evidence(result)
        return result


def _payload(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CPISettlementReconciliationError("raw exchange artifact is not valid JSON") from exc
    if type(value) is not dict:
        raise CPISettlementReconciliationError("raw exchange artifact must be a JSON object")
    return value


def _determination(
    payload: dict[str, Any], raw_hash: str, acquired_at: datetime
) -> ExchangeDetermination:
    required = (
        "determination_id",
        "market_ticker",
        "state",
        "result",
        "settlement_value_dollars",
        "determined_at",
        "finalized_at",
        "source_identity",
    )
    if any(key not in payload for key in required):
        raise CPISettlementReconciliationError(
            "raw exchange artifact lacks required determination fields"
        )
    try:
        state = DeterminationState(str(payload["state"]))
    except ValueError as exc:
        raise CPISettlementReconciliationError("unsupported exchange determination state") from exc
    settlement = (
        None
        if payload["settlement_value_dollars"] is None
        else _decimal(payload["settlement_value_dollars"], "settlement_value_dollars")
    )
    return ExchangeDetermination(
        determination_id=str(payload["determination_id"]),
        market_ticker=str(payload["market_ticker"]),
        state=state,
        result=None if payload["result"] is None else str(payload["result"]),
        settlement_value_dollars=settlement,
        exchange_at=_time(payload["determined_at"], "determined_at"),
        received_at=acquired_at,
        raw_hash=raw_hash,
        supersedes_determination_id=(
            None
            if payload.get("supersedes_determination_id") is None
            else str(payload["supersedes_determination_id"])
        ),
    )


def validate_exchange_evidence(evidence: KalshiFinalizedEvidence) -> None:
    if type(evidence) is not KalshiFinalizedEvidence or type(evidence.raw_response) is not bytes:
        raise CPISettlementReconciliationError("exchange evidence has wrong runtime type")
    if evidence.raw_artifact_hash != _hash(evidence.raw_response):
        raise CPISettlementReconciliationError("exchange raw artifact hash mismatch")
    if evidence.research_only is not True or evidence.production_influence != ZERO:
        raise CPISettlementReconciliationError("exchange evidence safety flags are invalid")
    _utc(evidence.acquired_at, "evidence acquisition timestamp")
    payload = _payload(evidence.raw_response)
    if payload.get("source_identity") != evidence.source_identity:
        raise CPISettlementReconciliationError("exchange source identity mismatch")
    expected = _determination(payload, evidence.raw_artifact_hash, evidence.acquired_at)
    if evidence.determination != expected:
        raise CPISettlementReconciliationError("caller-mutated exchange determination")
    if expected.state is not DeterminationState.FINALIZED or payload.get("finalized_at") is None:
        raise CPISettlementReconciliationError("exchange result is not finalized")
    if expected.result not in {ExpectedBinaryResult.YES, ExpectedBinaryResult.NO}:
        raise CPISettlementReconciliationError("exchange result is not binary")
    expected_value = ONE if expected.result == ExpectedBinaryResult.YES else ZERO
    if expected.settlement_value_dollars != expected_value:
        raise CPISettlementReconciliationError("binary result and settlement value disagree")
    if evidence.determination.exchange_at > evidence.acquired_at:
        raise CPISettlementReconciliationError("determination is after evidence acquisition")
    if payload.get("disputed") is True or payload.get("superseded") is True:
        raise CPISettlementReconciliationError("exchange lifecycle is disputed or superseded")
    if payload.get("conflicting_finalized_results") is True:
        raise CPISettlementReconciliationError("exchange has conflicting finalized results")
    if (
        expected.supersedes_determination_id is not None
        and payload.get("authoritative_latest_final") is not True
    ):
        raise CPISettlementReconciliationError(
            "superseding exchange final is not explicitly authoritative"
        )


def expected_binary_result(
    observation: CPIInitialReleaseObservation, specification: ContractSpecification
) -> ExpectedBinaryResult:
    _validate_inputs(observation, specification)
    if specification.threshold_value is None:
        raise CPISettlementReconciliationError("contract threshold is missing")
    value, threshold = observation.value, specification.threshold_value
    yes = {
        Comparator.GT: value > threshold,
        Comparator.GTE: value >= threshold,
        Comparator.LT: value < threshold,
        Comparator.LTE: value <= threshold,
        Comparator.EQ: value == threshold,
    }[specification.comparator]
    return ExpectedBinaryResult.YES if yes else ExpectedBinaryResult.NO


def reconcile_cpi_settlement(
    observation: CPIInitialReleaseObservation,
    specification: ContractSpecification,
    exchange: KalshiFinalizedEvidence,
) -> SettlementRecord:
    """Create MATCHED or MISMATCH only after both evidence packages validate."""
    _validate_inputs(observation, specification)
    validate_exchange_evidence(exchange)
    determination = exchange.determination
    _bind_exchange_to_specification(exchange, specification)
    if determination.result is None or determination.settlement_value_dollars is None:
        raise CPISettlementReconciliationError("exchange binary result is incomplete")
    expected = expected_binary_result(observation, specification)
    status = (
        ReconciliationStatus.MATCHED
        if determination.result == expected
        else ReconciliationStatus.MISMATCH
    )
    finalized = _time(_payload(exchange.raw_response)["finalized_at"], "finalized_at")
    return SettlementRecord(
        market_ticker=specification.market_ticker,
        rules_version=specification.rules_version_id,
        semantic_spec_id=specification.semantic_hash,
        result=determination.result,
        settlement_value_dollars=determination.settlement_value_dollars,
        determined_at=determination.exchange_at,
        finalized_at=finalized,
        exchange_record_hash=exchange.raw_artifact_hash,
        source_observation_id=observation.observation_id,
        reconciliation_status=status,
    )


def _validate_inputs(
    observation: CPIInitialReleaseObservation, specification: ContractSpecification
) -> None:
    try:
        validate_cpi_initial_release_observation(observation)
    except ValueError as exc:
        raise CPISettlementReconciliationError(
            "P6 observation failed transitive validation"
        ) from exc
    if type(specification) is not ContractSpecification:
        raise CPISettlementReconciliationError("exact ContractSpecification is required")
    if (
        specification.semantic_status is not SemanticStatus.VALID
        or specification.payout_model is not PayoutModel.SIMPLE_BINARY
    ):
        raise CPISettlementReconciliationError(
            "contract semantics are not valid simple binary semantics"
        )
    if (
        not specification.market_ticker.startswith("KXCPI")
        or not specification.event_ticker.startswith("KXCPI")
        or specification.series_ticker != "KXCPI"
    ):
        raise CPISettlementReconciliationError("contract is not the intended KXCPI family")
    if not specification.rules_version_id.strip() or not specification.market_rules_hash.strip():
        raise CPISettlementReconciliationError("contract rules identity is incomplete")
    if (
        specification.comparator not in SUPPORTED_COMPARATORS
        or specification.threshold_value is None
        or not specification.threshold_unit
    ):
        raise CPISettlementReconciliationError("contract comparator or threshold is unsupported")
    if specification.threshold_unit.casefold() not in {"percent", "%", "percentage points"}:
        raise CPISettlementReconciliationError(
            "contract threshold unit is not CPI percentage points"
        )
    measured = " ".join(specification.measured_event_or_value.casefold().replace(",", "").split())
    if measured != EXPECTED_MEASURED_VALUE.casefold() or specification.subject_entities != (
        "CPI-U",
    ):
        raise CPISettlementReconciliationError("contract measured domain is not the P6 CPI domain")
    if (
        specification.geographic_scope != "U.S. city average"
        or specification.rounding_rules != "one decimal initial release"
    ):
        raise CPISettlementReconciliationError("contract geography or rounding policy is not exact")
    if (
        not specification.settlement_authority
        or not specification.settlement_sources
        or specification.source_precedence_status not in {"EXCHANGE_NAMED", "AGREE"}
    ):
        raise CPISettlementReconciliationError(
            "contract settlement authority is incomplete or ambiguous"
        )
    if (
        specification.ambiguities
        or specification.contradictions
        or specification.unsupported_features
    ):
        raise CPISettlementReconciliationError("contract contains unresolved semantic issues")
    if (
        specification.revision_rules != "authoritative finalized exchange result"
        or specification.correction_rules != "authoritative latest final explicitly required"
    ):
        raise CPISettlementReconciliationError("contract revision/correction policy is not exact")
    if (
        observation.unit is not CPIUnit.PERCENT
        or observation.seasonal_basis is not CPISeasonalBasis.SA
        or observation.horizon is not CPIHorizon.MOM
        or observation.basket is not CPIBasket.ALL_ITEMS
        or observation.population is not CPIPopulation.CPI_U
        or observation.geography is not CPIGeography.US_CITY_AVERAGE
    ):
        raise CPISettlementReconciliationError("P6 observation is outside the exact CPI domain")
    if specification.occurrence_time and (
        observation.reference_year != specification.occurrence_time.year
        or observation.reference_month != specification.occurrence_time.month
    ):
        raise CPISettlementReconciliationError("P6 reference month conflicts with contract")


def _bind_exchange_to_specification(
    exchange: KalshiFinalizedEvidence, specification: ContractSpecification
) -> None:
    payload = _payload(exchange.raw_response)
    for key, expected in (
        ("market_ticker", specification.market_ticker),
        ("event_ticker", specification.event_ticker),
        ("series_ticker", specification.series_ticker),
        ("rules_version_id", specification.rules_version_id),
        ("market_rules_hash", specification.market_rules_hash),
        ("semantic_spec_id", specification.semantic_hash),
    ):
        if payload.get(key) != expected:
            raise CPISettlementReconciliationError(
                f"exchange evidence conflicts with contract {key}"
            )
    if specification.occurrence_time:
        if payload.get("reference_year") != specification.occurrence_time.year:
            raise CPISettlementReconciliationError(
                "exchange reference year conflicts with contract"
            )
        if payload.get("reference_month") != specification.occurrence_time.month:
            raise CPISettlementReconciliationError(
                "exchange reference month conflicts with contract"
            )
