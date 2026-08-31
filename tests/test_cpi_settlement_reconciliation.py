from __future__ import annotations

import hashlib
import http.client
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_manual_acquisition as manual
import services.forecasting.cpi_settlement_reconciliation as reconciliation
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
    terms = load_frozen_kalshi_acquisition("contract-terms")
    return market, event, series, build_historical_semantic_evidence(market, event, series, terms)


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
            contract_terms=semantic.contract_terms,
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
            market=market,
            event=event,
            series=series,
            contract_terms=semantic.contract_terms,
            specification=forged,
            _capability=None,
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


def test_official_terms_are_durable_and_content_addressed() -> None:
    terms = load_frozen_kalshi_acquisition("contract-terms")
    assert terms.raw_artifact_hash == reconciliation.CONTRACT_TERMS_SHA256
    assert terms.request_url == "https://assets.kalshi.com/contract_terms/CPI.pdf"
    object.__setattr__(terms, "raw_response", terms.raw_response + b"x")
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(terms)


def test_terms_url_and_hash_mutations_fail() -> None:
    terms = load_frozen_kalshi_acquisition("contract-terms")
    object.__setattr__(terms, "request_url", "https://evil.example/CPI.pdf")
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(terms)

    terms = load_frozen_kalshi_acquisition("contract-terms")
    object.__setattr__(terms, "raw_artifact_hash", "0" * 64)
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(terms)


def test_missing_historical_terms_binding_cannot_be_filled_by_p7_constants() -> None:
    market, event, series, semantic = _bundle("jul")
    del semantic
    forged_series = series
    raw = forged_series.raw_response.replace(
        b"https://assets.kalshi.com/contract_terms/CPI.pdf", b"https://evil.example/CPI.pdf"
    )
    object.__setattr__(forged_series, "raw_response", raw)
    object.__setattr__(forged_series, "raw_artifact_hash", hashlib.sha256(raw).hexdigest())
    object.__setattr__(forged_series, "fixture_id", None)
    with pytest.raises(CPISettlementReconciliationError):
        build_historical_semantic_evidence(
            market, event, forged_series, load_frozen_kalshi_acquisition("contract-terms")
        )


def test_historical_market_rules_are_required_for_semantic_authority() -> None:
    market, event, series, _semantic = _bundle("jul")
    forged_market = market
    raw = forged_market.raw_response.replace(b"single-decimal", b"whole-number")
    object.__setattr__(forged_market, "raw_response", raw)
    object.__setattr__(forged_market, "raw_artifact_hash", hashlib.sha256(raw).hexdigest())
    object.__setattr__(forged_market, "fixture_id", None)
    with pytest.raises(CPISettlementReconciliationError):
        build_historical_semantic_evidence(
            forged_market, event, series, load_frozen_kalshi_acquisition("contract-terms")
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("geographic_scope", "different geography"),
        ("basket", "core items"),
        ("measured_event_or_value", "different measurement"),
        ("rounding_rules", "whole number"),
        ("revision_rules", "revised"),
        ("correction_rules", "uncertain"),
        ("contract_terms_url", "https://evil.example/CPI.pdf"),
        ("contract_terms_sha256", "0" * 64),
        ("settlement_authority_url", "https://evil.example/bls"),
        ("payout_model", "non-binary"),
    ],
)
def test_every_normalized_policy_field_is_content_addressed(field: str, value: str) -> None:
    policy = replace(reconciliation.KXCPI_SEMANTIC_POLICY, **cast(Any, {field: value}))
    assert reconciliation._semantic_policy_identity(policy) != (
        reconciliation.KXCPI_SEMANTIC_POLICY_IDENTITY
    )


class _FakeHTTPResponse:
    def __init__(self, body: bytes, length: int | None) -> None:
        self.body = body
        self.length = length
        self.status = 200
        self.read_calls = 0

    def read(self, limit: int) -> bytes:
        self.read_calls += 1
        return self.body[:limit]


@pytest.mark.parametrize("declared,body,raises", [(2, b"{}", False), (3, b"{}", True)])
def test_definite_http_length_is_checked(declared: int, body: bytes, raises: bool) -> None:
    response = _FakeHTTPResponse(body, declared)
    if raises:
        with pytest.raises(CPISettlementReconciliationError):
            reconciliation._read_complete_response(response)
    else:
        assert reconciliation._read_complete_response(response) == body


def test_declared_oversize_rejects_before_read() -> None:
    response = _FakeHTTPResponse(b"{}", reconciliation.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(CPISettlementReconciliationError):
        reconciliation._read_complete_response(response)
    assert response.read_calls == 0


def test_incomplete_read_fails_closed() -> None:
    class Incomplete(_FakeHTTPResponse):
        def read(self, limit: int) -> bytes:
            del limit
            raise http.client.IncompleteRead(b"{}", 4)

    with pytest.raises(CPISettlementReconciliationError):
        reconciliation._read_complete_response(Incomplete(b"", None))


def test_close_delimited_response_is_explicitly_supported() -> None:
    assert reconciliation._read_complete_response(_FakeHTTPResponse(b"{}", None)) == b"{}"


def test_valid_json_prefix_with_incomplete_definite_response_never_issues_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"ticker":"KXCPI-25JUL-T0.1","status":"finalized","result":"yes"}'

    class Connection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def request(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def getresponse(self) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(body, len(body) + 10)

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    with pytest.raises(CPISettlementReconciliationError):
        reconciliation.acquire_kalshi_historical_get(
            "https://external-api.kalshi.com/trade-api/v2/historical/markets/KXCPI-25JUL-T0.1",
            reconciliation.KalshiEndpointRole.HISTORICAL_MARKET,
            expected_ticker="KXCPI-25JUL-T0.1",
            expected_event_ticker="KXCPI-25JUL",
        )
