#!/usr/bin/env python3
"""Run the M28C multi-city public-data weather model tournament.

PUBLIC READ ONLY. The runner discovers current Weather Company temperature series, scopes
settled Kalshi reads to reviewed settlement locations, acquires public Kalshi candlesticks at
the fixed 03Z decision checkpoint, and reads official NOAA/NCEI GHCN-Daily files. It writes
create-only evidence and stops at model evaluation. It never accesses credentials, account
state, production-control state, risk authorization, approval, execution authority, burn
state, or an order endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from services.forecasting.weather_calibration import parse_ghcnd_daily
from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.historical_replay.archive import stable_hash
from services.historical_replay.client import HistoricalClient, HistoricalError
from services.production_weather_strategy.contracts import (
    ModelArtifact,
    ModelState,
    SettlementLabelManifest,
    TemporalSplit,
    TrainingDatasetManifest,
)
from services.production_weather_strategy.model_tournament import (
    ClimateHistory,
    ClimateObservation,
    MarketCheckpoint,
    ModelTournamentError,
    TournamentPartition,
    build_feature_dataset,
    run_model_tournament,
)
from services.production_weather_strategy.series_scope import (
    SeriesScopeError,
    SeriesScopeManifest,
    candidate_temperature_series,
    scope_recent_settled_markets,
)
from services.production_weather_strategy.settlement_dataset import (
    HistoricalWeatherDatasetError,
    build_authoritative_weather_dataset,
)

KALSHI_ORIGIN = "https://external-api.kalshi.com"
NCEI_ORIGIN = "https://www.ncei.noaa.gov"
GHCND_ROOT = f"{NCEI_ORIGIN}/pub/data/ghcn/daily/all"
SCHEMA = "kalsh3.m28c.public-weather-model-tournament.v2"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
TIMEOUT_SECONDS = 25
MAX_BATCH_TICKERS = 100
CANDLE_LOOKBACK = timedelta(hours=24)
PREDICTION_CUTOFF_HOUR_UTC = 3


class PublicTransport:
    """Fixed-origin, unauthenticated public Kalshi GET transport with hashes."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        if headers:
            raise RuntimeError("M28C public transport forbids authorization headers")
        if not (
            path.startswith("/trade-api/v2/markets?")
            or path.startswith("/trade-api/v2/markets/candlesticks?")
            or path.startswith("/trade-api/v2/series?")
        ):
            raise RuntimeError("M28C public transport path is outside reviewed prefixes")
        status, body = _fixed_origin_get(KALSHI_ORIGIN, path, timeout_seconds=timeout_seconds)
        self.requests.append(
            {
                "path": path,
                "status": status,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("M28C Kalshi response was not JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("M28C Kalshi response root was not an object")
        return status, payload


def _fixed_origin_get(origin: str, path: str, *, timeout_seconds: float) -> tuple[int, bytes]:
    target = urljoin(origin, path)
    parsed = urlparse(target)
    expected = urlparse(origin)
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise RuntimeError("public GET escaped its fixed HTTPS origin")
    request = Request(  # noqa: S310
        target,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "kalsh3-m28c-public/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            final = urlparse(response.geturl())
            if (final.scheme, final.netloc) != (expected.scheme, expected.netloc):
                raise RuntimeError("public GET redirect escaped fixed origin")
            status = int(response.status)
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = int(exc.code)
        body = exc.read(MAX_RESPONSE_BYTES + 1)
    except URLError as exc:
        raise RuntimeError("bounded public GET failed") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("public response exceeded size bound")
    return status, body


def _ncei_get(station_id: str) -> tuple[bytes, dict[str, object]]:
    if not station_id.isalnum():
        raise RuntimeError("NCEI station id is malformed")
    path = f"/pub/data/ghcn/daily/all/{station_id}.dly"
    status, body = _fixed_origin_get(NCEI_ORIGIN, path, timeout_seconds=TIMEOUT_SECONDS)
    if status != 200:
        raise RuntimeError(f"NCEI GHCN-Daily status {status}")
    return body, {
        "path": path,
        "status": status,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _settled_markets(
    client: HistoricalClient,
    transport: PublicTransport,
) -> tuple[list[dict[str, Any]], SeriesScopeManifest]:
    # The live API returned {"series": null} for category=Weather even though the
    # documented list endpoint supports an optional category filter. One unfiltered
    # metadata GET is still bounded and dramatically cheaper than sweeping all settled
    # markets; candidate selection then uses title + exact settlement-source metadata.
    path = "/trade-api/v2/series?include_volume=false"
    status, payload = transport.get(path, {}, timeout_seconds=TIMEOUT_SECONDS)
    if status != 200:
        raise RuntimeError(f"Kalshi series discovery status {status}")
    candidate_series = candidate_temperature_series(payload)
    return scope_recent_settled_markets(client, candidate_series)


def _market_checkpoints(
    transport: PublicTransport,
    contracts: Sequence[Any],
) -> dict[str, MarketCheckpoint]:
    by_checkpoint: dict[datetime, list[Any]] = defaultdict(list)
    for contract in contracts:
        checkpoint = datetime.combine(
            contract.local_date, time(PREDICTION_CUTOFF_HOUR_UTC), tzinfo=UTC
        )
        by_checkpoint[checkpoint].append(contract)

    output: dict[str, MarketCheckpoint] = {}
    for checkpoint, grouped in sorted(by_checkpoint.items()):
        for start in range(0, len(grouped), MAX_BATCH_TICKERS):
            batch = grouped[start : start + MAX_BATCH_TICKERS]
            tickers = [contract.market_ticker for contract in batch]
            start_ts = int((checkpoint - CANDLE_LOOKBACK).timestamp())
            end_ts = int(checkpoint.timestamp())
            query = urlencode(
                {
                    "market_tickers": ",".join(tickers),
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "period_interval": 60,
                    "include_latest_before_start": "true",
                }
            )
            path = f"/trade-api/v2/markets/candlesticks?{query}"
            status, payload = transport.get(path, {}, timeout_seconds=TIMEOUT_SECONDS)
            if status != 200:
                raise RuntimeError(f"Kalshi batch candlestick status {status}")
            raw_markets = payload.get("markets")
            if not isinstance(raw_markets, list):
                raise RuntimeError("Kalshi batch candlestick response is malformed")
            request_hash = transport.requests[-1]["sha256"]
            seen: set[str] = set()
            for item in raw_markets:
                if not isinstance(item, dict):
                    raise RuntimeError("Kalshi batch candlestick item is malformed")
                ticker = item.get("market_ticker")
                candles = item.get("candlesticks")
                if not isinstance(ticker, str) or not isinstance(candles, list):
                    raise RuntimeError("Kalshi batch candlestick identity is malformed")
                if ticker in seen:
                    raise RuntimeError("Kalshi batch candlestick ticker is duplicated")
                seen.add(ticker)
                selected = _latest_candle(candles, end_ts)
                if selected is None:
                    continue
                probability = _market_probability(selected)
                if probability is None:
                    continue
                evidence_id = stable_hash(
                    (
                        "m28c-market-checkpoint-v1",
                        path,
                        request_hash,
                        ticker,
                        selected,
                        str(probability),
                    )
                )
                output[ticker] = MarketCheckpoint(
                    market_ticker=ticker,
                    checkpoint_at=checkpoint,
                    yes_probability=probability,
                    evidence_id=evidence_id,
                )
    return output


def _latest_candle(candles: Sequence[object], end_ts: int) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            raise RuntimeError("Kalshi candlestick row is malformed")
        period = candle.get("end_period_ts")
        if isinstance(period, bool) or not isinstance(period, int):
            raise RuntimeError("Kalshi candlestick period is malformed")
        if period <= end_ts:
            candidates.append(candle)
    if not candidates:
        return None
    return max(candidates, key=lambda candle: int(candle["end_period_ts"]))


def _market_probability(candle: Mapping[str, Any]) -> Decimal | None:
    bid = _nested_decimal(candle, "yes_bid", ("close_dollars", "close"))
    ask = _nested_decimal(candle, "yes_ask", ("close_dollars", "close"))
    if bid is not None and ask is not None and bid <= ask:
        return (bid + ask) / Decimal(2)
    close = _nested_decimal(candle, "price", ("close_dollars", "close"))
    if close is not None:
        return close
    previous = _nested_decimal(candle, "price", ("previous_dollars", "previous"))
    return previous


def _nested_decimal(
    payload: Mapping[str, Any],
    field: str,
    names: Sequence[str],
) -> Decimal | None:
    nested = payload.get(field)
    if not isinstance(nested, dict):
        return None
    value: object | None = None
    for name in names:
        if nested.get(name) is not None:
            value = nested.get(name)
            break
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("1"):
        return None
    return result


def _climate_histories(
    station_ids: Sequence[str], acquired_at: datetime
) -> tuple[dict[str, ClimateHistory], list[dict[str, object]]]:
    histories: dict[str, ClimateHistory] = {}
    requests: list[dict[str, object]] = []
    for station_id in sorted(set(station_ids)):
        source = PHYSICAL_WEATHER_SOURCES.get(station_id)
        if source is None:
            raise RuntimeError("settlement dataset references an unreviewed physical station")
        raw, request = _ncei_get(source.ghcnd_station_id)
        requests.append(request)
        snapshot = parse_ghcnd_daily(raw, source, acquired_at)
        observations = [
            ClimateObservation(
                station_id=station_id,
                measurement=row.measurement.value,
                local_date=row.local_date,
                temperature_deg_f=row.observed_deg_f,
                source_evidence_id=snapshot.evidence_identity,
            )
            for row in snapshot.observations
            if row.usable and row.observed_deg_f is not None
        ]
        histories[station_id] = ClimateHistory.build(station_id, observations)
    return histories, requests


def _temporal_split_from_features(rows: Sequence[Any]) -> TemporalSplit:
    train_dates = sorted(
        {row.local_date for row in rows if row.partition is TournamentPartition.TRAIN}
    )
    validation_dates = sorted(
        {row.local_date for row in rows if row.partition is TournamentPartition.VALIDATION}
    )
    test_dates = sorted(
        {row.local_date for row in rows if row.partition is TournamentPartition.TEST}
    )
    if not train_dates or not validation_dates or not test_dates:
        raise RuntimeError("M28C feature temporal partitions are incomplete")
    train_start = datetime.combine(train_dates[0], time(0), tzinfo=UTC)
    validation_start = datetime.combine(validation_dates[0], time(0), tzinfo=UTC)
    test_start = datetime.combine(test_dates[0], time(0), tzinfo=UTC)
    test_end = datetime.combine(test_dates[-1] + timedelta(days=1), time(0), tzinfo=UTC)
    return TemporalSplit(
        train_start=train_start,
        train_end=validation_start,
        validation_start=validation_start,
        validation_end=test_start,
        test_start=test_start,
        test_end=test_end,
    )


def _m28_contract_artifacts(
    *,
    settlement_dataset: Any,
    features: Any,
    tournament: Any,
    acquired_at: datetime,
) -> tuple[SettlementLabelManifest, TrainingDatasetManifest, ModelArtifact]:
    used_tickers = tuple(sorted({row.market_ticker for row in features.rows}))
    used_event_ids = tuple(sorted({row.event_id for row in features.rows}))
    contract_by_ticker = {
        contract.market_ticker: contract for contract in settlement_dataset.contracts
    }
    resolved_at = max(contract_by_ticker[ticker].settlement_at for ticker in used_tickers)
    settlement_mapping_id = stable_hash(
        (settlement_dataset.parser_version, settlement_dataset.label_authority)
    )
    labels = SettlementLabelManifest.build(
        settlement_mapping_id=settlement_mapping_id,
        authority=settlement_dataset.label_authority,
        event_ids=used_event_ids,
        market_tickers=used_tickers,
        resolved_at=resolved_at,
    )
    split = _temporal_split_from_features(features.rows)
    feature_artifact_ids = tuple(
        sorted(
            {row.market_evidence_id for row in features.rows}
            | {row.climate_evidence_id for row in features.rows}
        )
    )
    training = TrainingDatasetManifest.build(
        family="DAILY_TEMPERATURE",
        feature_schema_hash=features.feature_schema_hash,
        feature_artifact_ids=feature_artifact_ids,
        settlement_labels=labels,
        temporal_split=split,
        prediction_cutoff_rule=(
            "03:00:00Z on target local-date label; latest public 60-minute Kalshi candle "
            "ending at-or-before cutoff; NOAA GHCN-Daily climatology uses prior calendar "
            "years only with a +/-15-day seasonal window"
        ),
        created_at=acquired_at,
    )
    fit = tournament.fit
    model = ModelArtifact.build(
        family="DAILY_TEMPERATURE",
        algorithm=tournament.fit.validation_selected_model.value,
        hyperparameters=(
            ("pooled_alpha", str(fit.pooled_alpha)),
            ("pooled_bias", str(fit.pooled_bias)),
            ("city_bias_hash", stable_hash(fit.city_biases)),
            ("calibration_slope", str(fit.calibration_slope)),
            ("calibration_offset", str(fit.calibration_offset)),
            ("ensemble_weight", str(fit.ensemble_weight)),
        ),
        feature_schema_hash=features.feature_schema_hash,
        training_manifest=training,
        calibration_method="train-affine + validation-selected market ensemble",
        parent_model_id=None,
        trained_at=datetime.now(UTC),
        state=ModelState.DEVELOPMENT,
    )
    return labels, training, model


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return value.value  # type: ignore[union-attr]
    return str(value)


def _write_create_only(path: Path, payload: dict[str, object]) -> str:
    if path.exists():
        raise RuntimeError("M28C output already exists; evidence is create-only")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = json.dumps(payload, default=_json_default, indent=2, sort_keys=True).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(OSError):
            path.unlink()
        raise
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    acquired_at = datetime.now(UTC)
    transport = PublicTransport()
    ncei_requests: list[dict[str, object]] = []
    series_scope: SeriesScopeManifest | None = None
    try:
        client = HistoricalClient(transport, signer=None, timeout=TIMEOUT_SECONDS)
        settled_rows, series_scope = _settled_markets(client, transport)
        settlements = build_authoritative_weather_dataset(settled_rows)
        station_ids = sorted({event.station_id for event in settlements.events})
        if len(station_ids) < 2:
            raise ModelTournamentError("multi-city M28C requires at least two settlement stations")
        checkpoints = _market_checkpoints(transport, settlements.contracts)
        histories, ncei_requests = _climate_histories(station_ids, acquired_at)
        features = build_feature_dataset(
            settlements,
            market_checkpoints=checkpoints,
            climate_histories=histories,
        )
        tournament = run_model_tournament(features)
        labels, training, model = _m28_contract_artifacts(
            settlement_dataset=settlements,
            features=features,
            tournament=tournament,
            acquired_at=acquired_at,
        )
    except (
        HistoricalError,
        HistoricalWeatherDatasetError,
        ModelTournamentError,
        RuntimeError,
        SeriesScopeError,
    ) as exc:
        print(f"M28C_CLASSIFICATION=BLOCKED ({type(exc).__name__}: {exc})", file=sys.stderr)
        if series_scope is not None:
            print(f"CANDIDATE_SERIES={len(series_scope.candidate_series_tickers)}")
            print(f"INCLUDED_REVIEWED_SERIES={len(series_scope.included_series_tickers)}")
            print(f"EXCLUDED_SERIES={len(series_scope.excluded_series)}")
        print(f"KALSHI_NETWORK_REQUESTS={len(transport.requests)}")
        print(f"NCEI_NETWORK_REQUESTS={len(ncei_requests)}")
        print("REQUEST_TYPE=PUBLIC_GET_ONLY")
        print("CREDENTIAL_ACCESS=NONE")
        print("ACCOUNT_GETS=NONE")
        print("PRODUCTION_STATE_MUTATION=NONE")
        print("RISK_AUTHORIZATION=NONE")
        print("APPROVAL=NONE")
        print("EXECUTION_AUTHORIZATION=NONE")
        print("BURN=NONE")
        print("ORDER_SENT=NO")
        return 21

    if series_scope is None:
        raise RuntimeError("M28C series scope was not produced on successful evaluation")
    test_selected = tournament.selected_test_scorecard
    test_market = tournament.test_market_scorecard
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "classification": "SUCCESS",
        "acquired_at": acquired_at,
        "request_type": "PUBLIC_GET_ONLY",
        "kalshi_origin": KALSHI_ORIGIN,
        "ncei_origin": NCEI_ORIGIN,
        "series_scope": asdict(series_scope),
        "settled_market_count": len(settled_rows),
        "authoritative_weather_event_count": settlements.event_count,
        "authoritative_weather_contract_count": settlements.contract_count,
        "represented_station_ids": station_ids,
        "represented_station_count": len(station_ids),
        "settlement_dataset_id": settlements.dataset_id,
        "market_checkpoint_count": len(checkpoints),
        "feature_dataset": asdict(features),
        "tournament": asdict(tournament),
        "settlement_label_manifest": asdict(labels),
        "training_dataset_manifest": asdict(training),
        "development_model_artifact": asdict(model),
        "test_summary": {
            "selected_model": test_selected.model.value,
            "selected_event_weighted_brier": test_selected.event_weighted_brier,
            "market_event_weighted_brier": test_market.event_weighted_brier,
            "market_relative_skill": test_selected.market_relative_skill,
            "edge_classification": tournament.test_edge_classification,
            "hypothetical_trade_count": test_selected.hypothetical_trade_count,
            "hypothetical_total_pnl": test_selected.hypothetical_total_pnl,
            "hypothetical_friction_per_contract": "0.02",
            "promotion_authority": tournament.promotion_authority,
        },
        "kalshi_network_requests": transport.requests,
        "ncei_network_requests": ncei_requests,
        "credential_access": "NONE",
        "account_gets": "NONE",
        "production_state_mutation": "NONE",
        "risk_authorization": "NONE",
        "approval": "NONE",
        "execution_authorization": "NONE",
        "burn": "NONE",
        "order_sent": "NO",
    }
    output_hash = _write_create_only(output, payload)
    print("M28C_CLASSIFICATION=SUCCESS")
    print(f"OUTPUT={output}")
    print(f"OUTPUT_SHA256={output_hash}")
    print(f"CANDIDATE_SERIES={len(series_scope.candidate_series_tickers)}")
    print(f"INCLUDED_REVIEWED_SERIES={len(series_scope.included_series_tickers)}")
    print(f"EXCLUDED_SERIES={len(series_scope.excluded_series)}")
    print(f"SETTLED_MARKETS={len(settled_rows)}")
    print(f"WEATHER_EVENTS={settlements.event_count}")
    print(f"WEATHER_CONTRACTS={settlements.contract_count}")
    print(f"STATIONS={len(station_ids)}")
    print(f"FEATURE_ROWS={len(features.rows)}")
    print(f"FEATURE_EVENTS={features.unique_event_count}")
    print(f"MISSING_MARKET_CONTRACTS={features.missing_market_contracts}")
    print(f"MISSING_CLIMATE_CONTRACTS={features.missing_climate_contracts}")
    print(f"SELECTED_MODEL={test_selected.model.value}")
    print(f"TEST_MODEL_BRIER={test_selected.event_weighted_brier}")
    print(f"TEST_MARKET_BRIER={test_market.event_weighted_brier}")
    print(f"TEST_MARKET_RELATIVE_SKILL={test_selected.market_relative_skill}")
    print(f"TEST_EDGE_CLASSIFICATION={tournament.test_edge_classification}")
    print(f"HYPOTHETICAL_TEST_TRADES={test_selected.hypothetical_trade_count}")
    print(f"HYPOTHETICAL_TEST_PNL={test_selected.hypothetical_total_pnl}")
    print(f"TRAINING_MANIFEST_ID={training.manifest_id}")
    print(f"DEVELOPMENT_MODEL_ID={model.model_id}")
    print(f"KALSHI_NETWORK_REQUESTS={len(transport.requests)}")
    print(f"NCEI_NETWORK_REQUESTS={len(ncei_requests)}")
    print("REQUEST_TYPE=PUBLIC_GET_ONLY")
    print("CREDENTIAL_ACCESS=NONE")
    print("ACCOUNT_GETS=NONE")
    print("PRODUCTION_STATE_MUTATION=NONE")
    print("RISK_AUTHORIZATION=NONE")
    print("APPROVAL=NONE")
    print("EXECUTION_AUTHORIZATION=NONE")
    print("BURN=NONE")
    print("ORDER_SENT=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
