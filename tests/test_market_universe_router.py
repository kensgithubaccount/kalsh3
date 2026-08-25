from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.market_universe.lifecycle import LifecycleState, ProductType
from services.market_universe.router import MarketUniverseRouter, UniverseCensusError

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def series(category: str = "Economics") -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": category,
        "frequency": "daily",
        "settlement_sources": [{"name": "Official Source", "url": "https://example.invalid"}],
    }


def event(category: str = "Economics") -> dict[str, object]:
    return {
        "event_ticker": "KXEVENT",
        "series_ticker": "KXSERIES",
        "title": "Test event",
        "category": category,
    }


def market(ticker: str = "KXEVENT-10", **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": "KXEVENT",
        "title": "Test threshold",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "The market resolves Yes if the official value is at least 10.",
        "price_level_structure": "standard",
        "timezone": "UTC",
        "expiration_time": "2026-08-26T20:00:00Z",
        "volume_fp": "12.00",
        "open_interest_fp": "3.00",
    }
    row.update(changes)
    return row


def census(markets: list[dict[str, object]], **changes: object):
    values: dict[str, object] = {
        "market_rows": markets,
        "event_rows": [event()],
        "series_rows": [series()],
        "source_authority": "captured-public-kalshi",
        "request_locator": "fixture://whole-exchange",
        "response_sha256": "a" * 64,
        "captured_at": CAPTURED_AT,
    }
    values.update(changes)
    return MarketUniverseRouter().census(**values)  # type: ignore[arg-type]


def test_valid_contract_is_deterministically_understood() -> None:
    result = census([market()])
    assert result.manifest.accounted_market_count == 1
    assert result.manifest.state_counts == (("SEMANTICALLY_UNDERSTOOD", 1),)
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert result.records[0].product_type is ProductType.BINARY_EVENT


def test_malformed_market_is_quarantined_not_dropped() -> None:
    malformed = market()
    malformed.pop("ticker")
    result = census([malformed])
    assert result.manifest.input_market_count == 1
    assert result.manifest.accounted_market_count == 1
    assert result.records == ()
    assert len(result.quarantines) == 1
    assert result.quarantines[0].reason == "MARKET_PARSE_FAILURE"


def test_valid_market_and_malformed_sibling_are_both_accounted() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    result = census([market(), malformed])
    assert result.manifest.input_market_count == 2
    assert result.manifest.accounted_market_count == 2
    assert len(result.records) == 1
    assert len(result.quarantines) == 1


def test_duplicate_market_identity_fails_closed() -> None:
    with pytest.raises(UniverseCensusError, match="duplicate market identity"):
        census([market(), market()])


def test_missing_parent_remains_discovered() -> None:
    result = census([market()], event_rows=[])
    record = result.records[0]
    assert record.state is LifecycleState.DISCOVERED
    assert "MISSING_EVENT" in record.semantic_blockers


def test_unknown_product_remains_visible_and_discovered() -> None:
    result = census([market(market_type="mystery")])
    record = result.records[0]
    assert record.product_type is ProductType.UNKNOWN
    assert record.state is LifecycleState.DISCOVERED
    assert "UNKNOWN_PRODUCT" in record.unsupported_reasons


def test_non_event_product_is_hard_gated() -> None:
    result = census([market(product_type="perpetual_future")])
    record = result.records[0]
    assert record.product_type is ProductType.NON_EVENT
    assert record.state is LifecycleState.DISCOVERED
    assert "NON_EVENT_PRODUCT_OUT_OF_DOMAIN" in record.unsupported_reasons


def test_scalar_and_mve_are_not_coerced_to_binary() -> None:
    scalar = census([market(settlement_value_dollars="0.50")]).records[0]
    mve = census([market(mve_collection_ticker="MVE-1")]).records[0]
    assert scalar.product_type is ProductType.SCALAR_OR_PARTIAL
    assert scalar.state is LifecycleState.DISCOVERED
    assert mve.product_type is ProductType.MULTIVARIATE_EVENT
    assert mve.state is LifecycleState.DISCOVERED


def test_unsupported_semantic_grammar_remains_discovered() -> None:
    result = census([market(rules_primary="The official source will decide the outcome.")])
    record = result.records[0]
    assert record.state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in record.semantic_blockers


def test_unknown_category_is_not_a_discovery_blocker() -> None:
    result = census([market()], event_rows=[event("Unmapped")], series_rows=[series("Unmapped")])
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert result.records[0].advisory_family == "other/unknown"


def test_input_iteration_order_does_not_change_manifest_identity() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    left = census(markets)
    right = census(list(reversed(markets)))
    assert left.manifest.manifest_id == right.manifest.manifest_id
    assert [record.lifecycle_record_id for record in left.records] == [
        record.lifecycle_record_id for record in right.records
    ]


def quote_fields() -> dict[str, object]:
    return {
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "yes_bid_size_fp": "8.00",
        "yes_ask_size_fp": "9.00",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "volume_24h_fp": "7.00",
        "liquidity_dollars": "25.00",
    }


def test_m27b_route_is_composed_but_never_confers_semantic_authority() -> None:
    row = market(
        rules_primary="The official source will decide the outcome.",
        strike_type="greater",
        floor_strike="10",
    )
    record = census([row]).records[0]
    assert record.specialist_route_id is not None
    assert record.specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    assert record.state is LifecycleState.DISCOVERED


def test_family_coverage_is_descriptive_and_contains_no_readiness_score() -> None:
    row = market(**quote_fields())
    result = census([row])
    descriptor = result.coverage_descriptors[0]
    assert descriptor.volume_24h == 7
    assert descriptor.liquidity == 25
    assert descriptor.yes_bid == Decimal("0.40")
    assert result.coverage_manifest.category_counts == (("Economics", 1),)
    assert result.coverage_manifest.series_counts == (("KXSERIES", 1),)
    assert result.coverage_manifest.recurrence_counts == (("daily", 1),)
    assert result.coverage_manifest.product_counts == (("BINARY_EVENT", 1),)
    assert not hasattr(result.coverage_manifest, "research_readiness_score")
    assert not hasattr(descriptor, "historical_depth")
    assert not hasattr(descriptor, "executable_capacity")
    assert not hasattr(descriptor, "slippage")


def test_invalid_broad_descriptor_is_visible_but_not_a_semantic_blocker() -> None:
    fields = quote_fields()
    fields["yes_bid_dollars"] = "not-a-number"
    row = market(**fields)
    result = census([row])
    record = result.records[0]
    descriptor = result.coverage_descriptors[0]
    assert record.state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert "INVALID_DISCOVERY_QUOTE_INPUT" in record.specialist_route_reasons
    assert "INVALID_DESCRIPTOR_YES_BID_DOLLARS" in descriptor.descriptor_issues


def _previous(result):
    record = result.records[0]
    return {record.market_ticker: record}


@pytest.mark.parametrize(
    ("market_changes", "event_rows", "series_rows"),
    [
        ({"rules_primary": "The official value is at least 11."}, [event()], [series()]),
        (
            {},
            [event()],
            [
                {
                    **series(),
                    "settlement_sources": [
                        {"name": "Different Official Source", "url": "https://other.invalid"}
                    ],
                }
            ],
        ),
        ({"settlement_value_dollars": "0.50"}, [event()], [series()]),
        ({"expiration_time": "2026-08-27T20:00:00Z"}, [event()], [series()]),
        (
            {"event_ticker": "KXEVENT2"},
            [
                {
                    **event(),
                    "event_ticker": "KXEVENT2",
                }
            ],
            [series()],
        ),
    ],
)
def test_material_semantic_change_supersedes_prior_record(
    market_changes: dict[str, object],
    event_rows: list[dict[str, object]],
    series_rows: list[dict[str, object]],
) -> None:
    first = census([market()])
    second = census(
        [market(**market_changes)],
        event_rows=event_rows,
        series_rows=series_rows,
        response_sha256="b" * 64,
        previous_records=_previous(first),
    )
    assert second.records[0].supersedes_record_id == first.records[0].lifecycle_record_id
    assert first.records[0].supersedes_record_id is None


def test_price_only_change_does_not_supersede_semantic_proof() -> None:
    first = census([market(**quote_fields())])
    changed = quote_fields()
    changed["yes_bid_dollars"] = "0.41"
    changed["no_ask_dollars"] = "0.59"
    second = census(
        [market(**changed)],
        response_sha256="b" * 64,
        previous_records=_previous(first),
    )
    assert second.records[0].supersedes_record_id is None
    assert second.records[0].semantic_material_hash == first.records[0].semantic_material_hash
    assert second.records[0].lifecycle_record_id != first.records[0].lifecycle_record_id


def test_unchanged_replay_is_identical_even_when_prior_record_is_supplied() -> None:
    first = census([market()])
    second = census([market()], previous_records=_previous(first))
    assert second.records[0].lifecycle_record_id == first.records[0].lifecycle_record_id
    assert second.manifest.manifest_id == first.manifest.manifest_id
    assert second.coverage_manifest.manifest_id == first.coverage_manifest.manifest_id


def test_title_keywords_alone_cannot_promote_unsupported_rules() -> None:
    first = census([market(rules_primary="The official source decides the outcome.")])
    second = census(
        [
            market(
                title="At least 10 according to all the important keywords",
                rules_primary="The official source decides the outcome.",
            )
        ]
    )
    assert first.records[0].state is LifecycleState.DISCOVERED
    assert second.records[0].state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in second.records[0].semantic_blockers


def test_exchange_category_alone_cannot_promote_weather_without_station() -> None:
    rules = "The temperature is at least 10."
    first = census(
        [market(rules_primary=rules)],
        event_rows=[event("Weather")],
        series_rows=[series("Weather")],
    )
    second = census(
        [market(rules_primary=rules)],
        event_rows=[event("Economics")],
        series_rows=[series("Economics")],
    )
    assert first.records[0].state is LifecycleState.DISCOVERED
    assert second.records[0].state is LifecycleState.DISCOVERED
    assert "WEATHER_STATION_MISSING" in second.records[0].semantic_blockers


def test_derived_family_classifier_cannot_promote_unsupported_market(monkeypatch) -> None:
    from services.market_universe import router as router_module
    from services.market_universe.quality import Family

    monkeypatch.setattr(router_module, "classify", lambda _category, _title: Family.MACRO)
    result = census([market(rules_primary="The official source decides the outcome.")])
    assert result.records[0].advisory_family == "macro"
    assert result.records[0].state is LifecycleState.DISCOVERED
