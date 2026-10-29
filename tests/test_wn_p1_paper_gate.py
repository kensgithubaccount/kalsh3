"""Synthetic-only hostile cases; no live weather, Kalshi or order API."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_p1_paper_gate import (
    APPROVED_PROSPECTIVE_MODEL_IDS,
    PaperDecision,
    PaperGateError,
    PaperState,
    PredecisionObservation,
    evaluate,
    side_aware_gaps,
)

HASH = "a" * 64
DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 9, 0, 30, tzinfo=UTC)
TICKER = "KXHIGHTBOS-26NOV02"


def specimen() -> PredecisionObservation:
    return PredecisionObservation(
        city_id="boston",
        target_date=DAY,
        evaluated_at_utc=NOW,
        forecast_observed_at_utc=NOW - timedelta(seconds=10),
        forecast_ingested_at_utc=NOW - timedelta(minutes=2),
        forecast_sha256=HASH,
        model_id=None,
        model_probability_yes=None,
        model_evidence_sha256=None,
        event_ticker=TICKER,
        sibling_tickers=tuple(
            TICKER + suffix
            for suffix in ("-T60", "-B60.5", "-B62.5", "-B64.5", "-B66.5", "-T67")
        ),
        market_observed_at_utc=NOW - timedelta(seconds=5),
        market_sha256=HASH,
        verified_fee_policy_id="fixture-only-fee-policy",
        book_quantity=Decimal(1),
        yes_fee_inclusive_debit=Decimal("0.55"),
        no_fee_inclusive_debit=Decimal("0.50"),
    )


def test_unmodified_p50_has_no_prospective_model_and_must_abstain() -> None:
    decision = evaluate(specimen())
    assert decision.state is PaperState.ABSTAIN_NO_MODEL
    assert decision.selected_paper_side is None
    assert decision.yes_after_cost_gap is None
    assert decision.research_only is True
    assert decision.production_influence == "0"
    assert decision.trading_authority == "NONE"


def test_forged_calibrated_probability_cannot_activate_paper_candidate() -> None:
    assert frozenset() == APPROVED_PROSPECTIVE_MODEL_IDS
    value = replace(
        specimen(),
        model_id="USER_CLAIMS_CALIBRATED",
        model_probability_yes=Decimal("0.99"),
        model_evidence_sha256=HASH,
        yes_fee_inclusive_debit=Decimal("0.01"),
    )
    assert evaluate(value).state is PaperState.ABSTAIN_UNREGISTERED_MODEL


def test_caller_cannot_forge_positive_or_production_decision() -> None:
    base = evaluate(specimen())
    with pytest.raises(PaperGateError, match="cannot grant"):
        replace(base, state=PaperState.PAPER_DIAGNOSTIC_ONLY)
    with pytest.raises(PaperGateError, match="cannot grant"):
        replace(base, production_influence="1")
    with pytest.raises(PaperGateError, match="cannot grant"):
        PaperDecision(
            PaperState.ABSTAIN_NO_MODEL,
            "boston",
            DAY,
            base.evidence_id,
            "YES",
            Decimal("0.2"),
            None,
        )


def test_evidence_identity_is_deterministic_and_sensitive_to_input() -> None:
    first = evaluate(specimen()).evidence_id
    assert first == evaluate(specimen()).evidence_id
    assert first != evaluate(replace(specimen(), market_sha256="b" * 64)).evidence_id
    assert first != evaluate(
        replace(specimen(), market_observed_at_utc=NOW - timedelta(seconds=15))
    ).evidence_id


@pytest.mark.parametrize(
    "change",
    [
        {"target_date": date(2026, 10, 14), "event_ticker": "KXHIGHTBOS-26OCT14"},
        {"city_id": "chicago"},
        {"event_ticker": "KXHIGHMIA-26NOV02"},
        {"sibling_tickers": ("duplicate",) * 6},
        {"sibling_tickers": (["unhashable"],) * 6},
        {"settlement_source": "NWS"},
        {"research_only": False},
        {"production_influence": "1"},
        {"trading_authority": "ALLOW"},
        {"book_quantity": Decimal("2")},
        {"forecast_sha256": "FORGED"},
        {"model_probability_yes": Decimal("NaN")},
        {"market_observed_at_utc": NOW + timedelta(seconds=1)},
        {"forecast_ingested_at_utc": NOW + timedelta(seconds=1)},
        {"evaluated_at_utc": NOW + timedelta(minutes=6)},
        {"evaluated_at_utc": NOW - timedelta(seconds=31)},
        {"yes_fee_inclusive_debit": Decimal("Infinity")},
        {"no_fee_inclusive_debit": Decimal("-0.1")},
    ],
)
def test_identity_and_boundary_invariants_fail_closed(change: dict[str, object]) -> None:
    with pytest.raises(PaperGateError):
        evaluate(replace(specimen(), **change))  # type: ignore[arg-type]


def test_side_aware_economic_math_not_absolute_price_gap() -> None:
    yes, no = side_aware_gaps(Decimal("0.10"), Decimal("0.40"), Decimal("0.95"))
    assert yes == Decimal("-0.30")
    assert no == Decimal("-0.05")
    yes, no = side_aware_gaps(Decimal("0.80"), Decimal("0.65"), Decimal("0.50"))
    assert yes == Decimal("0.15")
    assert no == Decimal("-0.30")


@pytest.mark.parametrize(
    ("prob", "yes", "no"),
    [
        (Decimal("NaN"), Decimal("0.5"), Decimal("0.5")),
        (Decimal("1.1"), Decimal("0.5"), Decimal("0.5")),
        (Decimal("0.5"), Decimal("-0.1"), Decimal("0.8")),
        (Decimal("0.5"), Decimal("0.1"), Decimal("0.1")),
    ],
)
def test_inadmissible_economics_fail_closed(prob: Decimal, yes: Decimal, no: Decimal) -> None:
    with pytest.raises(PaperGateError):
        side_aware_gaps(prob, yes, no)
