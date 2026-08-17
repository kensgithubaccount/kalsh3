from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_settlement_mapping import (
    Consistency,
    EvidenceClass,
    GHCNComparisonObservation,
    KalshiSettlementAuthorityEvidence,
    SettlementImpliedObservation,
    TWCValueEvidence,
    classify_ghcn_against_settlement,
)

SHA = "a" * 64


def authority(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "series": "CLIMDW",
        "settlement_source": "The Weather Company",
        "location": "Chicago",
        "measurement": "DAILY_MAX",
        "source_url": "https://docs.kalshi.com/api-reference/market/get-series",
        "contract_url": None,
        "contract_terms_url": None,
        "acquired_at": "2026-08-17T12:00:00-04:00",
        "raw_sha256": SHA,
        "parser_policy_version": "test-v1",
    }
    value.update(changes)
    return value


def twc(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "target_date": "2025-07-04",
        "value_f": "91",
        "evidence_class": EvidenceClass.CURRENT_HISTORICAL_SNAPSHOT.value,
        "product_identity": "TWC historical observations product (unresolved Kalshi mapping)",
        "station_or_location": "unresolved",
        "source_url": "https://www.ibm.com/docs/en/environmental-intel-suite?topic=apis-history-demand",
        "acquired_at": "2026-08-17T12:00:00-04:00",
        "raw_sha256": SHA,
        "parser_policy_version": "test-v1",
    }
    value.update(changes)
    return value


def test_correct_kalshi_authority_passes_and_is_zero_influence() -> None:
    result = KalshiSettlementAuthorityEvidence.from_mapping(authority())
    assert result.series == "CLIMDW"
    assert result.research_only and result.production_influence == Decimal("0")


@pytest.mark.parametrize(
    "changes",
    [
        {"series": "KXHIGHCHI"},
        {"location": "Chicago Midway International Airport"},
        {"measurement": "DAILY_MIN"},
        {"settlement_source": "NOAA"},
    ],
)
def test_wrong_authority_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ForecastError):
        KalshiSettlementAuthorityEvidence.from_mapping(authority(**changes))


def test_unknown_fields_and_malformed_hash_fail() -> None:
    with pytest.raises(ForecastError, match="unknown"):
        KalshiSettlementAuthorityEvidence.from_mapping(authority(unreviewed_note="x"))
    with pytest.raises(ForecastError, match="sha256"):
        KalshiSettlementAuthorityEvidence.from_mapping(authority(raw_sha256="not-a-hash"))


def test_unofficial_twc_value_cannot_be_authoritative() -> None:
    with pytest.raises(ForecastError, match="unofficial"):
        TWCValueEvidence.from_mapping(twc(source_url="https://weather.example/value"))
    with pytest.raises(ForecastError, match="not authoritative"):
        TWCValueEvidence.from_mapping(twc(evidence_class=EvidenceClass.GHCN_COMPARISON.value))


@pytest.mark.parametrize("target", ["2026-08-01", "2026-09-01"])
def test_august_and_prospective_dates_are_rejected(target: str) -> None:
    with pytest.raises(ForecastError):
        TWCValueEvidence.from_mapping(twc(target_date=target))


def test_current_snapshot_cannot_claim_settlement_vintage() -> None:
    result = TWCValueEvidence.from_mapping(twc())
    assert result.evidence_class is EvidenceClass.CURRENT_HISTORICAL_SNAPSHOT
    assert result.evidence_class is not EvidenceClass.SETTLEMENT_VINTAGED


def test_exact_decimal_comparison_has_no_implicit_rounding() -> None:
    settlement = SettlementImpliedObservation(
        date(2025, 7, 4), "RANGE", Decimal("90.5"), Decimal("91"), True, "e" * 64
    )
    ghcn = GHCNComparisonObservation(date(2025, 7, 4), Decimal("90.49"), "g" * 64)
    assert (
        classify_ghcn_against_settlement(settlement, ghcn)
        is Consistency.GHCND_INCONSISTENT_WITH_SETTLEMENT
    )


def test_settlement_interval_does_not_become_point_value() -> None:
    settlement = SettlementImpliedObservation(
        date(2025, 7, 4), "RANGE", Decimal("90"), Decimal("91"), True, "e" * 64
    )
    ghcn = GHCNComparisonObservation(date(2025, 7, 4), Decimal("90.5"), "g" * 64)
    assert (
        classify_ghcn_against_settlement(settlement, ghcn)
        is Consistency.GHCND_CONSISTENT_WITH_SETTLEMENT
    )
    assert not hasattr(settlement, "settlement_value_f")


def test_ambiguous_settlement_abstains_and_never_falls_back() -> None:
    settlement = SettlementImpliedObservation(
        date(2025, 7, 4), "UNKNOWN", None, None, True, "e" * 64
    )
    ghcn = GHCNComparisonObservation(date(2025, 7, 4), Decimal("90"), "g" * 64)
    assert classify_ghcn_against_settlement(settlement, ghcn) is Consistency.AMBIGUOUS


def test_nonzero_influence_is_rejected() -> None:
    with pytest.raises(ForecastError):
        GHCNComparisonObservation(
            date(2025, 7, 4), Decimal("90"), "g" * 64, production_influence=Decimal("0.01")
        )
