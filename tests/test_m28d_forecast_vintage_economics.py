from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.opportunity_engine.fees import FeeType
from services.production_weather_strategy.forecast_vintage import (
    ForecastRevisionFeatures,
    ForecastVintageError,
    ForecastVintagePoint,
    choose_latest_pre_cutoff_vintage,
)
from services.production_weather_strategy.historical_economics import (
    HistoricalEconomicsError,
    HistoricalQuoteCheckpoint,
    TradeSide,
    evaluate_historical_opportunity,
    reconstruct_checkpoint_economics,
)


def _vintage(*, hours_before_cutoff: int, forecast: str) -> ForecastVintagePoint:
    cutoff = datetime(2026, 8, 23, 3, tzinfo=UTC)
    reference = cutoff - timedelta(hours=hours_before_cutoff)
    return ForecastVintagePoint.build(
        source_name="NOAA/NDFD",
        source_family="NDFD_MAXT",
        station_id="CLIKMDW",
        measurement="DAILY_MAX",
        target_local_date=date(2026, 8, 23),
        forecast_reference_time=reference,
        source_published_at=reference + timedelta(minutes=20),
        decision_cutoff=cutoff,
        retrieved_at=cutoff + timedelta(days=3),
        forecast_deg_f=Decimal(forecast),
        source_hash=f"hash-{hours_before_cutoff}",
    )


def test_forecast_vintage_rejects_post_cutoff_publication() -> None:
    cutoff = datetime(2026, 8, 23, 3, tzinfo=UTC)
    with pytest.raises(ForecastVintageError, match="published after"):
        ForecastVintagePoint.build(
            source_name="NOAA/NDFD",
            source_family="NDFD_MAXT",
            station_id="CLIKMDW",
            measurement="DAILY_MAX",
            target_local_date=date(2026, 8, 23),
            forecast_reference_time=cutoff - timedelta(hours=1),
            source_published_at=cutoff + timedelta(seconds=1),
            decision_cutoff=cutoff,
            retrieved_at=cutoff + timedelta(days=1),
            forecast_deg_f=Decimal("76.0"),
            source_hash="source",
        )


def test_latest_pre_cutoff_vintage_and_revision_are_deterministic() -> None:
    prior = _vintage(hours_before_cutoff=9, forecast="77.5")
    latest = _vintage(hours_before_cutoff=3, forecast="76.0")
    selected = choose_latest_pre_cutoff_vintage(
        (prior, latest),
        station_id="CLIKMDW",
        measurement="DAILY_MAX",
        target_local_date=date(2026, 8, 23),
        decision_cutoff=datetime(2026, 8, 23, 3, tzinfo=UTC),
    )
    assert selected == latest
    revision = ForecastRevisionFeatures.build(latest, prior)
    assert revision.revision_deg_f == Decimal("-1.5")
    assert revision.reference_time_delta_seconds == 6 * 3600
    assert revision.content_hash == ForecastRevisionFeatures.build(latest, prior).content_hash


def test_revision_rejects_cross_event_or_non_older_prior() -> None:
    latest = _vintage(hours_before_cutoff=3, forecast="76.0")
    with pytest.raises(ForecastVintageError, match="not older"):
        ForecastRevisionFeatures.build(latest, latest)


def test_quote_checkpoint_derives_complementary_no_book() -> None:
    quote = HistoricalQuoteCheckpoint.build(
        market_ticker="KXHIGHCHI-26AUG23-B76.5",
        checkpoint_at=datetime(2026, 8, 23, 3, tzinfo=UTC),
        yes_bid=Decimal("0.43"),
        yes_ask=Decimal("0.44"),
        quote_evidence_id="candle-hash",
    )
    assert quote.no_bid == Decimal("0.56")
    assert quote.no_ask == Decimal("0.57")


def test_fee_aware_economics_matches_reviewed_formula() -> None:
    quote = HistoricalQuoteCheckpoint.build(
        market_ticker="KXHIGHCHI-26AUG23-B76.5",
        checkpoint_at=datetime(2026, 8, 23, 3, tzinfo=UTC),
        yes_bid=Decimal("0.43"),
        yes_ask=Decimal("0.44"),
        quote_evidence_id="candle-hash",
    )
    economics = reconstruct_checkpoint_economics(
        quote,
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal("1"),
    )
    assert economics.yes.taker_fee == Decimal("0.0173")
    assert economics.yes.all_in_cost == Decimal("0.4573")
    assert economics.no.taker_fee == Decimal("0.0172")
    assert economics.no.all_in_cost == Decimal("0.5872")
    assert economics.exact_fill_truth is False


def test_fee_reconstruction_rejects_pre_policy_checkpoint() -> None:
    quote = HistoricalQuoteCheckpoint.build(
        market_ticker="KXHIGHCHI-26JUL01-B76.5",
        checkpoint_at=datetime(2026, 7, 1, 3, tzinfo=UTC),
        yes_bid=Decimal("0.43"),
        yes_ask=Decimal("0.44"),
        quote_evidence_id="old-candle-hash",
    )
    with pytest.raises(HistoricalEconomicsError, match="does not apply"):
        reconstruct_checkpoint_economics(
            quote,
            fee_type=FeeType.QUADRATIC,
            fee_multiplier=Decimal("1"),
        )


def test_opportunity_separates_correct_prediction_from_profitable_trade() -> None:
    quote = HistoricalQuoteCheckpoint.build(
        market_ticker="KXHIGHCHI-26AUG23-B80.5",
        checkpoint_at=datetime(2026, 8, 23, 3, tzinfo=UTC),
        yes_bid=Decimal("0.05"),
        yes_ask=Decimal("0.06"),
        quote_evidence_id="candle-hash",
    )
    economics = reconstruct_checkpoint_economics(
        quote,
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal("1"),
    )
    opportunity = evaluate_historical_opportunity(
        economics,
        model_yes_probability=Decimal("0.1117241379310344827586206897"),
        resolved_yes=0,
        model_evidence_id="m27-probability",
    )
    assert opportunity.side is TradeSide.YES
    assert opportunity.after_cost_edge > 0
    assert opportunity.hypothetical_pnl < 0


def test_invalid_quote_and_probability_fail_closed() -> None:
    with pytest.raises(HistoricalEconomicsError, match="bid exceeds"):
        HistoricalQuoteCheckpoint.build(
            market_ticker="KX",
            checkpoint_at=datetime(2026, 8, 23, 3, tzinfo=UTC),
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.50"),
            quote_evidence_id="quote",
        )

    quote = HistoricalQuoteCheckpoint.build(
        market_ticker="KX",
        checkpoint_at=datetime(2026, 8, 23, 3, tzinfo=UTC),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.50"),
        quote_evidence_id="quote",
    )
    economics = reconstruct_checkpoint_economics(
        quote,
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal("1"),
    )
    with pytest.raises(HistoricalEconomicsError, match="outside"):
        evaluate_historical_opportunity(
            economics,
            model_yes_probability=Decimal("1.1"),
            resolved_yes=1,
            model_evidence_id="model",
        )
