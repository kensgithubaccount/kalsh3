"""Research-only importer for browser-attested BLS annual CPI archives.

The importer is deliberately an archive adapter: it hashes the ZIP and every
member, accepts only the reviewed official annual locator, and extracts only
the current/reference-month cell from each reviewed News Release Table 1.
Historical seasonal-adjustment columns are never read as observations.
"""

from __future__ import annotations

import io
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from services.forecasting.cpi_initial_release_value import (
    CPIBasket,
    CPIGeography,
    CPIHorizon,
    CPIPopulation,
    CPISeasonalBasis,
    CPIUnit,
)

MAX_ARCHIVE_BYTES = 32_000_000
MAX_MEMBER_BYTES = 2_000_000
MAX_TARGET_YEAR = 2024
ARCHIVE_ORIGIN = "https://www.bls.gov"
ARCHIVE_LOCATOR_TEMPLATE = ARCHIVE_ORIGIN + "/cpi/tables/supplemental-files/archive-{year}.zip"
ARCHIVE_ACQUISITION_MODE = "ANNUAL_ARCHIVE_BROWSER_ATTESTED"
ARCHIVE_ATTESTATION = (
    "I attest that this exact ZIP was saved from the exact official BLS annual "
    "supplemental archive locator in a normal browser."
)
ZERO = Decimal("0")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_TABLE1 = re.compile(r"(?:^|/)news-release-table1-(20[0-9]{2})(0[1-9]|1[0-2])\.xlsx$")
_RELEASE_DATES: dict[tuple[int, int], str] = {
    (2021, 1): "02102021",
    (2021, 2): "03102021",
    (2021, 3): "04132021",
    (2021, 4): "05122021",
    (2021, 5): "06102021",
    (2021, 6): "07132021",
    (2021, 7): "08112021",
    (2021, 8): "09142021",
    (2021, 9): "10132021",
    (2021, 10): "11102021",
    (2021, 11): "12102021",
    (2021, 12): "01122022",
    (2022, 1): "02102022",
    (2022, 2): "03102022",
    (2022, 3): "04122022",
    (2022, 4): "05112022",
    (2022, 5): "06102022",
    (2022, 6): "07132022",
    (2022, 7): "08102022",
    (2022, 8): "09132022",
    (2022, 9): "10132022",
    (2022, 10): "11102022",
    (2022, 11): "12132022",
    (2022, 12): "01122023",
    (2023, 1): "02142023",
    (2023, 2): "03142023",
    (2023, 3): "04122023",
    (2023, 4): "05102023",
    (2023, 5): "06132023",
    (2023, 6): "07122023",
    (2023, 7): "08102023",
    (2023, 8): "09132023",
    (2023, 9): "10122023",
    (2023, 10): "11142023",
    (2023, 11): "12122023",
    (2023, 12): "01112024",
    (2024, 1): "02132024",
    (2024, 2): "03122024",
    (2024, 3): "04102024",
    (2024, 4): "05152024",
    (2024, 5): "06122024",
    (2024, 6): "07112024",
    (2024, 7): "08142024",
    (2024, 8): "09112024",
    (2024, 9): "10102024",
    (2024, 10): "11132024",
    (2024, 11): "12112024",
    (2024, 12): "01152025",
}
FROZEN_ARCHIVE_SHA256: MappingProxyType[str, str] = MappingProxyType(
    {
        "2021": "ffe44bd007ebefb8babfde6d3ceb42227a4971cec822c984b0f33efb541aaf70",
        "2022": "287bc44fe43ce05ca3c0ffdfe6dfd7aade1aace7fd13eae701a09b18d43bbe0f",
        "2023": "858d1df5917982c0593d0423233b89e4592f62fe037d6bdd1c539f8c43190a11",
        "2024": "e8eeaccce382d4378b837d38b0c32d86d8efb05d92ab6df0ea04c02e59dffdf7",
    }
)
TARGET_START = (2021, 6)
TARGET_END = (2024, 12)
FROZEN_TARGET_COHORT_DIGEST = "f3775e888425cbe5e9d0066e4cf6a12816682dec8e0e8a8a50ee828e766888ca"
PROVEN_LATER_REFERENCE_MONTHS = ((2025, 7), (2025, 12), (2026, 1))
PUBLISHED_REMAINING_REFERENCE_MONTHS = (
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
NO_RELEASE_REFERENCE_MONTHS = ((2025, 10),)


class CPIAnnualArchiveError(ValueError):
    """An annual archive failed an exact, fail-closed validation."""


def frozen_kalshi_cohort_partition() -> MappingProxyType[str, tuple[tuple[int, int], ...]]:
    archive_months = tuple(
        (year, month)
        for year in range(2021, 2025)
        for month in range(1, 13)
        if (year, month) >= TARGET_START
    )
    return MappingProxyType(
        {
            "archive_backed": archive_months,
            "p6_p7_proven": PROVEN_LATER_REFERENCE_MONTHS,
            "published_remaining": PUBLISHED_REMAINING_REFERENCE_MONTHS,
            "no_release": NO_RELEASE_REFERENCE_MONTHS,
        }
    )


@dataclass(frozen=True, slots=True)
class CPIAnnualArchiveObservation:
    archive_year: int
    reference_year: int
    reference_month: int
    release_locator: str
    release_instant: datetime
    archive_locator: str
    member_path: str
    archive_sha256: str
    member_sha256: str
    value: Decimal
    unit: CPIUnit
    seasonal_basis: CPISeasonalBasis
    horizon: CPIHorizon
    basket: CPIBasket
    population: CPIPopulation
    geography: CPIGeography
    precision: int
    research_only: bool
    production_influence: Decimal


@dataclass(frozen=True, slots=True)
class CPIAnnualArchiveImport:
    archive_year: int
    archive_locator: str
    archive_sha256: str
    member_hashes: MappingProxyType[str, str]
    observations: tuple[CPIAnnualArchiveObservation, ...]
    imported_at: datetime
    acquisition_mode: str = ARCHIVE_ACQUISITION_MODE
    research_only: bool = True
    production_influence: Decimal = ZERO


def _read_regular_file(path: str | Path) -> bytes:
    try:
        fd = os.open(Path(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CPIAnnualArchiveError("annual archive cannot be opened") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise CPIAnnualArchiveError("annual archive must be a non-empty regular file")
        if metadata.st_size > MAX_ARCHIVE_BYTES:
            raise CPIAnnualArchiveError("annual archive exceeds bounded size")
        body = os.read(fd, MAX_ARCHIVE_BYTES + 1)
    finally:
        os.close(fd)
    if len(body) > MAX_ARCHIVE_BYTES:
        raise CPIAnnualArchiveError("annual archive exceeds bounded size")
    return body


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))  # noqa: S314
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.iter(_NS + "t"))
        for item in root.findall(_NS + "si")
    ]


def _rows(payload: bytes) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            shared = _shared_strings(book)
            root = ElementTree.fromstring(  # noqa: S314
                book.read("xl/worksheets/sheet1.xml")
            )
    except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise CPIAnnualArchiveError("Table 1 XLSX is malformed") from exc
    result: list[dict[str, str]] = []
    for row in root.findall(".//" + _NS + "row"):
        values: dict[str, str] = {}
        for cell in row.findall(_NS + "c"):
            value = cell.find(_NS + "v")
            text = "" if value is None else value.text or ""
            if cell.get("t") == "s":
                try:
                    text = shared[int(text)]
                except (ValueError, IndexError) as exc:
                    raise CPIAnnualArchiveError("Table 1 shared-string cell is invalid") from exc
            values[cell.get("r", "")] = text
        result.append(values)
    return result


def _current_value(payload: bytes, reference_year: int, reference_month: int) -> Decimal:
    rows = _rows(payload)
    if len(rows) < 7 or not rows[0].get("B1", "").endswith(f"{reference_year}"):
        raise CPIAnnualArchiveError("Table 1 headline does not bind to reference year")
    title = rows[0].get("B1", "")
    if (
        "consumer price index for all urban consumers (cpi-u): u.s. city average"
        not in title.casefold()
    ):
        raise CPIAnnualArchiveError("Table 1 title does not prove CPI-U city-average semantics")
    month_name = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )[reference_month - 1]
    if not re.search(rf"\b{month_name}\b", title):
        raise CPIAnnualArchiveError("Table 1 headline does not bind to reference month")
    semantic_header = rows[3]
    current_header = rows[4]
    if semantic_header.get("B4", "").strip().casefold() != "expenditure category":
        raise CPIAnnualArchiveError("Table 1 category header is not canonical")
    if "seasonally adjusted percent change" not in semantic_header.get("I4", "").casefold():
        raise CPIAnnualArchiveError("Table 1 SA header is not canonical")
    if "percent change" not in semantic_header.get("G4", "").casefold():
        raise CPIAnnualArchiveError("Table 1 percent-change header is missing")
    current_header_text = current_header.get("K5", "").replace("\n", " ")
    month_abbreviations = (
        "Jan.",
        "Feb.",
        "Mar.",
        "Apr.",
        "May",
        "Jun.",
        "Jul.",
        "Aug.",
        "Sep.",
        "Oct.",
        "Nov.",
        "Dec.",
    )
    preceding = month_abbreviations[(reference_month - 2) % 12]
    preceding_year = reference_year if reference_month > 1 else reference_year - 1
    if not current_header_text.startswith(f"{preceding} {preceding_year}"):
        raise CPIAnnualArchiveError("Table 1 SA header does not prove preceding-month horizon")
    if month_abbreviations[reference_month - 1] not in current_header_text:
        raise CPIAnnualArchiveError("Table 1 SA header does not prove current month")
    if any(key in current_header for key in ("L5", "L7")):
        raise CPIAnnualArchiveError("Table 1 has an unreviewed SA column after current month")
    if str(reference_year) not in current_header_text or not current_header_text.endswith(
        str(reference_year)
    ):
        raise CPIAnnualArchiveError("Table 1 current SA column is not reference-bound")
    if not rows[6].get("B7", "").strip().casefold() == "all items":
        raise CPIAnnualArchiveError("Table 1 all-items row is missing")
    token = rows[6].get("K7", "").strip()
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise CPIAnnualArchiveError("Table 1 current SA value is malformed") from exc
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -1:
        raise CPIAnnualArchiveError("Table 1 current SA value is not one decimal")
    return value.quantize(Decimal("0.0"))


def import_attested_bls_annual_archive(
    file_path: str | Path,
    *,
    year: int,
    operator_attestation: str,
    imported_at: datetime | None = None,
) -> CPIAnnualArchiveImport:
    """Import one exact browser-attested annual archive into P8 evidence."""
    if type(year) is not int or year < 2000 or year > 2099:
        raise CPIAnnualArchiveError("archive year is invalid")
    archive_locator = ARCHIVE_LOCATOR_TEMPLATE.format(year=year)
    if operator_attestation != ARCHIVE_ATTESTATION:
        raise CPIAnnualArchiveError("exact annual-archive attestation is required")
    body = _read_regular_file(file_path)
    archive_hash = sha256(body).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise CPIAnnualArchiveError("annual archive is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        if len({info.filename for info in infos}) != len(infos):
            raise CPIAnnualArchiveError("annual archive contains duplicate member paths")
        all_hashes: dict[str, str] = {}
        table_members: list[tuple[str, int, int]] = []
        for info in infos:
            if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                raise CPIAnnualArchiveError("annual archive contains unsafe member")
            if info.file_size > MAX_MEMBER_BYTES:
                raise CPIAnnualArchiveError("annual archive member exceeds bounded size")
            member = archive.read(info)
            all_hashes[info.filename] = sha256(member).hexdigest()
            match = _TABLE1.search(info.filename)
            if match:
                table_members.append((info.filename, int(match.group(1)), int(match.group(2))))
        if len(table_members) != 12 or {item[1] for item in table_members} != {year}:
            raise CPIAnnualArchiveError(
                "annual archive does not contain exactly 12 bound Table 1 members"
            )
        observations: list[CPIAnnualArchiveObservation] = []
        for member_path, ref_year, month in sorted(table_members):
            value = _current_value(archive.read(member_path), ref_year, month)
            try:
                release_id = _RELEASE_DATES[(ref_year, month)]
            except KeyError as exc:
                raise CPIAnnualArchiveError("release chronology is not reviewed") from exc
            release_locator = f"{ARCHIVE_ORIGIN}/news.release/archives/cpi_{release_id}.htm"
            release_instant = datetime.strptime(release_id, "%m%d%Y").replace(
                hour=8, minute=30, tzinfo=ZoneInfo("America/New_York")
            )
            observations.append(
                CPIAnnualArchiveObservation(
                    archive_year=year,
                    reference_year=ref_year,
                    reference_month=month,
                    release_locator=release_locator,
                    release_instant=release_instant,
                    archive_locator=archive_locator,
                    member_path=member_path,
                    archive_sha256=archive_hash,
                    member_sha256=all_hashes[member_path],
                    value=value,
                    unit=CPIUnit.PERCENT,
                    seasonal_basis=CPISeasonalBasis.SA,
                    horizon=CPIHorizon.MOM,
                    basket=CPIBasket.ALL_ITEMS,
                    population=CPIPopulation.CPI_U,
                    geography=CPIGeography.US_CITY_AVERAGE,
                    precision=1,
                    research_only=True,
                    production_influence=ZERO,
                )
            )
    timestamp = datetime.now(UTC) if imported_at is None else imported_at
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise CPIAnnualArchiveError("import timestamp must be timezone-aware")
    return CPIAnnualArchiveImport(
        archive_year=year,
        archive_locator=archive_locator,
        archive_sha256=archive_hash,
        member_hashes=MappingProxyType(all_hashes),
        observations=tuple(observations),
        imported_at=timestamp.astimezone(UTC),
    )


def load_frozen_target_cohort(
    repository_root: str | Path,
) -> tuple[CPIAnnualArchiveObservation, ...]:
    root = Path(repository_root)
    observations: list[CPIAnnualArchiveObservation] = []
    for year in range(2021, MAX_TARGET_YEAR + 1):
        path = root / "docs/reviews/artifacts/bls-annual-zips" / f"archive-{year}.zip"
        body = _read_regular_file(path)
        if sha256(body).hexdigest() != FROZEN_ARCHIVE_SHA256[str(year)]:
            raise CPIAnnualArchiveError("frozen annual archive SHA-256 mismatch")
        receipt = import_attested_bls_annual_archive(
            path, year=year, operator_attestation=ARCHIVE_ATTESTATION
        )
        observations.extend(
            item
            for item in receipt.observations
            if TARGET_START <= (item.reference_year, item.reference_month) <= TARGET_END
        )
    observations.sort(key=lambda item: (item.reference_year, item.reference_month))
    keys = [(item.reference_year, item.reference_month) for item in observations]
    expected = [
        (year, month)
        for year in range(2021, 2025)
        for month in range(1, 13)
        if TARGET_START <= (year, month) <= TARGET_END
    ]
    if len(keys) != len(set(keys)):
        raise CPIAnnualArchiveError("frozen target cohort contains duplicate months")
    if keys != expected or len(observations) != 43:
        raise CPIAnnualArchiveError("frozen target cohort is missing or misordered months")
    frozen = tuple(observations)
    if frozen_target_cohort_digest(frozen) != FROZEN_TARGET_COHORT_DIGEST:
        raise CPIAnnualArchiveError("frozen target cohort digest mismatch")
    return frozen


def frozen_target_cohort_digest(observations: tuple[CPIAnnualArchiveObservation, ...]) -> str:
    if len(observations) != 43:
        raise CPIAnnualArchiveError("frozen target digest requires exactly 43 observations")
    rows = (
        "|".join(
            (
                str(item.reference_year),
                str(item.reference_month),
                item.release_locator,
                item.release_instant.isoformat(),
                item.archive_locator,
                item.archive_sha256,
                item.member_path,
                item.member_sha256,
                str(item.value),
                item.unit.value,
                item.population.value,
                item.geography.value,
                item.basket.value,
                item.seasonal_basis.value,
                item.horizon.value,
                str(item.precision),
                str(item.research_only),
                str(item.production_influence),
            )
        )
        for item in observations
    )
    return sha256("\n".join(rows).encode()).hexdigest()


def merge_annual_archive_imports(
    imports: tuple[CPIAnnualArchiveImport, ...],
) -> tuple[CPIAnnualArchiveObservation, ...]:
    """Combine archive receipts while rejecting duplicate or conflicting months."""
    seen: dict[tuple[int, int], CPIAnnualArchiveObservation] = {}
    for receipt in imports:
        if type(receipt) is not CPIAnnualArchiveImport:
            raise CPIAnnualArchiveError("annual archive receipt has wrong exact type")
        for observation in receipt.observations:
            key = (observation.reference_year, observation.reference_month)
            prior = seen.get(key)
            if prior is not None:
                raise CPIAnnualArchiveError("duplicate or conflicting CPI month observation")
            seen[key] = observation
    return tuple(seen[key] for key in sorted(seen))
