from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import services.production_weather_strategy.historical_economics as economics_module
import services.production_weather_strategy.model_tournament as model_module
from services.historical_replay.archive import stable_hash
from services.opportunity_engine.fees import FeeType, current_event_formula_policy
from services.production_weather_strategy.forecast_vintage import (
    ForecastSourceArtifact,
    ForecastVintageError,
    ForecastVintageEvidence,
    build_forecast_vintage_evidence,
)
from services.production_weather_strategy.historical_economics import (
    ExecutableQuoteEvidence,
    HistoricalEconomicsEvidenceError,
    HistoricalFeePolicyEvidence,
    NoQuoteProvenance,
    build_executable_quote_evidence,
)
from services.production_weather_strategy.model_tournament import (
    MARKET_CANDLE_INTERVAL_MINUTES,
    HistoricalMarketResponseEvidence,
    MarketCheckpoint,
)

CUTOFF = datetime(2026, 8, 23, 3, tzinfo=UTC)
TICKER = "KXHIGHCHI-26AUG23-B76.5"


def _forecast(*, reference: datetime, retrieved: datetime) -> ForecastSourceArtifact:
    return ForecastSourceArtifact(
        provider="NOAA/NDFD",
        source_identity="synthetic-ndfd-archive",
        station_id="CLIMDW",
        measurement="DAILY_MAX",
        target_local_date=date(2026, 8, 23),
        forecast_reference_time=reference,
        retrieved_at=retrieved,
        parser_version="synthetic-parser-v1",
        forecast_deg_f=Decimal("76.0"),
        raw_artifact=b"synthetic-forecast",
    )


def _market_chain(
    candle: dict[str, object],
) -> tuple[HistoricalMarketResponseEvidence, MarketCheckpoint]:
    end_ts = int(CUTOFF.timestamp())
    start_ts = end_ts - 24 * 60 * 60
    candle_hash = stable_hash(candle)
    response = HistoricalMarketResponseEvidence(
        request_path=model_module._candle_request_path(
            TICKER,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
        market_ticker=TICKER,
        request_start_ts=start_ts,
        request_end_ts=end_ts,
        interval_minutes=MARKET_CANDLE_INTERVAL_MINUTES,
        response_sha256=stable_hash(("m28d-r1-extra", candle_hash)),
        candle_hashes=(candle_hash,),
        _capability=model_module._HISTORICAL_MARKET_RESPONSE_CAPABILITY,
    )
    checkpoint = MarketCheckpoint.from_candles(
        market_ticker=TICKER,
        checkpoint_at=CUTOFF,
        candles=(candle,),
        response_evidence=response,
    )
    assert checkpoint is not None
    return response, checkpoint


@pytest.mark.parametrize(
    ("reference", "retrieved", "cutoff", "match"),
    (
        (
            datetime(2026, 8, 22, 21),
            CUTOFF + timedelta(days=1),
            CUTOFF,
            "forecast reference time must be timezone-aware",
        ),
        (
            CUTOFF - timedelta(hours=6),
            datetime(2026, 8, 25, 3),
            CUTOFF,
            "forecast retrieval time must be timezone-aware",
        ),
    ),
)
def test_forecast_source_rejects_naive_times(
    reference: datetime,
    retrieved: datetime,
    cutoff: datetime,
    match: str,
) -> None:
    with pytest.raises(ForecastVintageError, match=match):
        artifact = _forecast(reference=reference, retrieved=retrieved)
        build_forecast_vintage_evidence(artifact, decision_cutoff=cutoff)


def test_forecast_cutoff_rejects_naive_time() -> None:
    artifact = _forecast(
        reference=CUTOFF - timedelta(hours=6),
        retrieved=CUTOFF + timedelta(days=1),
    )
    with pytest.raises(ForecastVintageError, match="decision cutoff must be timezone-aware"):
        build_forecast_vintage_evidence(
            artifact,
            decision_cutoff=datetime(2026, 8, 23, 3),
        )


@pytest.mark.parametrize("bad_value", ("not-a-number", True))
def test_executable_quote_rejects_nonnumeric_or_malformed_bid(bad_value: object) -> None:
    candle = {
        "end_period_ts": int(CUTOFF.timestamp()) - 60,
        "yes_bid": {"close_dollars": bad_value},
        "yes_ask": {"close_dollars": "0.44"},
        "price": {"close_dollars": "0.43"},
    }
    response, checkpoint = _market_chain(candle)
    with pytest.raises(HistoricalEconomicsEvidenceError, match="yes_bid value"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_direct_no_quote_is_distinguished_from_derived_complement() -> None:
    candle = {
        "end_period_ts": int(CUTOFF.timestamp()) - 60,
        "yes_bid": {"close_dollars": "0.43"},
        "yes_ask": {"close_dollars": "0.44"},
        "no_bid": {"close_dollars": "0.55"},
        "no_ask": {"close_dollars": "0.58"},
    }
    response, checkpoint = _market_chain(candle)
    evidence = build_executable_quote_evidence(
        response_evidence=response,
        checkpoint=checkpoint,
        selected_candle=candle,
    )
    assert evidence.no_quote_provenance is NoQuoteProvenance.DIRECTLY_OBSERVED
    assert evidence.no_bid == Decimal("0.55")
    assert evidence.no_ask == Decimal("0.58")


def test_unreviewed_fee_policy_cannot_mint_historical_fee_evidence() -> None:
    policy = replace(
        current_event_formula_policy(
            fee_type=FeeType.QUADRATIC,
            fee_multiplier=Decimal("1"),
        ),
        verified=False,
    )
    with pytest.raises(HistoricalEconomicsEvidenceError, match="not reviewed"):
        HistoricalFeePolicyEvidence(
            policy=policy,
            checkpoint_at=CUTOFF,
            review_evidence_id="synthetic-review",
            _capability=economics_module._HISTORICAL_FEE_POLICY_AUTHORITY_CAPABILITY,
        )


def test_r1_evidence_objects_create_no_promotion_or_execution_authority() -> None:
    forbidden_fields = {
        "promotion_authority",
        "risk_authority",
        "approval_authority",
        "execution_authority",
        "signer_authority",
        "order_authority",
    }
    for evidence_type in (
        ForecastVintageEvidence,
        ExecutableQuoteEvidence,
        HistoricalFeePolicyEvidence,
    ):
        assert forbidden_fields.isdisjoint({item.name for item in fields(evidence_type)})


def test_r1_modules_have_no_network_execution_or_profitability_surface() -> None:
    forbidden = (
        "import requests",
        "import httpx",
        "import urllib",
        "services.production_execution",
        "services.kalshi_account_gateway",
        "services.risk_engine.authorization",
        "submit_order",
        "private_key",
        "after_cost_edge",
        "hypothetical_pnl",
        "calculate_fee(",
    )
    for path in (
        Path("services/production_weather_strategy/forecast_vintage.py"),
        Path("services/production_weather_strategy/historical_economics.py"),
    ):
        source = path.read_text()
        assert all(term not in source for term in forbidden)
