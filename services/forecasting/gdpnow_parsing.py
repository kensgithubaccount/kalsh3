"""Pure deterministic GDPNow commentary parsing for D1-G1.

This module performs no network access, generates no acquisition timestamps, and
manufactures no provenance. It accepts already-acquired `GDPNowAcquisitionEvidence`
(see `gdpnow_source_acquisition.py`) through a narrow, bounded interface and
positively identifies the newest GDPNow commentary entry using an exact grammar
derived from the reviewed Atlanta Fed commentary page structure. Anything the
grammar cannot positively establish fails closed rather than being inferred or
guessed.

Also provides deterministic classification of two captures (NEW_UPDATE /
UNCHANGED_FORECAST / PAGE_CHANGED_NON_FORECAST / MALFORMED_OR_UNKNOWN) and the
narrowest durable research receipt needed to preserve future captures. No
scientific clock, evaluation boundary, or trading authority is created here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from services.forecasting.gdpnow_source_acquisition import (
    TRANSPORT_POLICY_IDENTITY,
    GDPNowAcquisitionEvidence,
    validate_gdpnow_acquisition_evidence,
)
from services.market_universe.domain import stable_hash

PARSER_VERSION = "d1-g1-gdpnow-commentary-parser-v1"
RECEIPT_SCHEMA_VERSION = "d1-g1-gdpnow-capture-receipt-v1"
ZERO = Decimal("0")

_ORDINAL_QUARTER: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
}

_MONTH_NUMBERS: dict[str, int] = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

# Matches only the exact newest-candidate entry header immediately followed by
# the exact reviewed GDPNow current-estimate sentence. Deliberately narrow: it
# does not match prose elsewhere on the page, older-entry-only mentions, or any
# quarter/value not stated in this exact sentence shape.
_ENTRY_RE = re.compile(
    r"<h2>\s*(?P<pub_month>[A-Za-z]+)\s+(?P<pub_day>\d{1,2}),\s*(?P<pub_year>\d{4})\s*</h2>\s*"
    r"<p>\s*The GDPNow model estimate for real GDP growth "
    r"\(seasonally adjusted annual rate\)\s+in the "
    r"(?P<ordinal>first|second|third|fourth) quarter of (?P<q_year>\d{4}) is\s+"
    r"<strong>\s*(?P<value>-?\d+\.\d+)\s*percent\s*</strong>\s+on\s+"
    r"(?P<sent_month>[A-Za-z]+)\s+(?P<sent_day>\d{1,2}),"
)

# Any commentary-style date header at all, used to detect whether the topmost
# header on the page is the one the narrow entry grammar actually matched.
_ANY_ENTRY_HEADER_RE = re.compile(r"<h2>\s*[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*</h2>")


class GDPNowParsingError(ValueError):
    """Deterministic GDPNow commentary parsing failed closed."""


_PARSED_VINTAGE_CAPABILITY = object()
_ISSUED_PARSED_FINGERPRINTS: dict[int, str] = {}


def _vintage_digest_values(values: dict[str, object]) -> str:
    publisher_stated_date = values["publisher_stated_date"]
    if type(publisher_stated_date) is not date:
        raise GDPNowParsingError("parsed publisher-stated date type is invalid")
    gdpnow_value = values["gdpnow_value"]
    if type(gdpnow_value) is not Decimal:
        raise GDPNowParsingError("parsed GDPNow value type is invalid")
    return stable_hash(
        (
            RECEIPT_SCHEMA_VERSION,
            values["parser_version"],
            values["acquisition_evidence_id"],
            values["target_quarter"],
            str(gdpnow_value),
            publisher_stated_date.isoformat(),
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class ParsedGDPNowVintage:
    """Issuer-controlled deterministic parse of one newest GDPNow commentary entry."""

    target_quarter: str
    gdpnow_value: Decimal
    publisher_stated_date: date
    parser_version: str
    acquisition_evidence_id: str
    vintage_id: str

    def __init__(
        self,
        *,
        target_quarter: str,
        gdpnow_value: Decimal,
        publisher_stated_date: date,
        acquisition_evidence_id: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _PARSED_VINTAGE_CAPABILITY:
            raise GDPNowParsingError(
                "parsed GDPNow vintage requires the reviewed parser capability"
            )
        if type(target_quarter) is not str or not re.fullmatch(r"\d{4}-Q[1-4]", target_quarter):
            raise GDPNowParsingError("parsed target quarter is malformed")
        if type(gdpnow_value) is not Decimal or not gdpnow_value.is_finite():
            raise GDPNowParsingError("parsed GDPNow value is malformed")
        if type(publisher_stated_date) is not date:
            raise GDPNowParsingError("parsed publisher-stated date is malformed")
        if type(acquisition_evidence_id) is not str or not acquisition_evidence_id:
            raise GDPNowParsingError("parsed acquisition evidence identity is required")
        values: dict[str, object] = {
            "target_quarter": target_quarter,
            "gdpnow_value": gdpnow_value,
            "publisher_stated_date": publisher_stated_date,
            "parser_version": PARSER_VERSION,
            "acquisition_evidence_id": acquisition_evidence_id,
        }
        values["vintage_id"] = _vintage_digest_values(values)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_PARSED_FINGERPRINTS[id(self)] = values["vintage_id"]  # type: ignore[assignment]


def validate_parsed_gdpnow_vintage(vintage: ParsedGDPNowVintage) -> None:
    """Revalidate a parsed vintage's fields and issuance provenance."""
    if type(vintage) is not ParsedGDPNowVintage:
        raise GDPNowParsingError("parsed GDPNow vintage must have exact issued type")
    if type(vintage.parser_version) is not str or vintage.parser_version != PARSER_VERSION:
        raise GDPNowParsingError("parsed GDPNow vintage parser version changed")
    expected = _vintage_digest_values(
        {
            "target_quarter": vintage.target_quarter,
            "gdpnow_value": vintage.gdpnow_value,
            "publisher_stated_date": vintage.publisher_stated_date,
            "parser_version": vintage.parser_version,
            "acquisition_evidence_id": vintage.acquisition_evidence_id,
        }
    )
    if type(vintage.vintage_id) is not str or vintage.vintage_id != expected:
        raise GDPNowParsingError("parsed GDPNow vintage identity failed revalidation")
    if _ISSUED_PARSED_FINGERPRINTS.get(id(vintage)) != expected:
        raise GDPNowParsingError("unissued, reconstructed, or mutated parsed GDPNow vintage")


def _extract_candidate(match: re.Match[str]) -> tuple[date, str, Decimal]:
    """Extract and cross-validate the (date, target_quarter, value) one entry claims."""
    pub_month = match.group("pub_month")
    sent_month = match.group("sent_month")
    if pub_month not in _MONTH_NUMBERS or sent_month not in _MONTH_NUMBERS:
        raise GDPNowParsingError("GDPNow commentary date uses an unrecognized month name")
    if pub_month != sent_month or match.group("pub_day") != match.group("sent_day"):
        raise GDPNowParsingError(
            "GDPNow commentary header date does not match the forecast sentence date"
        )
    try:
        publisher_stated_date = date(
            int(match.group("pub_year")),
            _MONTH_NUMBERS[pub_month],
            int(match.group("pub_day")),
        )
    except ValueError as exc:
        raise GDPNowParsingError("GDPNow commentary publisher-stated date is malformed") from exc
    target_quarter = f"{match.group('q_year')}-Q{_ORDINAL_QUARTER[match.group('ordinal')]}"
    gdpnow_value = Decimal(match.group("value"))
    return publisher_stated_date, target_quarter, gdpnow_value


def parse_gdpnow_commentary(evidence: GDPNowAcquisitionEvidence) -> ParsedGDPNowVintage:
    """Deterministically parse the newest GDPNow commentary entry from acquired evidence.

    `evidence` is independently re-validated here; the caller's claim that it is
    genuine positively-acquired evidence is never trusted. Fails closed on any
    ambiguity rather than guessing.
    """
    validate_gdpnow_acquisition_evidence(evidence)
    try:
        html = evidence.raw_body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GDPNowParsingError("GDPNow response body is not valid UTF-8 HTML") from exc

    first_header = _ANY_ENTRY_HEADER_RE.search(html)
    if first_header is None:
        raise GDPNowParsingError("no GDPNow commentary entry header found")

    matches = list(_ENTRY_RE.finditer(html))
    if not matches:
        raise GDPNowParsingError("no deterministically parseable GDPNow forecast entry found")

    newest = matches[0]
    if newest.start() != first_header.start():
        raise GDPNowParsingError(
            "newest GDPNow commentary entry is ambiguous: the topmost commentary header "
            "did not match the reviewed forecast grammar"
        )

    publisher_stated_date, target_quarter, gdpnow_value = _extract_candidate(newest)

    # Gather every entry claiming the exact same publisher-stated header date as the
    # newest entry (by raw header text) and require their parsed forecast tuples to
    # agree. Document order is never used as an implicit tie-breaker: if two
    # same-dated entries disagree, parsing fails closed rather than silently
    # preferring whichever happened to come first in the HTML. Entries that reduce
    # to the exact same (date, target_quarter, value) tuple are not ambiguous -- they
    # are the same forecast content published more than once (e.g. a duplicated or
    # mirrored block), not conflicting information, so they are deduplicated rather
    # than rejected.
    newest_header_key = (
        newest.group("pub_year"),
        newest.group("pub_month"),
        newest.group("pub_day"),
    )
    same_date_matches = (
        m
        for m in matches
        if (m.group("pub_year"), m.group("pub_month"), m.group("pub_day")) == newest_header_key
    )
    conflicting_tuples = {_extract_candidate(m) for m in same_date_matches}
    if len(conflicting_tuples) > 1:
        raise GDPNowParsingError(
            "newest GDPNow commentary entry is ambiguous: multiple entries sharing the "
            "newest publisher-stated date disagree on the parsed forecast"
        )

    vintage = ParsedGDPNowVintage(
        target_quarter=target_quarter,
        gdpnow_value=gdpnow_value,
        publisher_stated_date=publisher_stated_date,
        acquisition_evidence_id=evidence.content_hash,
        _capability=_PARSED_VINTAGE_CAPABILITY,
    )
    validate_parsed_gdpnow_vintage(vintage)
    return vintage


class UpdateClassification(StrEnum):
    NEW_UPDATE = "NEW_UPDATE"
    UNCHANGED_FORECAST = "UNCHANGED_FORECAST"
    PAGE_CHANGED_NON_FORECAST = "PAGE_CHANGED_NON_FORECAST"
    MALFORMED_OR_UNKNOWN = "MALFORMED_OR_UNKNOWN"


@dataclass(frozen=True, slots=True)
class GDPNowCapture:
    """One capture's parse outcome and raw-byte identity, for classification only."""

    parsed: ParsedGDPNowVintage | None
    raw_body_sha256: str


def _forecast_tuple(vintage: ParsedGDPNowVintage) -> tuple[str, date, Decimal]:
    return (vintage.target_quarter, vintage.publisher_stated_date, vintage.gdpnow_value)


def classify_gdpnow_update(
    *, previous: GDPNowCapture | None, current: GDPNowCapture
) -> UpdateClassification:
    """Classify `current` relative to `previous` using only the parsed forecast tuple.

    Raw-byte difference alone never proves a new forecast: a NEW_UPDATE requires
    the reviewed (target_quarter, publisher_stated_date, gdpnow_value) tuple to
    actually change. Multiple intra-quarter re-publications with an unchanged
    tuple are UNCHANGED_FORECAST, not independent scientific events.
    """
    if type(current) is not GDPNowCapture:
        raise GDPNowParsingError("current GDPNow capture must have exact type")
    if current.parsed is not None:
        validate_parsed_gdpnow_vintage(current.parsed)

    if current.parsed is None:
        if previous is not None and previous.raw_body_sha256 == current.raw_body_sha256:
            return UpdateClassification.MALFORMED_OR_UNKNOWN
        if previous is None:
            return UpdateClassification.MALFORMED_OR_UNKNOWN
        return UpdateClassification.PAGE_CHANGED_NON_FORECAST

    if previous is None or previous.parsed is None:
        return UpdateClassification.NEW_UPDATE

    validate_parsed_gdpnow_vintage(previous.parsed)
    if _forecast_tuple(previous.parsed) == _forecast_tuple(current.parsed):
        return UpdateClassification.UNCHANGED_FORECAST
    return UpdateClassification.NEW_UPDATE


_RECEIPT_ISSUANCE_CAPABILITY = object()
_ISSUED_RECEIPT_FINGERPRINTS: dict[int, str] = {}

_SHA256_HEX_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_TARGET_QUARTER_RE = re.compile(r"\A\d{4}-Q[1-4]\Z")

_RECEIPT_FIELD_NAMES = (
    "acquisition_evidence_id",
    "raw_body_sha256",
    "byte_count",
    "acquired_at",
    "transport_policy_identity",
    "parser_version",
    "target_quarter",
    "gdpnow_value",
    "publisher_stated_date",
    "research_only",
    "production_influence",
)


def _validate_receipt_semantic_fields(payload: Mapping[str, object]) -> dict[str, object]:
    """Positively validate every receipt field's canonical shape and pinned identities.

    This checks *semantics*, not just digest self-consistency: each field must be
    the exact canonical value a genuine acquisition+parse pair can produce, not
    merely internally consistent with a caller-chosen digest. Returns a
    normalized field dict suitable for `_receipt_digest_values`.
    """
    missing = [name for name in _RECEIPT_FIELD_NAMES if name not in payload]
    if missing:
        raise GDPNowParsingError(f"GDPNow capture receipt is missing required fields: {missing}")

    acquisition_evidence_id = payload["acquisition_evidence_id"]
    if type(acquisition_evidence_id) is not str or not _SHA256_HEX_RE.match(
        acquisition_evidence_id
    ):
        raise GDPNowParsingError("GDPNow capture receipt acquisition_evidence_id is malformed")

    raw_body_sha256 = payload["raw_body_sha256"]
    if type(raw_body_sha256) is not str or not _SHA256_HEX_RE.match(raw_body_sha256):
        raise GDPNowParsingError("GDPNow capture receipt raw_body_sha256 is malformed")

    byte_count = payload["byte_count"]
    if type(byte_count) is not int or isinstance(byte_count, bool) or byte_count <= 0:
        raise GDPNowParsingError("GDPNow capture receipt byte_count is malformed")

    acquired_at = payload["acquired_at"]
    if type(acquired_at) is not str:
        raise GDPNowParsingError("GDPNow capture receipt acquired_at is malformed")
    try:
        parsed_acquired_at = datetime.fromisoformat(acquired_at)
    except ValueError as exc:
        raise GDPNowParsingError(
            "GDPNow capture receipt acquired_at is not a valid ISO-8601 timestamp"
        ) from exc
    if (
        parsed_acquired_at.tzinfo is None
        or parsed_acquired_at.utcoffset() != timedelta(0)
        or parsed_acquired_at.isoformat() != acquired_at
    ):
        raise GDPNowParsingError("GDPNow capture receipt acquired_at is not canonical aware UTC")

    transport_policy_identity = payload["transport_policy_identity"]
    if (
        type(transport_policy_identity) is not str
        or transport_policy_identity != TRANSPORT_POLICY_IDENTITY
    ):
        raise GDPNowParsingError(
            "GDPNow capture receipt transport_policy_identity is not the canonical D1-G1 identity"
        )

    parser_version = payload["parser_version"]
    if type(parser_version) is not str or parser_version != PARSER_VERSION:
        raise GDPNowParsingError(
            "GDPNow capture receipt parser_version is not the reviewed parser version"
        )

    target_quarter = payload["target_quarter"]
    if type(target_quarter) is not str or not _TARGET_QUARTER_RE.match(target_quarter):
        raise GDPNowParsingError("GDPNow capture receipt target_quarter is malformed")

    gdpnow_value = payload["gdpnow_value"]
    if type(gdpnow_value) is not str:
        raise GDPNowParsingError("GDPNow capture receipt gdpnow_value is malformed")
    try:
        decimal_value = Decimal(gdpnow_value)
    except InvalidOperation as exc:
        raise GDPNowParsingError(
            "GDPNow capture receipt gdpnow_value is not a valid decimal"
        ) from exc
    if not decimal_value.is_finite() or str(decimal_value) != gdpnow_value:
        raise GDPNowParsingError(
            "GDPNow capture receipt gdpnow_value is not canonically serialized"
        )

    publisher_stated_date = payload["publisher_stated_date"]
    if type(publisher_stated_date) is not str:
        raise GDPNowParsingError("GDPNow capture receipt publisher_stated_date is malformed")
    try:
        parsed_date = date.fromisoformat(publisher_stated_date)
    except ValueError as exc:
        raise GDPNowParsingError(
            "GDPNow capture receipt publisher_stated_date is not a valid ISO date"
        ) from exc
    if parsed_date.isoformat() != publisher_stated_date:
        raise GDPNowParsingError("GDPNow capture receipt publisher_stated_date is not canonical")

    research_only = payload["research_only"]
    if type(research_only) is not bool or research_only is not True:
        raise GDPNowParsingError("GDPNow capture receipt research_only must be exactly true")

    production_influence = payload["production_influence"]
    if type(production_influence) is not str or production_influence != str(ZERO):
        raise GDPNowParsingError("GDPNow capture receipt production_influence must be exactly zero")

    return {
        "acquisition_evidence_id": acquisition_evidence_id,
        "raw_body_sha256": raw_body_sha256,
        "byte_count": byte_count,
        "acquired_at": acquired_at,
        "transport_policy_identity": transport_policy_identity,
        "parser_version": parser_version,
        "target_quarter": target_quarter,
        "gdpnow_value": gdpnow_value,
        "publisher_stated_date": publisher_stated_date,
        "research_only": research_only,
        "production_influence": production_influence,
    }


def _receipt_digest_values(values: Mapping[str, object]) -> str:
    return stable_hash(
        (
            RECEIPT_SCHEMA_VERSION,
            values["acquisition_evidence_id"],
            values["raw_body_sha256"],
            values["byte_count"],
            values["acquired_at"],
            values["transport_policy_identity"],
            values["parser_version"],
            values["target_quarter"],
            values["gdpnow_value"],
            values["publisher_stated_date"],
            values["research_only"],
            values["production_influence"],
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class GDPNowCaptureReceipt:
    """Issuer-controlled durable research receipt binding one acquisition+parse pair.

    Not publicly constructible from caller-supplied fields: the only production
    issuer is `build_gdpnow_capture_receipt`, which derives every field from a
    positively re-validated `GDPNowAcquisitionEvidence` and `ParsedGDPNowVintage`
    pair. `receipt_id` is always computed internally from the validated fields,
    never accepted as an argument.
    """

    receipt_id: str
    schema_version: str
    acquisition_evidence_id: str
    raw_body_sha256: str
    byte_count: int
    acquired_at: str
    transport_policy_identity: str
    parser_version: str
    target_quarter: str
    gdpnow_value: str
    publisher_stated_date: str
    research_only: bool
    production_influence: str

    def __init__(
        self,
        *,
        acquisition_evidence_id: str,
        raw_body_sha256: str,
        byte_count: int,
        acquired_at: str,
        transport_policy_identity: str,
        parser_version: str,
        target_quarter: str,
        gdpnow_value: str,
        publisher_stated_date: str,
        research_only: bool,
        production_influence: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _RECEIPT_ISSUANCE_CAPABILITY:
            raise GDPNowParsingError(
                "GDPNow capture receipt requires the reviewed receipt issuance capability"
            )
        fields = _validate_receipt_semantic_fields(
            {
                "acquisition_evidence_id": acquisition_evidence_id,
                "raw_body_sha256": raw_body_sha256,
                "byte_count": byte_count,
                "acquired_at": acquired_at,
                "transport_policy_identity": transport_policy_identity,
                "parser_version": parser_version,
                "target_quarter": target_quarter,
                "gdpnow_value": gdpnow_value,
                "publisher_stated_date": publisher_stated_date,
                "research_only": research_only,
                "production_influence": production_influence,
            }
        )
        receipt_id = _receipt_digest_values(fields)
        values: dict[str, object] = {
            **fields,
            "receipt_id": receipt_id,
            "schema_version": RECEIPT_SCHEMA_VERSION,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_RECEIPT_FINGERPRINTS[id(self)] = receipt_id


def build_gdpnow_capture_receipt(
    evidence: GDPNowAcquisitionEvidence, vintage: ParsedGDPNowVintage
) -> GDPNowCaptureReceipt:
    """Bind one acquisition and its deterministic parse into an immutable, issued receipt.

    Both inputs are independently re-validated here; the caller's claim that they
    are genuine issued objects is never trusted. This is the sole production
    issuer of `GDPNowCaptureReceipt`.
    """
    validate_gdpnow_acquisition_evidence(evidence)
    validate_parsed_gdpnow_vintage(vintage)
    if vintage.acquisition_evidence_id != evidence.content_hash:
        raise GDPNowParsingError("parsed vintage does not bind to the given acquisition evidence")
    receipt = GDPNowCaptureReceipt(
        acquisition_evidence_id=evidence.content_hash,
        raw_body_sha256=evidence.raw_body_sha256,
        byte_count=evidence.byte_count,
        acquired_at=evidence.acquired_at.isoformat(),
        transport_policy_identity=evidence.transport_policy_identity,
        parser_version=vintage.parser_version,
        target_quarter=vintage.target_quarter,
        gdpnow_value=str(vintage.gdpnow_value),
        publisher_stated_date=vintage.publisher_stated_date.isoformat(),
        research_only=True,
        production_influence=str(ZERO),
        _capability=_RECEIPT_ISSUANCE_CAPABILITY,
    )
    validate_gdpnow_capture_receipt(receipt)
    return receipt


def validate_gdpnow_capture_receipt(receipt: GDPNowCaptureReceipt) -> None:
    """Revalidate a receipt's semantic fields, digest, and issuance provenance."""
    if type(receipt) is not GDPNowCaptureReceipt:
        raise GDPNowParsingError("GDPNow capture receipt must have exact issued type")
    if type(receipt.schema_version) is not str or receipt.schema_version != RECEIPT_SCHEMA_VERSION:
        raise GDPNowParsingError("GDPNow capture receipt schema version changed")
    fields = _validate_receipt_semantic_fields(
        {name: getattr(receipt, name) for name in _RECEIPT_FIELD_NAMES}
    )
    expected = _receipt_digest_values(fields)
    if type(receipt.receipt_id) is not str or receipt.receipt_id != expected:
        raise GDPNowParsingError("GDPNow capture receipt identity failed revalidation")
    if _ISSUED_RECEIPT_FINGERPRINTS.get(id(receipt)) != expected:
        raise GDPNowParsingError("unissued, reconstructed, or mutated GDPNow capture receipt")


def write_gdpnow_capture_receipt(
    evidence: GDPNowAcquisitionEvidence,
    vintage: ParsedGDPNowVintage,
    *,
    directory: Path,
) -> Path:
    """Durably persist one acquisition+parse pair as a content-addressed receipt.

    Accepts only positively validated, issued `GDPNowAcquisitionEvidence` and
    `ParsedGDPNowVintage` -- never a caller-constructed `GDPNowCaptureReceipt` and
    never independently supplied raw bytes. The receipt is built and revalidated
    internally, immediately before persistence, and the exact raw bytes persisted
    are always `evidence.raw_body` itself, so there is no public path for
    caller-authored data (a forged receipt, or raw bytes from a different
    acquisition) to masquerade as a canonical GDPNow capture receipt.

    Both the raw artifact and the receipt are written under content-derived
    filenames and are never overwritten with different content: a second write
    of the same content is a no-op, and any attempt to write different content
    under the same content-derived name fails closed. There is no mutable
    "latest" file; discovering the newest receipt is a directory listing concern
    for the caller, not an authority this function creates.
    """
    validate_gdpnow_acquisition_evidence(evidence)
    validate_parsed_gdpnow_vintage(vintage)
    receipt = build_gdpnow_capture_receipt(evidence, vintage)
    validate_gdpnow_capture_receipt(receipt)
    if receipt.byte_count != len(evidence.raw_body):
        raise GDPNowParsingError(
            "receipt byte_count does not match the acquisition raw byte length"
        )

    raw_dir = directory / "raw"
    receipts_dir = directory / "receipts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{receipt.raw_body_sha256}.html"
    _write_content_addressed(raw_path, evidence.raw_body)

    receipt_payload = {name: getattr(receipt, name) for name in _RECEIPT_FIELD_NAMES}
    receipt_payload["receipt_id"] = receipt.receipt_id
    receipt_payload["schema_version"] = receipt.schema_version
    receipt_bytes = json.dumps(
        receipt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    receipt_path = receipts_dir / f"{receipt.receipt_id}.json"
    _write_content_addressed(receipt_path, receipt_bytes)
    return receipt_path


def reopen_gdpnow_capture_receipt(directory: Path, receipt_id: str) -> dict[str, object]:
    """Read back one persisted receipt and verify its internal self-consistency.

    This is a plain-filesystem integrity check, not a cryptographic proof of
    origin: it confirms the persisted receipt's own digest is self-consistent,
    every field is canonically well-formed and matches the pinned D1-G1 transport
    and parser identities, and the referenced raw artifact's SHA-256 matches.
    Anyone with write access to `directory` could construct a self-consistent
    file from scratch; this function does not and cannot prove who originally
    wrote it. A positive result means "this receipt has not been corrupted or
    tampered with since it was written in this self-consistent form", not fresh
    acquisition or parsing authority.
    """
    if type(receipt_id) is not str or not _SHA256_HEX_RE.match(receipt_id):
        raise GDPNowParsingError("receipt_id must be a canonical sha256 hex digest")
    receipt_path = directory / "receipts" / f"{receipt_id}.json"
    try:
        raw_json = receipt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GDPNowParsingError(f"cannot read persisted GDPNow receipt: {exc}") from exc
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise GDPNowParsingError(f"persisted GDPNow receipt is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GDPNowParsingError("persisted GDPNow receipt is not a JSON object")

    fields = _validate_receipt_semantic_fields(payload)
    expected_digest = _receipt_digest_values(fields)
    if payload.get("receipt_id") != expected_digest:
        raise GDPNowParsingError("persisted GDPNow receipt digest failed reopen verification")
    if expected_digest != receipt_id:
        raise GDPNowParsingError(
            "persisted GDPNow receipt is not stored at its own content address"
        )
    if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise GDPNowParsingError("persisted GDPNow receipt schema version changed")

    raw_path = directory / "raw" / f"{fields['raw_body_sha256']}.html"
    try:
        raw_bytes = raw_path.read_bytes()
    except OSError as exc:
        raise GDPNowParsingError(f"cannot read persisted GDPNow raw artifact: {exc}") from exc
    if hashlib.sha256(raw_bytes).hexdigest() != fields["raw_body_sha256"]:
        raise GDPNowParsingError("persisted GDPNow raw artifact does not match its bound SHA-256")

    return dict(fields, receipt_id=expected_digest, schema_version=RECEIPT_SCHEMA_VERSION)


def _write_content_addressed(path: Path, content: bytes) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing != content:
            raise GDPNowParsingError(
                f"refusing to overwrite existing content-addressed artifact at {path}"
            )
        return
    path.write_bytes(content)
