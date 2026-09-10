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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from services.forecasting.gdpnow_source_acquisition import (
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

    pub_month = newest.group("pub_month")
    sent_month = newest.group("sent_month")
    if pub_month not in _MONTH_NUMBERS or sent_month not in _MONTH_NUMBERS:
        raise GDPNowParsingError("GDPNow commentary date uses an unrecognized month name")
    if pub_month != sent_month or newest.group("pub_day") != newest.group("sent_day"):
        raise GDPNowParsingError(
            "GDPNow commentary header date does not match the forecast sentence date"
        )

    try:
        publisher_stated_date = date(
            int(newest.group("pub_year")),
            _MONTH_NUMBERS[pub_month],
            int(newest.group("pub_day")),
        )
    except ValueError as exc:
        raise GDPNowParsingError("GDPNow commentary publisher-stated date is malformed") from exc

    target_quarter = f"{newest.group('q_year')}-Q{_ORDINAL_QUARTER[newest.group('ordinal')]}"
    gdpnow_value = Decimal(newest.group("value"))

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


@dataclass(frozen=True, slots=True)
class GDPNowCaptureReceipt:
    """Narrowest durable research receipt binding one acquisition+parse pair."""

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


def _receipt_digest_values(values: dict[str, object]) -> str:
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


def build_gdpnow_capture_receipt(
    evidence: GDPNowAcquisitionEvidence, vintage: ParsedGDPNowVintage
) -> GDPNowCaptureReceipt:
    """Bind one acquisition and its deterministic parse into an immutable receipt."""
    validate_gdpnow_acquisition_evidence(evidence)
    validate_parsed_gdpnow_vintage(vintage)
    if vintage.acquisition_evidence_id != evidence.content_hash:
        raise GDPNowParsingError("parsed vintage does not bind to the given acquisition evidence")
    values: dict[str, object] = {
        "acquisition_evidence_id": evidence.content_hash,
        "raw_body_sha256": evidence.raw_body_sha256,
        "byte_count": evidence.byte_count,
        "acquired_at": evidence.acquired_at.isoformat(),
        "transport_policy_identity": evidence.transport_policy_identity,
        "parser_version": vintage.parser_version,
        "target_quarter": vintage.target_quarter,
        "gdpnow_value": str(vintage.gdpnow_value),
        "publisher_stated_date": vintage.publisher_stated_date.isoformat(),
        "research_only": True,
        "production_influence": str(ZERO),
    }
    receipt_id = _receipt_digest_values(values)
    return GDPNowCaptureReceipt(
        receipt_id=receipt_id,
        schema_version=RECEIPT_SCHEMA_VERSION,
        **values,  # type: ignore[arg-type]
    )


def validate_gdpnow_capture_receipt(receipt: GDPNowCaptureReceipt) -> None:
    """Recompute the receipt identity from its own bound fields and require a match."""
    if type(receipt) is not GDPNowCaptureReceipt:
        raise GDPNowParsingError("GDPNow capture receipt must have exact type")
    if receipt.schema_version != RECEIPT_SCHEMA_VERSION:
        raise GDPNowParsingError("GDPNow capture receipt schema version changed")
    if receipt.research_only is not True or receipt.production_influence != str(ZERO):
        raise GDPNowParsingError("GDPNow capture receipt lost research-only/zero-influence binding")
    expected = _receipt_digest_values(
        {
            "acquisition_evidence_id": receipt.acquisition_evidence_id,
            "raw_body_sha256": receipt.raw_body_sha256,
            "byte_count": receipt.byte_count,
            "acquired_at": receipt.acquired_at,
            "transport_policy_identity": receipt.transport_policy_identity,
            "parser_version": receipt.parser_version,
            "target_quarter": receipt.target_quarter,
            "gdpnow_value": receipt.gdpnow_value,
            "publisher_stated_date": receipt.publisher_stated_date,
            "research_only": receipt.research_only,
            "production_influence": receipt.production_influence,
        }
    )
    if receipt.receipt_id != expected:
        raise GDPNowParsingError("GDPNow capture receipt identity failed revalidation")


def write_gdpnow_capture_receipt(
    receipt: GDPNowCaptureReceipt, raw_body: bytes, *, directory: Path
) -> Path:
    """Durably persist one receipt and its exact raw bytes, content-addressed.

    Both the raw artifact and the receipt are written under content-derived
    filenames and are never overwritten with different content: a second write
    of the same content is a no-op, and any attempt to write different content
    under the same content-derived name fails closed. There is no mutable
    "latest" file; discovering the newest receipt is a directory listing
    concern for the caller, not an authority this function creates.
    """
    validate_gdpnow_capture_receipt(receipt)
    if hashlib.sha256(raw_body).hexdigest() != receipt.raw_body_sha256:
        raise GDPNowParsingError("raw bytes do not match the receipt's bound raw_body_sha256")

    raw_dir = directory / "raw"
    receipts_dir = directory / "receipts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{receipt.raw_body_sha256}.html"
    _write_content_addressed(raw_path, raw_body)

    receipt_payload = {
        "receipt_id": receipt.receipt_id,
        "schema_version": receipt.schema_version,
        "acquisition_evidence_id": receipt.acquisition_evidence_id,
        "raw_body_sha256": receipt.raw_body_sha256,
        "byte_count": receipt.byte_count,
        "acquired_at": receipt.acquired_at,
        "transport_policy_identity": receipt.transport_policy_identity,
        "parser_version": receipt.parser_version,
        "target_quarter": receipt.target_quarter,
        "gdpnow_value": receipt.gdpnow_value,
        "publisher_stated_date": receipt.publisher_stated_date,
        "research_only": receipt.research_only,
        "production_influence": receipt.production_influence,
        "raw_artifact_path": str(raw_path.relative_to(directory)),
    }
    receipt_bytes = json.dumps(
        receipt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    receipt_path = receipts_dir / f"{receipt.receipt_id}.json"
    _write_content_addressed(receipt_path, receipt_bytes)
    return receipt_path


def _write_content_addressed(path: Path, content: bytes) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing != content:
            raise GDPNowParsingError(
                f"refusing to overwrite existing content-addressed artifact at {path}"
            )
        return
    path.write_bytes(content)
