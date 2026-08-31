from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_initial_release_value as value
import services.forecasting.cpi_manual_acquisition as manual
import services.forecasting.cpi_pit_availability as pit

LOCATOR = "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
IMPORT_TIME = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)


def artifact(
    *,
    narrative: str = "0.2",
    earlier: str = "0.3",
    table: str = "0.2",
    trailing: str = "2.7",
    month: str = "Jul. 2025",
    title: str = (
        "Table A. Percent changes in CPI for All Urban Consumers (CPI-U): U.S. city average"
    ),
    row_label: str = "All items",
) -> bytes:
    return f"""<html><body>
    <h1>CONSUMER PRICE INDEX - JULY 2025</h1>
    <p>Transmission of material in this release is embargoed until 8:30 a.m. (ET)
    Tuesday, August 12, 2025</p>
    <p>The Consumer Price Index for All Urban Consumers (CPI-U) increased {narrative}
    percent on a seasonally adjusted basis in July, the U.S. Bureau of Labor Statistics
    reported today.</p>
    <p>{title}</p><table><tr><th>Seasonally adjusted changes from preceding month</th></tr>
    <tr><th>Jun. 2025</th><th>{month}</th><th>Unadjusted 12-mos. ended Jul. 2025</th></tr>
    <tr><td>{row_label}</td><td>{earlier}</td><td>{table}</td><td>{trailing}</td></tr></table>
    </body></html>""".encode()


def test_dual_representation_returns_exact_decimal() -> None:
    parsed = value.parse_cpi_initial_release_value(artifact())
    assert parsed.value == Decimal("0.2")
    assert (parsed.reference_year, parsed.reference_month) == (2025, 7)


def test_earlier_bls_placeholder_passes_but_current_placeholder_does_not() -> None:
    assert value.parse_cpi_initial_release_value(artifact(earlier="-")).value == Decimal("0.2")
    with pytest.raises(value.CPIInitialReleaseValueError):
        value.parse_cpi_initial_release_value(artifact(table="-"))


@pytest.mark.parametrize("current", ["", "0.20", "malformed"])
def test_current_value_must_be_present_and_exactly_one_decimal(current: str) -> None:
    with pytest.raises(value.CPIInitialReleaseValueError):
        value.parse_cpi_initial_release_value(artifact(table=current))


def test_arbitrary_malformed_earlier_cell_is_not_a_placeholder() -> None:
    with pytest.raises(value.CPIInitialReleaseValueError):
        value.parse_cpi_initial_release_value(artifact(earlier="0.20"))


def test_trailing_12_month_value_is_not_selected_as_current() -> None:
    assert value.parse_cpi_initial_release_value(artifact(trailing="9.9")).value == Decimal("0.2")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"table": "0.3"},  # disagreement
        {"month": "Jun. 2025"},  # wrong current month
        {
            "title": (
                "Table A. Percent changes in CPI for Urban Wage Earners and Clerical Workers "
                "(CPI-W): U.S. city average"
            )
        },
        {"row_label": "All items less food and energy"},  # core substitution
        {"narrative": "0.20"},  # non-canonical precision
    ],
)
def test_domain_or_representation_substitution_fails_closed(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        value.parse_cpi_initial_release_value(artifact(**kwargs))


def test_missing_narrative_or_table_fails_closed() -> None:
    narrative_only = artifact().replace(b"<p>Table A.", b"<p>Not a reviewed table.")
    table_only = artifact().replace(b"The Consumer Price Index for All Urban Consumers", b"The CPI")
    for body in (narrative_only, table_only):
        with pytest.raises(value.CPIInitialReleaseValueError):
            value.parse_cpi_initial_release_value(body)


def test_duplicate_table_and_malformed_values_fail_closed() -> None:
    duplicate = artifact() + artifact()
    malformed = artifact(table="not-a-number")
    for body in (duplicate, malformed):
        with pytest.raises(value.CPIInitialReleaseValueError):
            value.parse_cpi_initial_release_value(body)


def _manual_issuance(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "release.htm"
    path.write_bytes(artifact())
    monkeypatch.setattr(manual, "_utc_now", lambda: IMPORT_TIME)
    evidence = manual.attest_and_import_manual_bls_cpi_release(
        LOCATOR, path, operator_attestation=manual.OPERATOR_ATTESTATION
    )
    return issuer.issue_manual_acquisition_bound_cpi_evidence(evidence)


def test_issuer_derives_value_and_preserves_manual_provenance(tmp_path, monkeypatch) -> None:
    issuance = _manual_issuance(tmp_path, monkeypatch)
    observation = value.issue_cpi_initial_release_observation(issuance)
    value.validate_cpi_initial_release_observation(observation)
    assert observation.value == Decimal("0.2")
    assert observation.acquisition_evidence_id == issuance.acquisition_evidence.evidence_id
    assert observation.acquisition_mode == manual.ACQUISITION_MODE
    assert observation.raw_body_sha256 == issuance.acquisition_evidence.raw_body_sha256
    assert observation.research_only is True
    assert observation.production_influence == Decimal("0")


def test_value_cannot_be_caller_supplied_or_reconstructed(tmp_path, monkeypatch) -> None:
    observation = value.issue_cpi_initial_release_observation(
        _manual_issuance(tmp_path, monkeypatch)
    )
    with pytest.raises(TypeError):
        value.CPIInitialReleaseObservation(value=Decimal("9.9"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        replace(observation, value=Decimal("9.9"))
    original = observation.value
    try:
        object.__setattr__(observation, "value", Decimal("9.9"))
        with pytest.raises(value.CPIInitialReleaseValueError):
            value.validate_cpi_initial_release_observation(observation)
    finally:
        object.__setattr__(observation, "value", original)
    value.validate_cpi_initial_release_observation(observation)


def test_safety_flags_and_bound_identity_are_revalidated(tmp_path, monkeypatch) -> None:
    observation = value.issue_cpi_initial_release_observation(
        _manual_issuance(tmp_path, monkeypatch)
    )
    original = observation.production_influence
    try:
        object.__setattr__(observation, "production_influence", Decimal("1"))
        with pytest.raises(value.CPIInitialReleaseValueError):
            value.validate_cpi_initial_release_observation(observation)
    finally:
        object.__setattr__(observation, "production_influence", original)
    value.validate_cpi_initial_release_observation(observation)


def test_wrapper_timing_identity_forgery_fails_closed(tmp_path, monkeypatch) -> None:
    valid = _manual_issuance(tmp_path, monkeypatch)
    forged = replace(valid, timing_evidence_identity="attacker-chosen-id")
    with pytest.raises(ValueError):
        value.issue_cpi_initial_release_observation(forged)


def test_wrapper_components_must_remain_the_canonical_transitive_chain(
    tmp_path, monkeypatch
) -> None:
    valid = _manual_issuance(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        value.issue_cpi_initial_release_observation(
            replace(
                valid, parsed_timing=replace(valid.parsed_timing, observation_identity="forged")
            )
        )
    with pytest.raises(ValueError):
        value.issue_cpi_initial_release_observation(
            replace(
                valid,
                availability=pit.build_unknown_cpi_availability(actual_bot_ingest_at=IMPORT_TIME),
            )
        )
    with pytest.raises(ValueError):
        original = valid.publication_evidence.timing_evidence_identity
        try:
            object.__setattr__(valid.publication_evidence, "timing_evidence_identity", "other")
            value.issue_cpi_initial_release_observation(valid)
        finally:
            object.__setattr__(valid.publication_evidence, "timing_evidence_identity", original)
