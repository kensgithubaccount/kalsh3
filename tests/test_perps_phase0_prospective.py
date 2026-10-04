from __future__ import annotations

from decimal import Decimal

import pytest

from services.perps_shadow_research.phase0_prospective import (
    ASYNCAPI_SHA256,
    CONFIRMATORY_HORIZON_MS,
    MIN_ELIGIBLE_SESSIONS,
    MIN_PRIMARY_MEASURED_MIDPOINT_IMPULSES,
    NO_SUBSTITUTION,
    OPENAPI_SHA256,
    PRIMARY_HORIZON_MS,
    PRIMARY_INDEPENDENCE_UNIT,
    PRODUCTION_INFLUENCE,
    PROTOCOL_SHA256,
    SESSION_DURATION_SECONDS,
    CollectionStatus,
    Phase0Decision,
    ProspectiveProtocolError,
    SessionMechanismSummary,
    evaluate_prospective_sessions,
    expected_session_ids,
    protocol_sha256,
    scheduled_at,
)


def summary(
    session_id: str,
    *,
    primary: str | None,
    confirmatory: str | None,
    primary_count: int = 6,
    confirmatory_count: int = 6,
    status: CollectionStatus = CollectionStatus.CAPTURED,
) -> SessionMechanismSummary:
    return SessionMechanismSummary(
        session_id=session_id,
        collection_status=status,
        primary_measured_impulses=primary_count,
        confirmatory_measured_impulses=confirmatory_count,
        primary_mean_signed_midpoint_bps=None if primary is None else Decimal(primary),
        confirmatory_mean_signed_midpoint_bps=(
            None if confirmatory is None else Decimal(confirmatory)
        ),
    )


def roster_with(
    eligible_values: list[tuple[str, str]],
    *,
    remaining_status: CollectionStatus = CollectionStatus.CAPTURED,
) -> list[SessionMechanismSummary]:
    ids = expected_session_ids()
    rows = [
        summary(ids[index], primary=p, confirmatory=c)
        for index, (p, c) in enumerate(eligible_values)
    ]
    for session_id in ids[len(rows) :]:
        rows.append(
            summary(
                session_id,
                primary=None,
                confirmatory=None,
                primary_count=0,
                confirmatory_count=0,
                status=remaining_status,
            )
        )
    return rows


def test_protocol_identity_and_schedule_are_frozen() -> None:
    assert protocol_sha256() == PROTOCOL_SHA256
    assert PROTOCOL_SHA256 == ("74d653afaf2e2b7b830814e99f5fd7dca12cb69b1a025987c680574c3074a1ba")
    assert len(expected_session_ids()) == 8
    assert scheduled_at("P0-S01").isoformat() == "2026-10-05T12:00:00+00:00"
    assert scheduled_at("P0-S08").isoformat() == "2026-10-06T09:00:00+00:00"
    assert SESSION_DURATION_SECONDS == 60
    assert NO_SUBSTITUTION is True
    assert PRIMARY_INDEPENDENCE_UNIT == "SESSION"
    assert PRODUCTION_INFLUENCE == 0
    assert PRIMARY_HORIZON_MS == 2_000
    assert CONFIRMATORY_HORIZON_MS == 5_000
    assert OPENAPI_SHA256.startswith("d13cb9")
    assert ASYNCAPI_SHA256.startswith("e5cc0f")


def test_unknown_session_fails_closed() -> None:
    with pytest.raises(ProspectiveProtocolError, match="unknown"):
        scheduled_at("P0-S99")


def test_promising_diagnostic_requires_cluster_level_breadth() -> None:
    rows = roster_with(
        [
            ("1.0", "0.8"),
            ("0.6", "0.5"),
            ("0.4", "0.2"),
            ("0.3", "0.1"),
            ("0.2", "0.1"),
        ]
    )
    result = evaluate_prospective_sessions(rows)
    assert result.decision is Phase0Decision.PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH
    assert result.eligible_sessions == MIN_ELIGIBLE_SESSIONS
    assert result.primary_measured_impulses == MIN_PRIMARY_MEASURED_MIDPOINT_IMPULSES
    assert result.positive_primary_session_fraction == Decimal("1")


def test_insufficient_sessions_remain_inconclusive() -> None:
    rows = roster_with(
        [("1", "1"), ("1", "1"), ("1", "1"), ("1", "1")],
        remaining_status=CollectionStatus.FAILED,
    )
    result = evaluate_prospective_sessions(rows)
    assert result.decision is Phase0Decision.INCONCLUSIVE_INSUFFICIENT
    assert result.failed_sessions == 4


def test_adequate_nonpositive_primary_and_confirmatory_is_no_mechanism() -> None:
    rows = roster_with(
        [
            ("-0.2", "-0.1"),
            ("-0.4", "-0.3"),
            ("0.1", "-0.1"),
            ("-0.1", "-0.2"),
            ("-0.3", "-0.2"),
        ]
    )
    result = evaluate_prospective_sessions(rows)
    assert result.decision is Phase0Decision.NO_APPARENT_MECHANISM
    assert result.cluster_equal_primary_mean_bps is not None
    assert result.cluster_equal_primary_mean_bps <= 0


def test_adequate_mixed_pattern_is_inconclusive_not_promoted() -> None:
    rows = roster_with(
        [
            ("0.4", "-0.1"),
            ("0.3", "-0.1"),
            ("0.2", "-0.1"),
            ("-0.1", "-0.1"),
            ("-0.1", "-0.1"),
        ]
    )
    result = evaluate_prospective_sessions(rows)
    assert result.decision is Phase0Decision.INCONCLUSIVE_MIXED


def test_every_frozen_session_must_be_accounted_once() -> None:
    rows = roster_with([("1", "1")] * 5)
    with pytest.raises(ProspectiveProtocolError, match="every frozen session"):
        evaluate_prospective_sessions(rows[:-1])
    with pytest.raises(ProspectiveProtocolError, match="duplicate"):
        evaluate_prospective_sessions([*rows[:-1], rows[0]])
