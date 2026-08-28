from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import services.forecasting.cpi_pit_availability as pit
import services.forecasting.cpi_publication_timing as timing
from services.forecasting.cpi_source_authority import (
    CPISourceAuthorityError,
    CPISourceProfile,
)

PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
NY = ZoneInfo("America/New_York")


def fixture_html(statement: str, *, extra: str = "") -> bytes:
    return (
        "<!doctype html><html><head><title>TEST FIXTURE ONLY</title></head>"
        f"<body><main><pre>{statement}</pre>{extra}</main></body></html>"
    ).encode("ascii")


def artifact(
    statement: str,
    *,
    locator: str,
    extra: str = "",
) -> pit.CPIHistoricalReleaseArtifact:
    return pit.CPIHistoricalReleaseArtifact(
        profile=PROFILE,
        source_locator=locator,
        actual_bot_ingest_at=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
        raw_artifact=fixture_html(statement, extra=extra),
    )


def test_positive_explicit_est_statement() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (EST) Tuesday, January 14, 2020",
        locator="https://www.bls.gov/news.release/archives/cpi_01142020.htm",
    )
    evidence = timing.issue_cpi_publication_evidence(source)
    assert evidence.source_publish_at == datetime(2020, 1, 14, 8, 30, tzinfo=NY)
    assert evidence.source_publish_at.utcoffset() == timedelta(hours=-5)


def test_positive_explicit_edt_statement_and_old_preformatted_shape() -> None:
    source = artifact(
        "TRANSMISSION OF MATERIAL\nIN THIS RELEASE IS EMBARGOED\n"
        "UNTIL 8:30 A.M. (EDT) Friday, August 11, 2017",
        locator="https://www.bls.gov/news.release/archives/cpi_08112017.htm",
    )
    evidence = timing.issue_cpi_publication_evidence(source)
    assert evidence.source_publish_at == datetime(2017, 8, 11, 8, 30, tzinfo=NY)
    assert evidence.source_publish_at.utcoffset() == timedelta(hours=-4)


def test_positive_generic_et_statement() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    evidence = timing.issue_cpi_publication_evidence(source)
    assert evidence.source_publish_at == datetime(2025, 8, 12, 8, 30, tzinfo=NY)
    assert evidence.source_publish_at.utcoffset() == timedelta(hours=-4)


def test_output_uses_exact_america_new_york_zoneinfo() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) August 12, 2020",
        locator="https://www.bls.gov/news.release/archives/cpi_08122020.htm",
    )
    published = timing.issue_cpi_publication_evidence(source).source_publish_at
    assert type(published.tzinfo) is ZoneInfo
    assert published.tzinfo.key == "America/New_York"


def test_est_on_edt_date_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (EST) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_edt_on_est_date_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (EDT) Tuesday, January 14, 2020",
        locator="https://www.bls.gov/news.release/archives/cpi_01142020.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_public_api_has_no_caller_timestamp_or_derived_timing_parameters() -> None:
    signature = inspect.signature(timing.issue_cpi_publication_evidence)
    assert tuple(signature.parameters) == ("artifact",)
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    forbidden = (
        {"source_publish_at": datetime.now(UTC)},
        {"release_time": "08:30"},
        {"release_date": "2025-08-12"},
        {"timezone": "ET"},
        {"assumed_latency": timedelta(0)},
        {"replay_available_at": datetime.now(UTC)},
    )
    for kwargs in forbidden:
        with pytest.raises(TypeError):
            timing.issue_cpi_publication_evidence(source, **kwargs)  # type: ignore[call-arg]


def test_date_only_statement_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_time_only_statement_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until 8:30 a.m. (ET)",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_missing_timezone_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_conflicting_timing_statements_are_rejected() -> None:
    first = (
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025"
    )
    second = (
        "Transmission of material in this release is embargoed until "
        "9:00 a.m. (ET) Tuesday, August 12, 2025"
    )
    source = artifact(
        first,
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        extra=f"<p>{second}</p>",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_malformed_date_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Sunday, February 30, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_02282025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_malformed_time_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "13:99 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_impossible_new_york_local_time_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "2:30 a.m. (ET) Sunday, March 9, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_03092025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_archive_filename_cannot_create_timing() -> None:
    source = artifact(
        "CONSUMER PRICE INDEX - JULY 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_artifact_statement_locator_date_conflict_is_rejected() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08132025.htm",
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_calendar_current_api_pdf_and_txt_sources_are_rejected() -> None:
    statement = (
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025"
    )
    locators = (
        "https://www.bls.gov/schedule/news_release/cpi.htm",
        "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
        "https://www.bls.gov/news.release/archives/cpi_08122025.pdf",
        "https://www.bls.gov/news.release/archives/cpi_08122025.txt",
        "https://www.bls.gov/news.release/archives/ppi_08122025.htm",
    )
    for locator in locators:
        with pytest.raises(CPISourceAuthorityError):
            artifact(statement, locator=locator)


class EqualProfile(StrEnum):
    SAME = PROFILE.value


def test_wrong_or_equal_valued_noncanonical_p1_profile_is_rejected() -> None:
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.CPIHistoricalReleaseArtifact(
            profile=EqualProfile.SAME,  # type: ignore[arg-type]
            source_locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
            actual_bot_ingest_at=datetime.now(UTC),
            raw_artifact=fixture_html(
                "Transmission of material in this release is embargoed until "
                "8:30 a.m. (ET) Tuesday, August 12, 2025"
            ),
        )


def test_changed_raw_bytes_or_hash_are_rejected_before_parsing() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    original = source.raw_artifact
    try:
        object.__setattr__(source, "raw_artifact", original + b" ")
        with pytest.raises(pit.CPIPITAvailabilityError):
            timing.issue_cpi_publication_evidence(source)
    finally:
        object.__setattr__(source, "raw_artifact", original)
    pit.validate_cpi_release_artifact(source)


def test_current_or_revised_vintage_cannot_masquerade_as_initial_release() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    original = source.vintage
    try:
        object.__setattr__(source, "vintage", "REVISED")
        with pytest.raises(pit.CPIPITAvailabilityError):
            timing.issue_cpi_publication_evidence(source)
    finally:
        object.__setattr__(source, "vintage", original)
    pit.validate_cpi_release_artifact(source)


def test_caller_cannot_select_timing_evidence_identity() -> None:
    signature = inspect.signature(timing.issue_cpi_publication_evidence)
    assert "timing_evidence_identity" not in signature.parameters
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    with pytest.raises(TypeError):
        timing.issue_cpi_publication_evidence(  # type: ignore[call-arg]
            source,
            timing_evidence_identity="caller",
        )


def test_timing_identity_changes_when_authoritative_statement_changes() -> None:
    first = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    second = artifact(
        "Transmission of material in this release is embargoed until "
        "9:00 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    first_evidence = timing.issue_cpi_publication_evidence(first)
    second_evidence = timing.issue_cpi_publication_evidence(second)
    assert first_evidence.timing_evidence_identity != second_evidence.timing_evidence_identity


def test_same_artifact_is_deterministic_for_semantics_and_identity() -> None:
    source = artifact(
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025",
        locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    first = timing.issue_cpi_publication_evidence(source)
    second = timing.issue_cpi_publication_evidence(source)
    assert first.source_publish_at == second.source_publish_at
    assert first.timing_semantics is second.timing_semantics
    assert first.timing_evidence_identity == second.timing_evidence_identity
    assert first.evidence_id == second.evidence_id


def test_plain_text_detached_from_reviewed_html_shape_is_rejected() -> None:
    statement = (
        "Transmission of material in this release is embargoed until "
        "8:30 a.m. (ET) Tuesday, August 12, 2025"
    )
    source = pit.CPIHistoricalReleaseArtifact(
        profile=PROFILE,
        source_locator="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        actual_bot_ingest_at=datetime(2025, 8, 13, 4, 0, tzinfo=UTC),
        raw_artifact=statement.encode("ascii"),
    )
    with pytest.raises(timing.CPIPublicationTimingError):
        timing.issue_cpi_publication_evidence(source)


def test_only_p3_is_allowed_to_consume_the_p2_private_seam() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    p2 = repo_root / "services/forecasting/cpi_pit_availability.py"
    p3 = repo_root / "services/forecasting/cpi_publication_timing.py"
    forbidden = (
        "_issue_actual_cpi_publication_evidence",
        "_PUBLICATION_AUTHORITY_CAPABILITY",
    )
    p3_source = p3.read_text(encoding="utf-8")
    assert all(name in p3_source for name in forbidden)
    violations: list[str] = []
    for path in sorted((repo_root / "services").rglob("*.py")):
        if path in {p2, p3}:
            continue
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in source:
                violations.append(f"{path.relative_to(repo_root)}:{name}")
    assert violations == []


def test_p3_has_no_io_acquisition_gate_model_economics_or_execution_dependencies() -> None:
    source = inspect.getsource(timing)
    forbidden = (
        "requests",
        "httpx",
        "urllib.request",
        "urlopen",
        "ArchiveManifest",
        "DatasetManifest",
        "SettlementLabel",
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
        "modelability",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
        "production_execution",
        "credential",
        "account",
        "order",
        "signer",
    )
    assert all(value not in source for value in forbidden)
