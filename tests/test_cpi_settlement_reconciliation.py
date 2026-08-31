from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_manual_acquisition as manual
from services.forecasting.cpi_initial_release_value import (
    CPIInitialReleaseObservation,
    issue_cpi_initial_release_observation,
)
from services.forecasting.cpi_settlement_reconciliation import (
    CPIHistoricalSemanticEvidence,
    CPISettlementReconciliationError,
    ExpectedBinaryResult,
    KalshiFinalizedEvidence,
    KalshiHistoricalAcquisitionEvidence,
    build_historical_semantic_evidence,
    expected_binary_result,
    load_frozen_kalshi_acquisition,
    reconcile_cpi_settlement,
    validate_kalshi_acquisition,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, month: str, value: str, year: int
) -> CPIInitialReleaseObservation:
    path = tmp_path / f"cpi-{month}.htm"
    path.write_text(
        f"""<html><body><h1>CONSUMER PRICE INDEX - {month.upper()} {year}</h1>
        <p>Transmission of material in this release is embargoed until 8:30 a.m.
        (ET) Tuesday, August 12, 2025</p>
        <p>The Consumer Price Index for All Urban Consumers (CPI-U) increased {value}
        percent on a seasonally adjusted basis in {month}, the U.S. Bureau of Labor
        Statistics reported today.</p>
        <p>Table A. Percent changes in CPI for All Urban Consumers (CPI-U):
        U.S. city average</p>
        <table><tr><th>Seasonally adjusted changes from preceding month</th></tr>
        <tr><th>Jun. {year}</th><th>{month[:3].title()}. {year}</th>
        <th>Unadjusted 12-mos. ended {month[:3].title()} {year}</th></tr>
        <tr><td>All items</td><td>0.1</td><td>{value}</td><td>2.7</td></tr>
        </table></body></html>"""
    )
    monkeypatch.setattr(manual, "_utc_now", lambda: NOW)
    acquisition = manual.attest_and_import_manual_bls_cpi_release(
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        path,
        operator_attestation=manual.OPERATOR_ATTESTATION,
    )
    return issue_cpi_initial_release_observation(
        issuer.issue_manual_acquisition_bound_cpi_evidence(acquisition)
    )


def _bundle(
    prefix: str,
) -> tuple[
    KalshiHistoricalAcquisitionEvidence,
    KalshiHistoricalAcquisitionEvidence,
    KalshiHistoricalAcquisitionEvidence,
    CPIHistoricalSemanticEvidence,
]:
    market = load_frozen_kalshi_acquisition(f"market-{prefix}")
    event = load_frozen_kalshi_acquisition(f"event-{prefix}")
    series = load_frozen_kalshi_acquisition("series")
    return market, event, series, build_historical_semantic_evidence(market, event, series)


@pytest.mark.parametrize(
    "prefix,month,value",
    [("jul", "july", "0.2"), ("dec", "december", "0.3"), ("jan", "january", "0.2")],
)
def test_real_frozen_public_markets_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: str, month: str, value: str
) -> None:
    observation = _observation(
        tmp_path, monkeypatch, month, value, 2026 if prefix == "jan" else 2025
    )
    market, _event, _series, semantic = _bundle(prefix)
    exchange = KalshiFinalizedEvidence.from_acquisition(market)
    record = reconcile_cpi_settlement(observation, semantic, exchange)
    assert record.reconciliation_status.value == "MATCHED"
    assert record.eligible_training_label is True
    assert record.settlement_value_dollars == 1
    assert expected_binary_result(observation, semantic) is ExpectedBinaryResult.YES


def test_arbitrary_raw_json_has_no_acquisition_constructor() -> None:
    with pytest.raises((TypeError, CPISettlementReconciliationError)):
        KalshiHistoricalAcquisitionEvidence(response=object(), role=object())  # type: ignore[arg-type]
    assert not hasattr(KalshiFinalizedEvidence, "from_raw_response")


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_response", b"{}"),
        ("request_url", "https://evil.example/trade-api/v2/series/KXCPI"),
        ("method", "POST"),
        ("http_status", 201),
        ("endpoint_role", object()),
        ("expected_ticker", "FORGED"),
    ],
)
def test_acquisition_mutations_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    forged = acquisition
    object.__setattr__(forged, field, value)
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(forged)


def test_frozen_artifact_hash_is_recomputed() -> None:
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    forged = acquisition
    object.__setattr__(forged, "raw_artifact_hash", "0" * 64)
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(forged)


def test_semantics_are_rebuilt_and_arbitrary_spec_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = _observation(tmp_path, monkeypatch, "july", "0.2", 2025)
    market, event, series, semantic = _bundle("jul")
    exchange = KalshiFinalizedEvidence.from_acquisition(market)
    with pytest.raises(CPISettlementReconciliationError):
        reconcile_cpi_settlement(
            observation, cast(CPIHistoricalSemanticEvidence, semantic.specification), exchange
        )
    with pytest.raises(CPISettlementReconciliationError):
        CPIHistoricalSemanticEvidence(
            market=market,
            event=event,
            series=series,
            specification=replace(
                semantic.specification, comparator=semantic.specification.comparator
            ),
            _capability=None,
        )


@pytest.mark.parametrize(
    "field",
    [
        "comparator",
        "threshold_value",
        "occurrence_time",
        "rules_version_id",
        "market_rules_hash",
        "semantic_hash",
    ],
)
def test_caller_mutated_specification_cannot_enter_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    market, event, series, semantic = _bundle("jul")
    changes: dict[str, Any] = {
        "comparator": semantic.specification.comparator,
        "threshold_value": semantic.specification.threshold_value,
        "occurrence_time": semantic.specification.occurrence_time,
        "rules_version_id": semantic.specification.rules_version_id,
        "market_rules_hash": semantic.specification.market_rules_hash,
        "semantic_hash": "forged",
    }
    if field == "threshold_value":
        assert semantic.specification.threshold_value is not None
        changes[field] = semantic.specification.threshold_value + 1
    elif field == "occurrence_time":
        changes[field] = datetime(2025, 6, 1, tzinfo=UTC)
    elif field == "comparator":
        changes[field] = type(semantic.specification.comparator).GTE
    else:
        changes[field] = "forged"
    forged = replace(semantic.specification, **changes)
    with pytest.raises(CPISettlementReconciliationError):
        CPIHistoricalSemanticEvidence(
            market=market, event=event, series=series, specification=forged, _capability=None
        )


def test_result_value_contradiction_and_nonfinal_are_rejected() -> None:
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    object.__setattr__(
        acquisition, "raw_response", acquisition.raw_response.replace(b'"yes"', b'"no"')
    )
    with pytest.raises(CPISettlementReconciliationError):
        KalshiFinalizedEvidence.from_acquisition(acquisition)


def test_safety_flags_are_fixed() -> None:
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    object.__setattr__(acquisition, "research_only", False)
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(acquisition)
