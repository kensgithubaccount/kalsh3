from __future__ import annotations

from copy import copy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import services.production_weather_strategy.model_tournament as model_module
from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.historical_economics import (
    MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS,
    HistoricalEconomicsEvidenceError,
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


def _market_response(
    candles: tuple[dict[str, object], ...],
    *,
    ticker: str = TICKER,
    response_salt: str = "base",
) -> HistoricalMarketResponseEvidence:
    end_ts = int(CUTOFF.timestamp())
    start_ts = end_ts - 24 * 60 * 60
    hashes = tuple(stable_hash(candle) for candle in candles)
    return HistoricalMarketResponseEvidence(
        request_path=model_module._candle_request_path(
            ticker,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
        market_ticker=ticker,
        request_start_ts=start_ts,
        request_end_ts=end_ts,
        interval_minutes=MARKET_CANDLE_INTERVAL_MINUTES,
        response_sha256=stable_hash(("m28d-r1", response_salt, ticker, hashes)),
        candle_hashes=hashes,
        _capability=model_module._HISTORICAL_MARKET_RESPONSE_CAPABILITY,
    )


def _checkpoint(
    candles: tuple[dict[str, object], ...],
    *,
    response: HistoricalMarketResponseEvidence | None = None,
    ticker: str = TICKER,
) -> tuple[HistoricalMarketResponseEvidence, MarketCheckpoint]:
    exact_response = response or _market_response(candles, ticker=ticker)
    checkpoint = MarketCheckpoint.from_candles(
        market_ticker=ticker,
        checkpoint_at=CUTOFF,
        candles=candles,
        response_evidence=exact_response,
    )
    assert checkpoint is not None
    return exact_response, checkpoint


def _quote_candle(
    *,
    seconds_before_cutoff: int = 60,
    yes_bid: object = "0.43",
    yes_ask: object = "0.44",
) -> dict[str, object]:
    return {
        "end_period_ts": int(CUTOFF.timestamp()) - seconds_before_cutoff,
        "yes_bid": {"close_dollars": yes_bid},
        "yes_ask": {"close_dollars": yes_ask},
        "price": {"close_dollars": "0.435"},
    }


def test_exact_canonical_selected_candle_with_valid_bid_ask_succeeds() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    evidence = build_executable_quote_evidence(
        response_evidence=response,
        checkpoint=checkpoint,
        selected_candle=candle,
    )
    assert evidence.market_ticker == TICKER
    assert evidence.yes_bid == Decimal("0.43")
    assert evidence.yes_ask == Decimal("0.44")


@pytest.mark.parametrize(
    "candle",
    (
        {
            "end_period_ts": int(CUTOFF.timestamp()) - 60,
            "midpoint": {"close_dollars": "0.50"},
            "price": {"close_dollars": "0.50"},
        },
        {
            "end_period_ts": int(CUTOFF.timestamp()) - 60,
            "price": {"close_dollars": "0.50"},
        },
        {
            "end_period_ts": int(CUTOFF.timestamp()) - 60,
            "price": {"previous_dollars": "0.50"},
        },
    ),
    ids=("midpoint-only", "close-only", "last-price-only"),
)
def test_probability_or_trade_price_fallback_is_not_executable(candle: dict[str, object]) -> None:
    response, checkpoint = _checkpoint((candle,))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="yes_bid field is missing"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_missing_bid_or_ask_fails() -> None:
    candle = {
        "end_period_ts": int(CUTOFF.timestamp()) - 60,
        "yes_bid": {"close_dollars": "0.40"},
        "price": {"close_dollars": "0.45"},
    }
    response, checkpoint = _checkpoint((candle,))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="yes_ask field is missing"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_crossed_bid_ask_fails_even_if_m28c_probability_can_fallback() -> None:
    candle = _quote_candle(yes_bid="0.60", yes_ask="0.50")
    response, checkpoint = _checkpoint((candle,))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="bid exceeds"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


@pytest.mark.parametrize("field", ("yes_bid", "yes_ask"))
def test_out_of_range_bid_ask_fails(field: str) -> None:
    candle = _quote_candle()
    candle[field] = {"close_dollars": "1.01"}
    response, checkpoint = _checkpoint((candle,))
    with pytest.raises(HistoricalEconomicsEvidenceError, match=r"outside \[0,1\]"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_quote_from_another_ticker_fails() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    corrupted = copy(checkpoint)
    object.__setattr__(corrupted, "market_ticker", "OTHER")
    with pytest.raises(HistoricalEconomicsEvidenceError, match=r"checkpoint identity|ticker"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=corrupted,
            selected_candle=candle,
        )


def test_quote_from_another_response_fails() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    other = _market_response((candle,), response_salt="other")
    assert other.evidence_id != response.evidence_id
    with pytest.raises(HistoricalEconomicsEvidenceError, match="another response"):
        build_executable_quote_evidence(
            response_evidence=other,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_quote_from_another_selected_candle_fails() -> None:
    older = _quote_candle(seconds_before_cutoff=120)
    selected = _quote_candle(seconds_before_cutoff=60)
    response, checkpoint = _checkpoint((older, selected))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="candle hash binding"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=older,
        )


def test_altered_selected_candle_fails() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    altered = dict(candle)
    altered["yes_ask"] = {"close_dollars": "0.45"}
    with pytest.raises(HistoricalEconomicsEvidenceError, match="candle hash binding"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=altered,
        )


def test_quote_after_cutoff_fails() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    future = dict(candle)
    future["end_period_ts"] = int(CUTOFF.timestamp()) + 1
    future_hash = stable_hash(future)
    tampered_response = copy(response)
    object.__setattr__(tampered_response, "candle_hashes", (future_hash,))
    response_digest = stable_hash(
        (
            tampered_response.schema_version,
            tampered_response.request_path,
            tampered_response.market_ticker,
            tampered_response.request_start_ts,
            tampered_response.request_end_ts,
            tampered_response.interval_minutes,
            tampered_response.response_sha256,
            tampered_response.candle_hashes,
        )
    )
    object.__setattr__(tampered_response, "evidence_id", response_digest)
    object.__setattr__(tampered_response, "content_hash", response_digest)
    tampered_checkpoint = copy(checkpoint)
    object.__setattr__(tampered_checkpoint, "response_evidence_id", response_digest)
    object.__setattr__(tampered_checkpoint, "selected_candle_end_ts", future["end_period_ts"])
    object.__setattr__(tampered_checkpoint, "selected_candle_hash", future_hash)
    checkpoint_digest = stable_hash(
        (
            model_module.MARKET_CHECKPOINT_SCHEMA_VERSION,
            tampered_checkpoint.market_ticker,
            tampered_checkpoint.checkpoint_at.isoformat(),
            tampered_checkpoint.request_start_ts,
            tampered_checkpoint.request_end_ts,
            tampered_checkpoint.request_path,
            tampered_checkpoint.response_evidence_id,
            tampered_checkpoint.selected_candle_end_ts,
            tampered_checkpoint.selected_candle_hash,
            str(tampered_checkpoint.yes_probability),
        )
    )
    object.__setattr__(tampered_checkpoint, "checkpoint_id", checkpoint_digest)
    object.__setattr__(tampered_checkpoint, "content_hash", checkpoint_digest)
    with pytest.raises(HistoricalEconomicsEvidenceError, match="after decision cutoff"):
        build_executable_quote_evidence(
            response_evidence=tampered_response,
            checkpoint=tampered_checkpoint,
            selected_candle=future,
        )


def test_stale_quote_outside_fixed_bound_fails() -> None:
    candle = _quote_candle(seconds_before_cutoff=MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS + 1)
    response, checkpoint = _checkpoint((candle,))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="staleness"):
        build_executable_quote_evidence(
            response_evidence=response,
            checkpoint=checkpoint,
            selected_candle=candle,
        )


def test_actual_quote_age_participates_in_evidence_identity() -> None:
    recent = _quote_candle(seconds_before_cutoff=60)
    older = _quote_candle(seconds_before_cutoff=120)
    recent_response, recent_checkpoint = _checkpoint((recent,))
    older_response, older_checkpoint = _checkpoint((older,), response=_market_response((older,)))
    first = build_executable_quote_evidence(
        response_evidence=recent_response,
        checkpoint=recent_checkpoint,
        selected_candle=recent,
    )
    second = build_executable_quote_evidence(
        response_evidence=older_response,
        checkpoint=older_checkpoint,
        selected_candle=older,
    )
    assert first.quote_age_seconds == 60
    assert second.quote_age_seconds == 120
    assert first.content_hash != second.content_hash


def test_derived_no_quote_is_marked_derived_complement() -> None:
    candle = _quote_candle()
    response, checkpoint = _checkpoint((candle,))
    evidence = build_executable_quote_evidence(
        response_evidence=response,
        checkpoint=checkpoint,
        selected_candle=candle,
    )
    assert evidence.no_bid == Decimal("0.56")
    assert evidence.no_ask == Decimal("0.57")
    assert evidence.no_quote_provenance is NoQuoteProvenance.DERIVED_COMPLEMENT
