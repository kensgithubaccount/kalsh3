"""Deterministic M28C discovery/scoping over canonical M28B settlement semantics.

Series metadata is discovery evidence only. Row-level settlement regime, reviewed location,
station identity, finality, and contract semantics remain exclusively owned by M28B.
This module performs no network I/O itself and cannot create canonical settlement labels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.settlement_dataset import (
    HistoricalWeatherDatasetError,
    SettlementRegime,
    classify_resolved_temperature_market,
)

SERIES_SCOPE_SCHEMA_VERSION = "m28c-reviewed-series-scope-v3"
SERIES_SCOPE_RECORD_VERSION = "m28c-series-scope-record-v3"
_WEATHER_CATEGORIES = frozenset({"Weather", "Climate and Weather"})
_UNREVIEWED_LOCATION = "temperature market uses an unreviewed settlement location"


class SeriesScopeError(ValueError):
    """Series discovery/scoping violated an M28C invariant."""


class RecentSettledMarketClient(Protocol):
    def recent_settled_markets(self, *, series_ticker: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class SeriesScopeRecord:
    series_ticker: str
    decision: str
    reason: str
    raw_market_count: int
    legacy_regime_market_count: int
    supported_event_count: int
    supported_contract_count: int
    station_ids: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class SeriesScopeManifest:
    candidate_series_tickers: tuple[str, ...]
    included_series_tickers: tuple[str, ...]
    excluded_series: tuple[SeriesScopeRecord, ...]
    included_series: tuple[SeriesScopeRecord, ...]
    raw_market_count: int
    legacy_regime_market_count: int
    supported_event_count: int
    supported_contract_count: int
    station_ids: tuple[str, ...]
    schema_version: str
    content_hash: str


def candidate_temperature_series(series_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return deterministic daily-temperature candidates from public series metadata.

    Settlement-source metadata is intentionally ignored: an existing series may straddle
    legacy NWS and current Weather Company regimes, so only canonical M28B row parsing can
    establish the regime of a settled contract.
    """

    raw_series = series_payload.get("series")
    if not isinstance(raw_series, list):
        raise SeriesScopeError(
            f"Kalshi Weather series response is malformed (series_type={type(raw_series).__name__})"
        )
    bad_types = Counter(type(row).__name__ for row in raw_series if not isinstance(row, dict))
    if bad_types:
        shape = ",".join(f"{name}:{count}" for name, count in sorted(bad_types.items()))
        raise SeriesScopeError(
            "Kalshi Weather series response is malformed "
            f"(series_count={len(raw_series)}, non_object_types={shape})"
        )

    selected: list[str] = []
    seen: set[str] = set()
    for row in raw_series:
        if row.get("category") not in _WEATHER_CATEGORIES:
            continue
        ticker = row.get("ticker")
        title = row.get("title")
        frequency = row.get("frequency")
        if not isinstance(ticker, str) or not ticker.strip():
            raise SeriesScopeError("Weather series ticker is missing or malformed")
        ticker = ticker.strip()
        if ticker in seen:
            raise SeriesScopeError("Weather series ticker is duplicated")
        seen.add(ticker)
        if not isinstance(title, str) or not title.strip():
            raise SeriesScopeError("Weather series title is missing or malformed")
        if "temperature" not in title.casefold():
            continue
        if not isinstance(frequency, str) or not frequency.strip():
            raise SeriesScopeError("temperature series frequency is missing or malformed")
        if frequency.casefold() != "daily":
            continue
        selected.append(ticker)

    if not selected:
        raise SeriesScopeError("no current daily-temperature series were discovered")
    return tuple(sorted(selected))


def scope_recent_settled_markets(
    client: RecentSettledMarketClient,
    series_tickers: Sequence[str],
) -> tuple[list[dict[str, Any]], SeriesScopeManifest]:
    """Inspect candidate settled rows using M28B classification without minting labels."""

    normalized = tuple(sorted(series_tickers))
    if not normalized or any(
        not isinstance(ticker, str) or not ticker.strip() for ticker in normalized
    ):
        raise SeriesScopeError("candidate series tickers must be nonempty")
    if len(set(normalized)) != len(normalized):
        raise SeriesScopeError("candidate series tickers must be unique")

    included_rows: list[dict[str, Any]] = []
    included: list[SeriesScopeRecord] = []
    excluded: list[SeriesScopeRecord] = []
    all_station_ids: set[str] = set()

    for ticker in normalized:
        rows = client.recent_settled_markets(series_ticker=ticker)
        if not rows:
            excluded.append(_record(ticker, "EXCLUDED", "NO_RECENT_SETTLED_MARKETS", 0))
            continue
        if any(not isinstance(row, dict) for row in rows):
            raise SeriesScopeError("recent settled market response contains a non-object row")

        legacy_count = 0
        current_rows: list[dict[str, Any]] = []
        current_event_ids: set[str] = set()
        station_ids: set[str] = set()
        current_tickers: set[str] = set()
        unreviewed_location = False

        for raw in rows:
            try:
                classification = classify_resolved_temperature_market(raw)
            except HistoricalWeatherDatasetError as exc:
                if str(exc) == _UNREVIEWED_LOCATION:
                    unreviewed_location = True
                    break
                raise HistoricalWeatherDatasetError(f"{ticker}: {exc}") from exc

            if classification.regime is SettlementRegime.LEGACY_NWS:
                legacy_count += 1
                continue
            if classification.regime is SettlementRegime.UNRELATED:
                continue
            contract = classification.contract
            if contract is None:
                raise SeriesScopeError("current-regime classification lost its contract")
            if contract.series_ticker != ticker:
                raise SeriesScopeError("scoped settlement row crossed its series identity")
            if contract.market_ticker in current_tickers:
                raise SeriesScopeError("scoped series contains a duplicate market ticker")
            current_tickers.add(contract.market_ticker)
            current_event_ids.add(contract.event_id)
            station_ids.add(contract.station_id)
            current_rows.append(dict(raw))

        if unreviewed_location:
            excluded.append(
                _record(
                    ticker,
                    "EXCLUDED",
                    "UNREVIEWED_SETTLEMENT_LOCATION",
                    len(rows),
                    legacy_regime_market_count=legacy_count,
                )
            )
            continue
        if not current_rows:
            excluded.append(
                _record(
                    ticker,
                    "EXCLUDED",
                    "NO_SUPPORTED_CURRENT_REGIME_MARKETS",
                    len(rows),
                    legacy_regime_market_count=legacy_count,
                )
            )
            continue
        ordered_station_ids = tuple(sorted(station_ids))
        if len(ordered_station_ids) != 1:
            raise SeriesScopeError(
                "one temperature series must bind exactly one settlement station"
            )
        if ordered_station_ids[0] not in PHYSICAL_WEATHER_SOURCES:
            raise SeriesScopeError(
                "reviewed settlement series lacks reviewed physical-source evidence"
            )

        record = _record(
            ticker,
            "INCLUDED",
            "REVIEWED_CURRENT_REGIME",
            len(rows),
            legacy_regime_market_count=legacy_count,
            supported_event_count=len(current_event_ids),
            supported_contract_count=len(current_rows),
            station_ids=ordered_station_ids,
        )
        included.append(record)
        included_rows.extend(current_rows)
        all_station_ids.update(ordered_station_ids)

    if not included:
        raise SeriesScopeError("no reviewed current-regime temperature series remain after scoping")

    included.sort(key=lambda record: record.series_ticker)
    excluded.sort(key=lambda record: record.series_ticker)
    included_rows.sort(key=_row_ticker)
    all_records = (*included, *excluded)
    included_tickers = tuple(record.series_ticker for record in included)
    raw_total = sum(record.raw_market_count for record in all_records)
    legacy_total = sum(record.legacy_regime_market_count for record in all_records)
    supported_events = sum(record.supported_event_count for record in included)
    supported_contracts = sum(record.supported_contract_count for record in included)
    station_tuple = tuple(sorted(all_station_ids))
    material = (
        SERIES_SCOPE_SCHEMA_VERSION,
        normalized,
        included_tickers,
        tuple(record.content_hash for record in included),
        tuple(record.content_hash for record in excluded),
        tuple(_row_ticker(row) for row in included_rows),
        raw_total,
        legacy_total,
        supported_events,
        supported_contracts,
        station_tuple,
    )
    digest = stable_hash(material)
    return included_rows, SeriesScopeManifest(
        candidate_series_tickers=normalized,
        included_series_tickers=included_tickers,
        excluded_series=tuple(excluded),
        included_series=tuple(included),
        raw_market_count=raw_total,
        legacy_regime_market_count=legacy_total,
        supported_event_count=supported_events,
        supported_contract_count=supported_contracts,
        station_ids=station_tuple,
        schema_version=SERIES_SCOPE_SCHEMA_VERSION,
        content_hash=digest,
    )


def _row_ticker(row: Mapping[str, Any]) -> str:
    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        raise SeriesScopeError("scoped market row is missing its ticker")
    return ticker.strip()


def _record(
    ticker: str,
    decision: str,
    reason: str,
    raw_market_count: int,
    *,
    legacy_regime_market_count: int = 0,
    supported_event_count: int = 0,
    supported_contract_count: int = 0,
    station_ids: tuple[str, ...] = (),
) -> SeriesScopeRecord:
    material = (
        SERIES_SCOPE_RECORD_VERSION,
        ticker,
        decision,
        reason,
        raw_market_count,
        legacy_regime_market_count,
        supported_event_count,
        supported_contract_count,
        station_ids,
    )
    digest = stable_hash(material)
    return SeriesScopeRecord(
        series_ticker=ticker,
        decision=decision,
        reason=reason,
        raw_market_count=raw_market_count,
        legacy_regime_market_count=legacy_regime_market_count,
        supported_event_count=supported_event_count,
        supported_contract_count=supported_contract_count,
        station_ids=station_ids,
        content_hash=digest,
    )
