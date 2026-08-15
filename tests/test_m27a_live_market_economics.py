from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.kalshi_account_gateway.orderbooks import (
    MAX_RESPONSE_BYTES,
    ExactOrderbookClient,
    OrderbookReadError,
    OrderbookReply,
)
from services.kalshi_account_gateway.read_credentials import ExactReadCredential, ReadEnvironment
from services.market_universe.domain import Series, UniverseValidationError
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import (
    FeeEstimateQuality,
    FeeType,
    calculate_fee,
    current_event_formula_policy,
)
from services.opportunity_engine.live_economics import (
    DiscoveryQuotes,
    MarketEconomicsEvidence,
    normalize_live_orderbook,
    taker_cost,
    validate_economics_time,
)
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    FeeChange,
    resolve_current_fee_regime,
)

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)


def ranges(step: str = "0.01") -> list[dict[str, str]]:
    return [{"start": "0.0000", "end": "1.0000", "step": step}]


def test_all_live_price_structures_and_unknown_label_are_range_authoritative() -> None:
    structures = {
        "linear_cent": ranges("0.0100"),
        "deci_cent": ranges("0.0010"),
        "center_half_edge_half_cent": ranges("0.0050"),
        "tapered_deci_cent": [
            {"start": "0", "end": ".1", "step": ".001"},
            {"start": ".1", "end": ".9", "step": ".01"},
            {"start": ".9", "end": "1", "step": ".001"},
        ],
        "future_unknown": ranges("0.0025"),
    }
    for label, raw in structures.items():
        ladder = PriceLadder.parse(label, raw)
        assert ladder.is_valid(ladder.ranges[0].step)
        assert not ladder.is_valid(Decimal(0)) and not ladder.is_valid(Decimal(1))
    assert not PriceLadder.parse("linear_cent", ranges(".01")).is_valid(Decimal(".005"))


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"start": "0", "end": ".5", "step": ".01"}],
        [{"start": "0", "end": ".4", "step": ".1"}, {"start": ".5", "end": "1", "step": ".1"}],
        [{"start": "0", "end": ".6", "step": ".1"}, {"start": ".5", "end": "1", "step": ".1"}],
        [{"start": "0", "end": "1", "step": ".03"}],
        [{"start": "NaN", "end": "1", "step": ".01"}],
    ],
)
def test_live_price_range_fail_closed(raw: list[dict[str, str]]) -> None:
    with pytest.raises(UniverseValidationError):
        PriceLadder.parse("anything", raw)


def series(multiplier: Any) -> dict[str, Any]:
    return {
        "ticker": "KXATPMATCH",
        "title": "ATP",
        "category": "Sports",
        "frequency": "match",
        "tags": [],
        "settlement_sources": [],
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": multiplier,
        "last_updated_ts": "2026-08-15T12:00:00Z",
    }


def test_live_series_numeric_multiplier_contract() -> None:
    assert Series.parse(series(1)).fee_multiplier == 1
    assert Series.parse(series("1.25")).fee_multiplier == Decimal("1.25")
    for bad in (True, 1.0, -1, "-1", "NaN", "Infinity", "wat"):
        with pytest.raises(UniverseValidationError):
            Series.parse(series(bad))


def test_event_series_fee_metadata_resolution_and_changes() -> None:
    current = CurrentSeriesFeeObservation.parse(series(1), observed_at=NOW)
    none = EventFeeOverride.parse({})
    override = EventFeeOverride.parse(
        {"fee_type_override": "quadratic", "fee_multiplier_override": 2}
    )
    assert resolve_current_fee_regime(current, none).source == "current_series"
    resolved = resolve_current_fee_regime(current, override)
    assert resolved.source == "event_override" and resolved.fee_multiplier == 2
    for raw in (
        {"fee_type_override": "quadratic"},
        {"fee_multiplier_override": 1},
        {"fee_type_override": "flat", "fee_multiplier_override": 1},
        {"fee_type_override": "quadratic", "fee_multiplier_override": True},
    ):
        with pytest.raises(OpportunityError):
            EventFeeOverride.parse(raw)
    change = FeeChange.parse(
        {
            "id": "c1",
            "series_ticker": "KXATPMATCH",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2026-08-16T00:00:00Z",
        }
    )
    assert change.is_scheduled(NOW) and change.production_influence == 0


def test_formula_maker_taker_and_rounding_quality() -> None:
    maker_market = current_event_formula_policy(
        fee_type=FeeType.QUADRATIC_WITH_MAKER_FEES, fee_multiplier=Decimal(2)
    )
    assert maker_market.policy_id == "kalshi-event-fees-2026-07-07-v1"
    assert maker_market.effective_at == datetime(2026, 7, 7, tzinfo=UTC)
    assert maker_market.source_reference == "Kalshi Fee Schedule, effective 2026-07-07"
    assert maker_market.production_influence == Decimal("0")
    taker = calculate_fee(maker_market, Decimal(".4487"), Decimal(".253"))
    maker = calculate_fee(maker_market, Decimal(".4487"), Decimal(".253"), maker=True)
    assert taker.theoretical_trade_fee == maker.theoretical_trade_fee * 4
    assert taker.quality is FeeEstimateQuality.BOUNDED_PRETRADE
    no_maker = current_event_formula_policy(fee_type=FeeType.QUADRATIC, fee_multiplier=Decimal(1))
    assert calculate_fee(no_maker, Decimal(".5"), Decimal("1000"), maker=True).total_fee == 0
    with pytest.raises(OpportunityError):
        current_event_formula_policy(fee_type=FeeType.FLAT, fee_multiplier=Decimal(1))


def test_live_book_adapter_both_sides_depth_and_evidence_identity() -> None:
    ladder = PriceLadder.parse("deci_cent", ranges(".001"))
    raw = {
        "ticker": "M",
        "orderbook_fp": {
            "yes_dollars": [[".421", ".25"], [".400", "5"]],
            "no_dollars": [[".551", ".75"], [".540", "5"]],
        },
    }
    observed = normalize_live_orderbook(
        raw,
        ticker="M",
        ladder=ladder,
        source_id="snapshot-1",
        observed_at=NOW,
        market_rules_hash="rules",
    )
    policy = current_event_formula_policy(fee_type=FeeType.QUADRATIC, fee_multiplier=Decimal(1))
    yes = taker_cost(observed.book, OutcomeSide.YES, Decimal("1.2"), policy)
    no = taker_cost(observed.book, OutcomeSide.NO, Decimal("1.2"), policy)
    assert yes.depth.levels_consumed == 2 and no.depth.levels_consumed == 2
    assert yes.conservative_total_entry_cost is None
    values = dict(
        market_ticker="M",
        event_ticker="E",
        series_ticker="S",
        market_source_id="market-source",
        market_rules_hash="rules",
        market_metadata_hash="metadata",
        price_range_hash=observed.price_range_hash,
        event_fee_hash="event-fee",
        series_fee_observation_id="series-fee",
        resolved_fee_regime_id="regime",
        fee_policy_id=policy.policy_id,
        orderbook_source_id=observed.source_id,
        orderbook_source_hash=observed.source_hash,
        market_observed_at=NOW,
        orderbook_observed_at=NOW,
        economics_observed_at=NOW,
        requested_quantity=Decimal("1.2"),
        yes=yes,
        no=no,
    )
    first = MarketEconomicsEvidence.create(**values)
    assert first == MarketEconomicsEvidence.create(**values)
    values["market_rules_hash"] = "changed"
    assert MarketEconomicsEvidence.create(**values).evidence_id != first.evidence_id
    with pytest.raises(OpportunityError):
        MarketEconomicsEvidence.create(**values, production_influence=Decimal(".1"))
    with pytest.raises(OpportunityError):
        taker_cost(observed.book, OutcomeSide.YES, Decimal("100"), policy)


def test_discovery_boundaries_fractional_and_temporal_safety() -> None:
    raw = {
        "yes_bid_dollars": "0",
        "yes_ask_dollars": "1",
        "yes_bid_size_fp": ".125",
        "yes_ask_size_fp": ".25",
        "no_bid_dollars": "0",
        "no_ask_dollars": "1",
        "volume_fp": "10.5",
        "volume_24h_fp": "2.25",
        "open_interest_fp": "4.125",
        "liquidity_dollars": "99.99",
    }
    quote = DiscoveryQuotes.parse(raw)
    assert quote.yes_bid is None and quote.yes_ask is None and quote.yes_bid_size == Decimal(".125")
    with pytest.raises(UniverseValidationError):
        DiscoveryQuotes.parse({**raw, "yes_ask_dollars": ".9"})
    validate_economics_time(
        market_at=NOW, book_at=NOW, observed_at=NOW, maximum_book_age=timedelta(seconds=1)
    )
    with pytest.raises(OpportunityError):
        validate_economics_time(
            market_at=NOW,
            book_at=NOW - timedelta(minutes=1),
            observed_at=NOW,
            maximum_book_age=timedelta(seconds=1),
        )


class Signer:
    def __init__(self, *_: Any) -> None:
        pass

    def headers(self, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
        assert timestamp_ms == 1 and method == "GET"
        return {"signed": path}


class Transport:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.paths, self.calls = rows, [], 0

    def get(
        self, origin: str, path: str, headers: dict[str, str], *, timeout_seconds: float
    ) -> OrderbookReply:
        self.calls += 1
        self.paths.append(path)
        return OrderbookReply(200, json.dumps({"orderbooks": self.rows}).encode())


def test_exact_orderbook_transport_cardinality_scope_and_canonical_query() -> None:
    rows = [
        {"ticker": ticker, "orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        for ticker in ("A", "B")
    ]
    transport = Transport(rows)
    credential = ExactReadCredential(ReadEnvironment.PRODUCTION, "id", b"key")
    client = ExactOrderbookClient(credential, transport, clock_ms=lambda: 1, signer_factory=Signer)  # type: ignore[arg-type]
    assert set(client.fetch(["B", "A"])) == {"A", "B"}
    assert transport.paths == ["/trade-api/v2/markets/orderbooks?tickers=A&tickers=B"]
    for bad in ([], ["A", "A"], ["A?x=1"], [str(i) for i in range(101)]):
        with pytest.raises(OrderbookReadError):
            client.fetch(bad)
    with pytest.raises(OrderbookReadError):
        ExactOrderbookClient(
            ExactReadCredential(ReadEnvironment.DEMO, "id", b"key"),
            transport,
            signer_factory=Signer,
        )  # type: ignore[arg-type]


class ReplyTransport:
    def __init__(self, replies: list[OrderbookReply | Exception]) -> None:
        self.replies = replies
        self.calls = 0

    def get(
        self, origin: str, path: str, headers: dict[str, str], *, timeout_seconds: float
    ) -> OrderbookReply:
        item = self.replies[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def test_orderbook_response_failures_retry_bound_and_hundred_tickers() -> None:
    credential = ExactReadCredential(ReadEnvironment.PRODUCTION, "id", b"key")
    hundred = [f"M{i:03}" for i in range(100)]
    body = json.dumps(
        {
            "orderbooks": [
                {
                    "ticker": ticker,
                    "orderbook_fp": {"yes_dollars": [], "no_dollars": []},
                }
                for ticker in hundred
            ]
        }
    ).encode()
    client = ExactOrderbookClient(
        credential,
        ReplyTransport([OrderbookReply(200, body)]),
        clock_ms=lambda: 1,
        signer_factory=Signer,
    )  # type: ignore[arg-type]
    assert len(client.fetch(list(reversed(hundred)))) == 100
    bad_replies = (
        OrderbookReply(302, b"", location="https://example.invalid"),
        OrderbookReply(200, b"not-json"),
        OrderbookReply(200, b"[]"),
        OrderbookReply(200, b"x" * (MAX_RESPONSE_BYTES + 1)),
        OrderbookReply(200, json.dumps({"orderbooks": []}).encode()),
        OrderbookReply(
            200,
            json.dumps({"orderbooks": [{"ticker": "A"}, {"ticker": "A"}]}).encode(),
        ),
        OrderbookReply(
            200,
            json.dumps({"orderbooks": [{"ticker": "A"}, {"ticker": "B"}]}).encode(),
        ),
    )
    for reply in bad_replies:
        failing = ExactOrderbookClient(
            credential, ReplyTransport([reply]), clock_ms=lambda: 1, signer_factory=Signer
        )  # type: ignore[arg-type]
        with pytest.raises(OrderbookReadError):
            failing.fetch(["A"])
    retry_transport = ReplyTransport([TimeoutError(), TimeoutError(), TimeoutError()])
    retrying = ExactOrderbookClient(
        credential,
        retry_transport,
        clock_ms=lambda: 1,
        sleep=lambda _: None,
        max_retries=2,
        signer_factory=Signer,
    )  # type: ignore[arg-type]
    with pytest.raises(OrderbookReadError):
        retrying.fetch(["A"])
    assert retry_transport.calls == 3
