"""KU-A1 immutable research-only whole-exchange lifecycle evidence.

This module defines only the two lifecycle states authorized by KU-A1.  It has no
network, credential, account, forecasting, economics, risk, promotion, execution,
signer, arm, or order authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from .domain import stable_hash

LIFECYCLE_SCHEMA_VERSION = "ku-a1-market-lifecycle-v1"
CAPTURE_SCHEMA_VERSION = "ku-a1-universe-capture-v1"
ZERO_INFLUENCE = Decimal("0")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LIFECYCLE_ISSUANCE_CAPABILITY = object()


class LifecycleError(ValueError):
    """KU-A1 lifecycle evidence is malformed or attempts to exceed authority."""


class LifecycleState(StrEnum):
    DISCOVERED = "DISCOVERED"
    SEMANTICALLY_UNDERSTOOD = "SEMANTICALLY_UNDERSTOOD"


class ProductType(StrEnum):
    BINARY_EVENT = "BINARY_EVENT"
    MULTIVARIATE_EVENT = "MULTIVARIATE_EVENT"
    SCALAR_OR_PARTIAL = "SCALAR_OR_PARTIAL"
    NON_EVENT = "NON_EVENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class UniverseCaptureEvidence:
    """Caller-supplied identity for one offline captured public-universe response.

    ``response_sha256`` is descriptive captured-input provenance for KU-A1. It is not
    independently authenticated Kalshi acquisition or transport authority.
    """

    source_authority: str
    request_locator: str
    response_sha256: str
    captured_at: datetime
    schema_version: str = field(init=False, default=CAPTURE_SCHEMA_VERSION)
    capture_id: str = field(init=False)
    content_hash: str = field(init=False)
    research_only: bool = field(init=False, default=True)
    production_influence: Decimal = field(init=False, default=ZERO_INFLUENCE)

    def __post_init__(self) -> None:
        authority = self.source_authority.strip()
        locator = self.request_locator.strip()
        response_hash = self.response_sha256.strip().lower()
        if not authority or not locator:
            raise LifecycleError("capture provenance is incomplete")
        if not _SHA256.fullmatch(response_hash):
            raise LifecycleError("capture response hash must be sha256")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise LifecycleError("capture timestamp must be timezone-aware")
        captured = self.captured_at.astimezone(UTC)
        material = (
            CAPTURE_SCHEMA_VERSION,
            authority,
            locator,
            response_hash,
            captured.isoformat(),
            "RESEARCH_ONLY",
            "0",
        )
        digest = stable_hash(material)
        object.__setattr__(self, "source_authority", authority)
        object.__setattr__(self, "request_locator", locator)
        object.__setattr__(self, "response_sha256", response_hash)
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "capture_id", digest)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True, init=False)
class MarketLifecycleRecord:
    """Content-addressed KU-A1 state issued only by the canonical router.

    ``semantic_material_hash`` deliberately excludes market prices.  It is used by the
    router only to decide whether a prior semantic proof was materially superseded; the
    record itself still binds the exact capture and market-input evidence identities.
    """

    capture_id: str
    market_input_hash: str
    market_id: str | None
    market_ticker: str
    event_id: str | None
    event_ticker: str
    series_id: str | None
    series_ticker: str | None
    product_type: ProductType
    payout_model: str
    state: LifecycleState
    rules_hash: str
    metadata_hash: str
    parent_evidence_hash: str | None
    settlement_source_identity: str | None
    specialist_route_id: str | None
    specialist_route_state: str | None
    specialist_route_reasons: tuple[str, ...]
    advisory_family: str
    semantic_status: str | None
    semantic_proof_ids: tuple[str, ...]
    semantic_blockers: tuple[str, ...]
    unsupported_reasons: tuple[str, ...]
    semantic_material_hash: str
    supersedes_record_id: str | None
    schema_version: str
    lifecycle_record_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("MarketLifecycleRecord is canonical-router-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        capture_id: str,
        market_input_hash: str,
        market_id: str | None,
        market_ticker: str,
        event_id: str | None,
        event_ticker: str,
        series_id: str | None,
        series_ticker: str | None,
        product_type: ProductType,
        payout_model: str,
        state: LifecycleState,
        rules_hash: str,
        metadata_hash: str,
        parent_evidence_hash: str | None,
        settlement_source_identity: str | None,
        specialist_route_id: str | None,
        specialist_route_state: str | None,
        specialist_route_reasons: tuple[str, ...],
        advisory_family: str,
        semantic_status: str | None,
        semantic_proof_ids: tuple[str, ...],
        semantic_blockers: tuple[str, ...],
        unsupported_reasons: tuple[str, ...],
        semantic_material_hash: str,
        supersedes_record_id: str | None = None,
    ) -> MarketLifecycleRecord:
        if capability is not _LIFECYCLE_ISSUANCE_CAPABILITY:
            raise LifecycleError("lifecycle issuance capability is invalid")
        self = object.__new__(cls)
        for name, value in (
            ("capture_id", capture_id),
            ("market_input_hash", market_input_hash),
            ("market_id", market_id),
            ("market_ticker", market_ticker),
            ("event_id", event_id),
            ("event_ticker", event_ticker),
            ("series_id", series_id),
            ("series_ticker", series_ticker),
            ("product_type", product_type),
            ("payout_model", payout_model),
            ("state", state),
            ("rules_hash", rules_hash),
            ("metadata_hash", metadata_hash),
            ("parent_evidence_hash", parent_evidence_hash),
            ("settlement_source_identity", settlement_source_identity),
            ("specialist_route_id", specialist_route_id),
            ("specialist_route_state", specialist_route_state),
            ("specialist_route_reasons", specialist_route_reasons),
            ("advisory_family", advisory_family),
            ("semantic_status", semantic_status),
            ("semantic_proof_ids", semantic_proof_ids),
            ("semantic_blockers", semantic_blockers),
            ("unsupported_reasons", unsupported_reasons),
            ("semantic_material_hash", semantic_material_hash),
            ("supersedes_record_id", supersedes_record_id),
        ):
            object.__setattr__(self, name, value)
        self._finalize()
        return self

    def _reissue_with_supersession(
        self, *, capability: object, supersedes_record_id: str
    ) -> MarketLifecycleRecord:
        return type(self)._issue(
            capability=capability,
            capture_id=self.capture_id,
            market_input_hash=self.market_input_hash,
            market_id=self.market_id,
            market_ticker=self.market_ticker,
            event_id=self.event_id,
            event_ticker=self.event_ticker,
            series_id=self.series_id,
            series_ticker=self.series_ticker,
            product_type=self.product_type,
            payout_model=self.payout_model,
            state=self.state,
            rules_hash=self.rules_hash,
            metadata_hash=self.metadata_hash,
            parent_evidence_hash=self.parent_evidence_hash,
            settlement_source_identity=self.settlement_source_identity,
            specialist_route_id=self.specialist_route_id,
            specialist_route_state=self.specialist_route_state,
            specialist_route_reasons=self.specialist_route_reasons,
            advisory_family=self.advisory_family,
            semantic_status=self.semantic_status,
            semantic_proof_ids=self.semantic_proof_ids,
            semantic_blockers=self.semantic_blockers,
            unsupported_reasons=self.unsupported_reasons,
            semantic_material_hash=self.semantic_material_hash,
            supersedes_record_id=supersedes_record_id,
        )

    def _finalize(self) -> None:
        required = (
            self.capture_id,
            self.market_input_hash,
            self.market_ticker,
            self.event_ticker,
            self.payout_model,
            self.rules_hash,
            self.metadata_hash,
            self.advisory_family,
            self.semantic_material_hash,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise LifecycleError("lifecycle record identity/evidence is incomplete")
        if self.state is LifecycleState.SEMANTICALLY_UNDERSTOOD:
            if self.product_type is not ProductType.BINARY_EVENT:
                raise LifecycleError(
                    "non-binary product cannot be semantically understood in KU-A1"
                )
            if self.semantic_status != "VALID" or not self.semantic_proof_ids:
                raise LifecycleError("semantic state requires deterministic VALID proof")
            if self.semantic_blockers or self.unsupported_reasons:
                raise LifecycleError("semantic state cannot retain blocking reasons")
        if self.state is LifecycleState.DISCOVERED and not (
            self.semantic_blockers or self.unsupported_reasons
        ):
            raise LifecycleError(
                "discovered record requires an explicit blocker or unsupported reason"
            )
        route_reasons = tuple(sorted(set(self.specialist_route_reasons)))
        semantic_proofs = tuple(sorted(set(self.semantic_proof_ids)))
        blockers = tuple(sorted(set(self.semantic_blockers)))
        unsupported = tuple(sorted(set(self.unsupported_reasons)))
        material = (
            LIFECYCLE_SCHEMA_VERSION,
            self.capture_id,
            self.market_input_hash,
            self.market_id,
            self.market_ticker,
            self.event_id,
            self.event_ticker,
            self.series_id,
            self.series_ticker,
            self.product_type.value,
            self.payout_model,
            self.state.value,
            self.rules_hash,
            self.metadata_hash,
            self.parent_evidence_hash,
            self.settlement_source_identity,
            self.specialist_route_id,
            self.specialist_route_state,
            route_reasons,
            self.advisory_family,
            self.semantic_status,
            semantic_proofs,
            blockers,
            unsupported,
            self.semantic_material_hash,
            self.supersedes_record_id,
            "RESEARCH_ONLY",
            "0",
        )
        digest = stable_hash(material)
        object.__setattr__(self, "specialist_route_reasons", route_reasons)
        object.__setattr__(self, "semantic_proof_ids", semantic_proofs)
        object.__setattr__(self, "semantic_blockers", blockers)
        object.__setattr__(self, "unsupported_reasons", unsupported)
        object.__setattr__(self, "schema_version", LIFECYCLE_SCHEMA_VERSION)
        object.__setattr__(self, "lifecycle_record_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
