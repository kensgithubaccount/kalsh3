from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_manual_acquisition as manual
import services.forecasting.cpi_source_acquisition as automated
import services.forecasting.cpi_pit_availability as pit
from services.forecasting.cpi_source_authority import CPISourceAuthorityError
from services.historical_replay.domain import AvailabilityBasis, AvailabilityQuality

LOCATOR = "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
IMPORTED_AT = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)


def html() -> bytes:
    return (
        "<!doctype html><html><body><p>Transmission of material in this release is "
        "embargoed until 8:30 a.m. (ET) Tuesday, August 12, 2025</p></body></html>"
    ).encode("ascii")


def import_manual(tmp_path, monkeypatch: pytest.MonkeyPatch) -> manual.CPIBLSManualAcquisitionEvidence:
    path = tmp_path / "cpi_08122025.htm"
    path.write_bytes(html())
    monkeypatch.setattr(manual, "_utc_now", lambda: IMPORTED_AT)
    return manual.attest_and_import_manual_bls_cpi_release(
        LOCATOR,
        path,
        operator_attestation=manual.OPERATOR_ATTESTATION,
    )


def test_public_manual_api_requires_file_and_explicit_attestation_not_bytes_or_time(
    tmp_path,
) -> None:
    assert tuple(inspect.signature(manual.attest_and_import_manual_bls_cpi_release).parameters) == (
        "source_locator",
        "file_path",
        "operator_attestation",
    )
    path = tmp_path / "release.htm"
    path.write_bytes(html())
    with pytest.raises(TypeError):
        manual.attest_and_import_manual_bls_cpi_release(  # type: ignore[call-arg]
            LOCATOR,
            path,
            operator_attestation=manual.OPERATOR_ATTESTATION,
            raw_body=html(),
        )
    with pytest.raises(TypeError):
        manual.attest_and_import_manual_bls_cpi_release(  # type: ignore[call-arg]
            LOCATOR,
            path,
            operator_attestation=manual.OPERATOR_ATTESTATION,
            imported_at=IMPORTED_AT,
        )
    with pytest.raises(manual.CPIManualAcquisitionError, match="attestation"):
        manual.attest_and_import_manual_bls_cpi_release(
            LOCATOR,
            path,
            operator_attestation="yes",
        )


def test_manual_import_is_exact_p1_bls_only(tmp_path) -> None:
    path = tmp_path / "release.htm"
    path.write_bytes(html())
    with pytest.raises(CPISourceAuthorityError):
        manual.attest_and_import_manual_bls_cpi_release(
            "https://example.com/news.release/archives/cpi_08122025.htm",
            path,
            operator_attestation=manual.OPERATOR_ATTESTATION,
        )


def test_manual_evidence_binds_exact_file_hash_import_time_and_distinct_mode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_manual(tmp_path, monkeypatch)
    assert evidence.raw_body == html()
    assert evidence.raw_body_sha256 == sha256(html()).hexdigest()
    assert evidence.byte_count == len(html())
    assert evidence.imported_at == IMPORTED_AT
    assert evidence.acquisition_mode == "MANUAL_BROWSER_ATTESTED"
    assert evidence.operator_attestation == manual.OPERATOR_ATTESTATION
    assert evidence.manual_policy_identity == manual.MANUAL_POLICY_IDENTITY
    assert evidence.manual_policy_identity != automated.TRANSPORT_POLICY_IDENTITY
    assert evidence.research_only is True
    assert evidence.production_influence == Decimal("0")
    assert not hasattr(evidence, "http_status")
    assert not hasattr(evidence, "http_method")
    assert not hasattr(evidence, "transport_policy_identity")


def test_empty_nonregular_and_oversized_manual_files_fail_closed(tmp_path) -> None:
    empty = tmp_path / "empty.htm"
    empty.write_bytes(b"")
    with pytest.raises(manual.CPIManualAcquisitionError, match="non-empty"):
        manual.attest_and_import_manual_bls_cpi_release(
            LOCATOR,
            empty,
            operator_attestation=manual.OPERATOR_ATTESTATION,
        )
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(manual.CPIManualAcquisitionError):
        manual.attest_and_import_manual_bls_cpi_release(
            LOCATOR,
            directory,
            operator_attestation=manual.OPERATOR_ATTESTATION,
        )
    oversized = tmp_path / "oversized.htm"
    oversized.write_bytes(b"x" * (manual.MAX_MANUAL_ARTIFACT_BYTES + 1))
    with pytest.raises(manual.CPIManualAcquisitionError, match="bounded size"):
        manual.attest_and_import_manual_bls_cpi_release(
            LOCATOR,
            oversized,
            operator_attestation=manual.OPERATOR_ATTESTATION,
        )


def test_manual_evidence_cannot_be_directly_constructed_replaced_or_mutated(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_manual(tmp_path, monkeypatch)
    authority = manual._reviewed_authority(LOCATOR)
    with pytest.raises(manual.CPIManualAcquisitionError, match="capability"):
        manual.CPIBLSManualAcquisitionEvidence(
            source_locator=LOCATOR,
            raw_body=html(),
            imported_at=IMPORTED_AT,
            authority=authority,
            operator_attestation=manual.OPERATOR_ATTESTATION,
        )
    with pytest.raises((TypeError, manual.CPIManualAcquisitionError)):
        replace(evidence, imported_at=datetime(2025, 8, 12, 12, tzinfo=UTC))
    original = evidence.raw_body
    try:
        object.__setattr__(evidence, "raw_body", b"mutated")
        with pytest.raises(manual.CPIManualAcquisitionError):
            manual.validate_cpi_bls_manual_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "raw_body", original)
    manual.validate_cpi_bls_manual_acquisition_evidence(evidence)


def test_manual_evidence_cannot_masquerade_as_p4_transport(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_manual(tmp_path, monkeypatch)
    with pytest.raises(automated.CPISourceAcquisitionError, match="exact issued type"):
        automated.validate_cpi_bls_acquisition_evidence(evidence)  # type: ignore[arg-type]


def test_manual_import_runs_p3_and_p2_without_claiming_automated_transport(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_manual(tmp_path, monkeypatch)
    issued = issuer.issue_manual_acquisition_bound_cpi_evidence(evidence)
    assert issued.acquisition_evidence is evidence
    assert issued.release_artifact.raw_artifact == evidence.raw_body
    assert issued.release_artifact.raw_artifact_sha256 == evidence.raw_body_sha256
    assert issued.release_artifact.actual_bot_ingest_at == IMPORTED_AT
    assert issued.parsed_timing.source_artifact_id == issued.release_artifact.artifact_id
    assert issued.publication_evidence.source_artifact_id == issued.release_artifact.artifact_id
    assert issued.publication_evidence.source_publish_at == issued.parsed_timing.publication_instant
    assert issued.availability.actual_bot_ingest_at == IMPORTED_AT
    assert issued.availability.basis is AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE
    assert issued.availability.quality is AvailabilityQuality.CONSERVATIVE_ASSUMPTION
    assert issued.timing_evidence_identity != evidence.evidence_id
    pit.validate_cpi_publication_evidence(issued.publication_evidence)


def test_manual_convenience_path_uses_importer_clock_not_caller_time(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "release.htm"
    path.write_bytes(html())
    monkeypatch.setattr(manual, "_utc_now", lambda: IMPORTED_AT)
    issued = issuer.attest_import_and_issue_manual_cpi_evidence(
        LOCATOR,
        path,
        operator_attestation=manual.OPERATOR_ATTESTATION,
    )
    assert issued.acquisition_evidence.imported_at == IMPORTED_AT
    assert issued.availability.actual_bot_ingest_at == IMPORTED_AT
