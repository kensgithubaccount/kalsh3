from __future__ import annotations

from pathlib import Path

import pytest

from services.forecasting.cpi_annual_archive_acquisition import (
    ARCHIVE_ATTESTATION,
    CPIAnnualArchiveError,
    import_attested_bls_annual_archive,
    merge_annual_archive_imports,
)


@pytest.mark.parametrize("year", (2021, 2022, 2023, 2024))
def test_supplied_annual_archive_has_twelve_current_month_observations(year: int) -> None:
    path = Path(f"/Users/ksyme/Downloads/archive-{year}.zip")
    if not path.exists():
        pytest.skip("browser-supplied audit artifact is not present")
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
    path = Path("/Users/ksyme/Downloads/archive-2023.zip")
    if not path.exists():
        pytest.skip("browser-supplied audit artifact is not present")
    receipt = import_attested_bls_annual_archive(
        path, year=2023, operator_attestation=ARCHIVE_ATTESTATION
    )
    with pytest.raises(CPIAnnualArchiveError, match="duplicate"):
        merge_annual_archive_imports((receipt, receipt))
