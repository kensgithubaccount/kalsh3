from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.gas_a1 import (
    GasA1Error,
    GasA1Store,
    conservative_taker_debits,
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
    import sqlite3

    with sqlite3.connect(reopened.path) as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 1
        assert db.execute("select count(*) from source_observations").fetchone()[0] == 1
