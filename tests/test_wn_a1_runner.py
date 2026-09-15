from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from services.forecasting.wn_a1_current_daily_high_authority import (
    CurrentDailyHighRouteState,
)
from services.forecasting.wn_a1_domain import AlertState
from services.forecasting.wn_a1_probability import compute_member_daily_highs
from services.forecasting.wn_a1_runner import (
    discover_event,
    evaluate_candidate,
    event_ticker_for,
    select_candidate,
)
from services.forecasting.wn_a1_weathernext_evidence import (
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    EnsembleStatus,
    build_ensemble_evidence,
)
from tests.test_wn_a1_current_daily_high_authority import EARLY_CLOSE, RULES_SECONDARY, series_raw

INIT_TIME = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SOURCE_OBJECT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/20260914_12hr_XX_preds/predictions.zarr/"


def _fake_response(payload: dict, path: str, observed_at: datetime) -> dict:
    body = json.dumps(payload).encode()
    return {
        "path": path,
        "observed_at": observed_at.isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
        "classification": "SUCCESS",
        "payload": payload,
    }


def _market_row(ticker: str, strike_type: str, floor_strike, cap_strike, rule_bounds: str) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": "KXHIGHCHI-26SEP15",
        "market_type": "binary",
        "status": "active",
        "rules_primary": (
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            f"{rule_bounds}\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
        "rules_secondary": RULES_SECONDARY,
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
        "strike_type": strike_type,
        "floor_strike": floor_strike,
        "cap_strike": cap_strike,
        "early_close_condition": EARLY_CLOSE,
    }


def test_event_ticker_naming_matches_observed_live_convention() -> None:
    assert event_ticker_for(date(2026, 9, 15)) == "KXHIGHCHI-26SEP15"


def test_discover_event_routes_every_sibling_market() -> None:
    markets = [
        _market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87"),
        _market_row("KXHIGHCHI-26SEP15-T80", "less", None, 80, "less than 80"),
    ]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "Highest temperature in Chicago on Sep 15, 2026?",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    def fake_get_event(ticker: str):
        return _fake_response(event_payload, "/trade-api/v2/events/" + ticker, now), b""

    def fake_get_series(path: str):
        return _fake_response({"series": series_raw()}, path, now)

    discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15", get_event=fake_get_event, get_series=fake_get_series
    )
    assert len(discovery.routes) == 2
    assert len(discovery.supported) == 2
    assert all(r.state is CurrentDailyHighRouteState.SUPPORTED for r in discovery.routes)
    contracts = discovery.contracts_by_ticker
    assert contracts["KXHIGHCHI-26SEP15-T87"].comparator == "GT"
    assert contracts["KXHIGHCHI-26SEP15-T80"].comparator == "LT"


def _synthetic_members(kelvin_by_sample: dict[int, Decimal]):
    rows = [
        {
            "sample": s,
            "lead_time_hours": 0,
            "lead_subtime_minutes": 0,
            "valid_time": INIT_TIME,
            "value_kelvin": k,
        }
        for s, k in kelvin_by_sample.items()
    ]
    result = build_ensemble_evidence(
        model=MODEL,
        source_object=SOURCE_OBJECT,
        init_time=INIT_TIME,
        acquired_at=INIT_TIME,
        variable=VARIABLE,
        latitude=Decimal("41.80"),
        longitude=Decimal("-87.75"),
        unit=UNIT,
        raw_rows=rows,
        source_content_hash="c" * 64,
    )
    assert result.status is EnsembleStatus.COMPLETE
    assert result.evidence is not None
    members = compute_member_daily_highs(result.evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    return result.evidence, members


def _t87_contract():
    markets = [_market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15",
        get_event=lambda t: (_fake_response(event_payload, "x", now), b""),
        get_series=lambda p: _fake_response({"series": series_raw()}, p, now),
    )
    return discovery.contracts_by_ticker


def test_end_to_end_take_a_look_example_with_fake_transports() -> None:
    """A synthetic, clearly-fake-transport end-to-end run: WeatherNext -> alert = TAKE A LOOK."""
    contracts = _t87_contract()
    # 42/64 members (~66%) forecast hot enough to trigger the >87F contract.
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.27"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.TAKE_A_LOOK
    assert "TAKE A LOOK" in outcome.primary_alert_text
    assert "Nothing has been bought." in outcome.primary_alert_text
    assert outcome.record.decision_state == "TAKE A LOOK"


def test_end_to_end_skip_example_with_fake_transports() -> None:
    contracts = _t87_contract()
    # 18/64 members (~28%) clear 87F; close to the 25% market price -> small gap -> SKIP.
    kelvin = {i: Decimal("304.5") if i < 18 else Decimal("300.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.25"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.SKIP
    assert "SKIP" in outcome.primary_alert_text


def test_data_not_ready_when_ensemble_missing() -> None:
    contracts = _t87_contract()
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=None,
        weathernext_evidence_identity=None,
        ensemble_status=EnsembleStatus.INCOMPLETE,
        snapshot=_fake_snapshot(Decimal("0.25"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.DATA_NOT_READY
    assert "DATA NOT READY" in outcome.primary_alert_text


def test_select_candidate_picks_the_largest_gap() -> None:
    contracts = {
        **_t87_contract(),
    }
    markets = [_market_row("KXHIGHCHI-26SEP15-T80", "less", None, 80, "less than 80")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    other_discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15",
        get_event=lambda t: (_fake_response(event_payload, "x", now), b""),
        get_series=lambda p: _fake_response({"series": series_raw()}, p, now),
    )
    contracts.update(other_discovery.contracts_by_ticker)
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    _evidence, members = _synthetic_members(kelvin)
    prices = {"KXHIGHCHI-26SEP15-T87": Decimal("0.27"), "KXHIGHCHI-26SEP15-T80": Decimal("0.50")}
    best = select_candidate(contracts, members, prices)
    assert best == "KXHIGHCHI-26SEP15-T87"


def _fake_snapshot(yes_best_ask: Decimal, observed_at: datetime):
    from services.forecasting.wn_a1_market_economics import KalshiMarketSnapshot

    return KalshiMarketSnapshot(
        market=None,  # type: ignore[arg-type]
        event=None,  # type: ignore[arg-type]
        series=None,  # type: ignore[arg-type]
        yes_best_ask=yes_best_ask,
        no_best_ask=Decimal(1) - yes_best_ask,
        yes_taker_cost=None,
        no_taker_cost=None,
        quantity=Decimal(1),
        fee_policy=None,  # type: ignore[arg-type]
        market_observed_at=observed_at,
        orderbook_observed_at=observed_at,
        snapshot_identity="fake-snapshot",
    )
