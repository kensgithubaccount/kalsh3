import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.gas_a1 import (
    DiscoveryError,
    GasA1Error,
    GasA1Store,
    collect_once,
    conservative_taker_debits,
    discover_series_markets,
    measure_update_ordering,
    parse_aaa_html,
    parse_active_market,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_parse_aaa_national_and_states_preserves_hash() -> None:
    body = (
        b"<table><tr><td>Current Avg.</td><td>$3.2100</td></tr>"
        b"<tr><td>Yesterday Avg.</td><td>$3.2000</td></tr>"
        b"<tr><td>Texas</td><td>$3.0000</td></tr></table>"
    )
    rows = parse_aaa_html(body, url="https://gasprices.aaa.com/", observed_at=NOW)
    assert rows[0]["value"] == "3.2100"
    assert rows[0]["raw_sha256"]
    assert any(row["geography"] == "Texas" for row in rows)


def test_aaa_missing_national_fails_closed() -> None:
    with pytest.raises(GasA1Error):
        parse_aaa_html(
            b"<table><tr><td>Texas</td><td>$3.00</td></tr></table>",
            url="https://gasprices.aaa.com/",
            observed_at=NOW,
        )


def test_market_requires_exact_aaa_authority() -> None:
    raw = {
        "ticker": "KXAAAGASD-26SEP15-3.20",
        "event_ticker": "KXAAAGASD-26SEP15",
        "status": "active",
        "rules_primary": "AAA Fuel Prices at https://gasprices.aaa.com/",
        "rules_secondary": "",
        "title": "Will the average be above $3.20 on 2026-09-15?",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "close_time": "2026-09-14T23:00:00Z",
        "settlement_ts": "2026-09-16T12:00:00Z",
    }
    parsed = parse_active_market(raw, raw_sha256="a" * 64, observed_at=NOW)
    assert parsed.strike == Decimal("3.20")
    assert parsed.latency_eligible is True
    with pytest.raises(GasA1Error):
        parse_active_market(
            {**raw, "rules_primary": "EIA fuel prices"}, raw_sha256="a" * 64, observed_at=NOW
        )


def test_synchronized_page_changes_are_not_a_latency_edge() -> None:
    national = ((NOW, "a"), (NOW.replace(minute=10), "b"))
    states = ((NOW, "x"), (NOW.replace(minute=11), "y"))
    result = measure_update_ordering(national, states)
    assert result["classification"] == "SYNCHRONIZED"
    assert result["latency_claim_allowed"] is False


def test_taker_debits_use_opposite_side_asks_and_fees() -> None:
    result = conservative_taker_debits(
        yes_bids=[["0.40", "2"]],
        no_bids=[["0.30", "2"]],
        fee_type="quadratic",
        fee_multiplier=Decimal("1"),
    )
    assert result["yes_best_ask"] == "0.70"
    assert result["no_best_ask"] == "0.60"
    assert Decimal(result["yes_all_in_debit"]) > Decimal("0.70")


def test_store_reopens_and_registers_before_rows(tmp_path: Path) -> None:
    path = tmp_path / "gas.sqlite3"
    store = GasA1Store(path)
    store.register_run("r1", NOW)
    store.source("r1", {"raw_sha256": "x"})
    reopened = GasA1Store(path)
    with sqlite3.connect(reopened.path) as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 1
        assert db.execute("select count(*) from source_observations").fetchone()[0] == 1


def _market_evidence(
    markets: list[dict[str, object]], cursor: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {"markets": markets}
    if cursor is not None:
        payload["cursor"] = cursor
    return {"classification": "SUCCESS", "payload": payload}


def test_series_discovery_does_not_scan_or_filter_generic_first_page() -> None:
    requested: list[str] = []
    generic_page: list[dict[str, object]] = [
        {"ticker": f"KXOTHER-{index}"} for index in range(1000)
    ]
    gas_market: dict[str, object] = {"ticker": "KXAAAGASD-26SEP15-3.20"}
    assert len(generic_page) == 1000

    def fetch(path: str) -> dict[str, object]:
        requested.append(path)
        assert "series_ticker=KXAAAGASD" in path
        assert "status=open" in path
        return _market_evidence([gas_market])

    result = discover_series_markets(fetch)
    assert result == (gas_market,)
    assert len(requested) == 1
    assert all("series_ticker=KXAAAGASD" in path for path in requested)


def test_series_discovery_paginates_and_rejects_incomplete_cursor() -> None:
    requested: list[str] = []

    def fetch(path: str) -> dict[str, object]:
        requested.append(path)
        if len(requested) == 1:
            return _market_evidence([], "next")
        return _market_evidence([{"ticker": "KXAAAGASD-26SEP16-3.30"}])

    assert len(discover_series_markets(fetch)) == 1
    assert "cursor=next" in requested[1]

    def incomplete(_path: str) -> dict[str, object]:
        return _market_evidence([], "same")

    with pytest.raises(DiscoveryError, match="cursor"):
        discover_series_markets(incomplete, max_pages=2)


def test_empty_series_query_is_durable_no_market(tmp_path: Path) -> None:
    store = GasA1Store(tmp_path / "gas.sqlite3")
    run_id = "empty"
    collect_once(store, run_id=run_id, discovery_fetch=lambda _path: _market_evidence([]))
    with sqlite3.connect(store.path) as db:
        assert db.execute("select kind from failures where run_id=?", (run_id,)).fetchone() == (
            "NO_MARKET",
        )


def test_series_query_failure_is_not_no_market(tmp_path: Path) -> None:
    store = GasA1Store(tmp_path / "gas.sqlite3")

    def failed(_path: str) -> dict[str, object]:
        return {"classification": "HTTP_OR_NETWORK_FAILURE"}

    collect_once(store, run_id="failed", discovery_fetch=failed)
    with sqlite3.connect(store.path) as db:
        assert db.execute("select kind from failures where run_id=?", ("failed",)).fetchone() == (
            "DISCOVERY_FAILED",
        )
