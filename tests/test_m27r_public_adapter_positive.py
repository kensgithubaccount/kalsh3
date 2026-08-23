"""Positive, fake-transport-only M27R public reconstruction fixture."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.market_universe.event_snapshot import acquire_event_snapshot
from services.market_universe.market_snapshot import acquire_market_snapshot
from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.market_universe.public_read import BASE, HOST
from services.supervised_canary import m27j
from services.supervised_canary.m27r_public_adapter import GetOnlyPublicEvidenceProvider
from tests.test_m27c_daily_temperature_contract_authority import event as build_event
from tests.test_m27c_daily_temperature_contract_authority import market as build_market
from tests.test_m27c_weather_probability import artifact as population_artifact
from tests.test_m27n2_candidate_packet import _extraction_2026

NOW = datetime(2026, 8, 20, 3, 10, tzinfo=UTC)
MARKET_TICKER = "KXHIGHCHI-26AUG20-T84"
EVENT_TICKER = "KXHIGHCHI-26AUG20"
SERIES_TICKER = "KXHIGHCHI"


def _raw_market() -> dict[str, object]:
    raw = dict(
        build_market(
            measurement="maximum",
            location="Chicago",
            identifier="CLIMDW",
            strike_type="greater",
            floor=84,
            cap=None,
            date_text="Aug 20, 2026",
        ).raw
    )
    raw.update(
        {
            "ticker": MARKET_TICKER,
            "event_ticker": EVENT_TICKER,
            "price_level_structure": "deci_cent",
            "price_ranges": [
                {"start": "0.0000", "end": "1.0000", "step": ".001"}
            ],
        }
    )
    return raw


def _raw_event() -> dict[str, object]:
    return dict(
        build_event(
            event_ticker=EVENT_TICKER,
            series_ticker=SERIES_TICKER,
        ).raw
    )


def _series_fee_payload() -> dict[str, object]:
    return {
        "ticker": SERIES_TICKER,
        "title": "Chicago high temperature",
        "category": "Weather",
        "frequency": "daily",
        "tags": [],
        "settlement_sources": [],
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": "1",
        "last_updated_ts": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }


def _m27e_public() -> dict[str, object]:
    market = _raw_market()
    return {
        "schema": "kalsh3.m27e.public-read.v1",
        "host": "https://" + HOST,
        "started_at": NOW.isoformat(),
        "exchange_status": {"classification": "SUCCESS", "payload": {}},
        "series": {
            "classification": "SUCCESS",
            "payload": {"series": _series_fee_payload()},
        },
        "markets": {
            "classification": "SUCCESS",
            "pages": [
                {
                    "classification": "SUCCESS",
                    "payload": {"markets": [market], "cursor": ""},
                }
            ],
            "pagination_complete": True,
            "market_count": 1,
            "total_returned": 1,
        },
    }


def _evidence(body: bytes, *, path: str, observed_at: datetime) -> dict[str, object]:
    return {
        "path": path,
        "observed_at": observed_at.isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "classification": "SUCCESS",
        "payload": json.loads(body),
    }


def _market_acquirer(ticker: str, *, clock):
    raw = _raw_market()
    body = json.dumps({"market": raw}, sort_keys=True).encode()

    def transport(requested: str):
        assert requested == ticker == MARKET_TICKER
        return _evidence(
            body,
            path=f"{BASE}/markets/{ticker}",
            observed_at=clock(),
        ), body

    return acquire_market_snapshot(ticker, clock=clock, transport=transport)


def _event_acquirer(ticker: str, *, clock):
    raw = _raw_event()
    body = json.dumps({"event": raw}, sort_keys=True).encode()

    def transport(requested: str):
        assert requested == ticker == EVENT_TICKER
        return _evidence(
            body,
            path=f"{BASE}/events/{ticker}",
            observed_at=clock(),
        ), body

    return acquire_event_snapshot(ticker, clock=clock, transport=transport)


def _orderbook_acquirer(ticker: str, *, clock):
    payload = {
        "orderbooks": [
            {
                "ticker": MARKET_TICKER,
                "orderbook_fp": {
                    "yes_dollars": [["0.050", "5"]],
                    "no_dollars": [["0.900", "5"]],
                },
            }
        ]
    }
    body = json.dumps(payload, sort_keys=True).encode()
    path = f"{BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})

    def transport(requested: str):
        assert requested == ticker == MARKET_TICKER
        return _evidence(body, path=path, observed_at=clock()), body

    return acquire_orderbook_snapshot(ticker, clock=clock, transport=transport)


def _rules_acquirer(ticker: str, *, clock):
    raw = _raw_market()
    body = json.dumps({"market": raw}, sort_keys=True).encode()

    def transport(requested: str):
        assert requested == ticker == MARKET_TICKER
        return _evidence(
            body,
            path=f"{BASE}/markets/{ticker}",
            observed_at=clock(),
        ), body

    return m27j.acquire_current_market_rules(ticker, clock=clock, transport=transport)


def test_full_public_scope_reconstructs_one_exact_market_slice(tmp_path: Path) -> None:
    raw_grib = parse_wgrib2_max_t_evidence(
        _extraction_2026(),
        raw_grib_sha256="m27r-positive-raw",
        extraction_sha256="m27r-positive-extraction",
    )
    provider = GetOnlyPublicEvidenceProvider(
        raw_grib_evidence=raw_grib,
        population_artifact_payload=population_artifact(lead=54_000),
        population_training_start=date(2024, 1, 1),
        population_training_end=date(2025, 6, 30),
        requested_quantity=Decimal(1),
        output_dir=tmp_path,
        public_acceptance_acquirer=lambda **_: _m27e_public(),
        market_snapshot_acquirer=_market_acquirer,
        event_snapshot_acquirer=_event_acquirer,
        orderbook_snapshot_acquirer=_orderbook_acquirer,
        rules_acquirer=_rules_acquirer,
    )

    result = provider.collect_public_evidence(now=NOW)

    assert len(result.markets) == 1
    market = result.markets[0]
    probability, current, economics = market.candidate_input
    assert market.market_ticker == MARKET_TICKER
    assert probability.market_ticker == MARKET_TICKER
    assert probability.event_ticker == EVENT_TICKER
    assert probability.series_ticker == SERIES_TICKER
    assert current.local_target_date == date(2026, 8, 20)
    assert current.record_number == 1
    assert economics.market_ticker == MARKET_TICKER
    assert economics.event_ticker == EVENT_TICKER
    assert economics.series_ticker == SERIES_TICKER
    assert market.current_series_fee_observation.series_ticker == SERIES_TICKER
    assert market.m27a_binding_evidence_path.exists()
    assert market.m27j_evidence_path.exists()

    binding = json.loads(market.m27a_binding_evidence_path.read_text())
    rules = json.loads(market.m27j_evidence_path.read_text())
    assert binding["market_ticker"] == MARKET_TICKER
    assert binding["event_ticker"] == EVENT_TICKER
    assert rules["ticker"] == MARKET_TICKER
    assert rules["rules_hash"] == binding["market_rules_hash"]
