from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime

from services.market_universe.lifecycle import MarketLifecycleRecord
from services.market_universe.router import MarketUniverseRouter, UniverseCensusResult

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _series(
    *,
    source_name: str = "Official Source",
    source_url: str = "https://example.invalid",
) -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": "Economics",
        "frequency": "daily",
        "settlement_sources": [{"name": source_name, "url": source_url}],
    }


def _event(ticker: str = "KXEVENT", *, series_ticker: str = "KXSERIES") -> dict[str, object]:
    return {
        "event_ticker": ticker,
        "series_ticker": series_ticker,
        "title": "Test event",
        "category": "Economics",
    }


def _market(ticker: str = "KXEVENT-10", **changes: object) -> dict[str, object]:
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


def _quote_fields() -> dict[str, object]:
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


def _census(
    markets: list[dict[str, object]],
    *,
    event_rows: list[dict[str, object]] | None = None,
    series_rows: list[dict[str, object]] | None = None,
    response_sha256: str = "a" * 64,
    previous_records: Mapping[str, MarketLifecycleRecord] | None = None,
) -> UniverseCensusResult:
    return MarketUniverseRouter().census(
        market_rows=markets,
        event_rows=[_event()] if event_rows is None else event_rows,
        series_rows=[_series()] if series_rows is None else series_rows,
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2-identity-freeze",
        response_sha256=response_sha256,
        captured_at=CAPTURED_AT,
        previous_records=previous_records,
    )


def _ids(result: UniverseCensusResult) -> dict[str, object]:
    return {
        "lifecycle_record_ids": [record.lifecycle_record_id for record in result.records],
        "quarantine_ids": [record.quarantine_id for record in result.quarantines],
        "census_manifest_id": result.manifest.manifest_id,
        "descriptor_ids": [record.descriptor_id for record in result.coverage_descriptors],
        "family_coverage_manifest_id": result.coverage_manifest.manifest_id,
    }


def test_capture_canonical_ku_a1_identity_baseline_before_private_router_refactor() -> None:
    baseline: dict[str, object] = {}

    baseline["valid"] = _ids(_census([_market()]))
    baseline["unsupported"] = _ids(
        _census([_market(rules_primary="The official source decides the outcome.")])
    )
    baseline["missing_parent"] = _ids(_census([_market()], event_rows=[]))
    baseline["mve"] = _ids(_census([_market(mve_collection_ticker="MVE-1")]))
    baseline["scalar"] = _ids(_census([_market(settlement_value_dollars="0.50")]))
    baseline["non_event"] = _ids(_census([_market(product_type="perpetual_future")]))

    malformed = _market()
    malformed.pop("ticker")
    baseline["quarantine"] = _ids(_census([malformed]))

    malformed_sibling = _market("KXEVENT-BAD")
    malformed_sibling.pop("rules_primary")
    baseline["mixed"] = _ids(_census([_market(), malformed_sibling]))
    baseline["settlement_sources"] = _ids(
        _census(
            [_market()],
            series_rows=[
                _series(
                    source_name="Named Source",
                    source_url="https://source.invalid/path",
                )
            ],
        )
    )

    first = _census([_market()])
    first_record = first.records[0]
    material = _census(
        [_market(rules_primary="The official value is at least 11.")],
        response_sha256="b" * 64,
        previous_records={first_record.market_ticker: first_record},
    )
    baseline["material_supersession_first"] = _ids(first)
    baseline["material_supersession_second"] = _ids(material)

    unchanged = _census(
        [_market()],
        previous_records={first_record.market_ticker: first_record},
    )
    baseline["unchanged_replay"] = _ids(unchanged)

    quoted = _census([_market(**_quote_fields())])
    quoted_record = quoted.records[0]
    changed_quote = _quote_fields()
    changed_quote["yes_bid_dollars"] = "0.41"
    changed_quote["no_ask_dollars"] = "0.59"
    price_only = _census(
        [_market(**changed_quote)],
        response_sha256="b" * 64,
        previous_records={quoted_record.market_ticker: quoted_record},
    )
    baseline["price_only_first"] = _ids(quoted)
    baseline["price_only_second"] = _ids(price_only)

    warnings.warn(
        "KU_A1_IDENTITY_BASELINE=" + json.dumps(baseline, sort_keys=True),
        stacklevel=1,
    )
