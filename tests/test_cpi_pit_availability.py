from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import services.forecasting.cpi_pit_availability as pit
from services.forecasting.cpi_source_authority import (
    POLICY_IDENTITY as CANONICAL_P1_POLICY_IDENTITY,
)
from services.forecasting.cpi_source_authority import (
    CPISourceAuthorityError,
    CPISourceProfile,
)
from services.historical_replay.domain import AvailabilityBasis, AvailabilityQuality

PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
DOCUMENT_URL = "https://www.bls.gov/news.release/archives/cpi_08122026.htm"
CALENDAR_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0"
NY = ZoneInfo("America/New_York")
ACTUAL = pit.CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO
SCHEDULED = pit.CPIPublicationTimingSemantics.SCHEDULED_RELEASE


def artifact(
    *,
    raw: bytes = b"archived-cpi-release",
    ingest_at: datetime = datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
    locator: str = DOCUMENT_URL,
    profile: CPISourceProfile = PROFILE,
) -> pit.CPIHistoricalReleaseArtifact:
    return pit.CPIHistoricalReleaseArtifact(
        profile=profile,
        source_locator=locator,
        actual_bot_ingest_at=ingest_at,
        raw_artifact=raw,
    )


def issue(
    source: pit.CPIHistoricalReleaseArtifact,
    *,
    published_at: datetime = datetime(2026, 8, 12, 8, 30, tzinfo=NY),
    semantics: pit.CPIPublicationTimingSemantics = ACTUAL,
    evidence_identity: str = "fixture:exact-actual-release-instant",
) -> pit.CPIActualPublicationEvidence:
    return pit._issue_actual_cpi_publication_evidence(
        artifact=source,
        source_publish_at=published_at,
        timing_semantics=semantics,
        timing_evidence_identity=evidence_identity,
        _capability=pit._PUBLICATION_AUTHORITY_CAPABILITY,
    )


def test_positive_path_uses_exact_canonical_replay_basis_quality_and_p1_policy() -> None:
    source = artifact()
    proof = issue(source)
    availability = pit.build_cpi_reconstructed_availability(
        source,
        publication_evidence=proof,
    )
    assert pit.P1_POLICY_IDENTITY == CANONICAL_P1_POLICY_IDENTITY
    assert source.p1_policy_identity == pit.P1_POLICY_IDENTITY
    assert proof.p1_policy_identity == pit.P1_POLICY_IDENTITY
    assert availability.basis is AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE
    assert availability.quality is AvailabilityQuality.CONSERVATIVE_ASSUMPTION
    assert availability.source_publish_at == proof.source_publish_at
    assert availability.replay_available_at == (
        datetime(2026, 8, 12, 23, 59, 59, 999999, tzinfo=NY)
    )
    assert availability.replay_available_at == (
        availability.source_publish_at + availability.assumed_latency
    )


def test_positive_builder_has_no_caller_timestamp_or_latency_authority() -> None:
    signature = inspect.signature(pit.build_cpi_reconstructed_availability)
    assert tuple(signature.parameters) == ("artifact", "publication_evidence")
    source = artifact()
    proof = issue(source, evidence_identity="fixture:no-caller-times")
    for kwargs in (
        {"source_publish_at": datetime.now(UTC)},
        {"replay_available_at": datetime.now(UTC)},
        {"assumed_latency": timedelta(0)},
        {"actual_bot_ingest_at": datetime.now(UTC)},
    ):
        with pytest.raises(TypeError):
            pit.build_cpi_reconstructed_availability(
                source,
                publication_evidence=proof,
                **kwargs,  # type: ignore[arg-type]
            )


def test_ordinary_construction_cannot_mint_positive_publication_evidence() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.CPIActualPublicationEvidence(
            artifact=source,
            source_publish_at=datetime(2026, 8, 12, 8, 30, tzinfo=NY),
            timing_semantics=ACTUAL,
            timing_evidence_identity="caller-authored",
        )


def test_artifact_constructor_does_not_accept_caller_authority_or_vintage_fields() -> None:
    forbidden = (
        {"source_role": "release-calendar"},
        {"vintage": "REVISED"},
        {"p1_authority_identity": "caller"},
        {"p1_policy_identity": "caller"},
        {"artifact_id": "caller"},
        {"content_hash": "caller"},
    )
    for extra in forbidden:
        with pytest.raises(TypeError):
            pit.CPIHistoricalReleaseArtifact(
                profile=PROFILE,
                source_locator=DOCUMENT_URL,
                actual_bot_ingest_at=datetime.now(UTC),
                raw_artifact=b"x",
                **extra,  # type: ignore[arg-type]
            )


def test_date_only_evidence_cannot_enter_positive_path_and_unknown_is_available() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        issue(
            source,
            published_at=date(2026, 8, 12),  # type: ignore[arg-type]
            evidence_identity="fixture:date-only",
        )
    unknown = pit.build_unknown_cpi_availability(actual_bot_ingest_at=source.actual_bot_ingest_at)
    assert unknown.basis is AvailabilityBasis.UNKNOWN
    assert unknown.quality is AvailabilityQuality.DESCRIPTIVE_ONLY
    assert unknown.source_publish_at is None
    assert unknown.replay_available_at is None
    assert unknown.assumed_latency is None


def test_exact_release_instant_requires_timezone_aware_datetime() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        issue(
            source,
            published_at=datetime(2026, 8, 12, 8, 30),
            evidence_identity="fixture:naive",
        )


def test_fixed_offset_publication_time_cannot_substitute_for_new_york_zoneinfo() -> None:
    source = artifact()
    fixed_edt = timezone(timedelta(hours=-4))
    with pytest.raises(pit.CPIPITAvailabilityError):
        issue(
            source,
            published_at=datetime(2026, 8, 12, 8, 30, tzinfo=fixed_edt),
            evidence_identity="fixture:fixed-offset",
        )


def test_wrong_zoneinfo_key_cannot_substitute_for_new_york_semantics() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        issue(
            source,
            published_at=datetime(
                2026,
                8,
                12,
                7,
                30,
                tzinfo=ZoneInfo("America/Chicago"),
            ),
            evidence_identity="fixture:wrong-zone",
        )


def test_conservative_boundary_is_final_local_instant_in_est() -> None:
    published = datetime(2026, 1, 13, 8, 30, tzinfo=NY)
    boundary = pit.conservative_replay_boundary(published)
    assert boundary == datetime(2026, 1, 13, 23, 59, 59, 999999, tzinfo=NY)
    assert boundary.utcoffset() == timedelta(hours=-5)


def test_conservative_boundary_is_final_local_instant_in_edt() -> None:
    published = datetime(2026, 8, 12, 8, 30, tzinfo=NY)
    boundary = pit.conservative_replay_boundary(published)
    assert boundary == datetime(2026, 8, 12, 23, 59, 59, 999999, tzinfo=NY)
    assert boundary.utcoffset() == timedelta(hours=-4)


def test_assumed_latency_is_deterministically_derived() -> None:
    published = datetime(2026, 8, 12, 8, 30, tzinfo=NY)
    source = artifact()
    proof = issue(
        source,
        published_at=published,
        evidence_identity="fixture:derived-latency",
    )
    availability = pit.build_cpi_reconstructed_availability(
        source,
        publication_evidence=proof,
    )
    expected = pit.conservative_replay_boundary(published) - published
    assert availability.assumed_latency == expected


def test_scheduled_timing_cannot_mint_actual_release_proof() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        issue(
            source,
            semantics=SCHEDULED,
            evidence_identity="fixture:scheduled-only",
        )


def test_plain_string_cannot_replace_actual_timing_strenum() -> None:
    source = artifact()
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit._issue_actual_cpi_publication_evidence(
            artifact=source,
            source_publish_at=datetime(2026, 8, 12, 8, 30, tzinfo=NY),
            timing_semantics=ACTUAL.value,  # type: ignore[arg-type]
            timing_evidence_identity="fixture:string-semantics",
            _capability=pit._PUBLICATION_AUTHORITY_CAPABILITY,
        )


def test_calendar_source_cannot_masquerade_as_actual_release_artifact() -> None:
    with pytest.raises(CPISourceAuthorityError):
        artifact(locator=CALENDAR_URL)


def test_current_bls_api_cannot_enter_positive_artifact_path() -> None:
    with pytest.raises(CPISourceAuthorityError):
        artifact(locator=API_URL)


@pytest.mark.parametrize(
    "locator",
    [
        "https://www.bls.gov/news.release/archives/cpi_08122026.pdf",
        "https://www.bls.gov/news.release/archives/cpi_08122026.txt",
    ],
)
def test_unreviewed_pdf_and_txt_cannot_enter_positive_artifact_path(locator: str) -> None:
    with pytest.raises(CPISourceAuthorityError):
        artifact(locator=locator)


def test_archive_filename_date_does_not_create_publication_time() -> None:
    source = artifact()
    assert "08122026" in source.source_locator
    with pytest.raises(TypeError):
        pit.build_cpi_reconstructed_availability(source)  # type: ignore[call-arg]


def test_acquisition_time_cannot_substitute_for_publication_evidence() -> None:
    source = artifact(ingest_at=datetime(2026, 8, 12, 8, 30, tzinfo=NY))
    with pytest.raises(TypeError):
        pit.build_cpi_reconstructed_availability(source)  # type: ignore[call-arg]


def test_actual_bot_ingest_before_conservative_boundary_fails_closed() -> None:
    published = datetime(2026, 8, 12, 8, 30, tzinfo=NY)
    boundary = pit.conservative_replay_boundary(published)
    source = artifact(ingest_at=boundary.astimezone(UTC) - timedelta(microseconds=1))
    proof = issue(
        source,
        published_at=published,
        evidence_identity="fixture:ingest-before",
    )
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.build_cpi_reconstructed_availability(
            source,
            publication_evidence=proof,
        )


def test_actual_bot_ingest_equal_to_conservative_boundary_is_allowed() -> None:
    published = datetime(2026, 8, 12, 8, 30, tzinfo=NY)
    boundary = pit.conservative_replay_boundary(published)
    source = artifact(ingest_at=boundary.astimezone(UTC))
    proof = issue(
        source,
        published_at=published,
        evidence_identity="fixture:ingest-equal",
    )
    availability = pit.build_cpi_reconstructed_availability(
        source,
        publication_evidence=proof,
    )
    assert availability.actual_bot_ingest_at == boundary
    assert availability.replay_available_at == boundary


def test_cross_artifact_publication_proof_is_rejected() -> None:
    original = artifact(raw=b"original")
    proof = issue(original, evidence_identity="fixture:original-artifact")
    changed = artifact(raw=b"changed")
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.build_cpi_reconstructed_availability(
            changed,
            publication_evidence=proof,
        )


def test_raw_artifact_hash_mutation_is_rejected_before_positive_replay() -> None:
    source = artifact()
    proof = issue(source, evidence_identity="fixture:raw-mutation")
    original = source.raw_artifact
    try:
        object.__setattr__(source, "raw_artifact", b"mutated")
        with pytest.raises(pit.CPIPITAvailabilityError):
            pit.build_cpi_reconstructed_availability(
                source,
                publication_evidence=proof,
            )
    finally:
        object.__setattr__(source, "raw_artifact", original)
    pit.validate_cpi_release_artifact(source)


def test_publication_evidence_p1_policy_identity_mutation_is_rejected() -> None:
    proof = issue(artifact(), evidence_identity="fixture:p1-policy-mutation")
    original = proof.p1_policy_identity
    try:
        object.__setattr__(proof, "p1_policy_identity", "forged")
        with pytest.raises(pit.CPIPITAvailabilityError):
            pit.validate_cpi_publication_evidence(proof)
    finally:
        object.__setattr__(proof, "p1_policy_identity", original)
    pit.validate_cpi_publication_evidence(proof)


def test_publication_evidence_p1_authority_identity_mutation_is_rejected() -> None:
    proof = issue(artifact(), evidence_identity="fixture:p1-authority-mutation")
    original = proof.p1_authority_identity
    try:
        object.__setattr__(proof, "p1_authority_identity", "forged")
        with pytest.raises(pit.CPIPITAvailabilityError):
            pit.validate_cpi_publication_evidence(proof)
    finally:
        object.__setattr__(proof, "p1_authority_identity", original)
    pit.validate_cpi_publication_evidence(proof)


def test_publication_evidence_mutate_and_public_rehash_cannot_mint_authority() -> None:
    proof = issue(artifact(), evidence_identity="fixture:mutate-rehash")
    old_locator = proof.source_locator
    old_evidence_id = proof.evidence_id
    old_content_hash = proof.content_hash
    try:
        object.__setattr__(
            proof,
            "source_locator",
            "https://www.bls.gov/news.release/archives/cpi_08132026.htm",
        )
        forged = pit._publication_digest(proof)
        object.__setattr__(proof, "evidence_id", forged)
        object.__setattr__(proof, "content_hash", forged)
        with pytest.raises(pit.CPIPITAvailabilityError):
            pit.validate_cpi_publication_evidence(proof)
    finally:
        object.__setattr__(proof, "source_locator", old_locator)
        object.__setattr__(proof, "evidence_id", old_evidence_id)
        object.__setattr__(proof, "content_hash", old_content_hash)
    pit.validate_cpi_publication_evidence(proof)


def test_dataclasses_replace_cannot_create_changed_publication_authority() -> None:
    proof = issue(artifact(), evidence_identity="fixture:replace")
    with pytest.raises((TypeError, pit.CPIPITAvailabilityError)):
        replace(proof, timing_evidence_identity="forged")


def test_direct_reconstruction_cannot_validate_as_issued_publication_authority() -> None:
    forged = object.__new__(pit.CPIActualPublicationEvidence)
    with pytest.raises((AttributeError, pit.CPIPITAvailabilityError)):
        pit.validate_cpi_publication_evidence(forged)


class EqualProfile(StrEnum):
    SAME = PROFILE.value


def test_equal_valued_noncanonical_profile_type_is_rejected() -> None:
    with pytest.raises(pit.CPIPITAvailabilityError):
        artifact(profile=EqualProfile.SAME)  # type: ignore[arg-type]


@pytest.mark.parametrize("profile", ["KXCPIYOY", "KXCPICORE", "KXCPICOREYOY"])
def test_related_cpi_domains_cannot_inherit_p2_profile(profile: str) -> None:
    with pytest.raises(pit.CPIPITAvailabilityError):
        artifact(profile=profile)  # type: ignore[arg-type]


def test_wrong_p1_locator_is_rejected() -> None:
    with pytest.raises(CPISourceAuthorityError):
        artifact(locator="https://www.bls.gov/news.release/archives/ppi_08122026.htm")


def test_publication_timing_identity_is_required_and_exact() -> None:
    source = artifact()
    for identity in ("", "  ", " proof "):
        with pytest.raises(pit.CPIPITAvailabilityError):
            issue(source, evidence_identity=identity)


def test_conflicting_or_wrong_publication_evidence_type_cannot_become_positive() -> None:
    source = artifact()
    first = issue(source, evidence_identity="fixture:first")
    second = issue(
        source,
        published_at=datetime(2026, 8, 12, 9, 0, tzinfo=NY),
        evidence_identity="fixture:second",
    )
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.build_cpi_reconstructed_availability(
            source,
            publication_evidence=(first, second),  # type: ignore[arg-type]
        )


def test_research_only_and_zero_production_influence_are_fixed() -> None:
    source = artifact()
    proof = issue(source, evidence_identity="fixture:research-only")
    assert source.research_only is True
    assert source.production_influence == Decimal("0")
    assert proof.research_only is True
    assert proof.production_influence == Decimal("0")


def test_unknown_builder_rejects_naive_actual_ingest_time() -> None:
    with pytest.raises(pit.CPIPITAvailabilityError):
        pit.build_unknown_cpi_availability(actual_bot_ingest_at=datetime(2026, 8, 12, 12, 0))


def test_private_publication_authority_seam_has_only_reviewed_p3_consumer() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    defining_module = repo_root / "services/forecasting/cpi_pit_availability.py"
    issuer_module = repo_root / "services/forecasting/cpi_publication_timing.py"
    forbidden = (
        "_issue_actual_cpi_publication_evidence",
        "_PUBLICATION_AUTHORITY_CAPABILITY",
    )
    issuer_source = issuer_module.read_text(encoding="utf-8")
    assert all(name in issuer_source for name in forbidden)
    violations: list[str] = []
    for path in sorted((repo_root / "services").rglob("*.py")):
        if path in {defining_module, issuer_module}:
            continue
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in source:
                violations.append(f"{path.relative_to(repo_root)}:{name}")
    assert violations == []


def test_module_has_no_io_acquisition_gate_model_economics_or_execution_dependencies() -> None:
    source = inspect.getsource(pit)
    forbidden = (
        "requests",
        "httpx",
        "urllib.request",
        "ArchiveManifest",
        "DatasetManifest",
        "SettlementLabel",
        "services.market_universe.empirical_researchability",
        "services.market_universe.modelability",
        "ReleaseVintage",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
        "production_execution",
        "credential",
        "signer",
    )
    assert all(value not in source for value in forbidden)


def test_public_interfaces_do_not_accept_ticker_family_title_category_or_release_target() -> None:
    kwargs: tuple[dict[str, Any], ...] = (
        {"ticker": "KXCPI"},
        {"family": "BINARY_THRESHOLD"},
        {"title": "CPI"},
        {"category": "Economics"},
        {"release_target": "CPI"},
    )
    for extra in kwargs:
        with pytest.raises(TypeError):
            pit.CPIHistoricalReleaseArtifact(
                profile=PROFILE,
                source_locator=DOCUMENT_URL,
                actual_bot_ingest_at=datetime.now(UTC),
                raw_artifact=b"x",
                **extra,  # type: ignore[arg-type]
            )
