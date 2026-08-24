"""Positive, fake-transport-only M27R public reconstruction fixture."""

from __future__ import annotations

import base64
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
from services.supervised_canary import m27d, m27j
from services.supervised_canary.m27d import CandidateState, select_experimental_candidate
from services.supervised_canary.m27r_operator_runner import _selected_market_evidence
from services.supervised_canary.m27r_public_adapter import GetOnlyPublicEvidenceProvider
from tests.test_m27c_daily_temperature_contract_authority import event as build_event
from tests.test_m27c_daily_temperature_contract_authority import market as build_market
from tests.test_m27c_weather_probability import artifact as population_artifact
from tests.test_m27n2_candidate_packet import _extraction_2026

NOW = datetime(2026, 8, 20, 3, 10, tzinfo=UTC)
MARKET_TICKER = "KXHIGHCHI-26AUG20-T84"
EVENT_TICKER = "KXHIGHCHI-26AUG20"
SERIES_TICKER = "KXHIGHCHI"
OTHER_MARKET_TICKER = "KXHIGHCHI-26AUG20-T82"
OTHER_EVENT_TICKER = EVENT_TICKER


def _raw_market(
    *,
    ticker: str = MARKET_TICKER,
    event_ticker: str = EVENT_TICKER,
    floor: int = 84,
    date_text: str = "Aug 20, 2026",
) -> dict[str, object]:
    raw = dict(
        build_market(
            measurement="maximum",
            location="Chicago",
            identifier="CLIMDW",
            strike_type="greater",
            floor=floor,
            cap=None,
            date_text=date_text,
        ).raw
    )
    raw.update(
        {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "price_level_structure": "deci_cent",
            "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": ".001"}],
        }
    )
    return raw


def _raw_event(event_ticker: str = EVENT_TICKER) -> dict[str, object]:
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


def _public_response(path: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload, sort_keys=True).encode()
    return {
        "path": path,
        "observed_at": NOW.isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
        "classification": "SUCCESS",
        "payload": payload,
    }


def _m27e_public(markets: list[dict[str, object]] | None = None) -> dict[str, object]:
    discovered = [_raw_market()] if markets is None else markets
    return {
        "schema": "kalsh3.m27e.public-read.v1",
        "host": "https://" + HOST,
        "started_at": NOW.isoformat(),
        "exchange_status": _public_response(
            BASE + "/exchange/status",
            {"exchange_active": True, "trading_active": True},
        ),
        "series": _public_response(
            BASE + "/series/KXHIGHCHI",
            {"series": _series_fee_payload()},
        ),
        "markets": {
            "classification": "SUCCESS",
            "pages": [
                _public_response(
                    BASE + "/markets?series_ticker=KXHIGHCHI&limit=1000",
                    {"markets": discovered, "cursor": ""},
                )
            ],
            "pagination_complete": True,
            "market_count": len(discovered),
            "total_returned": len(discovered),
        },
    }


def _evidence(body: bytes, *, path: str, observed_at: datetime) -> dict[str, object]:
    return {
        "path": path,
        "observed_at": observed_at.isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
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

    result = provider.collect_public_evidence(clock=lambda: NOW)

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
    assert market.current_series_fee_observation.observed_at == NOW
    assert market.current_event_fee_observed_at == NOW
    assert market.m27a_binding_evidence_path.exists()
    assert market.m27j_evidence_path.exists()

    binding = json.loads(market.m27a_binding_evidence_path.read_text())
    rules = json.loads(market.m27j_evidence_path.read_text())
    assert binding["market_ticker"] == MARKET_TICKER
    assert binding["event_ticker"] == EVENT_TICKER
    assert rules["ticker"] == MARKET_TICKER
    assert rules["rules_hash"] == binding["market_rules_hash"]


def test_full_public_scope_reconstructs_and_binds_two_markets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A single public discovery scope keeps every reconstructed market slice isolated."""
    other_market = _raw_market(
        ticker=OTHER_MARKET_TICKER,
        event_ticker=OTHER_EVENT_TICKER,
        floor=82,
        date_text="Aug 20, 2026",
    )
    raw_markets = {MARKET_TICKER: _raw_market(), OTHER_MARKET_TICKER: other_market}
    raw_events = {
        EVENT_TICKER: _raw_event(),
        OTHER_EVENT_TICKER: _raw_event(OTHER_EVENT_TICKER),
    }
    orderbooks = {
        MARKET_TICKER: {"yes_dollars": [["0.050", "5"]], "no_dollars": [["0.900", "5"]]},
        OTHER_MARKET_TICKER: {
            "yes_dollars": [["0.100", "5"]],
            "no_dollars": [["0.100", "5"]],
        },
    }

    def market_acquirer(ticker: str, *, clock):
        raw = raw_markets[ticker]
        body = json.dumps({"market": raw}, sort_keys=True).encode()

        def transport(requested: str):
            assert requested == ticker
            return _evidence(
                body,
                path=f"{BASE}/markets/{ticker}",
                observed_at=clock(),
            ), body

        return acquire_market_snapshot(ticker, clock=clock, transport=transport)

    def event_acquirer(ticker: str, *, clock):
        raw = raw_events[ticker]
        body = json.dumps({"event": raw}, sort_keys=True).encode()

        def transport(requested: str):
            assert requested == ticker
            return _evidence(
                body,
                path=f"{BASE}/events/{ticker}",
                observed_at=clock(),
            ), body

        return acquire_event_snapshot(ticker, clock=clock, transport=transport)

    def orderbook_acquirer(ticker: str, *, clock):
        body = json.dumps(
            {"orderbooks": [{"ticker": ticker, "orderbook_fp": orderbooks[ticker]}]},
            sort_keys=True,
        ).encode()
        path = f"{BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})

        def transport(requested: str):
            assert requested == ticker
            return _evidence(body, path=path, observed_at=clock()), body

        return acquire_orderbook_snapshot(ticker, clock=clock, transport=transport)

    def rules_acquirer(ticker: str, *, clock):
        raw = raw_markets[ticker]
        body = json.dumps({"market": raw}, sort_keys=True).encode()

        def transport(requested: str):
            assert requested == ticker
            return _evidence(
                body,
                path=f"{BASE}/markets/{ticker}",
                observed_at=clock(),
            ), body

        return m27j.acquire_current_market_rules(ticker, clock=clock, transport=transport)

    raw_grib = parse_wgrib2_max_t_evidence(
        _extraction_2026(),
        raw_grib_sha256="m27r-multi-market-raw",
        extraction_sha256="m27r-multi-market-extraction",
    )
    provider = GetOnlyPublicEvidenceProvider(
        raw_grib_evidence=raw_grib,
        population_artifact_payload=population_artifact(lead=54_000),
        population_training_start=date(2024, 1, 1),
        population_training_end=date(2025, 6, 30),
        requested_quantity=Decimal(1),
        output_dir=tmp_path,
        public_acceptance_acquirer=lambda **_: _m27e_public(list(raw_markets.values())),
        market_snapshot_acquirer=market_acquirer,
        event_snapshot_acquirer=event_acquirer,
        orderbook_snapshot_acquirer=orderbook_acquirer,
        rules_acquirer=rules_acquirer,
    )

    result = provider.collect_public_evidence(clock=lambda: NOW)
    assert len(result.markets) == 2
    by_ticker = {market.market_ticker: market for market in result.markets}
    assert set(by_ticker) == {MARKET_TICKER, OTHER_MARKET_TICKER}

    # This synthetic population is independently validated but is not the repository's frozen
    # calibration artifact.  Admit its computed identity only for this fixture; all M27D
    # selection logic remains the real, unchanged selector.
    model_identities = {item[0].model_identity for item in result.candidate_inputs}
    assert len(model_identities) == 1
    monkeypatch.setitem(m27d.FROZEN_MODEL_IDENTITIES, 54_000, model_identities.pop())
    selection = select_experimental_candidate(result.candidate_inputs, now=NOW)
    assert selection.state is CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
    assert len(selection.candidates) == 1
    assert selection.selected is not None
    assert selection.selected.market_ticker == MARKET_TICKER

    selected = _selected_market_evidence(
        public=result,
        candidate=selection.selected,
        now=NOW,
    )
    other = by_ticker[OTHER_MARKET_TICKER]
    selected_probability, selected_forecast, selected_economics = selected.candidate_input
    other_probability, other_forecast, other_economics = other.candidate_input

    assert selected_probability.market_ticker == MARKET_TICKER
    assert selected_probability.event_ticker == EVENT_TICKER
    assert selected_probability.result_identity != other_probability.result_identity
    assert selected_forecast.local_target_date == date(2026, 8, 20)
    assert other_forecast.local_target_date == date(2026, 8, 20)
    # These contracts legitimately share one event and one physical forecast record; the
    # candidate result remains contract-specific because route semantics include the floor.
    assert selected_forecast.evidence_identity == other_forecast.evidence_identity
    assert selected_probability.result_identity != other_probability.result_identity
    assert selected_economics.market_ticker == MARKET_TICKER
    assert selected_economics.event_ticker == EVENT_TICKER
    assert selected_economics.evidence_id != other_economics.evidence_id
    assert selected_economics.market_source_id.startswith("m27r-market:")
    assert other_economics.market_source_id.startswith("m27r-market:")
    assert selected_economics.market_source_id != other_economics.market_source_id
    assert selected_economics.orderbook_source_id.startswith("m27r-orderbook:")
    assert selected_economics.orderbook_source_id != other_economics.orderbook_source_id
    assert selected_economics.yes is not None
    assert selected_economics.yes.depth.worst_price == Decimal("0.100")
    assert other_economics.yes is not None
    assert other_economics.yes.depth.worst_price == Decimal("0.900")
    assert selected.current_event_fee_observed_at == NOW
    assert other.current_event_fee_observed_at == NOW

    selected_binding = json.loads(selected.m27a_binding_evidence_path.read_text())
    other_binding = json.loads(other.m27a_binding_evidence_path.read_text())
    selected_rules = json.loads(selected.m27j_evidence_path.read_text())
    other_rules = json.loads(other.m27j_evidence_path.read_text())
    assert selected.m27a_binding_evidence_path != other.m27a_binding_evidence_path
    assert selected.m27j_evidence_path != other.m27j_evidence_path
    assert selected_binding["market_ticker"] == MARKET_TICKER
    assert other_binding["market_ticker"] == OTHER_MARKET_TICKER
    assert selected_binding["event_ticker"] == EVENT_TICKER
    assert other_binding["event_ticker"] == OTHER_EVENT_TICKER
    assert selected_binding["economics_evidence_id"] == selected_economics.evidence_id
    assert other_binding["economics_evidence_id"] == other_economics.evidence_id
    assert selected_binding["expected_snapshot"]["ticker"] == MARKET_TICKER
    assert other_binding["expected_snapshot"]["ticker"] == OTHER_MARKET_TICKER
    assert selected_binding["orderbook_source_hash"] == selected_economics.orderbook_source_hash
    assert other_binding["orderbook_source_hash"] == other_economics.orderbook_source_hash
    assert selected_binding["market_rules_hash"] == selected_rules["rules_hash"]
    assert other_binding["market_rules_hash"] == other_rules["rules_hash"]
    assert selected_rules["ticker"] == MARKET_TICKER
    assert other_rules["ticker"] == OTHER_MARKET_TICKER
    assert MARKET_TICKER not in other_binding["market_ticker"]
    assert OTHER_MARKET_TICKER not in selected_binding["market_ticker"]
    # Event-level fee metadata is legitimately shared because both contracts are under the same
    # event.  The per-market economics, market body, orderbook, and rules evidence are not shared.
    assert selected_economics.event_fee_hash == other_economics.event_fee_hash
