"""Deterministic M28C scoping for current-regime public temperature series.

The model tournament must not sweep the entire Kalshi settled-market universe. This module
accepts already-discovered public series metadata plus a read-only recent-settled client and
returns only series whose current settlement evidence is both daily-temperature-like and
covered by the reviewed Weather Company / physical-source authority.

Legacy National Weather Service rows from the pre-August-2026 settlement regime are counted
and excluded explicitly before current-regime parsing. Unreviewed current settlement locations
remain explicit exclusions. Every other malformed current-regime temperature row still fails
closed rather than weakening the authoritative settlement parser.
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
    build_authoritative_weather_dataset,
    parse_resolved_temperature_market,
)

_UNREVIEWED_LOCATION = "temperature market uses an unreviewed settlement location"
_NO_SUPPORTED_MARKETS = "no supported finalized daily-temperature markets found"
_WEATHER_CATEGORIES = frozenset({"Weather", "Climate and Weather"})
_LEGACY_NWS_SOURCE = "according to the National Weather Service's Climatological Report (Daily)"


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
    content_hash: str


def candidate_temperature_series(series_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return current daily-temperature series from reviewed weather categories.

    Series-level settlement-source metadata is intentionally not authoritative here. The
    daily-temperature series crossed settlement regimes in place, so current-regime Weather
    Company authority is established later from the actual settled market rules by
    ``build_authoritative_weather_dataset``.
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
        category = row.get("category")
        if category not in _WEATHER_CATEGORIES:
            continue

        ticker = row.get("ticker")
        title = row.get("title")
        frequency = row.get("frequency")
        if not isinstance(ticker, str) or not ticker.strip():
            raise SeriesScopeError("Weather series ticker is missing or malformed")
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
    """Acquire candidate series and keep only reviewed current-regime settlement rows.

    The pre-transition NWS grammar is an explicit historical regime exclusion, not a parser
    failure and not evidence for current Weather Company settlement semantics. Unreviewed
    current locations are also explicit discovery exclusions. Every other current-regime
    parser failure propagates.
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

        current_rows, legacy_count = _current_regime_rows(rows)
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

        try:
            dataset = build_authoritative_weather_dataset(tuple(current_rows))
        except HistoricalWeatherDatasetError as exc:
            reason = str(exc)
            if reason == _UNREVIEWED_LOCATION:
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
            if reason == _NO_SUPPORTED_MARKETS:
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
            failure = _first_row_parser_failure(current_rows)
            raise HistoricalWeatherDatasetError(
                f"{ticker}: {reason}; first_row_parser_failure={failure!r}"
            ) from exc

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
            legacy_regime_market_count=legacy_count,
            supported_event_count=dataset.event_count,
            supported_contract_count=dataset.contract_count,
            station_ids=station_ids,
        )
        included.append(record)
        included_rows.extend(current_rows)
        all_station_ids.update(station_ids)

    if not included:
        raise SeriesScopeError("no reviewed current-regime temperature series remain after scoping")

    included_rows.sort(key=_row_ticker)
    included_tickers = tuple(record.series_ticker for record in included)
    legacy_total = sum(record.legacy_regime_market_count for record in (*included, *excluded))
    material = (
        "m28c-reviewed-series-scope-v2",
        normalized,
        tuple(record.content_hash for record in included),
        tuple(record.content_hash for record in excluded),
        tuple(_row_ticker(row) for row in included_rows),
        legacy_total,
    )
    digest = stable_hash(material)
    manifest = SeriesScopeManifest(
        candidate_series_tickers=normalized,
        included_series_tickers=included_tickers,
        excluded_series=tuple(excluded),
        included_series=tuple(included),
        raw_market_count=len(included_rows),
        legacy_regime_market_count=legacy_total,
        supported_event_count=sum(record.supported_event_count for record in included),
        supported_contract_count=sum(record.supported_contract_count for record in included),
        station_ids=tuple(sorted(all_station_ids)),
        content_hash=digest,
    )
    return included_rows, manifest


def _current_regime_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    current: list[dict[str, Any]] = []
    legacy_count = 0
    for row in rows:
        rule = row.get("rules_primary")
        if isinstance(rule, str) and _LEGACY_NWS_SOURCE in rule:
            legacy_count += 1
            continue
        current.append(dict(row))
    return current, legacy_count


def _first_row_parser_failure(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, str] | None:
    for row in rows:
        try:
            parse_resolved_temperature_market(row)
        except HistoricalWeatherDatasetError as exc:
            ticker = row.get("ticker")
            rule = row.get("rules_primary")
            return (
                ticker if isinstance(ticker, str) else type(ticker).__name__,
                str(exc),
                rule[:1000] if isinstance(rule, str) else type(rule).__name__,
            )
    return None


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
    legacy_regime_market_count: int = 0,
    supported_event_count: int = 0,
    supported_contract_count: int = 0,
    station_ids: tuple[str, ...] = (),
) -> SeriesScopeRecord:
    material = (
        "m28c-series-scope-record-v2",
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
