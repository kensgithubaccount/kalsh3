"""Append-only hypothetical settlement and economic evaluation.

The observation constructor below is a private disposable fixture seam.  No reviewed
final-settlement acquisition adapter is configured, so it is never a production
authority boundary.  Its calculations test economic semantics only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from services.market_universe.domain import stable_hash

from .one_decision import (
    _ISSUED,
    _ISSUED_FINGERPRINTS,
    _ISSUER,
    DecisionError,
    DecisionReceipt,
    _register_issued,
    validate_decision_receipt,
)


class OutcomeClass(StrEnum):
    COMPLETE = "COMPLETE"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


@dataclass(frozen=True, slots=True, init=False)
class _FixtureSettlementObservation:
    decision_id: str
    original_decision_hash: str
    market_ticker: str
    final: bool
    result: str | None
    settled_at: datetime | None
    completed_at: datetime
    evidence_id: str

    def __init__(
        self,
        *,
        response: bytes,
        decision: DecisionReceipt,
        completed_at: datetime,
        _capability: object,
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("settlement evidence requires reviewed issuer")
        try:
            payload = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionError("settlement evidence is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise DecisionError("settlement evidence is not an object")
        if (
            payload.get("decision_id") != decision.decision_id
            or payload.get("original_decision_hash") != decision.payload_hash
        ):
            raise DecisionError("settlement is not linked to the original decision")
        settled_raw = payload.get("settled_at")
        settled = None
        if isinstance(settled_raw, str):
            try:
                parsed = datetime.fromisoformat(settled_raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError("naive settlement")
                settled = parsed.astimezone(UTC)
            except ValueError as exc:
                raise DecisionError("settlement timestamp is malformed") from exc
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise DecisionError("settlement completion timestamp is naive")
        completed = completed_at.astimezone(UTC)
        raw_hash = hashlib.sha256(response).hexdigest()
        material = (
            raw_hash,
            completed.isoformat(),
            payload.get("decision_id"),
            payload.get("original_decision_hash"),
            payload.get("market_ticker"),
            payload.get("final"),
            payload.get("result"),
            payload.get("settled_at"),
        )
        values: dict[str, object] = {
            "decision_id": str(payload.get("decision_id")),
            "original_decision_hash": str(payload.get("original_decision_hash")),
            "market_ticker": str(payload.get("market_ticker")),
            "final": payload.get("final") is True,
            "result": payload.get("result") if isinstance(payload.get("result"), str) else None,
            "settled_at": settled,
            "completed_at": completed,
            "evidence_id": hashlib.sha256(repr(material).encode()).hexdigest(),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _register_issued(self, self.evidence_id)


@dataclass(frozen=True, slots=True)
class OutcomeReceipt:
    outcome_class: OutcomeClass
    decision_id: str
    original_decision_hash: str
    settlement_evidence_id: str
    realized_payout: Decimal | None
    net_cash_flow: Decimal | None
    capital_duration_seconds: Decimal | None
    outcome_hash: str


def _build_fixture_outcome_receipt(
    decision: DecisionReceipt, settlement: _FixtureSettlementObservation
) -> OutcomeReceipt:
    """Build a disposable outcome receipt; this does not prove settlement provenance."""
    validate_decision_receipt(decision)
    if (
        type(settlement) is not _FixtureSettlementObservation
        or _ISSUED.get(id(settlement)) != settlement.evidence_id
        or _ISSUED_FINGERPRINTS.get(id(settlement)) != stable_hash(repr(settlement))
    ):
        raise DecisionError("settlement evidence is reconstructed or not issuer-issued")
    complete = (
        settlement.final
        and settlement.result in {"yes", "no"}
        and settlement.settled_at is not None
        and settlement.market_ticker == decision.selected_market_ticker
        and decision.classification.value in {"TRADE_YES", "TRADE_NO"}
    )
    if not complete:
        return _outcome(OutcomeClass.EVIDENCE_INCOMPLETE, decision, settlement, None, None, None)
    selected_wins = (
        decision.classification.value == "TRADE_YES" and settlement.result == "yes"
    ) or (decision.classification.value == "TRADE_NO" and settlement.result == "no")
    payout = Decimal("1.00") if selected_wins else Decimal("0.00")
    if decision.all_in_debit is None or settlement.settled_at is None:
        raise DecisionError("complete settlement lacks decision debit or timestamp")
    if decision.bundle is None:
        raise DecisionError("complete decision lacks evidence bundle")
    duration = settlement.settled_at - decision.bundle.book.response_complete
    if duration.total_seconds() < 0:
        raise DecisionError("settlement precedes hypothetical entry")
    return _outcome(
        OutcomeClass.COMPLETE,
        decision,
        settlement,
        payout,
        payout - decision.all_in_debit,
        Decimal(str(duration.total_seconds())),
    )


def _outcome(
    outcome_class: OutcomeClass,
    decision: DecisionReceipt,
    settlement: _FixtureSettlementObservation,
    payout: Decimal | None,
    net: Decimal | None,
    duration: Decimal | None,
) -> OutcomeReceipt:
    digest = hashlib.sha256(
        repr(
            (
                outcome_class,
                decision.decision_id,
                decision.payload_hash,
                settlement.evidence_id,
                payout,
                net,
                duration,
            )
        ).encode()
    ).hexdigest()
    return OutcomeReceipt(
        outcome_class,
        decision.decision_id,
        decision.payload_hash,
        settlement.evidence_id,
        payout,
        net,
        duration,
        digest,
    )


def _outcome_digest(receipt: OutcomeReceipt) -> str:
    return hashlib.sha256(
        repr(
            (
                receipt.outcome_class,
                receipt.decision_id,
                receipt.original_decision_hash,
                receipt.settlement_evidence_id,
                receipt.realized_payout,
                receipt.net_cash_flow,
                receipt.capital_duration_seconds,
            )
        ).encode()
    ).hexdigest()


def validate_outcome_receipt(
    receipt: OutcomeReceipt,
    *,
    decision: DecisionReceipt | None = None,
    settlement: _FixtureSettlementObservation | None = None,
) -> None:
    """Validate receipt integrity and optional bindings, not source provenance."""
    if type(receipt) is not OutcomeReceipt:
        raise DecisionError("outcome receipt type is invalid")
    if receipt.outcome_hash != _outcome_digest(receipt):
        raise DecisionError("outcome receipt digest mismatch")
    if type(receipt.decision_id) is not str or type(receipt.original_decision_hash) is not str:
        raise DecisionError("outcome decision binding is invalid")
    if decision is not None:
        validate_decision_receipt(decision)
        if (
            receipt.decision_id != decision.decision_id
            or receipt.original_decision_hash != decision.payload_hash
        ):
            raise DecisionError("outcome does not bind to the supplied decision")
    if settlement is not None:
        if (
            type(settlement) is not _FixtureSettlementObservation
            or _ISSUED.get(id(settlement)) != settlement.evidence_id
            or _ISSUED_FINGERPRINTS.get(id(settlement)) != stable_hash(repr(settlement))
        ):
            raise DecisionError("settlement observation is reconstructed or tampered")
        if receipt.settlement_evidence_id != settlement.evidence_id:
            raise DecisionError("outcome does not bind to the supplied settlement")
    if receipt.outcome_class is OutcomeClass.COMPLETE:
        if receipt.realized_payout not in {Decimal("0.00"), Decimal("1.00")}:
            raise DecisionError("complete outcome payout is invalid")
        if receipt.net_cash_flow is None or receipt.capital_duration_seconds is None:
            raise DecisionError("complete outcome economics are incomplete")
        if receipt.capital_duration_seconds < Decimal("0"):
            raise DecisionError("capital duration is negative")
    elif any(
        value is not None
        for value in (
            receipt.realized_payout,
            receipt.net_cash_flow,
            receipt.capital_duration_seconds,
        )
    ):
        raise DecisionError("incomplete outcome contains final economics")
    if decision is not None and settlement is not None:
        expected = _build_fixture_outcome_receipt(decision, settlement)
        if receipt != expected:
            raise DecisionError("outcome economics or binding fields do not replay")


__all__ = ["OutcomeClass", "OutcomeReceipt", "validate_outcome_receipt"]
