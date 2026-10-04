"""Frozen prospective protocol for the first Perps reference-lag untouched slice.

This module is deliberately pure and offline. It freezes the exact market,
schedule, dependence unit, mechanism metrics, and pass/continue/kill vocabulary
before any prospective Phase-0 evidence is collected.

It contains no network, credential, order, probability, fee, P&L, sizing, or
production-authority capability.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from .domain import ShadowResearchError
from .reference_lag import HorizonStatus, ReferenceImpulse, ReferenceLagMeasurement, ReferenceMove

PROTOCOL_RECORD_TYPE = "PERPS-REFERENCE-LAG-P0-PROSPECTIVE-PROTOCOL-v1"
PROTOCOL_STATUS = "FROZEN_BEFORE_FIRST_PROSPECTIVE_SESSION"
ENVIRONMENT = "production"
TICKER = "BTC-PERP"
NO_SUBSTITUTION = True
SESSION_DURATION_SECONDS = 60
SESSION_START_LATE_TOLERANCE_SECONDS = 300
PRIMARY_INDEPENDENCE_UNIT = "SESSION"
IMPULSES_INDEPENDENT = False
HORIZONS_MS = (1_000, 2_000, 5_000, 10_000)
PRIMARY_HORIZON_MS = 2_000
CONFIRMATORY_HORIZON_MS = 5_000
MIN_MEASURED_MIDPOINT_IMPULSES_PER_ELIGIBLE_SESSION = 3
MIN_ELIGIBLE_SESSIONS = 5
MIN_PRIMARY_MEASURED_MIDPOINT_IMPULSES = 30
PROMISING_MIN_POSITIVE_SESSION_FRACTION = Decimal("0.60")
BOOK_MAX_AGE_MS = 30_000
OPENAPI_SHA256 = "d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0"
ASYNCAPI_SHA256 = "e5cc0f026b8e306e917860b870e23c9152d0d782c33137d42698f051a9dbe824"
PRODUCTION_INFLUENCE = Decimal("0")

SESSION_SCHEDULE: tuple[tuple[str, datetime], ...] = (
    ("P0-S01", datetime(2026, 10, 5, 12, 0, tzinfo=UTC)),
    ("P0-S02", datetime(2026, 10, 5, 15, 0, tzinfo=UTC)),
    ("P0-S03", datetime(2026, 10, 5, 18, 0, tzinfo=UTC)),
    ("P0-S04", datetime(2026, 10, 5, 21, 0, tzinfo=UTC)),
    ("P0-S05", datetime(2026, 10, 6, 0, 0, tzinfo=UTC)),
    ("P0-S06", datetime(2026, 10, 6, 3, 0, tzinfo=UTC)),
    ("P0-S07", datetime(2026, 10, 6, 6, 0, tzinfo=UTC)),
    ("P0-S08", datetime(2026, 10, 6, 9, 0, tzinfo=UTC)),
)


class ProspectiveProtocolError(ShadowResearchError):
    """Frozen Phase-0 prospective protocol violation."""


class CollectionStatus(StrEnum):
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    MISSING = "MISSING"


class Phase0Decision(StrEnum):
    PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH = "PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH"
    NO_APPARENT_MECHANISM = "NO_APPARENT_MECHANISM"
    INCONCLUSIVE_INSUFFICIENT = "INCONCLUSIVE_INSUFFICIENT"
    INCONCLUSIVE_MIXED = "INCONCLUSIVE_MIXED"


@dataclass(frozen=True, slots=True)
class SessionMechanismSummary:
    session_id: str
    collection_status: CollectionStatus
    primary_measured_impulses: int
    confirmatory_measured_impulses: int
    primary_mean_signed_midpoint_bps: Decimal | None
    confirmatory_mean_signed_midpoint_bps: Decimal | None

    @property
    def eligible(self) -> bool:
        return (
            self.collection_status is CollectionStatus.CAPTURED
            and self.primary_measured_impulses
            >= MIN_MEASURED_MIDPOINT_IMPULSES_PER_ELIGIBLE_SESSION
            and self.confirmatory_measured_impulses
            >= MIN_MEASURED_MIDPOINT_IMPULSES_PER_ELIGIBLE_SESSION
            and self.primary_mean_signed_midpoint_bps is not None
            and self.confirmatory_mean_signed_midpoint_bps is not None
        )


@dataclass(frozen=True, slots=True)
class ProspectiveEvaluation:
    decision: Phase0Decision
    expected_sessions: int
    captured_sessions: int
    failed_sessions: int
    missing_sessions: int
    eligible_sessions: int
    primary_measured_impulses: int
    cluster_equal_primary_mean_bps: Decimal | None
    cluster_equal_confirmatory_mean_bps: Decimal | None
    positive_primary_session_fraction: Decimal | None
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != 0:
            raise ProspectiveProtocolError("prospective evaluation cannot influence production")


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def protocol_payload() -> dict[str, object]:
    return {
        "record_type": PROTOCOL_RECORD_TYPE,
        "status": PROTOCOL_STATUS,
        "environment": ENVIRONMENT,
        "ticker": TICKER,
        "no_substitution": NO_SUBSTITUTION,
        "session_duration_seconds": SESSION_DURATION_SECONDS,
        "session_start_late_tolerance_seconds": SESSION_START_LATE_TOLERANCE_SECONDS,
        "sessions": [_iso_z(value) for _session_id, value in SESSION_SCHEDULE],
        "primary_independence_unit": PRIMARY_INDEPENDENCE_UNIT,
        "impulses_independent": IMPULSES_INDEPENDENT,
        "horizons_ms": list(HORIZONS_MS),
        "primary_horizon_ms": PRIMARY_HORIZON_MS,
        "confirmatory_horizon_ms": CONFIRMATORY_HORIZON_MS,
        "minimum_measured_midpoint_impulses_per_eligible_session": (
            MIN_MEASURED_MIDPOINT_IMPULSES_PER_ELIGIBLE_SESSION
        ),
        "minimum_eligible_sessions": MIN_ELIGIBLE_SESSIONS,
        "minimum_primary_measured_midpoint_impulses": MIN_PRIMARY_MEASURED_MIDPOINT_IMPULSES,
        "promising_min_positive_session_fraction": str(
            PROMISING_MIN_POSITIVE_SESSION_FRACTION
        ),
        "book_max_age_ms": BOOK_MAX_AGE_MS,
        "no_retry": True,
        "no_backfill": True,
        "no_probability": True,
        "no_fees": True,
        "no_pnl": True,
        "no_orders": True,
        "production_influence": str(PRODUCTION_INFLUENCE),
        "phase0_spec_openapi_sha256": OPENAPI_SHA256,
        "phase0_spec_asyncapi_sha256": ASYNCAPI_SHA256,
    }


def protocol_sha256() -> str:
    encoded = json.dumps(
        protocol_payload(), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


PROTOCOL_SHA256 = "839c2b16099bf99c5812abc4ef9aad1f506dc4f439dedcd0ffeac2694a8724ed"


def expected_session_ids() -> tuple[str, ...]:
    return tuple(session_id for session_id, _scheduled_at in SESSION_SCHEDULE)


def scheduled_at(session_id: str) -> datetime:
    matches = [value for candidate, value in SESSION_SCHEDULE if candidate == session_id]
    if len(matches) != 1:
        raise ProspectiveProtocolError("unknown prospective session id")
    return matches[0]


def _signed_midpoint_bps(
    impulse: ReferenceImpulse,
    measurement: ReferenceLagMeasurement,
) -> Decimal | None:
    if (
        measurement.status is not HorizonStatus.MEASURED
        or measurement.midpoint_change_bps is None
    ):
        return None
    if measurement.impulse_id != impulse.impulse_id:
        raise ProspectiveProtocolError("measurement does not bind the supplied impulse")
    sign = Decimal("1") if impulse.reference_move is ReferenceMove.UP else Decimal("-1")
    return measurement.midpoint_change_bps * sign


def summarize_session(
    session_id: str,
    collection_status: CollectionStatus,
    impulses: Mapping[str, ReferenceImpulse],
    measurements: Iterable[ReferenceLagMeasurement],
) -> SessionMechanismSummary:
    if session_id not in expected_session_ids():
        raise ProspectiveProtocolError("session is outside frozen roster")

    material = tuple(measurements)
    seen_pairs: set[tuple[str, int]] = set()
    primary: list[Decimal] = []
    confirmatory: list[Decimal] = []

    for row in material:
        pair = (row.impulse_id, row.horizon_ms)
        if pair in seen_pairs:
            raise ProspectiveProtocolError("duplicate impulse/horizon measurement")
        seen_pairs.add(pair)
        impulse = impulses.get(row.impulse_id)
        if impulse is None:
            raise ProspectiveProtocolError("measurement references unknown impulse")
        signed = _signed_midpoint_bps(impulse, row)
        if signed is None:
            continue
        if row.horizon_ms == PRIMARY_HORIZON_MS:
            primary.append(signed)
        elif row.horizon_ms == CONFIRMATORY_HORIZON_MS:
            confirmatory.append(signed)

    def mean(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal("0")) / Decimal(len(values))

    return SessionMechanismSummary(
        session_id=session_id,
        collection_status=collection_status,
        primary_measured_impulses=len(primary),
        confirmatory_measured_impulses=len(confirmatory),
        primary_mean_signed_midpoint_bps=mean(primary),
        confirmatory_mean_signed_midpoint_bps=mean(confirmatory),
    )


def evaluate_prospective_sessions(
    summaries: Iterable[SessionMechanismSummary],
) -> ProspectiveEvaluation:
    material = tuple(summaries)
    by_id = {item.session_id: item for item in material}
    if len(by_id) != len(material):
        raise ProspectiveProtocolError("duplicate prospective session summary")
    if set(by_id) != set(expected_session_ids()):
        raise ProspectiveProtocolError(
            "evaluation must account for every frozen session exactly once"
        )

    ordered = tuple(by_id[session_id] for session_id in expected_session_ids())
    captured = sum(item.collection_status is CollectionStatus.CAPTURED for item in ordered)
    failed = sum(item.collection_status is CollectionStatus.FAILED for item in ordered)
    missing = sum(item.collection_status is CollectionStatus.MISSING for item in ordered)
    eligible = tuple(item for item in ordered if item.eligible)

    primary_count = sum(item.primary_measured_impulses for item in eligible)

    if eligible:
        denominator = Decimal(len(eligible))
        primary_cluster_mean = (
            sum(
                (
                    item.primary_mean_signed_midpoint_bps
                    for item in eligible
                    if item.primary_mean_signed_midpoint_bps is not None
                ),
                Decimal("0"),
            )
            / denominator
        )
        confirmatory_cluster_mean = (
            sum(
                (
                    item.confirmatory_mean_signed_midpoint_bps
                    for item in eligible
                    if item.confirmatory_mean_signed_midpoint_bps is not None
                ),
                Decimal("0"),
            )
            / denominator
        )
        positive_fraction = (
            Decimal(
                sum(
                    item.primary_mean_signed_midpoint_bps is not None
                    and item.primary_mean_signed_midpoint_bps > 0
                    for item in eligible
                )
            )
            / denominator
        )
    else:
        primary_cluster_mean = confirmatory_cluster_mean = positive_fraction = None

    if (
        len(eligible) < MIN_ELIGIBLE_SESSIONS
        or primary_count < MIN_PRIMARY_MEASURED_MIDPOINT_IMPULSES
    ):
        decision = Phase0Decision.INCONCLUSIVE_INSUFFICIENT
    elif (
        primary_cluster_mean is not None
        and confirmatory_cluster_mean is not None
        and positive_fraction is not None
        and primary_cluster_mean > 0
        and confirmatory_cluster_mean >= 0
        and positive_fraction >= PROMISING_MIN_POSITIVE_SESSION_FRACTION
    ):
        decision = Phase0Decision.PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH
    elif (
        primary_cluster_mean is not None
        and confirmatory_cluster_mean is not None
        and primary_cluster_mean <= 0
        and confirmatory_cluster_mean <= 0
    ):
        decision = Phase0Decision.NO_APPARENT_MECHANISM
    else:
        decision = Phase0Decision.INCONCLUSIVE_MIXED

    return ProspectiveEvaluation(
        decision=decision,
        expected_sessions=len(ordered),
        captured_sessions=captured,
        failed_sessions=failed,
        missing_sessions=missing,
        eligible_sessions=len(eligible),
        primary_measured_impulses=primary_count,
        cluster_equal_primary_mean_bps=primary_cluster_mean,
        cluster_equal_confirmatory_mean_bps=confirmatory_cluster_mean,
        positive_primary_session_fraction=positive_fraction,
    )
