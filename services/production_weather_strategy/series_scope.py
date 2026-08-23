"""Deterministic M28C scoping for current-regime public temperature series.

The model tournament must not sweep the entire Kalshi settled-market universe. This module
accepts already-discovered public series metadata plus a read-only recent-settled client and
returns only series whose current settlement evidence is both daily-temperature-like and
covered by the reviewed Weather Company / physical-source authority.

Unreviewed settlement locations are preserved as explicit exclusions rather than weakening
the authoritative settlement parser. Any other malformed temperature evidence still fails
closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from services.forecasting.daily_temperature import SETTLEMENT_SOURCE
from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.settlement_dataset import (
    HistoricalWeatherDatasetError,
    build_authoritative_weather_dataset,
)

_UNREVIEWED_LOCATION = "temperature market uses an unreviewed settlement location"
_NO_SUPPORTED_MARKETS = "no supported finalized daily-temperature markets found"


class SeriesScopeError(ValueError):
    """Public series discovery or scoping violated an M28C invariant."""


class RecentSettledMarketClient(Protocol):
    def recent_settled_markets(self, *, series_ticker: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class SeriesScopeRecord:
    series_ticker: str
    decision: str
    reason: str
    raw_market_count: int
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
    supported_event_count: int
    supported_contract_count: int
    station_ids: tuple[str, ...]
    content_hash: str


def candidate_temperature_series(series_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return current Weather Company temperature series from a Weather-category response."""

    raw_series = series_payload.get("series")
    if not isinstance(raw_series, list) or any(not isinstance(row, dict) for row in raw_series):
        raise SeriesScopeError("Kalshi Weather series response is malformed")

    selected: list[str] = []
    seen: set[str] = set()
    for row in raw_series:
        ticker = row.get("ticker")
        title = row.get("title")
        sources = row.get("settlement_sources")
        if not isinstance(ticker, str) or not ticker.strip():
            raise SeriesScopeError("Weather series ticker is missing or malformed")
        if ticker in seen:
            raise SeriesScopeError("Weather series ticker is duplicated")
        seen.add(ticker)
        if not isinstance(title, str) or not title.strip():
            raise SeriesScopeError("Weather series title is missing or malformed")
        if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
            raise SeriesScopeError("Weather series settlement_sources is malformed")
        source_names: list[str] = []
        for source in sources:
            name = source.get("name")
            if not isinstance(name, str) or not name.strip():
                raise SeriesScopeError("Weather series settlement source name is malformed")
            source_names.append(name.strip())

        if "temperature" not in title.casefold():
            continue
        if SETTLEMENT_SOURCE not in source_names:
            continue
        selected.append(ticker)

    if not selected:
        raise SeriesScopeError("no current Weather Company temperature series were discovered")
    return tuple(sorted(selected))


def scope_recent_settled_markets(
    client: RecentSettledMarketClient,
    series_tickers: Sequence[str],
) -> tuple[list[dict[str, Any]], SeriesScopeManifest]:
    """Acquire only candidate series and keep reviewed current-regime settlement rows.

    An unreviewed location is an expected discovery exclusion. A series with no supported
    current-regime rows is also excluded. Every other authoritative parser failure is allowed
    to propagate so malformed reviewed evidence cannot be silently dropped.
    """

    normalized = tuple(sorted(series_tickers))
    if not normalized or any(not ticker.strip() for ticker in normalized):
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

        try:
            dataset = build_authoritative_weather_dataset(rows)
        except HistoricalWeatherDatasetError as exc:
            reason = str(exc)
            if reason == _UNREVIEWED_LOCATION:
                excluded.append(
                    _record(ticker, "EXCLUDED", "UNREVIEWED_SETTLEMENT_LOCATION", len(rows))
                )
                continue
            if reason == _NO_SUPPORTED_MARKETS:
                excluded.append(
                    _record(ticker, "EXCLUDED", "NO_SUPPORTED_CURRENT_REGIME_MARKETS", len(rows))
                )
                continue
            raise

        parsed_series = {contract.series_ticker for contract in dataset.contracts}
        if parsed_series != {ticker}:
            raise SeriesScopeError("scoped settlement rows crossed their series identity")
        station_ids = tuple(sorted({event.station_id for event in dataset.events}))
        if len(station_ids) != 1:
            raise SeriesScopeError(
                "one temperature series must bind exactly one settlement station"
            )
        missing_physical = tuple(
            station_id for station_id in station_ids if station_id not in PHYSICAL_WEATHER_SOURCES
        )
        if missing_physical:
            raise SeriesScopeError(
                "reviewed settlement series lacks reviewed physical-source evidence"
            )

        record = _record(
            ticker,
            "INCLUDED",
            "REVIEWED_CURRENT_REGIME",
            len(rows),
            supported_event_count=dataset.event_count,
            supported_contract_count=dataset.contract_count,
            station_ids=station_ids,
        )
        included.append(record)
        included_rows.extend(rows)
        all_station_ids.update(station_ids)

    if not included:
        raise SeriesScopeError("no reviewed current-regime temperature series remain after scoping")

    included_rows.sort(key=_row_ticker)
    included_tickers = tuple(record.series_ticker for record in included)
    material = (
        "m28c-reviewed-series-scope-v1",
        normalized,
        tuple(record.content_hash for record in included),
        tuple(record.content_hash for record in excluded),
        tuple(_row_ticker(row) for row in included_rows),
    )
    digest = stable_hash(material)
    manifest = SeriesScopeManifest(
        candidate_series_tickers=normalized,
        included_series_tickers=included_tickers,
        excluded_series=tuple(excluded),
        included_series=tuple(included),
        raw_market_count=len(included_rows),
        supported_event_count=sum(record.supported_event_count for record in included),
        supported_contract_count=sum(record.supported_contract_count for record in included),
        station_ids=tuple(sorted(all_station_ids)),
        content_hash=digest,
    )
    return included_rows, manifest


def _row_ticker(row: Mapping[str, Any]) -> str:
    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        raise SeriesScopeError("scoped market row is missing its ticker")
    return ticker


def _record(
    ticker: str,
    decision: str,
    reason: str,
    raw_market_count: int,
    *,
    supported_event_count: int = 0,
    supported_contract_count: int = 0,
    station_ids: tuple[str, ...] = (),
) -> SeriesScopeRecord:
    material = (
        "m28c-series-scope-record-v1",
        ticker,
        decision,
        reason,
        raw_market_count,
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
        supported_event_count=supported_event_count,
        supported_contract_count=supported_contract_count,
        station_ids=station_ids,
        content_hash=digest,
    )
