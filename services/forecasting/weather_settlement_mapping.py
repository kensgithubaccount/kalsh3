"""M27C Part 2C1 research-only TWC settlement mapping evidence.

This boundary intentionally does not fetch TWC data or alter the weather model.  It
represents only evidence whose provenance has been independently established and
keeps Kalshi settlement-implied intervals separate from point observations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlsplit

from services.market_universe.domain import stable_hash

from .domain import ForecastError

POLICY_VERSION = "m27c-part2c1-twc-settlement-mapping-v1"
ZERO = Decimal("0")
MAPPING_EVIDENCE_END = date(2026, 7, 31)
KALSHI_SOURCE = "The Weather Company"
KALSHI_SERIES = "CLIMDW"
KALSHI_LOCATION = "Chicago"
GHCND_STATION = "USW00014819"
TWC_OFFICIAL_HOSTS = frozenset(
    {"ibm.com", "www.ibm.com", "api.ibm.com", "weather.com", "api.weather.com"}
)
_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
_CAPABILITY = object()


class EvidenceClass(StrEnum):
    SETTLEMENT_VINTAGED = "settlement-vintaged"
    CURRENT_HISTORICAL_SNAPSHOT = "current-historical-snapshot"
    KALSHI_SETTLEMENT_IMPLIED = "kalshi-settlement-implied"
    GHCN_COMPARISON = "ghcn-comparison"


class MappingResearchStatus(StrEnum):
    NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE = "NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE"
    KALSHI_SETTLEMENT_CONSISTENCY_EVIDENCE = "KALSHI_SETTLEMENT_CONSISTENCY_EVIDENCE"
    TWC_SETTLEMENT_VINTAGED_EVIDENCE = "TWC_SETTLEMENT_VINTAGED_EVIDENCE"


class Consistency(StrEnum):
    GHCND_CONSISTENT_WITH_SETTLEMENT = "GHCND_CONSISTENT_WITH_SETTLEMENT"
    GHCND_INCONSISTENT_WITH_SETTLEMENT = "GHCND_INCONSISTENT_WITH_SETTLEMENT"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_CONTRACT_STRUCTURE = "INSUFFICIENT_CONTRACT_STRUCTURE"


@dataclass(frozen=True, slots=True, init=False)
class KalshiSettlementAuthorityEvidence:
    series: str
    settlement_source: str
    location: str
    measurement: str
    source_url: str
    contract_url: str | None
    contract_terms_url: str | None
    acquired_at: datetime
    raw_sha256: str
    parser_policy_version: str
    evidence_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ForecastError("settlement authority evidence is not caller-constructible")
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> KalshiSettlementAuthorityEvidence:
        _keys(
            value,
            {
                "series",
                "settlement_source",
                "location",
                "measurement",
                "source_url",
                "contract_url",
                "contract_terms_url",
                "acquired_at",
                "raw_sha256",
                "parser_policy_version",
            },
        )
        series = _text(value.get("series"), "series")
        source = _text(value.get("settlement_source"), "settlement_source")
        location = _text(value.get("location"), "location")
        measurement = _text(value.get("measurement"), "measurement")
        if (series, source, location, measurement) != (
            KALSHI_SERIES,
            KALSHI_SOURCE,
            KALSHI_LOCATION,
            "DAILY_MAX",
        ):
            raise ForecastError("Kalshi CLIMDW authority mismatch")
        source_url = _https_url(value.get("source_url"), "source_url")
        acquired_at = _datetime(value.get("acquired_at"))
        raw_sha256 = _sha(value.get("raw_sha256"))
        policy = _text(value.get("parser_policy_version"), "parser_policy_version")
        identity = stable_hash(
            (
                "kalshi-settlement-authority-v1",
                series,
                source,
                location,
                measurement,
                source_url,
                raw_sha256,
                policy,
            )
        )
        return cls(
            _capability=_CAPABILITY,
            series=series,
            settlement_source=source,
            location=location,
            measurement=measurement,
            source_url=source_url,
            contract_url=_optional_url(value.get("contract_url")),
            contract_terms_url=_optional_url(value.get("contract_terms_url")),
            acquired_at=acquired_at,
            raw_sha256=raw_sha256,
            parser_policy_version=policy,
            evidence_identity=identity,
            research_only=True,
            production_influence=ZERO,
        )


@dataclass(frozen=True, slots=True, init=False)
class TWCValueEvidence:
    target_date: date
    value_f: Decimal
    evidence_class: EvidenceClass
    product_identity: str
    station_or_location: str
    source_url: str
    acquired_at: datetime
    raw_sha256: str
    parser_policy_version: str
    evidence_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ForecastError("TWC value evidence is not caller-constructible")
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> TWCValueEvidence:
        _keys(
            value,
            {
                "target_date",
                "value_f",
                "evidence_class",
                "product_identity",
                "station_or_location",
                "source_url",
                "acquired_at",
                "raw_sha256",
                "parser_policy_version",
            },
        )
        target = _date(value.get("target_date"))
        if target > MAPPING_EVIDENCE_END:
            raise ForecastError("prospective or August mapping evidence is prohibited")
        try:
            evidence_class = EvidenceClass(_text(value.get("evidence_class"), "evidence_class"))
        except ValueError as exc:
            raise ForecastError("unsupported TWC evidence class") from exc
        if evidence_class not in {
            EvidenceClass.SETTLEMENT_VINTAGED,
            EvidenceClass.CURRENT_HISTORICAL_SNAPSHOT,
        }:
            raise ForecastError("TWC value evidence class is not authoritative")
        source_url = _https_url(value.get("source_url"), "source_url")
        if (urlsplit(source_url).hostname or "") not in TWC_OFFICIAL_HOSTS:
            raise ForecastError("unofficial data cannot be authoritative TWC evidence")
        product = _text(value.get("product_identity"), "product_identity")
        station = _text(value.get("station_or_location"), "station_or_location")
        value_f = _decimal(value.get("value_f"), "value_f")
        acquired_at = _datetime(value.get("acquired_at"))
        raw_sha256 = _sha(value.get("raw_sha256"))
        policy = _text(value.get("parser_policy_version"), "parser_policy_version")
        identity = stable_hash(
            (
                "twc-value-v1",
                target.isoformat(),
                str(value_f),
                evidence_class.value,
                product,
                station,
                source_url,
                raw_sha256,
                policy,
            )
        )
        return cls(
            _capability=_CAPABILITY,
            target_date=target,
            value_f=value_f,
            evidence_class=evidence_class,
            product_identity=product,
            station_or_location=station,
            source_url=source_url,
            acquired_at=acquired_at,
            raw_sha256=raw_sha256,
            parser_policy_version=policy,
            evidence_identity=identity,
            research_only=True,
            production_influence=ZERO,
        )


@dataclass(frozen=True, slots=True)
class SettlementImpliedObservation:
    target_date: date
    comparator: str
    lower_f: Decimal | None
    upper_f: Decimal | None
    winning: bool
    source_identity: str
    research_only: bool = True
    production_influence: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.target_date > MAPPING_EVIDENCE_END:
            raise ForecastError("prospective or August settlement evidence is prohibited")
        if not self.research_only or self.production_influence != ZERO:
            raise ForecastError("settlement-implied evidence must have zero production influence")


@dataclass(frozen=True, slots=True)
class GHCNComparisonObservation:
    target_date: date
    ghcn_value_f: Decimal
    evidence_identity: str
    evidence_class: EvidenceClass = EvidenceClass.GHCN_COMPARISON
    research_only: bool = True
    production_influence: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.target_date > MAPPING_EVIDENCE_END:
            raise ForecastError("prospective or August GHCN evidence is prohibited")
        if not self.research_only or self.production_influence != ZERO:
            raise ForecastError("GHCN comparison evidence must have zero production influence")


def classify_ghcn_against_settlement(
    observation: SettlementImpliedObservation, ghcn: GHCNComparisonObservation
) -> Consistency:
    if observation.target_date != ghcn.target_date:
        raise ForecastError("settlement and GHCN dates do not match")
    if not observation.winning:
        return Consistency.INSUFFICIENT_CONTRACT_STRUCTURE
    if (
        observation.comparator == "RANGE"
        and observation.lower_f is not None
        and observation.upper_f is not None
    ):
        return (
            Consistency.GHCND_CONSISTENT_WITH_SETTLEMENT
            if observation.lower_f <= ghcn.ghcn_value_f <= observation.upper_f
            else Consistency.GHCND_INCONSISTENT_WITH_SETTLEMENT
        )
    if observation.comparator == "GT" and observation.lower_f is not None:
        return (
            Consistency.GHCND_CONSISTENT_WITH_SETTLEMENT
            if ghcn.ghcn_value_f > observation.lower_f
            else Consistency.GHCND_INCONSISTENT_WITH_SETTLEMENT
        )
    if observation.comparator == "LT" and observation.lower_f is not None:
        return (
            Consistency.GHCND_CONSISTENT_WITH_SETTLEMENT
            if ghcn.ghcn_value_f < observation.lower_f
            else Consistency.GHCND_INCONSISTENT_WITH_SETTLEMENT
        )
    return Consistency.AMBIGUOUS


def _keys(value: Mapping[str, object], allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ForecastError(f"unknown consequential evidence keys: {sorted(unknown)}")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastError(f"invalid {field}")
    return value.strip()


def _date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ForecastError("invalid target date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ForecastError("invalid target date") from exc


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ForecastError("invalid acquired_at")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForecastError("invalid acquired_at") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ForecastError("acquired_at must be timezone-aware")
    return result.astimezone(UTC)


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ForecastError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ForecastError(f"invalid {field}") from exc
    if not result.is_finite():
        raise ForecastError(f"invalid {field}")
    return result


def _sha(value: object) -> str:
    result = _text(value, "raw_sha256")
    if not _HEX.fullmatch(result):
        raise ForecastError("malformed raw_sha256")
    return result


def _https_url(value: object, field: str) -> str:
    result = _text(value, field)
    parsed = urlsplit(result)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.port is not None
        or parsed.username
    ):
        raise ForecastError(f"{field} must be an HTTPS URL without credentials or explicit port")
    return result


def _optional_url(value: object) -> str | None:
    return None if value is None else _https_url(value, "optional URL")
