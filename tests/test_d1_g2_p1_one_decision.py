from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import services.forecasting.gdpnow_source_acquisition as gdpnow_acquisition
import services.production_gdp_strategy.one_decision as one_decision
from services.forecasting.gdpnow_parsing import parse_gdpnow_commentary
from services.production_gdp_strategy.one_decision import (
    ClockSample,
    DecisionClass,
    DecisionError,
    TransportResponse,
    replay_decision,
    run_one_research_decision,
)
from services.production_gdp_strategy.outcome import (
    _ISSUER,
    OutcomeClass,
    SettlementEvidence,
    build_outcome_receipt,
)


def _gdpnow() -> tuple[object, object]:
    body = (
        b"<h2>September 3, 2026</h2><p>The GDPNow model estimate for real GDP growth "
        b"(seasonally adjusted annual rate) in the third quarter of 2026 is "
        b"<strong>4.7 percent</strong> on September 3, down from 4.8 percent.</p>"
    )
    result = gdpnow_acquisition._GDPNowTransportResult(
        requested_locator=gdpnow_acquisition.SOURCE_LOCATOR,
        final_locator=gdpnow_acquisition.SOURCE_LOCATOR,
        method="GET",
        status=200,
        raw_body=body,
        acquired_at=datetime(2026, 9, 3, 12, 20, tzinfo=UTC),
        diagnostic_headers=(),
    )
    evidence = gdpnow_acquisition.GDPNowAcquisitionEvidence(
        result=result,
        _capability=gdpnow_acquisition._GDPNOW_EVIDENCE_ISSUANCE_CAPABILITY,
    )
    return evidence, parse_gdpnow_commentary(evidence)


def _response(path: str, payload: dict[str, object]) -> TransportResponse:
    return TransportResponse(path, 200, json.dumps(payload, sort_keys=True).encode())


def _market(
    ticker: str = "KXGDP-26SEP03-T4.5", status: str = "active", close: str = "12:29:00"
) -> TransportResponse:
    return _response(
        f"/trade-api/v2/markets/{ticker}",
        {
            "market": {
                "ticker": ticker,
                "event_ticker": "KXGDP-26SEP03",
                "market_type": "binary",
                "status": status,
                "rules_primary": (
                    "US real GDP seasonally adjusted annualized BEA Advance Estimate "
                    "more than threshold"
                ),
                "price_level_structure": "deci_cent",
                "open_time": "2026-09-03T11:00:00+00:00",
                "close_time": f"2026-09-03T{close}+00:00",
                "threshold": "4.5",
                "volume_fp": "0",
                "open_interest_fp": "0",
            }
        },
    )


def _schedule(close: str = "08:29:00") -> TransportResponse:
    return _response(
        "/reviewed/d1-g2/schedule",
        {
            "schedule": {
                "series_ticker": "KXGDP",
                "event_ticker": "KXGDP-26SEP03",
                "market_ticker": "KXGDP-26SEP03-T4.5",
                "target_quarter": "2026-Q3",
                "metric": (
                    "US real GDP quarter-over-quarter growth, seasonally adjusted annual rate"
                ),
                "settlement_edition": "BEA Advance Estimate",
                "timezone": "America/New_York",
                "bea_release_local": "2026-09-03T08:30:00",
                "market_open_local": "2026-09-03T07:00:00",
                "market_close_local": f"2026-09-03T{close}",
                "listed_thresholds": ["4.5", "5.0"],
            }
        },
    )


def _book() -> TransportResponse:
    return _response(
        "/trade-api/v2/markets/orderbooks?tickers=KXGDP-26SEP03-T4.5",
        {
            "orderbooks": [
                {
                    "ticker": "KXGDP-26SEP03-T4.5",
                    "orderbook_fp": {
                        "yes_dollars": [["0.30", "2.00"]],
                        "no_dollars": [["0.60", "2.00"]],
                    },
                }
            ]
        },
    )


def _fee(multiplier: str = "1") -> TransportResponse:
    return _response(
        "/reviewed/d1-g2/fee",
        {
            "fee": {
                "schedule_id": "kalshi-fee-test-v1",
                "effective_at": "2026-07-07T00:00:00+00:00",
                "series_ticker": "KXGDP",
                "fee_type": "quadratic",
                "fee_multiplier": multiplier,
                "regime": "MARKETABLE_TAKER",
                "formula": "0.07 * price * (1-price) * quantity * multiplier",
                "price_units": "USD_PER_CONTRACT",
                "quantity_convention": "CONTRACTS",
                "rounding": "CEILING_0.0001",
                "decimal_precision": "EXACT_DECIMAL",
                "override_precedence": "market>event>series>schedule",
            }
        },
    )


class FixtureSource:
    def __init__(
        self, *, before: str = "active", after: str = "active", close: str = "08:29:00"
    ) -> None:
        self._authority_capability = one_decision._SOURCE_CAPABILITY
        market_close = "12:31:00" if close == "08:31:00" else "12:29:00"
        self.responses = [
            _market(status=before, close=market_close),
            _market(status=after, close=market_close),
        ]
        self.close = close
        self.market_calls = 0

    def schedule(self) -> TransportResponse:
        return _schedule(self.close)

    def market(self, ticker: str) -> TransportResponse:
        assert ticker == "KXGDP-26SEP03-T4.5"
        response = self.responses[self.market_calls]
        self.market_calls += 1
        return response

    def orderbook(self, ticker: str) -> TransportResponse:
        assert ticker == "KXGDP-26SEP03-T4.5"
        return _book()

    def fee(self) -> TransportResponse:
        return _fee()


def _clock() -> callable:
    samples = iter(
        [
            ClockSample(datetime(2026, 9, 3, 12, 20, tzinfo=UTC), 0),
            ClockSample(datetime(2026, 9, 3, 12, 20, tzinfo=UTC), 0),
            ClockSample(datetime(2026, 9, 3, 12, 20, 0, 100000, tzinfo=UTC), 100_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, tzinfo=UTC), 5_000_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 100000, tzinfo=UTC), 5_100_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 200000, tzinfo=UTC), 5_200_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 300000, tzinfo=UTC), 5_300_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 400000, tzinfo=UTC), 5_400_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 500000, tzinfo=UTC), 5_500_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 600000, tzinfo=UTC), 5_600_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 700000, tzinfo=UTC), 5_700_000_000),
            ClockSample(datetime(2026, 9, 3, 12, 25, 0, 800000, tzinfo=UTC), 5_800_000_000),
        ]
    )
    return lambda: next(samples)


def test_real_shaped_passing_path_replays_and_evaluates_without_mutation() -> None:
    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=FixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.TRADE_YES
    assert decision.entry_price == Decimal("0.40")
    assert decision.all_in_debit == Decimal("0.4168")
    assert replay_decision(decision) == decision

    settlement = SettlementEvidence(
        response=json.dumps(
            {
                "decision_id": decision.decision_id,
                "original_decision_hash": decision.payload_hash,
                "market_ticker": decision.selected_market_ticker,
                "final": True,
                "result": "yes",
                "settled_at": "2026-09-03T13:00:00+00:00",
            }
        ).encode(),
        decision=decision,
        completed_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        _capability=_ISSUER,
    )
    outcome = build_outcome_receipt(decision, settlement)
    assert outcome.outcome_class is OutcomeClass.COMPLETE
    assert outcome.realized_payout == Decimal("1.00")
    assert outcome.net_cash_flow == Decimal("0.5832")
    assert decision.classification is DecisionClass.TRADE_YES


def test_schedule_conflict_is_evidence_incomplete() -> None:
    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=FixtureSource(close="08:31:00"), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.EVIDENCE_INCOMPLETE
    assert replay_decision(decision) == decision


def test_temporal_failure_is_evidence_incomplete() -> None:
    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=FixtureSource(before="inactive"), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.EVIDENCE_INCOMPLETE


def test_missing_fee_authority_is_recorded_as_evidence_incomplete() -> None:
    class MissingFee(FixtureSource):
        def fee(self) -> TransportResponse:
            return TransportResponse("/reviewed/d1-g2/fee", 503, b"unavailable")

    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=MissingFee(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.EVIDENCE_INCOMPLETE
    assert replay_decision(decision) == decision


def test_exact_gate_rejects_with_abstain() -> None:
    class HalfPrice(FixtureSource):
        def orderbook(self, ticker: str) -> TransportResponse:
            del ticker
            return _response(
                "/trade-api/v2/markets/orderbooks?tickers=KXGDP-26SEP03-T4.5",
                {
                    "orderbooks": [
                        {
                            "ticker": "KXGDP-26SEP03-T4.5",
                            "orderbook_fp": {
                                "yes_dollars": [["0.30", "2.00"]],
                                "no_dollars": [["0.50", "2.00"]],
                            },
                        }
                    ]
                },
            )

        def fee(self) -> TransportResponse:
            return _fee("0")

    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=HalfPrice(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.ABSTAIN
    assert decision.all_in_debit == Decimal("0.50")


def test_sub_one_contract_depth_is_not_identifiable() -> None:
    class ThinBook(FixtureSource):
        def orderbook(self, ticker: str) -> TransportResponse:
            del ticker
            return _response(
                "/trade-api/v2/markets/orderbooks?tickers=KXGDP-26SEP03-T4.5",
                {
                    "orderbooks": [
                        {
                            "ticker": "KXGDP-26SEP03-T4.5",
                            "orderbook_fp": {
                                "yes_dollars": [["0.30", "0.25"]],
                                "no_dollars": [["0.60", "0.25"]],
                            },
                        }
                    ]
                },
            )

    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=ThinBook(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.EXECUTION_NOT_IDENTIFIABLE


def test_mutated_issued_book_fails_replay() -> None:
    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=FixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.bundle is not None
    object.__setattr__(decision.bundle.book, "body", b"substituted")
    with pytest.raises(DecisionError, match=r"reconstructed|tampered"):
        replay_decision(decision)


def test_caller_authored_source_cannot_become_positive_authority() -> None:
    class UnreviewedSource(FixtureSource):
        def __init__(self) -> None:
            super().__init__()
            del self._authority_capability

    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=UnreviewedSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert decision.classification is DecisionClass.EVIDENCE_INCOMPLETE
    assert decision.bundle is None


def test_reconstructed_receipt_is_rejected() -> None:
    evidence, vintage = _gdpnow()
    decision = run_one_research_decision(
        source=FixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    from dataclasses import replace

    with pytest.raises((DecisionError, TypeError)):
        forged = replace(decision)
        replay_decision(forged)
