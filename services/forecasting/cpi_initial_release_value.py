"""Reviewed CPI-E1-P6 initial-release value evidence.

This is intentionally a one-profile parser.  It consumes only a complete,
already acquisition-bound P4/P5A issuance; it never accepts caller-supplied
values, bytes, dates, or source identities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from html.parser import HTMLParser

from services.forecasting.cpi_evidence_issuer import (
    CPIAcquisitionBoundIssuance,
    CPIManualAcquisitionBoundIssuance,
)
from services.forecasting.cpi_manual_acquisition import (
    ACQUISITION_MODE as MANUAL_ACQUISITION_MODE,
)
from services.forecasting.cpi_manual_acquisition import (
    validate_cpi_bls_manual_acquisition_evidence,
)
from services.forecasting.cpi_source_acquisition import (
    validate_cpi_bls_acquisition_evidence,
)
from services.market_universe.domain import stable_hash

PARSER_POLICY_VERSION = "cpi-e1-p6-reviewed-initial-release-value-parser-v1"
SCHEMA_VERSION = "cpi-e1-p6-cpi-u-sa-mom-value-observation-v1"
ZERO = Decimal("0")

_ISSUANCE_CAPABILITY = object()
_ISSUED_FINGERPRINTS: dict[int, str] = {}
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "JANUARY",
            "FEBRUARY",
            "MARCH",
            "APRIL",
            "MAY",
            "JUNE",
            "JULY",
            "AUGUST",
            "SEPTEMBER",
            "OCTOBER",
            "NOVEMBER",
            "DECEMBER",
        ),
        1,
    )
}
_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)
_ABBREVIATIONS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_NUMBER = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?|-\s*(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"


class CPIInitialReleaseValueError(ValueError):
    """The exact reviewed CPI value evidence failed closed."""


class CPIUnit(StrEnum):
    PERCENT = "percent"


class CPISeasonalBasis(StrEnum):
    SA = "SA"


class CPIHorizon(StrEnum):
    MOM = "MoM"


class CPIBasket(StrEnum):
    ALL_ITEMS = "ALL_ITEMS"


class CPIPopulation(StrEnum):
    CPI_U = "CPI_U"


class CPIGeography(StrEnum):
    US_CITY_AVERAGE = "US_CITY_AVERAGE"


@dataclass(frozen=True, slots=True)
class ParsedCPIInitialReleaseValue:
    reference_year: int
    reference_month: int
    value: Decimal
    narrative_value: Decimal
    table_value: Decimal
    parser_policy_version: str = PARSER_POLICY_VERSION
    schema_version: str = SCHEMA_VERSION


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple[str, ...]]] = []
        self.rows: list[tuple[str, ...]] = []
        self._table: list[tuple[str, ...]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._ignored += 1
        elif not self._ignored and tag == "table":
            self._table = []
        elif not self._ignored and tag == "tr":
            self._row = []
        elif not self._ignored and tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
        elif not self._ignored and tag in {"td", "th"} and self._row is not None:
            if self._cell is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif not self._ignored and tag == "tr" and self._row is not None:
            self.rows.append(tuple(self._row))
            if self._table is not None:
                self._table.append(tuple(self._row))
            self._row = None
        elif not self._ignored and tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if not self._ignored and self._cell is not None:
            self._cell.append(data)


def _visible_text(body: bytes) -> str:
    try:
        parts: list[str] = []

        class TextParser(HTMLParser):
            def handle_data(self, data: str) -> None:
                parts.append(data)

        parser = TextParser(convert_charrefs=True)
        parser.feed(body.decode("utf-8", errors="strict"))
        parser.close()
        return " ".join(" ".join(parts).split())
    except (UnicodeDecodeError, ValueError) as exc:
        raise CPIInitialReleaseValueError("CPI artifact is not valid UTF-8 HTML") from exc


def _decimal(text: str) -> Decimal:
    token = text.replace(" ", "")
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]", token):
        raise CPIInitialReleaseValueError("CPI value is not an exact one-decimal number")
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise CPIInitialReleaseValueError("CPI value is malformed") from exc
    if value.as_tuple().exponent != -1:
        raise CPIInitialReleaseValueError("CPI value precision is not one decimal")
    return value


def _release_period(text: str) -> tuple[int, int]:
    matches = tuple(
        re.finditer(rf"CONSUMER PRICE INDEX\s*-\s*({_MONTH_PATTERN})\s+(20[0-9]{{2}})", text, re.I)
    )
    if len(matches) != 1:
        raise CPIInitialReleaseValueError("CPI release headline reference month is missing")
    match = matches[0]
    return int(match.group(2)), _MONTHS[match.group(1).upper()]


def _parse_narrative(text: str) -> tuple[int, int, Decimal]:
    matches = tuple(
        re.finditer(
            rf"The Consumer Price Index for All Urban Consumers \(CPI-U\) increased "
            rf"(?P<value>{_NUMBER}) percent on a seasonally adjusted basis in "
            rf"(?P<month>{_MONTH_PATTERN})",
            text,
            re.I,
        )
    )
    if len(matches) != 1:
        raise CPIInitialReleaseValueError("reviewed CPI-U SA monthly narrative is missing")
    match = matches[0]
    value = _decimal(match.group("value"))
    return 0, _MONTHS[match.group("month").upper()], value


def _parse_table(body: bytes, expected_year: int, expected_month: int) -> Decimal:
    parser = _TableParser()
    try:
        parser.feed(body.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeDecodeError, ValueError) as exc:
        raise CPIInitialReleaseValueError("CPI artifact table HTML is malformed") from exc
    text = _visible_text(body)
    titles = re.findall(
        r"Table A\. Percent changes in CPI for All Urban Consumers \(CPI-U\): U\.S\. city average",
        text,
        re.I,
    )
    if len(titles) != 1:
        raise CPIInitialReleaseValueError("exactly one canonical Table A is required")
    candidates = [
        table
        for table in parser.tables
        if any(
            any(
                "Seasonally adjusted changes from preceding month".casefold() in cell.casefold()
                for cell in row
            )
            for row in table
        )
    ]
    if len(candidates) != 1:
        raise CPIInitialReleaseValueError(
            "exactly one canonical seasonal Table A structure is required"
        )
    rows = candidates[0]
    header_index = next(
        (
            i
            for i, row in enumerate(rows)
            if any(
                "Seasonally adjusted changes from preceding month".casefold() in cell.casefold()
                for cell in row
            )
        ),
        None,
    )
    if header_index is None or header_index + 1 >= len(rows):
        raise CPIInitialReleaseValueError("Table A seasonal-adjustment header is missing")
    month_cells = rows[header_index + 1]
    periods: list[tuple[int, int]] = []
    for cell in month_cells:
        match = re.fullmatch(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?(?:\s+)?(20[0-9]{2})", cell, re.I
        )
        if match:
            periods.append((int(match.group(2)), _ABBREVIATIONS[match.group(1)[:3].title()]))
    if not periods or periods[-1] != (expected_year, expected_month):
        raise CPIInitialReleaseValueError(
            "Table A final/current month does not match release month"
        )
    all_items = [
        row for row in rows[header_index + 2 :] if row and row[0].casefold() == "all items"
    ]
    if len(all_items) != 1:
        raise CPIInitialReleaseValueError("exactly one Table A All items row is required")
    values = all_items[0][1:]
    if len(values) != len(periods) + 1:
        raise CPIInitialReleaseValueError("Table A All items row has unexpected columns")
    if any(
        value.strip() == ""
        or value.strip() == "-"
        or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]", value.replace(" ", ""))
        for value in values
    ):
        raise CPIInitialReleaseValueError("Table A contains missing or malformed monthly values")
    return _decimal(values[-2])


def parse_cpi_initial_release_value(body: bytes) -> ParsedCPIInitialReleaseValue:
    """Parse both independent headline representations from exact frozen bytes."""
    if type(body) is not bytes or not body:
        raise CPIInitialReleaseValueError("exact non-empty CPI HTML bytes are required")
    text = _visible_text(body)
    year, month = _release_period(text)
    narrative_year, narrative_month, narrative_value = _parse_narrative(text)
    if narrative_year not in {0, year} or narrative_month != month:
        raise CPIInitialReleaseValueError(
            "narrative reference month disagrees with release headline"
        )
    table_value = _parse_table(body, year, month)
    if table_value != narrative_value:
        raise CPIInitialReleaseValueError("CPI narrative and Table A values disagree")
    return ParsedCPIInitialReleaseValue(year, month, narrative_value, narrative_value, table_value)


def _validate_issuance(
    issuance: CPIAcquisitionBoundIssuance | CPIManualAcquisitionBoundIssuance,
) -> tuple[bytes, str, str, str, str, str, str]:
    if type(issuance) is CPIAcquisitionBoundIssuance:
        acquisition = issuance.acquisition_evidence
        validate_cpi_bls_acquisition_evidence(acquisition)
        mode = "AUTOMATED_HTTPS"
        acquisition_policy = acquisition.transport_policy_identity
        raw_body = acquisition.raw_body
        raw_hash = acquisition.raw_body_sha256
        acquisition_id = acquisition.evidence_id
    elif type(issuance) is CPIManualAcquisitionBoundIssuance:
        manual_acquisition = issuance.acquisition_evidence
        validate_cpi_bls_manual_acquisition_evidence(manual_acquisition)
        mode = MANUAL_ACQUISITION_MODE
        acquisition_policy = manual_acquisition.manual_policy_identity
        raw_body = manual_acquisition.raw_body
        raw_hash = manual_acquisition.raw_body_sha256
        acquisition_id = manual_acquisition.evidence_id
    else:
        raise CPIInitialReleaseValueError("P6 requires an exact P4 or P5A bound issuance")
    if (
        issuance.release_artifact.raw_artifact != raw_body
        or issuance.release_artifact.raw_artifact_sha256 != raw_hash
    ):
        raise CPIInitialReleaseValueError("release artifact is not bound to acquisition bytes")
    if issuance.release_artifact.artifact_id != issuance.publication_evidence.source_artifact_id:
        raise CPIInitialReleaseValueError("publication evidence is not bound to release artifact")
    if (
        issuance.release_artifact.p1_authority_identity
        != issuance.publication_evidence.p1_authority_identity
        or issuance.release_artifact.p1_policy_identity
        != issuance.publication_evidence.p1_policy_identity
    ):
        raise CPIInitialReleaseValueError("P1 authority binding is inconsistent")
    return (
        raw_body,
        acquisition_id,
        mode,
        acquisition_policy,
        issuance.release_artifact.artifact_id,
        issuance.publication_evidence.evidence_id,
        issuance.timing_evidence_identity,
    )


def _digest(values: tuple[object, ...]) -> str:
    canonical = tuple(
        str(item)
        if isinstance(
            item,
            (
                Decimal,
                CPIUnit,
                CPISeasonalBasis,
                CPIHorizon,
                CPIBasket,
                CPIPopulation,
                CPIGeography,
            ),
        )
        else item
        for item in values
    )
    return stable_hash(canonical)


@dataclass(frozen=True, slots=True, init=False)
class CPIInitialReleaseObservation:
    acquisition_evidence_id: str
    acquisition_mode: str
    acquisition_policy_identity: str
    raw_body_sha256: str
    release_artifact_id: str
    p1_authority_identity: str
    p1_policy_identity: str
    publication_evidence_id: str
    publication_timing_evidence_id: str
    reference_year: int
    reference_month: int
    value: Decimal
    unit: CPIUnit
    seasonal_basis: CPISeasonalBasis
    horizon: CPIHorizon
    basket: CPIBasket
    population: CPIPopulation
    geography: CPIGeography
    precision: int
    parser_policy_version: str
    schema_version: str
    observation_id: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        issuance: CPIAcquisitionBoundIssuance | CPIManualAcquisitionBoundIssuance,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _ISSUANCE_CAPABILITY:
            raise CPIInitialReleaseValueError("P6 observation requires reviewed issuer capability")
        body, acquisition_id, mode, policy, _artifact_id, publication_id, timing_id = (
            _validate_issuance(issuance)
        )
        parsed = parse_cpi_initial_release_value(body)
        artifact = issuance.release_artifact
        values: dict[str, object] = {
            "acquisition_evidence_id": acquisition_id,
            "acquisition_mode": mode,
            "acquisition_policy_identity": policy,
            "raw_body_sha256": artifact.raw_artifact_sha256,
            "release_artifact_id": artifact.artifact_id,
            "p1_authority_identity": artifact.p1_authority_identity,
            "p1_policy_identity": artifact.p1_policy_identity,
            "publication_evidence_id": publication_id,
            "publication_timing_evidence_id": timing_id,
            "reference_year": parsed.reference_year,
            "reference_month": parsed.reference_month,
            "value": parsed.value,
            "unit": CPIUnit.PERCENT,
            "seasonal_basis": CPISeasonalBasis.SA,
            "horizon": CPIHorizon.MOM,
            "basket": CPIBasket.ALL_ITEMS,
            "population": CPIPopulation.CPI_U,
            "geography": CPIGeography.US_CITY_AVERAGE,
            "precision": 1,
            "parser_policy_version": PARSER_POLICY_VERSION,
            "schema_version": SCHEMA_VERSION,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _digest((SCHEMA_VERSION, *values.values()))
        values["observation_id"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_FINGERPRINTS[id(self)] = digest


def issue_cpi_initial_release_observation(
    issuance: CPIAcquisitionBoundIssuance | CPIManualAcquisitionBoundIssuance,
) -> CPIInitialReleaseObservation:
    """Derive trusted value evidence from an already validated P4/P5A receipt."""
    return CPIInitialReleaseObservation(issuance=issuance, _capability=_ISSUANCE_CAPABILITY)


def validate_cpi_initial_release_observation(observation: CPIInitialReleaseObservation) -> None:
    if type(observation) is not CPIInitialReleaseObservation:
        raise CPIInitialReleaseValueError("P6 observation has wrong exact runtime type")
    fields = (
        observation.acquisition_evidence_id,
        observation.acquisition_mode,
        observation.acquisition_policy_identity,
        observation.raw_body_sha256,
        observation.release_artifact_id,
        observation.p1_authority_identity,
        observation.p1_policy_identity,
        observation.publication_evidence_id,
        observation.publication_timing_evidence_id,
        observation.reference_year,
        observation.reference_month,
        observation.value,
        observation.unit,
        observation.seasonal_basis,
        observation.horizon,
        observation.basket,
        observation.population,
        observation.geography,
        observation.precision,
        observation.parser_policy_version,
        observation.schema_version,
        observation.research_only,
        observation.production_influence,
    )
    expected = _digest((SCHEMA_VERSION, *fields))
    if (
        _ISSUED_FINGERPRINTS.get(id(observation)) != expected
        or observation.observation_id != expected
    ):
        raise CPIInitialReleaseValueError("unissued, reconstructed, or mutated P6 observation")
    if (
        observation.value.as_tuple().exponent != -1
        or observation.precision != 1
        or observation.unit is not CPIUnit.PERCENT
        or observation.seasonal_basis is not CPISeasonalBasis.SA
        or observation.horizon is not CPIHorizon.MOM
        or observation.basket is not CPIBasket.ALL_ITEMS
        or observation.population is not CPIPopulation.CPI_U
        or observation.geography is not CPIGeography.US_CITY_AVERAGE
        or observation.research_only is not True
        or observation.production_influence != ZERO
    ):
        raise CPIInitialReleaseValueError("P6 observation domain or safety flags are invalid")
