from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.breaking_signals.adapters import (
    AdapterError,
    PredictBuddyAdapter,
    PredictBuddyMode,
    XFilteredStreamAdapter,
    XRule,
    parse_jetstream,
    verify_bluesky,
)
from services.breaking_signals.dedupe import DedupeIndex, independent_chains
from services.breaking_signals.matching import (
    CrossVenuePriceObservation,
    MatchClass,
    MatchSemantics,
    match,
)
from services.breaking_signals.models import (
    ExecutableSnapshot,
    ExternalObservation,
    ManipulationFlag,
    Originality,
    Relevance,
    SignalObservation,
    SignalStatus,
    SourceDefinition,
    SourceState,
    VerificationState,
)
from services.breaking_signals.pipeline import (
    Connector,
    ConnectorHealth,
    ConnectorState,
    ConnectorSupervisor,
    schedule_reactions,
    shadow_sink,
)
from services.breaking_signals.polymarket import (
    MarketEvent,
    PolymarketDiscovery,
    PolymarketStream,
    StreamState,
    SyncState,
)
from services.contract_intelligence.specification import Comparator, PayoutModel

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def market(i: int = 1) -> dict[str, Any]:
    return {
        "id": str(i),
        "conditionId": f"c{i}",
        "question": "Will CPI be at least 3?",
        "description": "Initial release",
        "eventId": "e",
        "tokens": [f"yes{i}", f"no{i}"],
        "outcomes": ["Yes", "No"],
        "endDate": "2026-09-01T00:00:00Z",
        "resolved": False,
        "enableOrderBook": True,
        "tickSize": "0.001",
        "minOrderSize": "0.01",
        "negRisk": False,
        "updatedAt": "2026-08-10T00:00:00Z",
    }


class Pages:
    def __init__(self, pages: list[Any]):
        self.pages = pages

    def get(self, url: str, *, timeout_seconds: float) -> dict[str, Any]:
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_polymarket_complete_pagination_loop_and_partial() -> None:
    discovery = PolymarketDiscovery(
        Pages([{"data": [market(1)], "next_cursor": "a"}, {"data": [market(2)], "next_cursor": ""}])
    )
    markets, run = discovery.markets()
    assert (
        run.state == SyncState.COMPLETE
        and len(markets) == 2
        and markets[0].tick_size == Decimal("0.001")
    )
    _, loop = PolymarketDiscovery(
        Pages([{"data": [market()], "next_cursor": "a"}, {"data": [], "next_cursor": "a"}])
    ).markets()
    assert loop.state == SyncState.PARTIAL
    known, partial = PolymarketDiscovery(
        Pages([{"data": [market()], "next_cursor": "a"}, TimeoutError()])
    ).markets()
    assert len(known) == 1 and partial.state == SyncState.PARTIAL


def test_polymarket_ws_commands_events_stale_backpressure_reconnect() -> None:
    stream = PolymarketStream(queue_limit=1)
    subscribe = stream.subscribe({"yes1"})
    assert subscribe["custom_feature_enabled"] is True and subscribe["operation"] == "subscribe"
    assert stream.unsubscribe({"yes1"})["operation"] == "unsubscribe"
    assert stream.enqueue(
        {
            "event_type": "tick_size_change",
            "asset_id": "yes1",
            "new_tick_size": "0.01",
            "timestamp": "2026-08-10T00:00:00Z",
        },
        NOW,
    )
    event = stream.parse_next()
    assert event.tick_size == Decimal("0.01") and "yes1" in stream.price_ladder_invalid
    fixtures = [
        {"event_type": "book", "market": "1"},
        {"event_type": "price_change", "asset_id": "yes1", "price": "0.42", "size": "1.25"},
        {"event_type": "last_trade_price", "asset_id": "yes1", "price": "0.43", "size": ".5"},
        {"event_type": "best_bid_ask", "asset_id": "yes1", "best_bid": "0.42", "best_ask": "0.44"},
        {"event_type": "new_market", "market": "2"},
        {"event_type": "market_resolved", "market": "1"},
    ]
    for raw in fixtures:
        assert MarketEvent.parse(raw).event_type == raw["event_type"]
    assert (
        stream.enqueue(fixtures[0], NOW)
        and not stream.enqueue(fixtures[1], NOW)
        and stream.state == StreamState.BACKPRESSURED
        and stream.gaps == 1
    )
    stream.queue.clear()
    assert stream.stale(NOW + timedelta(seconds=31)) and stream.reconnect_delay() == 1.0
    from services.market_universe.domain import UniverseValidationError

    with pytest.raises(UniverseValidationError):
        MarketEvent.parse({"event_type": "execution"})


def semantics(**changes: Any) -> MatchSemantics:
    base = dict(
        market_id="K",
        entities=("CPI",),
        event_type="release",
        outcome_meaning="initial CPI >= 3",
        comparator=Comparator.GTE,
        threshold=Decimal(3),
        date=NOW,
        timezone="UTC",
        geography="US",
        authority="BLS",
        cancellation_rules="cancel",
        revision_rules="initial",
        recount_rules="none",
        payout_model=PayoutModel.SIMPLE_BINARY,
        date_relation="on",
    )
    base.update(changes)
    return MatchSemantics(**base)


def test_conservative_matching_adversarial_fields() -> None:
    assert (
        match(semantics(), semantics(market_id="P")).classification
        == MatchClass.EXACT_DETERMINISTIC_MATCH
    )
    for changes in (
        {"date": NOW + timedelta(days=1)},
        {"timezone": "America/New_York"},
        {"comparator": Comparator.GT},
        {"authority": "BEA"},
        {"geography": "Canada"},
        {"revision_rules": "final"},
        {"cancellation_rules": "void"},
        {"entities": ("PPI",)},
        {"payout_model": PayoutModel.MULTIVARIATE},
        {"date_relation": "by"},
    ):
        assert match(semantics(), semantics(market_id="P", **changes)).classification in {
            MatchClass.SEMANTIC_CONFLICT,
            MatchClass.INCOMPATIBLE,
        }
    assert (
        match(
            semantics(), semantics(market_id="P", authority=None, timezone=None, date=None)
        ).classification
        != MatchClass.EXACT_DETERMINISTIC_MATCH
    )


def snapshot(venue: str, ask: str) -> ExecutableSnapshot:
    return ExecutableSnapshot(
        venue,
        "m",
        Decimal(".4"),
        Decimal(ask),
        Decimal(".4"),
        Decimal(".5"),
        Decimal(2),
        Decimal(3),
        NOW,
        NOW,
        True,
    )


def test_cross_venue_uses_executable_asks_not_midpoint() -> None:
    observation = CrossVenuePriceObservation.create(
        MatchClass.EXACT_DETERMINISTIC_MATCH,
        snapshot("kalshi", ".45"),
        snapshot("polymarket", ".48"),
    )
    assert (
        observation.yes_ask_difference == Decimal(".03")
        and observation.observation_type == "CROSS_VENUE_DISCREPANCY_OBSERVATION"
    )


def observation(event_id: str, text: str, **changes: Any) -> ExternalObservation:
    raw = dict(
        event_id=event_id,
        provider_event_id=event_id,
        source_id="rss",
        source_type="rss",
        author_stable_id="agency",
        author_display_identity="Agency",
        canonical_url=f"https://agency.gov/{event_id}",
        original_event_id=None,
        raw_content_hash=hashlib.sha256(text.encode()).hexdigest(),
        normalized_content_hash="",
        source_event_time=NOW,
        source_publish_time=NOW,
        provider_receive_time=None,
        bot_ingest_time=NOW,
        receive_monotonic_ns=1,
        raw_payload_reference=text,
        deletion_state="active",
        correction_of_event_id=None,
        language="en",
        linked_entities=("CPI",),
        candidate_markets=("M",),
        duplicate_of_event_id=None,
        verification_state=VerificationState.PRIMARY_CONFIRMED,
        originality=Originality.PRIMARY_ORIGINAL,
        manipulation_flags=(),
        parser_version="1",
    )
    raw.update(changes)
    return ExternalObservation(**raw)


def test_dedupe_numbers_lineage_recirculation_deletion_correction() -> None:
    index = DedupeIndex()
    first = observation("1", "CPI was 3.2%")
    assert index.add(first, "CPI was 3.2%").duplicate_of is None
    exact_dup = observation("2", "CPI was 3.2%", canonical_url=first.canonical_url)
    assert index.add(exact_dup, "CPI was 3.2%").duplicate_of == "1"
    different = observation("3", "CPI was 3.3%")
    assert index.add(different, "CPI was 3.3%").duplicate_of is None
    old = observation("4", "old", source_publish_time=NOW - timedelta(days=2))
    assert ManipulationFlag.STALE_RECIRCULATION in index.recirculation_flags(old, NOW)
    reposts = [observation(str(i), "claim", original_event_id="root") for i in range(10, 20)]
    assert independent_chains(reposts) == 1
    independent = [observation("a", "claim"), observation("b", "claim", source_id="other")]
    assert independent_chains(independent) == 2


def test_source_and_signal_are_zero_influence() -> None:
    source = SourceDefinition(
        "s",
        "Source",
        "provider",
        "rss",
        "primary",
        "official_https",
        "agency.gov",
        "Agency",
        ("macro",),
        "permitted",
        False,
        None,
        "hourly",
        1000,
        Decimal(0),
        SourceState.SHADOW,
        "exchange named",
        30,
        Decimal(0),
    )
    assert source.production_influence == 0
    with pytest.raises(ValueError):
        replace(source, production_influence=Decimal(1))
    signal = SignalObservation(
        "sig",
        ("1",),
        ("s",),
        NOW,
        ("M",),
        (),
        "macro",
        ("CPI",),
        (),
        VerificationState.PRIMARY_CONFIRMED,
        1,
        Originality.PRIMARY_ORIGINAL,
        0,
        "primary",
        True,
        (),
        None,
        None,
        None,
        None,
        SignalStatus.CANDIDATE_FOR_RESEARCH,
        "Research only",
        Relevance.DIRECTLY_RELEVANT,
        (),
    )
    assert shadow_sink(signal) == "SHADOW RESEARCH DATA"
    assert len(schedule_reactions("sig", NOW).due_at) == 5


def test_predictbuddy_authorized_only_vendor_labels_and_corroboration() -> None:
    with pytest.raises(AdapterError):
        PredictBuddyAdapter(PredictBuddyMode.OFFICIAL_API)
    adapter = PredictBuddyAdapter(PredictBuddyMode.MANUAL_IMPORT)
    payload = {
        "alert_id": "a",
        "timestamp": "2026-08-10T00:00:00Z",
        "wallet": "w",
        "market": "m",
        "vendor_label": "insider",
        "size": "10.25",
    }
    alert = adapter.import_authorized(payload, NOW)
    assert (
        alert.vendor_label == "insider"
        and alert.vendor == "PredictBuddy"
        and alert.verification == "UNCORROBORATED_VENDOR_SIGNAL"
    )
    assert adapter.corroborate(alert, "w", "m").verification == "DIRECT_MARKET_CORROBORATED"
    with pytest.raises(AdapterError):
        adapter.import_authorized(payload, NOW)


def test_x_setup_rules_parse_repost_and_no_token_repr() -> None:
    adapter = XFilteredStreamAdapter()
    assert adapter.state.value == "SETUP_REQUIRED"
    adapter.set_rules([XRule("1", "from:agency", "official", 1)])
    parsed = adapter.parse(
        {
            "data": {
                "id": "x",
                "author_id": "stable",
                "text": "release",
                "referenced_tweets": [{"id": "original"}],
            }
        }
    )
    assert (
        parsed["author_stable_id"] == "stable"
        and parsed["original_event_id"] == "original"
        and "bearer" not in repr(adapter).lower()
    )


def test_bluesky_discovery_only_did_handle_and_revalidation() -> None:
    payload = {
        "did": "did:plc:stable",
        "handle": "old.handle",
        "time_us": 1_775_000_000_000_000,
        "commit": {
            "collection": "app.bsky.feed.post",
            "rkey": "r",
            "cid": "c",
            "operation": "create",
            "record": {"text": "claim"},
        },
    }
    event = parse_jetstream(payload, NOW)
    assert (
        event.did == "did:plc:stable"
        and event.handle == "old.handle"
        and event.verification == VerificationState.UNVERIFIED
    )

    class Verify:
        def verify(self, did: str, uri: str, cid: str | None) -> bool:
            return did == "did:plc:stable"

    assert verify_bluesky(event, Verify()).verification == VerificationState.IDENTITY_VERIFIED


def test_connector_independence_backpressure_and_circuit() -> None:
    supervisor = ConnectorSupervisor()
    poly = Connector(ConnectorHealth("poly", queue_limit=1))
    x = Connector(ConnectorHealth("x"))
    supervisor.add(poly)
    supervisor.add(x)
    assert poly.enqueue({"x": 1}, NOW) and not poly.enqueue({"x": 2}, NOW)
    assert (
        supervisor.state("poly").state == ConnectorState.DEGRADED
        and supervisor.state("x").state == ConnectorState.NOT_STARTED
    )
    assert x.failed(NOW) == 2.0


def test_polymarket_comments_ping_and_vendor_lead_comparison() -> None:
    from services.breaking_signals.adapters import compare_predictbuddy
    from services.breaking_signals.polymarket import CommentEvent, CommentStream

    comment = CommentEvent.parse(
        {"event_type": "comment_created", "comment_id": "c", "market_id": "m", "body": "rumor"},
        NOW,
    )
    assert comment.body == "rumor"
    stream = CommentStream()
    assert stream.heartbeat_due(NOW) and stream.ping(NOW) == "PING"
    adapter = PredictBuddyAdapter(PredictBuddyMode.MANUAL_IMPORT)
    alert = adapter.import_authorized(
        {"timestamp": "2026-08-10T00:00:00Z", "wallet": "w", "market": "m"}, NOW
    )
    direct_first = compare_predictbuddy(
        alert, NOW - timedelta(seconds=2), vendor_added_metadata=False
    )
    vendor_first = compare_predictbuddy(
        alert, NOW + timedelta(seconds=2), vendor_added_metadata=True
    )
    assert direct_first.first_observed_by == "direct_polymarket"
    assert vendor_first.first_observed_by == "predictbuddy" and vendor_first.vendor_added_metadata


def test_large_polymarket_and_duplicate_burst_fixture() -> None:
    markets, run = PolymarketDiscovery(
        Pages([{"data": [market(i) for i in range(5000)], "next_cursor": ""}])
    ).markets()
    assert run.state == SyncState.COMPLETE and len(markets) == 5000
    index = DedupeIndex()
    first = observation("root", "Agency reports CPI 3.2%")
    index.add(first, first.raw_payload_reference)
    for i in range(20_000):
        duplicate = observation(
            f"dup-{i}",
            "Agency reports CPI 3.2%",
            canonical_url=first.canonical_url,
            original_event_id="root",
        )
        assert index.add(duplicate, duplicate.raw_payload_reference).duplicate_of == "root"
