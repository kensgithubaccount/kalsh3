"""M28B authoritative, event-grouped Kalshi weather settlement dataset.

This module is deliberately pure. It accepts already-acquired public historical Kalshi
market rows and turns only exact, rule-supported, finalized daily-temperature contracts
into content-addressed training/evaluation evidence.

It does not perform network I/O, access credentials, mutate production state, issue risk
authority, approve execution, or send orders.

Key invariants:

* Kalshi's finalized binary result is the production training label.
* The exact contract rule must independently bind the label to The Weather Company and a
  reviewed settlement location.
* Both reviewed Weather Company rule grammars are exact: the original CLI-identifier form
  and the August 2026 location-name form. The latter is accepted only when its location name
  maps one-to-one to the frozen reviewed settlement-location authority.
* Sibling contracts are grouped into one event/day unit before temporal splitting so one
  day's outcome cannot leak across train/validation/test windows.
* A group is rejected if its binary labels cannot all be true for at least one possible
  underlying temperature.
* Unsupported non-temperature markets are skipped; malformed temperature-like markets fail
  closed instead of silently disappearing from the dataset.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from services.forecasting.daily_temperature import SETTLEMENT_LOCATIONS, SETTLEMENT_SOURCE
from services.historical_replay.archive import stable_hash

from .contracts import TemporalSplit

PARSER_VERSION = "m28b-authoritative-weather-settlement-v2"
LABEL_AUTHORITY = (
    "Kalshi finalized result + exact The Weather Company contract rule + reviewed "
    "settlement-location authority"
)

_CANDIDATE = re.compile(r"\b(?:maximum|minimum) temperature recorded at\b", re.IGNORECASE)
_PREDICATE = (
    r"(?:(?:between (?P<between_low>[+-]?(?:\d+(?:\.\d+)?|\.\d+))-"
    r"(?P<between_high>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:greater than (?P<greater>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:less than (?P<less>[+-]?(?:\d+(?:\.\d+)?|\.\d+))))"
)
_RULE_WITH_IDENTIFIER = re.compile(
    r"\AIf the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?)\s*\((?P<identifier>CLI[A-Z]+)\) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    + _PREDICATE
    + rf"° fahrenheit according to {re.escape(SETTLEMENT_SOURCE)}, then the market "
    r"resolves to Yes\.\Z"
)
_RULE_LOCATION_ONLY = re.compile(
    r"\AResolves Yes if the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    + _PREDICATE
    + rf"° fahrenheit according to {re.escape(SETTLEMENT_SOURCE)}\.\Z"
)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_LOCATION_AUTHORITY = {item.location: item for item in SETTLEMENT_LOCATIONS.values()}
if len(_LOCATION_AUTHORITY) != len(SETTLEMENT_LOCATIONS):
    raise RuntimeError("reviewed settlement-location names must be unique")


class HistoricalWeatherDatasetError(ValueError):
    """Historical weather evidence violates an M28B invariant."""


class EventPartition(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


@dataclass(frozen=True, slots=True)
class ResolvedTemperatureContract:
    """One finalized binary temperature contract and its exact settlement semantics."""

    contract_id: str
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


@dataclass(frozen=True, slots=True)
class ResolvedWeatherEvent:
    """One independent weather outcome unit containing all recognized sibling contracts."""

    event_id: str
    event_ticker: str
    series_ticker: str
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
class AuthoritativeWeatherDataset:
    """Content-addressed event-grouped dataset ready for leakage-safe feature joining."""

    dataset_id: str
    parser_version: str
    label_authority: str
    event_count: int
    contract_count: int
    skipped_unsupported_count: int
    events: tuple[ResolvedWeatherEvent, ...]
    contracts: tuple[ResolvedTemperatureContract, ...]
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


def parse_resolved_temperature_market(
    row: Mapping[str, Any],
) -> ResolvedTemperatureContract | None:
    """Parse one public historical market row.

    Returns ``None`` only for rows that are clearly outside the supported daily-temperature
    family. If a row looks like a daily-temperature contract but is malformed, unresolved,
    contradictory, or outside the reviewed authority, the function fails closed.
    """

    rule = row.get("rules_primary")
    if not isinstance(rule, str) or not _CANDIDATE.search(rule):
        return None

    match = _RULE_WITH_IDENTIFIER.fullmatch(rule)
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
        match = _RULE_LOCATION_ONLY.fullmatch(rule)
        if match is None:
            raise HistoricalWeatherDatasetError(
                "temperature-like market has unsupported exact rule"
            )
        location = match.group("location").strip()
        reviewed = _LOCATION_AUTHORITY.get(location)
        if reviewed is None:
            raise HistoricalWeatherDatasetError(
                "temperature market uses an unreviewed settlement location"
            )
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
    expected_value = Decimal(realized_yes)
    if settlement_value != expected_value:
        raise HistoricalWeatherDatasetError(
            "settlement value conflicts with finalized binary result"
        )
    settlement_at = _timestamp(row.get("settlement_ts"), "settlement_ts")

    measurement = "DAILY_MAX" if match.group("measurement") == "maximum" else "DAILY_MIN"
    rules_hash = stable_hash((rule, row.get("rules_secondary")))
    source_row_hash = stable_hash(_canonical_row_material(row))
    material = (
        PARSER_VERSION,
        market_ticker,
        event_ticker,
        series_ticker,
        identifier,
        reviewed.location,
        reviewed.timezone,
        measurement,
        local_date.isoformat(),
        comparator,
        str(lower),
        None if upper is None else str(upper),
        result,
        str(settlement_value),
        settlement_at.isoformat(),
        rules_hash,
        source_row_hash,
    )
    digest = stable_hash(material)
    return ResolvedTemperatureContract(
        contract_id=digest,
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


def build_authoritative_weather_dataset(
    rows: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    temporal_split: TemporalSplit | None = None,
) -> AuthoritativeWeatherDataset:
    """Build a deterministic, event-grouped dataset from public historical market rows."""

    contracts: list[ResolvedTemperatureContract] = []
    skipped = 0
    seen_tickers: set[str] = set()
    for row in rows:
        parsed = parse_resolved_temperature_market(row)
        if parsed is None:
            skipped += 1
            continue
        if parsed.market_ticker in seen_tickers:
            raise HistoricalWeatherDatasetError("duplicate historical weather market ticker")
        seen_tickers.add(parsed.market_ticker)
        contracts.append(parsed)

    if not contracts:
        raise HistoricalWeatherDatasetError(
            "no supported finalized daily-temperature markets found"
        )

    grouped: dict[tuple[str, str, str, date], list[ResolvedTemperatureContract]] = {}
    for contract in contracts:
        key = (
            contract.event_ticker,
            contract.station_id,
            contract.measurement,
            contract.local_date,
        )
        grouped.setdefault(key, []).append(contract)

    events = tuple(
        _build_event(group) for _, group in sorted(grouped.items(), key=lambda item: item[0])
    )
    ordered_contracts = tuple(sorted(contracts, key=lambda item: item.market_ticker))

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
    material = (
        PARSER_VERSION,
        LABEL_AUTHORITY,
        tuple(event.content_hash for event in events),
        tuple(contract.content_hash for contract in ordered_contracts),
        tuple(train),
        tuple(validation),
        tuple(test),
        split_hash,
    )
    digest = stable_hash(material)
    return AuthoritativeWeatherDataset(
        dataset_id=digest,
        parser_version=PARSER_VERSION,
        label_authority=LABEL_AUTHORITY,
        event_count=len(events),
        contract_count=len(ordered_contracts),
        skipped_unsupported_count=skipped,
        events=events,
        contracts=ordered_contracts,
        train_event_ids=tuple(train),
        validation_event_ids=tuple(validation),
        test_event_ids=tuple(test),
        temporal_split_hash=split_hash,
        content_hash=digest,
    )


def _build_event(contracts: list[ResolvedTemperatureContract]) -> ResolvedWeatherEvent:
    ordered = tuple(sorted(contracts, key=lambda item: item.market_ticker))
    first = ordered[0]
    identity = (
        first.event_ticker,
        first.series_ticker,
        first.station_id,
        first.location,
        first.timezone,
        first.measurement,
        first.local_date,
    )
    for contract in ordered[1:]:
        other = (
            contract.event_ticker,
            contract.series_ticker,
            contract.station_id,
            contract.location,
            contract.timezone,
            contract.measurement,
            contract.local_date,
        )
        if other != identity:
            raise HistoricalWeatherDatasetError(
                "sibling temperature contracts disagree on event semantics"
            )

    witness = _feasible_witness(ordered)
    if witness is None:
        raise HistoricalWeatherDatasetError("sibling settlement labels are mutually contradictory")

    contract_ids = tuple(item.contract_id for item in ordered)
    market_tickers = tuple(item.market_ticker for item in ordered)
    material = (
        PARSER_VERSION,
        first.event_ticker,
        first.series_ticker,
        first.station_id,
        first.location,
        first.timezone,
        first.measurement,
        first.local_date.isoformat(),
        contract_ids,
        str(witness),
    )
    digest = stable_hash(material)
    return ResolvedWeatherEvent(
        event_id=digest,
        event_ticker=first.event_ticker,
        series_ticker=first.series_ticker,
        station_id=first.station_id,
        location=first.location,
        timezone=first.timezone,
        measurement=first.measurement,
        local_date=first.local_date,
        contract_ids=contract_ids,
        market_tickers=market_tickers,
        feasible_witness_deg_f=witness,
        content_hash=digest,
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
    candidates: set[Decimal] = {
        boundaries[0] - Decimal(1),
        boundaries[-1] + Decimal(1),
    }
    candidates.update(boundaries)
    for left, right in pairwise(boundaries):
        candidates.add((left + right) / Decimal(2))
    for candidate in sorted(candidates):
        consistent = all(
            contract.predicate(candidate) == bool(contract.realized_yes) for contract in contracts
        )
        if consistent:
            return candidate
    return None


def _event_instant(event: ResolvedWeatherEvent) -> datetime:
    local = datetime.combine(event.local_date, time.min, tzinfo=ZoneInfo(event.timezone))
    return local.astimezone(UTC)


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HistoricalWeatherDatasetError(f"historical market {field} is missing")
    return value.strip()


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise HistoricalWeatherDatasetError(f"historical market {field} is invalid")
    if isinstance(value, float):
        if not _NUMBER.fullmatch(str(value)):
            raise HistoricalWeatherDatasetError(f"historical market {field} is invalid")
        text = str(value)
    elif isinstance(value, (int, Decimal)):
        text = str(value)
    elif isinstance(value, str) and _NUMBER.fullmatch(value):
        text = value
    else:
        raise HistoricalWeatherDatasetError(f"historical market {field} is invalid")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise HistoricalWeatherDatasetError(f"historical market {field} is invalid") from exc
    if not result.is_finite():
        raise HistoricalWeatherDatasetError(f"historical market {field} is non-finite")
    return result


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalWeatherDatasetError(f"historical market {field} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HistoricalWeatherDatasetError(f"historical market {field} is malformed") from exc
    if parsed.tzinfo is None:
        raise HistoricalWeatherDatasetError(f"historical market {field} must be timezone-aware")
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
    return tuple((field, repr(row.get(field))) for field in fields)
