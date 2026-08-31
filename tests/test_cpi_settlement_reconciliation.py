from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_manual_acquisition as manual
from services.contract_intelligence.specification import (
    Comparator,
    ContractSpecification,
    ContractSpecificationParser,
    SemanticsInputBundle,
)
from services.forecasting.cpi_initial_release_value import (
    CPIInitialReleaseObservation,
    issue_cpi_initial_release_observation,
)
from services.forecasting.cpi_settlement_reconciliation import (
    CPISettlementReconciliationError,
    ExpectedBinaryResult,
    KalshiFinalizedEvidence,
    expected_binary_result,
    reconcile_cpi_settlement,
    validate_exchange_evidence,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _bls_artifact() -> bytes:
    return b"""<html><body><h1>CONSUMER PRICE INDEX - JULY 2025</h1>
    <p>Transmission of material in this release is embargoed until 8:30 a.m. (ET)
    Tuesday, August 12, 2025</p>
    <p>The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.2 percent
    on a seasonally adjusted basis in July, the U.S. Bureau of Labor Statistics reported today.</p>
    <p>Table A. Percent changes in CPI for All Urban Consumers (CPI-U): U.S. city average</p>
    <table><tr><th>Seasonally adjusted changes from preceding month</th></tr>
    <tr><th>Jun. 2025</th><th>Jul. 2025</th><th>Unadjusted 12-mos. ended Jul. 2025</th></tr>
    <tr><td>All items</td><td>0.3</td><td>0.2</td><td>2.7</td></tr></table></body></html>"""


def _observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CPIInitialReleaseObservation:
    path = tmp_path / "cpi.htm"
    path.write_bytes(_bls_artifact())
    monkeypatch.setattr(manual, "_utc_now", lambda: NOW)
    acquisition = manual.attest_and_import_manual_bls_cpi_release(
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        path,
        operator_attestation=manual.OPERATOR_ATTESTATION,
    )
    return issue_cpi_initial_release_observation(
        issuer.issue_manual_acquisition_bound_cpi_evidence(acquisition)
    )


def _spec() -> ContractSpecification:
    market = {
        "ticker": "KXCPI-25JUL-20",
        "event_ticker": "KXCPI-25JUL",
        "title": (
            "Will CPI-U U.S. city average all items seasonally adjusted change from "
            "preceding month be at least 0.2?"
        ),
        "yes_sub_title": "CPI is at least 0.2",
        "no_sub_title": "CPI is below 0.2",
        "rules_primary": (
            "YES if CPI-U U.S. city average all items seasonally adjusted change from "
            "preceding month is at least 0.2."
        ),
        "rules_secondary": "The initial release is rounded to one decimal.",
        "floor_strike": "0.2",
        "threshold_unit": "percent",
        "rules_version_id": "kalshi-kxcpi-july-2025-r1",
        "metadata_version_id": "m1",
        "measured_event_or_value": (
            "CPI-U U.S. city average all items seasonally adjusted change from preceding month"
        ),
        "subject_entities": ["CPI-U"],
        "geographic_scope": "U.S. city average",
        "rounding_rules": "one decimal initial release",
        "revision_rules": "authoritative finalized exchange result",
        "correction_rules": "authoritative latest final explicitly required",
        "timezone": "UTC",
        "occurrence_datetime": "2025-07-01T00:00:00Z",
        "expiration_time": "2025-08-31T00:00:00Z",
    }
    layer = {
        "event_ticker": "KXCPI-25JUL",
        "series_ticker": "KXCPI",
        "timezone": "UTC",
        "settlement_sources": [{"name": "Kalshi", "url": "https://kalshi.com"}],
    }
    series = {
        "ticker": "KXCPI",
        "title": "CPI",
        "category": "CPI",
        "settlement_sources": [{"name": "Kalshi", "url": "https://kalshi.com"}],
    }
    return ContractSpecificationParser().parse(
        SemanticsInputBundle.build(market, layer, series), NOW
    )


def _exchange(
    spec: ContractSpecification,
    *,
    result: str = "YES",
    state: str = "FINALIZED",
    value: str = "1",
    **changes: Any,
) -> KalshiFinalizedEvidence:
    payload = {
        "determination_id": "det-1",
        "market_ticker": spec.market_ticker,
        "event_ticker": spec.event_ticker,
        "series_ticker": spec.series_ticker,
        "rules_version_id": spec.rules_version_id,
        "market_rules_hash": spec.market_rules_hash,
        "semantic_spec_id": spec.semantic_hash,
        "source_identity": "public-kalshi-api-v2",
        "state": state,
        "result": result,
        "settlement_value_dollars": value,
        "determined_at": "2025-08-12T13:00:00Z",
        "finalized_at": "2025-08-12T13:01:00Z",
        "reference_year": 2025,
        "reference_month": 7,
    }
    payload.update(changes)
    return KalshiFinalizedEvidence.from_raw_response(
        json.dumps(payload, sort_keys=True).encode(),
        source_identity="public-kalshi-api-v2",
        acquired_at=NOW,
    )


def _historical_market() -> bytes:
    return json.dumps(
        {
            "market": {
                "ticker": "KXCPI-25JUL-T0.1",
                "event_ticker": "KXCPI-25JUL",
                "status": "finalized",
                "result": "yes",
                "settlement_value_dollars": "1.0000",
                "settlement_ts": "2025-08-12T13:09:49.950641Z",
                "rules_primary": (
                    "If the Consumer Price Index (CPI) increases by more than 0.1% "
                    "(single-decimal) in July 2025, then the market resolves to Yes."
                ),
                "rules_secondary": (
                    "The Expiration Value is the single-decimal value published at "
                    "the Source Agency."
                ),
            }
        },
        sort_keys=True,
    ).encode()


def test_public_historical_market_shape_is_raw_bound() -> None:
    evidence = KalshiFinalizedEvidence.from_raw_response(
        _historical_market(),
        source_identity="external-api.kalshi.com/trade-api/v2",
        acquired_at=NOW,
    )
    assert evidence.determination.market_ticker == "KXCPI-25JUL-T0.1"
    assert evidence.determination.state.value == "FINALIZED"
    assert evidence.determination.result == "YES"
    assert evidence.determination.exchange_at.isoformat() == "2025-08-12T13:09:49.950641+00:00"
    validate_exchange_evidence(evidence)


def test_public_market_status_is_not_inferred_from_result() -> None:
    raw = json.loads(_historical_market())
    raw["market"]["status"] = "closed"
    with pytest.raises(CPISettlementReconciliationError):
        KalshiFinalizedEvidence.from_raw_response(
            json.dumps(raw).encode(),
            source_identity="external-api.kalshi.com/trade-api/v2",
            acquired_at=NOW,
        )


def test_exact_match_is_eligible_and_transitively_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    record = reconcile_cpi_settlement(observation, spec, _exchange(spec))
    assert record.reconciliation_status.value == "MATCHED"
    assert record.eligible_training_label is True
    assert record.source_observation_id == observation.observation_id


def test_mismatch_is_first_class(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    record = reconcile_cpi_settlement(observation, spec, _exchange(spec, result="NO", value="0"))
    assert record.reconciliation_status.value == "MISMATCH"
    assert record.eligible_training_label is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("market_ticker", "WRONG"),
        ("event_ticker", "WRONG"),
        ("series_ticker", "KXCPICORE"),
        ("rules_version_id", "wrong"),
        ("market_rules_hash", "wrong"),
        ("semantic_spec_id", "wrong"),
    ],
)
def test_exact_identity_bindings_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    with pytest.raises(CPISettlementReconciliationError):
        reconcile_cpi_settlement(observation, spec, _exchange(spec, **{field: value}))


@pytest.mark.parametrize(
    "comparator,expected",
    [
        (Comparator.GT, ExpectedBinaryResult.NO),
        (Comparator.GTE, ExpectedBinaryResult.YES),
        (Comparator.LT, ExpectedBinaryResult.NO),
        (Comparator.LTE, ExpectedBinaryResult.YES),
        (Comparator.EQ, ExpectedBinaryResult.YES),
    ],
)
def test_supported_comparators_are_decimal_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    comparator: Comparator,
    expected: ExpectedBinaryResult,
) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    spec = replace(spec, comparator=comparator)
    assert expected_binary_result(observation, spec) is expected


@pytest.mark.parametrize(
    "changes",
    [
        {"finalized_at": None},
        {"state": "DISPUTED"},
        {"result": "YES", "settlement_value_dollars": "0"},
        {"result": "NO", "settlement_value_dollars": "1"},
        {"reference_month": 6},
    ],
)
def test_nonfinal_disputed_contradictory_or_wrong_period_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    with pytest.raises(CPISettlementReconciliationError):
        reconcile_cpi_settlement(observation, spec, _exchange(spec, **changes))


def test_forged_raw_hash_and_mutated_determination_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation, spec = _observation(tmp_path, monkeypatch), _spec()
    evidence = _exchange(spec)
    forged = replace(evidence, raw_artifact_hash="0" * 64)
    with pytest.raises(CPISettlementReconciliationError):
        reconcile_cpi_settlement(observation, spec, forged)
    mutated = replace(evidence, determination=replace(evidence.determination, result="NO"))
    with pytest.raises(CPISettlementReconciliationError):
        reconcile_cpi_settlement(observation, spec, mutated)
