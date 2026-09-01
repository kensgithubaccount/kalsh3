from __future__ import annotations

import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from services.forecasting import cpi_annual_archive_acquisition as archive_module
from services.forecasting.cpi_annual_archive_acquisition import (
    ARCHIVE_ATTESTATION,
    FROZEN_TARGET_COHORT_DIGEST,
    CPIAnnualArchiveError,
    CPIAnnualArchiveImport,
    frozen_kalshi_cohort_partition,
    frozen_target_cohort_digest,
    import_attested_bls_annual_archive,
    load_frozen_target_cohort,
    merge_annual_archive_imports,
)


@pytest.mark.parametrize("year", (2021, 2022, 2023, 2024))
def test_supplied_annual_archive_has_twelve_current_month_observations(year: int) -> None:
    path = (
        Path(__file__).parents[1] / "docs/reviews/artifacts/bls-annual-zips" / f"archive-{year}.zip"
    )
    receipt = import_attested_bls_annual_archive(
        path, year=year, operator_attestation=ARCHIVE_ATTESTATION
    )
    assert len(receipt.member_hashes) in {132, 133}
    assert len(receipt.observations) == 12
    assert {item.reference_month for item in receipt.observations} == set(range(1, 13))
    assert all(
        item.research_only and item.production_influence == 0 for item in receipt.observations
    )


def test_annual_archive_merge_rejects_duplicate_month() -> None:
    path = Path(__file__).parents[1] / "docs/reviews/artifacts/bls-annual-zips/archive-2023.zip"
    receipt = import_attested_bls_annual_archive(
        path, year=2023, operator_attestation=ARCHIVE_ATTESTATION
    )
    with pytest.raises(CPIAnnualArchiveError, match="duplicate"):
        merge_annual_archive_imports((receipt, receipt))


def test_frozen_cohort_is_exactly_43_and_content_addressed() -> None:
    cohort = load_frozen_target_cohort(Path(__file__).parents[1])
    assert len(cohort) == 43
    assert frozen_target_cohort_digest(cohort) == FROZEN_TARGET_COHORT_DIGEST
    assert (2021, 1) not in {(x.reference_year, x.reference_month) for x in cohort}


def test_frozen_partition_is_exact_60_and_has_no_october_value() -> None:
    partition = frozen_kalshi_cohort_partition()
    slots = [slot for group in partition.values() for slot in group]
    assert len(slots) == 60
    assert len(set(slots)) == 60
    assert partition["published_remaining"] == (
        (2025, 1),
        (2025, 2),
        (2025, 3),
        (2025, 4),
        (2025, 5),
        (2025, 6),
        (2025, 8),
        (2025, 9),
        (2025, 11),
        (2026, 2),
        (2026, 3),
        (2026, 4),
        (2026, 5),
    )
    assert partition["no_release"] == ((2025, 10),)


def test_frozen_loader_rejects_wrong_zip_bytes(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    destination = tmp_path / "docs/reviews/artifacts/bls-annual-zips"
    destination.mkdir(parents=True)
    for year in range(2021, 2025):
        shutil.copy2(
            root / "docs/reviews/artifacts/bls-annual-zips" / f"archive-{year}.zip", destination
        )
    damaged = destination / "archive-2021.zip"
    damaged.write_bytes(damaged.read_bytes()[:-1] + b"x")
    with pytest.raises(CPIAnnualArchiveError, match="archive SHA-256"):
        load_frozen_target_cohort(tmp_path)


def test_frozen_loader_rejects_missing_month(monkeypatch: pytest.MonkeyPatch) -> None:
    original = archive_module.import_attested_bls_annual_archive

    def missing(path: Path, *, year: int, operator_attestation: str) -> CPIAnnualArchiveImport:
        receipt = original(path, year=year, operator_attestation=operator_attestation)
        if year == 2024:
            return replace(receipt, observations=receipt.observations[:-1])
        return receipt

    monkeypatch.setattr(archive_module, "import_attested_bls_annual_archive", missing)
    with pytest.raises(CPIAnnualArchiveError, match="missing"):
        load_frozen_target_cohort(Path(__file__).parents[1])


def test_frozen_loader_rejects_duplicate_month(monkeypatch: pytest.MonkeyPatch) -> None:
    original = archive_module.import_attested_bls_annual_archive

    def duplicate(path: Path, *, year: int, operator_attestation: str) -> CPIAnnualArchiveImport:
        receipt = original(path, year=year, operator_attestation=operator_attestation)
        if year == 2024:
            return replace(receipt, observations=(*receipt.observations, receipt.observations[-1]))
        return receipt

    monkeypatch.setattr(archive_module, "import_attested_bls_annual_archive", duplicate)
    with pytest.raises(CPIAnnualArchiveError, match="duplicate"):
        load_frozen_target_cohort(Path(__file__).parents[1])


@pytest.mark.parametrize("field", ("B4", "G4", "I4", "K5", "B7"))
def test_table1_semantic_header_mutation_fails_closed(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    path = Path(__file__).parents[1] / "docs/reviews/artifacts/bls-annual-zips/archive-2024.zip"
    with zipfile.ZipFile(path) as archive:
        payload = archive.read("news-release-table1-202407.xlsx")
    original = archive_module._rows(payload)
    mutated = [dict(row) for row in original]
    mutated[3 if field in ("B4", "G4", "I4") else 4 if field == "K5" else 6][field] = "wrong"
    monkeypatch.setattr(archive_module, "_rows", lambda _: mutated)
    with pytest.raises(CPIAnnualArchiveError):
        archive_module._current_value(payload, 2024, 7)
