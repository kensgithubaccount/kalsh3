"""M28B settlement semantics and acquisition-bound Kalshi weather labels.

Semantic parsing and acquisition authority are intentionally separate. A caller-provided
mapping can be validated as a settlement-shaped row, but only an ``AcquisitionBoundMarketRow``
can produce canonical M28A ``SettlementLabel`` evidence.

This module performs no network I/O, reads no credentials or account state, mutates no
production state, and grants no risk, approval, execution, arm, burn, or order authority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from services.forecasting.daily_temperature import (
    SETTLEMENT_LOCATIONS,
    SETTLEMENT_SOURCE,
    SettlementLocation,
)
from services.historical_replay.archive import stable_hash

from .contracts import SettlementLabel, SettlementLabelManifest, TemporalSplit

PARSER_VERSION = "m28b-authoritative-weather-settlement-v4"
ACQUISITION_SCHEMA = "kalsh3.m28b.public-settled-weather-evidence.v3"
EVENT_ID_VERSION = "m28b-weather-event-identity-v2"
SETTLEMENT_MAPPING_VERSION = "m28b-weather-company-daily-temperature-mapping-v3"
PUBLIC_KALSHI_ORIGIN = "https://external-api.kalshi.com"
LEGACY_NWS_SOURCE = "according to the National Weather Service's Climatological Report (Daily)"
LABEL_AUTHORITY = (
    "Reviewed fixed-origin Kalshi public settlement evidence + exact Weather Company "
    "daily-temperature rule mapping"
)
_SUPPORTED_CURRENT_TWC_RULE_GRAMMARS = (
    "CLI_IDENTIFIER_IF",
    "LOCATION_ONLY_IF",
    "LOCATION_ONLY_RESOLVES",
)

_SETTLEMENT_MAPPING_MATERIAL = (
    SETTLEMENT_MAPPING_VERSION,
    SETTLEMENT_SOURCE,
    ("DAILY_MAX", "DAILY_MIN"),
    ("RANGE", "GT", "LT"),
    "degF",
    _SUPPORTED_CURRENT_TWC_RULE_GRAMMARS,
    tuple(
        sorted(
            (item.identifier, item.location, item.timezone)
            for item in SETTLEMENT_LOCATIONS.values()
        )
    ),
)
SETTLEMENT_MAPPING_ID = stable_hash(_SETTLEMENT_MAPPING_MATERIAL)

_CANDIDATE = re.compile(r"\b(?:maximum|minimum) temperature recorded at\b", re.IGNORECASE)
_PREDICATE = (
    r"(?:(?:between (?P<between_low>[+-]?(?:\d+(?:\.\d+)?|\.\d+))-"
    r"(?P<between_high>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:greater than (?P<greater>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:less than (?P<less>[+-]?(?:\d+(?:\.\d+)?|\.\d+))))"
)
_CURRENT_RULE_WITH_IDENTIFIER = re.compile(
    r"\AIf the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?)\s*\((?P<identifier>CLI[A-Z]+)\) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    + _PREDICATE
    + rf"° fahrenheit according to {re.escape(SETTLEMENT_SOURCE)}, then the market "
    r"resolves to Yes\.\Z"
)
_CURRENT_RULE_LOCATION_ONLY_IF = re.compile(
    r"\AIf the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    + _PREDICATE
    + rf"° fahrenheit according to {re.escape(SETTLEMENT_SOURCE)}, then the market "
    r"resolves to Yes\.\Z"
)
_CURRENT_RULE_LOCATION_ONLY_RESOLVES = re.compile(
    r"\AResolves Yes if the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    + _PREDICATE
    + rf"° fahrenheit according to {re.escape(SETTLEMENT_SOURCE)}\.\Z"
)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _build_location_authority(
    locations: Mapping[str, SettlementLocation],
) -> Mapping[str, SettlementLocation]:
    authority = {item.location: item for item in locations.values()}
    if len(authority) != len(locations):
        raise RuntimeError("reviewed settlement-location names must be unique")
    return MappingProxyType(authority)


_LOCATION_AUTHORITY = _build_location_authority(SETTLEMENT_LOCATIONS)


class HistoricalWeatherDatasetError(ValueError):
    """Historical weather evidence violates an M28B invariant."""


class EventPartition(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


class SettlementRegime(StrEnum):
    CURRENT_TWC = "CURRENT_TWC"
    LEGACY_NWS = "LEGACY_NWS"
    UNRELATED = "UNRELATED"


@dataclass(frozen=True, slots=True)
class ResolvedTemperatureContract:
    """Semantically validated finalized current-regime temperature contract."""

    contract_id: str
    event_id: str
    settlement_mapping_id: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    station_id: str
    location: str
    timezone: str
    measurement: str
    local_date: date
    comparator: str
    lower: Decimal
    upper: Decimal | None
    result: str
    realized_yes: int
    settlement_value_dollars: Decimal
    settlement_at: datetime
    rules_primary: str
    rules_hash: str
    source_row_hash: str
    content_hash: str

    def predicate(self, value: Decimal) -> bool:
        if self.comparator == "GT":
            return value > self.lower
        if self.comparator == "LT":
            return value < self.lower
        if self.comparator == "RANGE" and self.upper is not None:
            return self.lower <= value <= self.upper
        raise HistoricalWeatherDatasetError("unsupported resolved temperature comparator")

    @property
    def semantic_event_tuple(self) -> tuple[str, ...]:
        return (
            self.event_ticker,
            self.series_ticker,
            self.settlement_mapping_id,
            self.station_id,
            self.location,
            self.timezone,
            self.measurement,
            self.local_date.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class SettlementRowClassification:
    regime: SettlementRegime
    contract: ResolvedTemperatureContract | None
    reason: str


_PAGE_EVIDENCE_CAPABILITY = object()


@dataclass(frozen=True, slots=True, init=False)
class PublicPageEvidence:
    """Immutable fixed-origin page evidence derived only from exact response bytes."""

    request_path: str
    response_sha256: str
    page_number: int
    scope_series_ticker: str
    market_row_hashes: tuple[str, ...]
    acquisition_schema: str
    content_hash: str

    def __init__(
        self,
        *,
        request_path: str,
        response_sha256: str,
        page_number: int,
        scope_series_ticker: str,
        market_row_hashes: tuple[str, ...],
        _capability: object | None = None,
    ) -> None:
        if _capability is not _PAGE_EVIDENCE_CAPABILITY:
            raise HistoricalWeatherDatasetError(
                "public page evidence must be derived from exact response bytes"
            )
        if page_number < 1:
            raise HistoricalWeatherDatasetError("page evidence number must be positive")
        if not _SHA256.fullmatch(response_sha256):
            raise HistoricalWeatherDatasetError("page evidence response hash is invalid")
        scope = scope_series_ticker.strip()
        if not scope:
            raise HistoricalWeatherDatasetError("authoritative page evidence requires series scope")
        if request_path.startswith("/trade-api/v2/historical/markets?"):
            partition = "ARCHIVE"
        elif request_path.startswith("/trade-api/v2/markets?"):
            partition = "RECENT_SETTLED"
        else:
            raise HistoricalWeatherDatasetError(
                "page evidence path is outside reviewed market reads"
            )
        query = parse_qs(urlsplit(request_path).query, keep_blank_values=True)
        if query.get("series_ticker") != [scope]:
            raise HistoricalWeatherDatasetError("page evidence series scope does not match request")
        if partition == "RECENT_SETTLED" and query.get("status") != ["settled"]:
            raise HistoricalWeatherDatasetError("recent page evidence is not settlement-scoped")
        normalized_hashes = tuple(sorted(market_row_hashes))
        if len(set(normalized_hashes)) != len(normalized_hashes):
            raise HistoricalWeatherDatasetError("page evidence contains duplicate market rows")
        digest = stable_hash(
            (
                ACQUISITION_SCHEMA,
                PUBLIC_KALSHI_ORIGIN,
                partition,
                request_path,
                response_sha256,
                page_number,
                scope,
                normalized_hashes,
            )
        )
        object.__setattr__(self, "request_path", request_path)
        object.__setattr__(self, "response_sha256", response_sha256)
        object.__setattr__(self, "page_number", page_number)
        object.__setattr__(self, "scope_series_ticker", scope)
        object.__setattr__(self, "market_row_hashes", normalized_hashes)
        object.__setattr__(self, "acquisition_schema", ACQUISITION_SCHEMA)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True)
class AcquisitionBoundMarketRow:
    """JSON row immutably bound to one reviewed public response page."""

    row_json: str
    page_evidence: PublicPageEvidence
    market_ticker: str = field(init=False)
    market_row_hash: str = field(init=False)
    settlement_evidence_id: str = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.row_json)
        except json.JSONDecodeError as exc:
            raise HistoricalWeatherDatasetError("acquisition-bound row is not JSON") from exc
        if not isinstance(value, dict):
            raise HistoricalWeatherDatasetError("acquisition-bound row must be an object")
        ticker = value.get("ticker")
        if not isinstance(ticker, str) or not ticker.strip():
            raise HistoricalWeatherDatasetError("acquisition-bound row ticker is missing")
        row_hash = stable_hash(value)
        if row_hash not in self.page_evidence.market_row_hashes:
            raise HistoricalWeatherDatasetError(
                "market row is not contained in the bound public response page"
            )
        evidence_id = stable_hash(
            (
                ACQUISITION_SCHEMA,
                PUBLIC_KALSHI_ORIGIN,
                self.page_evidence.content_hash,
                self.page_evidence.request_path,
                self.page_evidence.response_sha256,
                self.page_evidence.page_number,
                self.page_evidence.scope_series_ticker,
                ticker,
                row_hash,
                PARSER_VERSION,
                SETTLEMENT_MAPPING_ID,
            )
        )
        object.__setattr__(self, "market_ticker", ticker)
        object.__setattr__(self, "market_row_hash", row_hash)
        object.__setattr__(self, "settlement_evidence_id", evidence_id)
        object.__setattr__(self, "content_hash", evidence_id)

    @classmethod
    def from_page(
        cls, row: Mapping[str, Any], page_evidence: PublicPageEvidence
    ) -> AcquisitionBoundMarketRow:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        return cls(row_json=encoded, page_evidence=page_evidence)

    def row(self) -> dict[str, Any]:
        value = json.loads(self.row_json)
        if not isinstance(value, dict):
            raise HistoricalWeatherDatasetError("acquisition-bound row must remain an object")
        return cast(dict[str, Any], value)


@dataclass(frozen=True, slots=True)
class SettlementProvenance:
    market_ticker: str
    request_path: str
    response_sha256: str
    page_number: int
    market_row_hash: str
    parser_version: str
    settlement_mapping_id: str
    settlement_evidence_id: str
    label_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ResolvedWeatherEvent:
    """One outcome-independent event identity plus outcome-dependent snapshot evidence."""

    event_id: str
    event_ticker: str
    series_ticker: str
    settlement_mapping_id: str
    station_id: str
    location: str
    timezone: str
    measurement: str
    local_date: date
    contract_ids: tuple[str, ...]
    market_tickers: tuple[str, ...]
    feasible_witness_deg_f: Decimal
    content_hash: str


@dataclass(frozen=True, slots=True)
class WeatherSettlementDataset:
    dataset_id: str
    parser_version: str
    settlement_mapping_id: str
    label_authority: str
    evidence_bound: bool
    coverage_claim: str
    event_count: int
    contract_count: int
    skipped_unsupported_count: int
    legacy_regime_excluded_count: int
    events: tuple[ResolvedWeatherEvent, ...]
    contracts: tuple[ResolvedTemperatureContract, ...]
    settlement_labels: SettlementLabelManifest | None
    provenance: tuple[SettlementProvenance, ...]
    train_event_ids: tuple[str, ...]
    validation_event_ids: tuple[str, ...]
    test_event_ids: tuple[str, ...]
    temporal_split_hash: str | None
    content_hash: str

    @property
    def unique_event_count(self) -> int:
        return self.event_count

    def split_for_event(self, event_id: str) -> EventPartition:
        if event_id in self.train_event_ids:
            return EventPartition.TRAIN
        if event_id in self.validation_event_ids:
            return EventPartition.VALIDATION
        if event_id in self.test_event_ids:
            return EventPartition.TEST
        raise HistoricalWeatherDatasetError("event is not assigned to this dataset")


def classify_resolved_temperature_market(row: Mapping[str, Any]) -> SettlementRowClassification:
    rule = row.get("rules_primary")
    if not isinstance(rule, str) or not _CANDIDATE.search(rule):
        return SettlementRowClassification(
            SettlementRegime.UNRELATED, None, "not daily temperature"
        )
    if LEGACY_NWS_SOURCE.casefold() in rule.casefold():
        return SettlementRowClassification(
            SettlementRegime.LEGACY_NWS,
            None,
            "recognized legacy National Weather Service settlement regime",
        )

    match = _CURRENT_RULE_WITH_IDENTIFIER.fullmatch(rule)
    if match is not None:
        identifier = match.group("identifier")
        reviewed = SETTLEMENT_LOCATIONS.get(identifier)
        if reviewed is None:
            raise HistoricalWeatherDatasetError(
                "temperature market uses an unreviewed settlement location"
            )
        if match.group("location").strip() != reviewed.location:
            raise HistoricalWeatherDatasetError(
                "temperature market location conflicts with reviewed authority"
            )
    else:
        match = _CURRENT_RULE_LOCATION_ONLY_IF.fullmatch(rule)
        if match is None:
            match = _CURRENT_RULE_LOCATION_ONLY_RESOLVES.fullmatch(rule)
        if match is None:
            raise HistoricalWeatherDatasetError("temperature-like market has unsupported exact rule")
        location = match.group("location")
        reviewed = _LOCATION_AUTHORITY.get(location)
        if reviewed is None:
            raise HistoricalWeatherDatasetError(
                "temperature market uses an unreviewed settlement location"
            )

    contract = _parse_current_match(row, rule, match, reviewed)
    return SettlementRowClassification(
        SettlementRegime.CURRENT_TWC, contract, "reviewed current TWC"
    )


def parse_resolved_temperature_market(
    row: Mapping[str, Any],
) -> ResolvedTemperatureContract | None:
    """Semantic parser only; this function does not establish acquisition provenance."""
    classification = classify_resolved_temperature_market(row)
    return classification.contract


def build_weather_settlement_dataset(
    rows: Sequence[Mapping[str, Any]], *, temporal_split: TemporalSplit | None = None
) -> WeatherSettlementDataset:
    """Build semantic/replay evidence only; naked rows cannot produce canonical labels."""
    contracts: list[ResolvedTemperatureContract] = []
    skipped = 0
    legacy = 0
    for row in rows:
        classification = classify_resolved_temperature_market(row)
        if classification.regime is SettlementRegime.UNRELATED:
            skipped += 1
        elif classification.regime is SettlementRegime.LEGACY_NWS:
            legacy += 1
        elif classification.contract is not None:
            contracts.append(classification.contract)
    return _assemble_dataset(
        contracts,
        skipped=skipped,
        legacy=legacy,
        temporal_split=temporal_split,
        settlement_labels=None,
        provenance=(),
        evidence_bound=False,
        coverage_claim="SEMANTIC_REPLAY_ONLY_NO_ACQUISITION_AUTHORITY",
    )


def build_evidence_bound_weather_dataset(
    rows: Sequence[AcquisitionBoundMarketRow], *, temporal_split: TemporalSplit | None = None
) -> WeatherSettlementDataset:
    """Build canonical labels only from acquisition-bound, reviewed-series public evidence."""
    if not rows:
        raise HistoricalWeatherDatasetError("evidence-bound dataset cannot be empty")
    if any(not isinstance(item, AcquisitionBoundMarketRow) for item in rows):
        raise HistoricalWeatherDatasetError("authoritative labels require acquisition-bound rows")
    scopes = {item.page_evidence.scope_series_ticker for item in rows}
    if len(scopes) != 1:
        raise HistoricalWeatherDatasetError(
            "authoritative dataset requires one reviewed series scope"
        )

    contracts: list[ResolvedTemperatureContract] = []
    bindings: dict[str, AcquisitionBoundMarketRow] = {}
    skipped = 0
    legacy = 0
    for bound in rows:
        row = bound.row()
        classification = classify_resolved_temperature_market(row)
        if classification.regime is SettlementRegime.UNRELATED:
            skipped += 1
            continue
        if classification.regime is SettlementRegime.LEGACY_NWS:
            legacy += 1
            continue
        contract = classification.contract
        if contract is None:
            raise HistoricalWeatherDatasetError("current settlement classification lost contract")
        if contract.series_ticker != bound.page_evidence.scope_series_ticker:
            raise HistoricalWeatherDatasetError("settlement row escaped reviewed series scope")
        if contract.market_ticker in bindings:
            raise HistoricalWeatherDatasetError("duplicate historical weather market ticker")
        bindings[contract.market_ticker] = bound
        contracts.append(contract)

    if not contracts:
        raise HistoricalWeatherDatasetError(
            "no supported finalized daily-temperature markets found"
        )
    labels = tuple(
        SettlementLabel.build(
            event_id=contract.event_id,
            market_ticker=contract.market_ticker,
            resolved_outcome=bool(contract.realized_yes),
            resolved_at=contract.settlement_at,
            settlement_evidence_id=bindings[contract.market_ticker].settlement_evidence_id,
        )
        for contract in sorted(contracts, key=lambda item: item.market_ticker)
    )
    manifest = SettlementLabelManifest.build(
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        authority=LABEL_AUTHORITY,
        labels=labels,
    )
    label_by_market = {item.market_ticker: item for item in manifest.labels}
    provenance = tuple(
        _provenance_record(
            contract, bindings[contract.market_ticker], label_by_market[contract.market_ticker]
        )
        for contract in sorted(contracts, key=lambda item: item.market_ticker)
    )
    return _assemble_dataset(
        contracts,
        skipped=skipped,
        legacy=legacy,
        temporal_split=temporal_split,
        settlement_labels=manifest,
        provenance=provenance,
        evidence_bound=True,
        coverage_claim="REVIEWED_SERIES_ARCHIVE_PLUS_RECENT_PARTITIONS_NO_TOTAL_COUNT_PROOF",
    )


def _parse_current_match(
    row: Mapping[str, Any],
    rule: str,
    match: re.Match[str],
    reviewed: SettlementLocation,
) -> ResolvedTemperatureContract:
    if row.get("status") != "settled":
        raise HistoricalWeatherDatasetError("temperature market status is not settled")
    identifier = reviewed.identifier
    try:
        local_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
    except ValueError as exc:
        raise HistoricalWeatherDatasetError("temperature market date is malformed") from exc

    market_ticker = _text(row, "ticker")
    event_ticker = _text(row, "event_ticker")
    if market_ticker == event_ticker or not market_ticker.startswith(event_ticker + "-"):
        raise HistoricalWeatherDatasetError("market ticker is not bound to its event ticker")
    if "-" not in event_ticker:
        raise HistoricalWeatherDatasetError("event ticker does not expose a series identity")
    series_ticker = event_ticker.rsplit("-", 1)[0]
    market_type = row.get("market_type")
    if market_type is not None and market_type != "binary":
        raise HistoricalWeatherDatasetError("temperature settlement label is not binary")

    strike_type = row.get("strike_type")
    if match.group("between_low") is not None:
        expected_strike = "between"
        comparator = "RANGE"
        rule_lower = Decimal(match.group("between_low"))
        rule_upper: Decimal | None = Decimal(match.group("between_high"))
        lower = _decimal(row.get("floor_strike"), "floor_strike")
        upper = _decimal(row.get("cap_strike"), "cap_strike")
        if lower >= upper:
            raise HistoricalWeatherDatasetError("temperature range is not strictly increasing")
    elif match.group("greater") is not None:
        expected_strike = "greater"
        comparator = "GT"
        rule_lower = Decimal(match.group("greater"))
        rule_upper = None
        lower = _decimal(row.get("floor_strike"), "floor_strike")
        upper = None
        if row.get("cap_strike") is not None:
            raise HistoricalWeatherDatasetError(
                "greater-than contract has an unexpected cap strike"
            )
    else:
        expected_strike = "less"
        comparator = "LT"
        rule_lower = Decimal(match.group("less"))
        rule_upper = None
        lower = _decimal(row.get("cap_strike"), "cap_strike")
        upper = None
        if row.get("floor_strike") is not None:
            raise HistoricalWeatherDatasetError("less-than contract has an unexpected floor strike")
    if strike_type != expected_strike:
        raise HistoricalWeatherDatasetError(
            "strike metadata conflicts with the exact contract rule"
        )
    if lower != rule_lower or upper != rule_upper:
        raise HistoricalWeatherDatasetError("strike values conflict with the exact contract rule")

    result = _text(row, "result").lower()
    if result not in {"yes", "no"}:
        raise HistoricalWeatherDatasetError("historical temperature market is not finally resolved")
    realized_yes = 1 if result == "yes" else 0
    settlement_value = _decimal(row.get("settlement_value_dollars"), "settlement_value_dollars")
    if settlement_value != Decimal(realized_yes):
        raise HistoricalWeatherDatasetError(
            "settlement value conflicts with finalized binary result"
        )
    settlement_at = _timestamp(row.get("settlement_ts"), "settlement_ts")
    measurement = "DAILY_MAX" if match.group("measurement") == "maximum" else "DAILY_MIN"
    event_id = stable_hash(
        (
            EVENT_ID_VERSION,
            SETTLEMENT_MAPPING_ID,
            event_ticker,
            series_ticker,
            identifier,
            reviewed.location,
            reviewed.timezone,
            measurement,
            local_date.isoformat(),
        )
    )
    rules_hash = stable_hash((rule, row.get("rules_secondary")))
    source_row_hash = stable_hash(_canonical_row_material(row))
    digest = stable_hash(
        (
            PARSER_VERSION,
            event_id,
            market_ticker,
            comparator,
            str(lower),
            None if upper is None else str(upper),
            result,
            str(settlement_value),
            settlement_at.isoformat(),
            rules_hash,
            source_row_hash,
        )
    )
    return ResolvedTemperatureContract(
        contract_id=digest,
        event_id=event_id,
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        series_ticker=series_ticker,
        station_id=identifier,
        location=reviewed.location,
        timezone=reviewed.timezone,
        measurement=measurement,
        local_date=local_date,
        comparator=comparator,
        lower=lower,
        upper=upper,
        result=result,
        realized_yes=realized_yes,
        settlement_value_dollars=settlement_value,
        settlement_at=settlement_at,
        rules_primary=rule,
        rules_hash=rules_hash,
        source_row_hash=source_row_hash,
        content_hash=digest,
    )


def _assemble_dataset(
    contracts: Sequence[ResolvedTemperatureContract],
    *,
    skipped: int,
    legacy: int,
    temporal_split: TemporalSplit | None,
    settlement_labels: SettlementLabelManifest | None,
    provenance: tuple[SettlementProvenance, ...],
    evidence_bound: bool,
    coverage_claim: str,
) -> WeatherSettlementDataset:
    if not contracts:
        raise HistoricalWeatherDatasetError(
            "no supported finalized daily-temperature markets found"
        )
    ordered_contracts = tuple(sorted(contracts, key=lambda item: item.market_ticker))
    if len({item.market_ticker for item in ordered_contracts}) != len(ordered_contracts):
        raise HistoricalWeatherDatasetError("duplicate historical weather market ticker")

    semantic_by_ticker: dict[str, tuple[str, ...]] = {}
    grouped: dict[str, list[ResolvedTemperatureContract]] = {}
    for contract in ordered_contracts:
        previous = semantic_by_ticker.setdefault(
            contract.event_ticker, contract.semantic_event_tuple
        )
        if previous != contract.semantic_event_tuple:
            raise HistoricalWeatherDatasetError(
                "event ticker maps to conflicting settlement semantics"
            )
        grouped.setdefault(contract.event_id, []).append(contract)
    events = tuple(_build_event(grouped[key]) for key in sorted(grouped))

    train: list[str] = []
    validation: list[str] = []
    test: list[str] = []
    if temporal_split is not None:
        for event in events:
            instant = _event_instant(event)
            if temporal_split.train_start <= instant < temporal_split.train_end:
                train.append(event.event_id)
            elif temporal_split.validation_start <= instant < temporal_split.validation_end:
                validation.append(event.event_id)
            elif temporal_split.test_start <= instant < temporal_split.test_end:
                test.append(event.event_id)
            else:
                raise HistoricalWeatherDatasetError(
                    "supported event lies outside declared train/validation/test windows"
                )
        if not train or not validation or not test:
            raise HistoricalWeatherDatasetError("every temporal partition must contain an event")
    split_hash = None if temporal_split is None else temporal_split.content_hash
    manifest_id = None if settlement_labels is None else settlement_labels.manifest_id
    material = (
        PARSER_VERSION,
        SETTLEMENT_MAPPING_ID,
        LABEL_AUTHORITY,
        evidence_bound,
        coverage_claim,
        tuple(event.content_hash for event in events),
        tuple(contract.content_hash for contract in ordered_contracts),
        manifest_id,
        tuple(item.content_hash for item in provenance),
        tuple(train),
        tuple(validation),
        tuple(test),
        split_hash,
        skipped,
        legacy,
    )
    digest = stable_hash(material)
    return WeatherSettlementDataset(
        dataset_id=digest,
        parser_version=PARSER_VERSION,
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        label_authority=LABEL_AUTHORITY,
        evidence_bound=evidence_bound,
        coverage_claim=coverage_claim,
        event_count=len(events),
        contract_count=len(ordered_contracts),
        skipped_unsupported_count=skipped,
        legacy_regime_excluded_count=legacy,
        events=events,
        contracts=ordered_contracts,
        settlement_labels=settlement_labels,
        provenance=provenance,
        train_event_ids=tuple(train),
        validation_event_ids=tuple(validation),
        test_event_ids=tuple(test),
        temporal_split_hash=split_hash,
        content_hash=digest,
    )


def _build_event(contracts: Sequence[ResolvedTemperatureContract]) -> ResolvedWeatherEvent:
    ordered = tuple(sorted(contracts, key=lambda item: item.market_ticker))
    first = ordered[0]
    if any(item.event_id != first.event_id for item in ordered):
        raise HistoricalWeatherDatasetError("event group contains multiple semantic identities")
    witness = _feasible_witness(ordered)
    if witness is None:
        raise HistoricalWeatherDatasetError("sibling settlement labels are mutually contradictory")
    contract_ids = tuple(item.contract_id for item in ordered)
    market_tickers = tuple(item.market_ticker for item in ordered)
    snapshot_hash = stable_hash(
        (
            first.event_id,
            contract_ids,
            market_tickers,
            str(witness),
        )
    )
    return ResolvedWeatherEvent(
        event_id=first.event_id,
        event_ticker=first.event_ticker,
        series_ticker=first.series_ticker,
        settlement_mapping_id=first.settlement_mapping_id,
        station_id=first.station_id,
        location=first.location,
        timezone=first.timezone,
        measurement=first.measurement,
        local_date=first.local_date,
        contract_ids=contract_ids,
        market_tickers=market_tickers,
        feasible_witness_deg_f=witness,
        content_hash=snapshot_hash,
    )


def _feasible_witness(contracts: tuple[ResolvedTemperatureContract, ...]) -> Decimal | None:
    boundaries = sorted(
        {
            boundary
            for contract in contracts
            for boundary in (contract.lower, contract.upper)
            if boundary is not None
        }
    )
    if not boundaries:
        return None
    candidates: set[Decimal] = {boundaries[0] - Decimal(1), boundaries[-1] + Decimal(1)}
    candidates.update(boundaries)
    for left, right in pairwise(boundaries):
        candidates.add((left + right) / Decimal(2))
    for candidate in sorted(candidates):
        if all(
            contract.predicate(candidate) == bool(contract.realized_yes) for contract in contracts
        ):
            return candidate
    return None


def _provenance_record(
    contract: ResolvedTemperatureContract,
    bound: AcquisitionBoundMarketRow,
    label: SettlementLabel,
) -> SettlementProvenance:
    page = bound.page_evidence
    material = (
        contract.market_ticker,
        page.request_path,
        page.response_sha256,
        page.page_number,
        bound.market_row_hash,
        PARSER_VERSION,
        SETTLEMENT_MAPPING_ID,
        bound.settlement_evidence_id,
        label.content_hash,
    )
    return SettlementProvenance(
        market_ticker=contract.market_ticker,
        request_path=page.request_path,
        response_sha256=page.response_sha256,
        page_number=page.page_number,
        market_row_hash=bound.market_row_hash,
        parser_version=PARSER_VERSION,
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        settlement_evidence_id=bound.settlement_evidence_id,
        label_id=label.content_hash,
        content_hash=stable_hash(material),
    )


def _event_instant(event: ResolvedWeatherEvent) -> datetime:
    local = datetime.combine(event.local_date, time.min, tzinfo=ZoneInfo(event.timezone))
    return local.astimezone(UTC)


def _text(row: Mapping[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is missing")
    return value.strip()


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is invalid")
    if isinstance(value, float):
        text = str(value)
        if not _NUMBER.fullmatch(text):
            raise HistoricalWeatherDatasetError(f"historical market {field_name} is invalid")
    elif isinstance(value, (int, Decimal)):
        text = str(value)
    elif isinstance(value, str) and _NUMBER.fullmatch(value):
        text = value
    else:
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is invalid")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is invalid") from exc
    if not result.is_finite():
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is non-finite")
    return result


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HistoricalWeatherDatasetError(f"historical market {field_name} is malformed") from exc
    if parsed.tzinfo is None:
        raise HistoricalWeatherDatasetError(
            f"historical market {field_name} must be timezone-aware"
        )
    return parsed.astimezone(UTC)


def _canonical_row_material(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    fields = (
        "ticker",
        "event_ticker",
        "market_type",
        "status",
        "result",
        "settlement_value_dollars",
        "settlement_ts",
        "rules_primary",
        "rules_secondary",
        "strike_type",
        "floor_strike",
        "cap_strike",
    )
    return tuple((field_name, repr(row.get(field_name))) for field_name in fields)
