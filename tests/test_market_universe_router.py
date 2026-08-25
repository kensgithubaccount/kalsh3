from datetime import UTC, datetime

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
    result = census(
        [market()], event_rows=[event("Unmapped")], series_rows=[series("Unmapped")]
    )
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
