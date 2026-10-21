"""Research-only manual-browser BLS CPI release-calendar acquisition for P12 readiness.

This lane exists because BLS/Akamai rejects the reviewed automated schedule request
from tested Python/curl clients while a normal Safari session can reach the exact
first-party CPI release-calendar page. It does not impersonate automated HTTP
transport. A human operator explicitly attests that one local HTML file was saved
from the exact reviewed BLS calendar locator in a normal browser.

The importer:
- accepts a file path, never caller-supplied raw bytes;
- stamps importer-observed UTC time internally;
- binds exact file bytes and SHA-256;
- resolves the existing P1 RELEASE_CALENDAR authority;
- deterministically extracts the scheduled CPI release instant for one reference month;
- remains research-only with zero production influence.

This module does not freeze P12 and does not by itself grant scientific evidence
authority. A later prospective protocol must bind the resulting content hash and
reference month before any test run.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from services.forecasting.cpi_source_authority import (
    BLS_HTTPS_ORIGIN,
    CPISourceInterface,
    CPISourceProfile,
    CPISourceRole,
    ReviewedCPISourceAuthority,
    resolve_cpi_source_authority,
    validate_cpi_source_authority,
)
from services.forecasting.cpi_source_authority import (
    POLICY_IDENTITY as CANONICAL_P1_POLICY_IDENTITY,
)
from services.market_universe.domain import stable_hash

MANUAL_CALENDAR_POLICY_VERSION = "cpi-p12-manual-browser-calendar-v1"
MANUAL_CALENDAR_SCHEMA_VERSION = "cpi-p12-manual-calendar-acquisition-evidence-v1"
ACQUISITION_MODE = "MANUAL_BROWSER_ATTESTED_RELEASE_CALENDAR"
SOURCE_LOCATOR = "https://www.bls.gov/schedule/news_release/cpi.htm"
OPERATOR_ATTESTATION = (
    "I attest that this exact HTML file was saved from the exact official BLS CPI "
    "release-calendar locator in a normal browser."
)
MAX_MANUAL_CALENDAR_BYTES = 4_000_000
ZERO = Decimal("0")
EASTERN = ZoneInfo("America/New_York")

_PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
_ROLE = CPISourceRole.RELEASE_CALENDAR
_INTERFACE = CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML
_MANUAL_CALENDAR_CAPABILITY = object()
_ISSUED_FINGERPRINTS: dict[int, str] = {}

MANUAL_CALENDAR_POLICY_IDENTITY = stable_hash(
    (
        MANUAL_CALENDAR_POLICY_VERSION,
        ACQUISITION_MODE,
        SOURCE_LOCATOR,
        BLS_HTTPS_ORIGIN,
        CANONICAL_P1_POLICY_IDENTITY,
        MAX_MANUAL_CALENDAR_BYTES,
        OPERATOR_ATTESTATION,
        "HUMAN_ATTESTED_NOT_CRYPTOGRAPHIC_HTTP_PROVENANCE",
        "IMPORT_TIME_IS_ISSUER_OBSERVED_NOT_BROWSER_DOWNLOAD_TIME",
        "RESEARCH_ONLY",
        str(ZERO),
    )
)


class CPICalendarManualAcquisitionError(ValueError):
    """Manual BLS calendar acquisition or semantic parsing failed closed."""


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPICalendarManualAcquisitionError(
            f"{field_name} must be an exact aware datetime"
        )
    return value.astimezone(UTC)


def _reviewed_authority() -> ReviewedCPISourceAuthority:
    authority = resolve_cpi_source_authority(
        profile=_PROFILE,
        role=_ROLE,
        locator=SOURCE_LOCATOR,
    )
    validate_cpi_source_authority(authority)
    if authority.source_interface is not _INTERFACE:
        raise CPICalendarManualAcquisitionError(
            "reviewed calendar source interface changed"
        )
    if authority.policy_identity != CANONICAL_P1_POLICY_IDENTITY:
        raise CPICalendarManualAcquisitionError(
            "canonical P1 source policy identity moved"
        )
    return authority


def _read_exact_local_file(file_path: str | Path) -> bytes:
    path = Path(file_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CPICalendarManualAcquisitionError(
            f"manual BLS calendar artifact cannot be opened: {exc}"
        ) from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise CPICalendarManualAcquisitionError(
                "manual BLS calendar artifact must be a regular file"
            )
        if metadata.st_size <= 0:
            raise CPICalendarManualAcquisitionError(
                "manual BLS calendar artifact must be non-empty"
            )
        if metadata.st_size > MAX_MANUAL_CALENDAR_BYTES:
            raise CPICalendarManualAcquisitionError(
                "manual BLS calendar artifact exceeded bounded size"
            )
        chunks: list[bytes] = []
        remaining = MAX_MANUAL_CALENDAR_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
    finally:
        os.close(fd)
    if not body:
        raise CPICalendarManualAcquisitionError(
            "manual BLS calendar artifact must be non-empty"
        )
    if len(body) > MAX_MANUAL_CALENDAR_BYTES:
        raise CPICalendarManualAcquisitionError(
            "manual BLS calendar artifact exceeded bounded size"
        )
    return body


def _normalize_reference_month(value: str) -> tuple[str, str]:
    if type(value) is not str or re.fullmatch(r"\d{4}-\d{2}", value) is None:
        raise CPICalendarManualAcquisitionError(
            "reference_month must be an exact YYYY-MM string"
        )
    year = int(value[:4])
    month = int(value[5:])
    if month < 1 or month > 12:
        raise CPICalendarManualAcquisitionError(
            "reference_month must be an exact YYYY-MM string"
        )
    label = datetime(year, month, 1).strftime("%B %Y")
    return value, label


def _visible_text(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CPICalendarManualAcquisitionError(
            "BLS calendar HTML is not valid UTF-8"
        ) from exc
    parser = _VisibleText()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise CPICalendarManualAcquisitionError(
            "BLS calendar HTML could not be parsed"
        ) from exc
    return " ".join(" ".join(parser.parts).split())


_MONTH_RE = (
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|"
    r"Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)"
)
_DATE_RE = re.compile(
    rf"(?P<month>{_MONTH_RE})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})",
    flags=re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>AM|PM|A\.M\.|P\.M\.)",
    flags=re.IGNORECASE,
)


def _parse_month_name(value: str) -> int:
    compact = value.replace(".", "").casefold()
    aliases = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    try:
        return aliases[compact]
    except KeyError as exc:
        raise CPICalendarManualAcquisitionError(
            "calendar release month name is unsupported"
        ) from exc


def _parse_release_instant(body: bytes, reference_month: str) -> datetime:
    _, label = _normalize_reference_month(reference_month)
    normalized = _visible_text(body)
    if "schedule of releases for the consumer price index" not in normalized.casefold():
        raise CPICalendarManualAcquisitionError(
            "page is not positively identified as the CPI release schedule"
        )

    candidates: set[datetime] = set()
    for ref_match in re.finditer(re.escape(label), normalized, flags=re.IGNORECASE):
        window = normalized[ref_match.end() : ref_match.end() + 260]
        date_match = _DATE_RE.search(window)
        time_match = _TIME_RE.search(window)
        if date_match is None or time_match is None:
            continue
        if date_match.start() > 180 or time_match.start() > 220:
            continue

        release_month = _parse_month_name(date_match.group("month"))
        release_day = int(date_match.group("day"))
        release_year = int(date_match.group("year"))
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        if hour < 1 or hour > 12 or minute > 59:
            continue
        ampm = time_match.group("ampm").replace(".", "").upper()
        if ampm == "AM":
            hour24 = 0 if hour == 12 else hour
        else:
            hour24 = 12 if hour == 12 else hour + 12

        try:
            local = datetime(
                release_year,
                release_month,
                release_day,
                hour24,
                minute,
                tzinfo=EASTERN,
            )
        except ValueError:
            continue
        candidates.add(local.astimezone(UTC))

    if len(candidates) != 1:
        raise CPICalendarManualAcquisitionError(
            "reference month did not bind uniquely to one CPI release instant"
        )
    return next(iter(candidates))


def _digest_values(values: dict[str, object]) -> str:
    imported_at = values["imported_at"]
    release_at = values["release_at_utc"]
    if type(imported_at) is not datetime or type(release_at) is not datetime:
        raise CPICalendarManualAcquisitionError(
            "manual calendar timestamp type is invalid"
        )
    return stable_hash(
        (
            MANUAL_CALENDAR_SCHEMA_VERSION,
            MANUAL_CALENDAR_POLICY_IDENTITY,
            ACQUISITION_MODE,
            values["source_locator"],
            values["raw_body_sha256"],
            values["byte_count"],
            imported_at.isoformat(),
            values["reference_month"],
            release_at.isoformat(),
            values["operator_attestation"],
            values["p1_authority_identity"],
            values["p1_policy_identity"],
            True,
            str(ZERO),
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class CPIBLSManualCalendarEvidence:
    """Issuer-controlled evidence for one manual-browser BLS calendar HTML import."""

    profile: CPISourceProfile
    source_role: CPISourceRole
    source_interface: CPISourceInterface
    source_locator: str
    reviewed_origin: str
    raw_body: bytes
    raw_body_sha256: str
    byte_count: int
    imported_at: datetime
    reference_month: str
    release_at_utc: datetime
    acquisition_mode: str
    operator_attestation: str
    manual_policy_identity: str
    p1_authority_identity: str
    p1_policy_identity: str
    schema_version: str
    evidence_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        raw_body: bytes,
        imported_at: datetime,
        reference_month: str,
        release_at_utc: datetime,
        authority: ReviewedCPISourceAuthority,
        operator_attestation: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _MANUAL_CALENDAR_CAPABILITY:
            raise CPICalendarManualAcquisitionError(
                "manual calendar evidence requires importer capability"
            )
        validate_cpi_source_authority(authority)
        canonical = _reviewed_authority()
        if authority is not canonical:
            raise CPICalendarManualAcquisitionError(
                "caller-stamped calendar source authority is forbidden"
            )
        if type(raw_body) is not bytes or not raw_body:
            raise CPICalendarManualAcquisitionError(
                "exact non-empty manual calendar bytes are required"
            )
        if len(raw_body) > MAX_MANUAL_CALENDAR_BYTES:
            raise CPICalendarManualAcquisitionError(
                "manual BLS calendar artifact exceeded bounded size"
            )
        if type(operator_attestation) is not str or operator_attestation != OPERATOR_ATTESTATION:
            raise CPICalendarManualAcquisitionError(
                "exact manual-browser operator attestation is required"
            )

        normalized_reference, _ = _normalize_reference_month(reference_month)
        imported = _aware_utc(imported_at, "manual calendar import timestamp")
        release_at = _aware_utc(release_at_utc, "calendar release timestamp")
        parsed_release_at = _parse_release_instant(raw_body, normalized_reference)
        if release_at != parsed_release_at:
            raise CPICalendarManualAcquisitionError(
                "calendar release instant is not derived from exact file bytes"
            )
        raw_hash = sha256(raw_body).hexdigest()
        values: dict[str, object] = {
            "profile": canonical.profile,
            "source_role": canonical.source_role,
            "source_interface": canonical.source_interface,
            "source_locator": SOURCE_LOCATOR,
            "reviewed_origin": BLS_HTTPS_ORIGIN,
            "raw_body": raw_body,
            "raw_body_sha256": raw_hash,
            "byte_count": len(raw_body),
            "imported_at": imported,
            "reference_month": normalized_reference,
            "release_at_utc": release_at,
            "acquisition_mode": ACQUISITION_MODE,
            "operator_attestation": operator_attestation,
            "manual_policy_identity": MANUAL_CALENDAR_POLICY_IDENTITY,
            "p1_authority_identity": canonical.authority_identity,
            "p1_policy_identity": canonical.policy_identity,
            "schema_version": MANUAL_CALENDAR_SCHEMA_VERSION,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _digest_values(values)
        values["evidence_id"] = digest
        values["content_hash"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_FINGERPRINTS[id(self)] = digest


def _digest(evidence: CPIBLSManualCalendarEvidence) -> str:
    return _digest_values(
        {
            "source_locator": evidence.source_locator,
            "raw_body_sha256": evidence.raw_body_sha256,
            "byte_count": evidence.byte_count,
            "imported_at": evidence.imported_at,
            "reference_month": evidence.reference_month,
            "release_at_utc": evidence.release_at_utc,
            "operator_attestation": evidence.operator_attestation,
            "p1_authority_identity": evidence.p1_authority_identity,
            "p1_policy_identity": evidence.p1_policy_identity,
        }
    )


def validate_cpi_bls_manual_calendar_evidence(
    evidence: CPIBLSManualCalendarEvidence,
) -> None:
    """Revalidate exact manual-calendar bytes and semantic row binding."""
    if type(evidence) is not CPIBLSManualCalendarEvidence:
        raise CPICalendarManualAcquisitionError(
            "manual calendar evidence must have exact issued type"
        )
    authority = _reviewed_authority()
    raw_hash = sha256(evidence.raw_body).hexdigest() if type(evidence.raw_body) is bytes else None
    imported = _aware_utc(evidence.imported_at, "manual calendar import timestamp")
    release_at = _aware_utc(evidence.release_at_utc, "calendar release timestamp")
    parsed_release_at = _parse_release_instant(evidence.raw_body, evidence.reference_month)
    expected = _digest(evidence)
    exact = (
        type(evidence.profile) is CPISourceProfile and evidence.profile is _PROFILE,
        type(evidence.source_role) is CPISourceRole and evidence.source_role is _ROLE,
        type(evidence.source_interface) is CPISourceInterface
        and evidence.source_interface is _INTERFACE,
        type(evidence.source_locator) is str and evidence.source_locator == SOURCE_LOCATOR,
        type(evidence.reviewed_origin) is str and evidence.reviewed_origin == BLS_HTTPS_ORIGIN,
        type(evidence.raw_body) is bytes and bool(evidence.raw_body),
        len(evidence.raw_body) <= MAX_MANUAL_CALENDAR_BYTES,
        type(evidence.raw_body_sha256) is str and evidence.raw_body_sha256 == raw_hash,
        type(evidence.byte_count) is int
        and not isinstance(evidence.byte_count, bool)
        and evidence.byte_count == len(evidence.raw_body),
        evidence.imported_at == imported and evidence.imported_at.tzinfo is UTC,
        type(evidence.reference_month) is str
        and evidence.reference_month == _normalize_reference_month(evidence.reference_month)[0],
        evidence.release_at_utc == release_at
        and evidence.release_at_utc.tzinfo is UTC
        and evidence.release_at_utc == parsed_release_at,
        type(evidence.acquisition_mode) is str
        and evidence.acquisition_mode == ACQUISITION_MODE,
        type(evidence.operator_attestation) is str
        and evidence.operator_attestation == OPERATOR_ATTESTATION,
        type(evidence.manual_policy_identity) is str
        and evidence.manual_policy_identity == MANUAL_CALENDAR_POLICY_IDENTITY,
        type(evidence.p1_authority_identity) is str
        and evidence.p1_authority_identity == authority.authority_identity,
        type(evidence.p1_policy_identity) is str
        and evidence.p1_policy_identity
        == authority.policy_identity
        == CANONICAL_P1_POLICY_IDENTITY,
        type(evidence.schema_version) is str
        and evidence.schema_version == MANUAL_CALENDAR_SCHEMA_VERSION,
        type(evidence.evidence_id) is str and evidence.evidence_id == expected,
        type(evidence.content_hash) is str and evidence.content_hash == expected,
        type(evidence.research_only) is bool and evidence.research_only is True,
        type(evidence.production_influence) is Decimal
        and evidence.production_influence == ZERO,
    )
    if not all(exact):
        raise CPICalendarManualAcquisitionError(
            "manual calendar evidence failed revalidation"
        )
    if _ISSUED_FINGERPRINTS.get(id(evidence)) != expected:
        raise CPICalendarManualAcquisitionError(
            "unissued, reconstructed, or mutated manual calendar evidence"
        )


def attest_and_import_manual_bls_cpi_calendar(
    file_path: str | Path,
    *,
    reference_month: str,
    operator_attestation: str,
) -> CPIBLSManualCalendarEvidence:
    """Import exact browser-saved BLS CPI calendar HTML under explicit attestation."""
    if type(operator_attestation) is not str or operator_attestation != OPERATOR_ATTESTATION:
        raise CPICalendarManualAcquisitionError(
            "exact manual-browser operator attestation is required"
        )
    authority = _reviewed_authority()
    body = _read_exact_local_file(file_path)
    normalized_reference, _ = _normalize_reference_month(reference_month)
    release_at = _parse_release_instant(body, normalized_reference)
    evidence = CPIBLSManualCalendarEvidence(
        raw_body=body,
        imported_at=_utc_now(),
        reference_month=normalized_reference,
        release_at_utc=release_at,
        authority=authority,
        operator_attestation=operator_attestation,
        _capability=_MANUAL_CALENDAR_CAPABILITY,
    )
    validate_cpi_bls_manual_calendar_evidence(evidence)
    return evidence
