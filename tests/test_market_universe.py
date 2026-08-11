from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.market_universe.domain import (
    Event,
    Market,
    MarketStatus,
    Series,
    UniverseValidationError,
    material_hashes,
    normalize_status,
    parse_quantity,
)
from services.market_universe.pricing import PriceLadder, chunk_tickers, normalize_book
from services.market_universe.quality import Family, QualityReason, classify, diagnose
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    UniverseSynchronizer,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def series(
    ticker: str = "S", updated: str = "2026-01-01T00:00:00Z", **changes: Any
) -> dict[str, Any]:
    raw = {
        "ticker": ticker,
        "title": "Weather",
        "category": "Weather",
        "frequency": "daily",
        "tags": [],
        "settlement_sources": [{"name": "NOAA", "url": "https://weather.gov"}],
        "fee_type": "quadratic",
        "fee_multiplier": "0.07",
        "last_updated_ts": updated,
    }
    raw.update(changes)
    return raw


def event(ticker: str = "E", **changes: Any) -> dict[str, Any]:
    raw = {
        "event_ticker": ticker,
        "series_ticker": "S",
        "title": "Temperature event",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }
    raw.update(changes)
    return raw


def market(ticker: str = "M", **changes: Any) -> dict[str, Any]:
    raw = {
        "ticker": ticker,
        "event_ticker": "E",
        "title": "Will temperature exceed 70?",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "NOAA reading",
        "rules_secondary": "final",
        "settlement_sources": [{"name": "NOAA"}],
        "price_level_structure": "linear",
        "price_ranges": [{"min": "0.01", "max": "0.99", "step": "0.01"}],
        "fractional_trading_enabled": False,
        "is_provisional": False,
        "volume_fp": "10.5",
        "open_interest_fp": "4",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }
    raw.update(changes)
    return raw


class Transport:
    def __init__(self, pages: dict[str, list[Any]]):
        self.pages = pages
        self.calls = []

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append(path)
        key = next(k for k in self.pages if k in path)
        value = self.pages[key].pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def sync_transport(kind: str, records: list[dict[str, Any]], page_size: int = 1) -> Transport:
    pages = []
    for i in range(0, len(records), page_size):
        pages.append(
            {
                kind: records[i : i + page_size],
                "cursor": str(i + page_size) if i + page_size < len(records) else "",
            }
        )
    return Transport({f"/{kind}?": pages})


def test_series_event_and_market_normalization_versions_and_idempotency() -> None:
    repo = MemoryUniverseRepository()
    t = Transport(
        {
            "/series?": [{"series": [series()], "cursor": ""}],
            "/events?": [{"events": [event()], "cursor": ""}],
            "/markets?": [{"markets": [market()], "cursor": ""}],
        }
    )
    s = UniverseSynchronizer(t, repo, clock=lambda: NOW)
    assert (
        s.sync("series").inserted == 1
        and s.sync("events").inserted == 1
        and s.sync("markets").inserted == 1
    )
    t.pages["/markets?"] = [{"markets": [market()], "cursor": ""}]
    assert s.sync("markets").unchanged == 1
    t.pages["/markets?"] = [{"markets": [market(rules_primary="changed")], "cursor": ""}]
    assert s.sync("markets").updated == 1 and len(repo.rules_versions["M"]) == 2
    old_rules, new_metadata = material_hashes(market(yes_bid_dollars="0.40"))
    new_rules, new_metadata2 = material_hashes(market(yes_bid_dollars="0.60"))
    assert old_rules == new_rules and new_metadata == new_metadata2


def test_complete_and_very_large_baseline_pagination() -> None:
    records = [market(f"M{i}") for i in range(3001)]
    repo = MemoryUniverseRepository()
    run = UniverseSynchronizer(
        sync_transport("markets", records, 73), repo, clock=lambda: NOW
    ).sync("markets")
    assert (
        run.completeness == Completeness.COMPLETE and run.pages > 40 and len(repo.markets) == 3001
    )


def test_repeated_cursor_and_later_failure_are_incomplete() -> None:
    for pages in (
        [{"markets": [market()], "cursor": "x"}, {"markets": [], "cursor": "x"}],
        [{"markets": [market()], "cursor": "x"}, TimeoutError()],
    ):
        run = UniverseSynchronizer(
            Transport({"/markets?": list(pages)}), MemoryUniverseRepository(), clock=lambda: NOW
        ).sync("markets")
        assert run.completeness == Completeness.PARTIAL and run.finished_at is not None


def test_incremental_overlap_watermark_and_failed_run_does_not_advance() -> None:
    repo = MemoryUniverseRepository()
    repo.watermarks["markets"] = NOW
    t = Transport(
        {"/markets?": [{"markets": [market(last_updated_ts="2026-01-02T00:00:10Z")], "cursor": ""}]}
    )
    run = UniverseSynchronizer(t, repo, clock=lambda: NOW + timedelta(minutes=1)).sync(
        "markets", incremental=True
    )
    assert (
        "min_updated_ts=" in t.calls[0]
        and "mve_filter=exclude" in t.calls[0]
        and run.requested_watermark == NOW - timedelta(seconds=60)
        and repo.watermarks["markets"] > NOW
    )
    prior = repo.watermarks["markets"]
    bad = Transport({"/markets?": [TimeoutError()]})
    UniverseSynchronizer(bad, repo, clock=lambda: NOW).sync("markets", incremental=True)
    assert repo.watermarks["markets"] == prior


def test_series_event_incremental_paths() -> None:
    for kind, item in (("series", series()), ("events", event())):
        repo = MemoryUniverseRepository()
        t = Transport({f"/{kind}?": [{kind: [item], "cursor": ""}]})
        run = UniverseSynchronizer(t, repo, clock=lambda: NOW).sync(kind, incremental=True)
        assert run.completeness == Completeness.COMPLETE and "min_updated_ts=" in t.calls[0]


def test_status_price_ladders_and_quantity() -> None:
    assert {normalize_status(x) for x in MarketStatus} == set(MarketStatus)
    with pytest.raises(UniverseValidationError):
        normalize_status("open")
    cent = PriceLadder.parse("linear", [{"min": "0.01", "max": "0.99", "step": "0.01"}])
    assert (
        cent.is_valid(Decimal("0.50"))
        and cent.next_above(Decimal("0.50")) == Decimal("0.51")
        and cent.next_below(Decimal("0.50")) == Decimal("0.49")
    )
    deci = PriceLadder.parse("linear", [{"min": "0.001", "max": "0.999", "step": "0.001"}])
    assert deci.precision == 3
    tapered = PriceLadder.parse(
        "tapered",
        [
            {"min": "0.01", "max": "0.10", "step": "0.01"},
            {"min": "0.11", "max": "0.90", "step": "0.05"},
        ],
    )
    assert tapered.is_valid(Decimal("0.16")) and not tapered.is_valid(Decimal("0.15"))
    with pytest.raises(UniverseValidationError):
        PriceLadder.parse("mystery", [])
    assert parse_quantity("2") == 2 and parse_quantity("0.12") == Decimal("0.12")
    with pytest.raises(UniverseValidationError):
        parse_quantity("0.125")


def test_books_complement_chunks_and_quality() -> None:
    book = normalize_book([["0.40", "2.5"]], [["0.55", "3"]], NOW, NOW)
    assert (
        book.best_yes_bid == Decimal("0.40")
        and book.best_yes_ask == Decimal("0.45")
        and book.spread == Decimal("0.05")
    )
    assert chunk_tickers([str(i) for i in range(201)]) == [
        [str(i) for i in range(100)],
        [str(i) for i in range(100, 200)],
        ["200"],
    ]
    m = Market.parse(market(is_provisional=True, mve_collection_ticker="MVE"))
    reasons = diagnose(m, None, structure_supported=False, has_source=False, now=NOW)
    assert {
        QualityReason.PROVISIONAL,
        QualityReason.MVE_UNSUPPORTED,
        QualityReason.MISSING_QUOTE,
        QualityReason.UNSUPPORTED_PRICE_STRUCTURE,
    }.issubset(reasons)
    assert "liquidity_dollars" not in "".join(reason.value for reason in reasons)


def test_taxonomy_cutoff_and_orderbook_batching() -> None:
    assert (
        classify("Weather", "rain") == Family.WEATHER
        and classify("", "unrecognized") == Family.UNKNOWN
    )
    transport = Transport(
        {
            "historical/cutoff": [
                {
                    "market_settled_ts": "2025-01-01T00:00:00Z",
                    "trades_created_ts": "2025-01-02T00:00:00Z",
                    "orders_updated_ts": "2025-01-03T00:00:00Z",
                }
            ],
            "orderbooks?": [{"orderbooks": []}, {"orderbooks": []}, {"orderbooks": []}],
        }
    )
    sync = UniverseSynchronizer(transport, MemoryUniverseRepository(), clock=lambda: NOW)
    assert sync.sync_historical_cutoff().observed_at == NOW
    sync.fetch_orderbooks([str(i) for i in range(201)])
    assert len([x for x in transport.calls if "orderbooks" in x]) == 3


def test_malformed_optional_and_required_series() -> None:
    parsed = Series.parse(series(fee_multiplier=None, last_updated_ts=None))
    assert parsed.fee_multiplier is None
    with pytest.raises(UniverseValidationError):
        Series.parse(series(title=None))
    changed = Series.parse(series(settlement_sources=[{"name": "NWS"}], fee_multiplier="0.08"))
    assert changed.metadata_hash != parsed.metadata_hash
    assert Event.parse(event()).series_ticker == "S"
