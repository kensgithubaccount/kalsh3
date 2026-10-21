from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

import pytest

import services.forecasting.cpi_calendar_manual_acquisition as calendar
from services.forecasting.cpi_source_authority import (
    CPISourceInterface,
    CPISourceRole,
)

IMPORTED_AT = datetime(2026, 10, 21, 15, 27, 38, tzinfo=UTC)
REFERENCE_MONTH = "2026-10"
EXPECTED_RELEASE = datetime(2026, 11, 10, 13, 30, tzinfo=UTC)


def html(
    *,
    reference_month: str = "October 2026",
    release_date: str = "Nov. 10, 2026",
    release_time: str = "08:30 AM",
) -> bytes:
    return f"""<!doctype html>
<html>
  <head><title>Schedule of Releases for the Consumer Price Index</title></head>
  <body>
    <nav>SEPTEMBER 2026 OCTOBER 2026 NOVEMBER 2026 DECEMBER 2026</nav>
    <h1>Schedule of Releases for the Consumer Price Index</h1>
    <table>
      <tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>
      <tr><td>{reference_month}</td><td>{release_date}</td><td>{release_time}</td></tr>
      <tr><td>November 2026</td><td>Dec. 10, 2026</td><td>08:30 AM</td></tr>
    </table>
  </body>
</html>
""".encode("utf-8")


def import_calendar(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> calendar.CPIBLSManualCalendarEvidence:
    path = tmp_path / "cpi.htm"
    path.write_bytes(html())
    monkeypatch.setattr(calendar, "_utc_now", lambda: IMPORTED_AT)
    return calendar.attest_and_import_manual_bls_cpi_calendar(
        path,
        reference_month=REFERENCE_MONTH,
        operator_attestation=calendar.OPERATOR_ATTESTATION,
    )


def test_public_api_requires_file_reference_month_and_attestation_only(tmp_path) -> None:
    params = tuple(
        inspect.signature(calendar.attest_and_import_manual_bls_cpi_calendar).parameters
    )
    assert params == ("file_path", "reference_month", "operator_attestation")

    path = tmp_path / "cpi.htm"
    path.write_bytes(html())
    with pytest.raises(TypeError):
        calendar.attest_and_import_manual_bls_cpi_calendar(  # type: ignore[call-arg]
            path,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
            raw_body=html(),
        )
    with pytest.raises(TypeError):
        calendar.attest_and_import_manual_bls_cpi_calendar(  # type: ignore[call-arg]
            path,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
            imported_at=IMPORTED_AT,
        )


def test_import_binds_existing_p1_release_calendar_authority(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_calendar(tmp_path, monkeypatch)
    assert evidence.source_role is CPISourceRole.RELEASE_CALENDAR
    assert evidence.source_interface is CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML
    assert evidence.source_locator == calendar.SOURCE_LOCATOR
    assert evidence.reference_month == REFERENCE_MONTH
    assert evidence.release_at_utc == EXPECTED_RELEASE
    assert evidence.imported_at == IMPORTED_AT
    assert evidence.raw_body == html()
    assert evidence.raw_body_sha256 == sha256(html()).hexdigest()
    assert evidence.byte_count == len(html())
    assert evidence.acquisition_mode == "MANUAL_BROWSER_ATTESTED_RELEASE_CALENDAR"
    assert evidence.operator_attestation == calendar.OPERATOR_ATTESTATION
    assert evidence.research_only is True
    assert evidence.production_influence == Decimal("0")


def test_sidebar_month_text_cannot_substitute_for_exact_table_row(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = html(reference_month="September 2026")
    path = tmp_path / "cpi.htm"
    path.write_bytes(page)
    monkeypatch.setattr(calendar, "_utc_now", lambda: IMPORTED_AT)
    with pytest.raises(
        calendar.CPICalendarManualAcquisitionError,
        match="bind uniquely",
    ):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            path,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )


@pytest.mark.parametrize(
    ("release_date", "release_time"),
    [
        ("Nov. 11, 2026", "08:30 AM"),
        ("Nov. 10, 2026", "09:30 AM"),
    ],
)
def test_parser_derives_changed_schedule_from_exact_bytes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    release_date: str,
    release_time: str,
) -> None:
    path = tmp_path / "cpi.htm"
    path.write_bytes(html(release_date=release_date, release_time=release_time))
    monkeypatch.setattr(calendar, "_utc_now", lambda: IMPORTED_AT)
    evidence = calendar.attest_and_import_manual_bls_cpi_calendar(
        path,
        reference_month=REFERENCE_MONTH,
        operator_attestation=calendar.OPERATOR_ATTESTATION,
    )
    assert evidence.release_at_utc != EXPECTED_RELEASE


def test_wrong_page_or_malformed_target_row_fails_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calendar, "_utc_now", lambda: IMPORTED_AT)
    wrong = tmp_path / "wrong.htm"
    wrong.write_text("<html><body><table><tr><td>October 2026</td>"
                     "<td>Nov. 10, 2026</td><td>08:30 AM</td></tr></table></body></html>")
    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="identified"):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            wrong,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )

    malformed = tmp_path / "malformed.htm"
    malformed.write_bytes(html(release_time="not-a-time"))
    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="bind uniquely"):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            malformed,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )


@pytest.mark.parametrize("reference_month", ["2026-00", "2026-13", "Oct 2026", "2026-1", ""])
def test_reference_month_must_be_exact(reference_month: str, tmp_path) -> None:
    path = tmp_path / "cpi.htm"
    path.write_bytes(html())
    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="reference_month"):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            path,
            reference_month=reference_month,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )


def test_exact_attestation_is_required(tmp_path) -> None:
    path = tmp_path / "cpi.htm"
    path.write_bytes(html())
    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="attestation"):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            path,
            reference_month=REFERENCE_MONTH,
            operator_attestation="yes",
        )


def test_empty_directory_symlink_and_oversized_files_fail_closed(tmp_path) -> None:
    empty = tmp_path / "empty.htm"
    empty.write_bytes(b"")
    with pytest.raises(calendar.CPICalendarManualAcquisitionError):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            empty,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )

    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(calendar.CPICalendarManualAcquisitionError):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            directory,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )

    target = tmp_path / "target.htm"
    target.write_bytes(html())
    link = tmp_path / "link.htm"
    link.symlink_to(target)
    if getattr(__import__("os"), "O_NOFOLLOW", 0):
        with pytest.raises(calendar.CPICalendarManualAcquisitionError):
            calendar.attest_and_import_manual_bls_cpi_calendar(
                link,
                reference_month=REFERENCE_MONTH,
                operator_attestation=calendar.OPERATOR_ATTESTATION,
            )

    oversized = tmp_path / "oversized.htm"
    oversized.write_bytes(b"x" * (calendar.MAX_MANUAL_CALENDAR_BYTES + 1))
    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="bounded size"):
        calendar.attest_and_import_manual_bls_cpi_calendar(
            oversized,
            reference_month=REFERENCE_MONTH,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )


def test_evidence_cannot_be_constructed_replaced_or_mutated(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_calendar(tmp_path, monkeypatch)
    authority = calendar._reviewed_authority()

    with pytest.raises(calendar.CPICalendarManualAcquisitionError, match="capability"):
        calendar.CPIBLSManualCalendarEvidence(
            raw_body=html(),
            imported_at=IMPORTED_AT,
            reference_month=REFERENCE_MONTH,
            release_at_utc=EXPECTED_RELEASE,
            authority=authority,
            operator_attestation=calendar.OPERATOR_ATTESTATION,
        )

    with pytest.raises((TypeError, calendar.CPICalendarManualAcquisitionError)):
        replace(evidence, release_at_utc=datetime(2026, 11, 11, 13, 30, tzinfo=UTC))

    original = evidence.raw_body
    try:
        object.__setattr__(evidence, "raw_body", html(release_date="Nov. 11, 2026"))
        with pytest.raises(calendar.CPICalendarManualAcquisitionError):
            calendar.validate_cpi_bls_manual_calendar_evidence(evidence)
    finally:
        object.__setattr__(evidence, "raw_body", original)

    calendar.validate_cpi_bls_manual_calendar_evidence(evidence)


def test_import_timestamp_is_internal_not_caller_authority(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = import_calendar(tmp_path, monkeypatch)
    assert evidence.imported_at == IMPORTED_AT
    assert "imported_at" not in inspect.signature(
        calendar.attest_and_import_manual_bls_cpi_calendar
    ).parameters
