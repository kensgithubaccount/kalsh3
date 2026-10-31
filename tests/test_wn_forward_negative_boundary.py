"""Negative-only five-city local journal and one-shot Kalshi GET boundary tests."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.forecasting.wn_forward_negative_ledger import (
    NegativeJournalError,
    classify_negative,
    read_negative_date,
    reserve_date,
)
from services.forecasting.wn_forward_readonly_capture import (
    CaptureError,
    capture_untrusted_six_sibling_bundle,
)
from services.forecasting.wn_p3_sibling_preflight import CITY_SERIES, event_ticker_for
from services.market_universe import public_read

TARGET = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SERIES = "KXHIGHTBOS"
SPECS = (
    ("MOCKLT", "less", None, 70),
    ("MOCKB70", "between", 70, 71),
    ("MOCKB72", "between", 72, 73),
    ("MOCKB74", "between", 74, 75),
    ("MOCKB76", "between", 76, 77),
    ("MOCKGT", "greater", 77, None),
)


def db_path(tmp_path: Path) -> Path:
    return tmp_path / "research-only.sqlite3"


def test_atomic_five_city_reservation_and_one_terminal_per_city(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    initial = read_negative_date(path, TARGET)
    assert initial["denominator_date_clusters"] == 1
    assert initial["denominator_city_attempts"] == 5
    assert initial["denominator_sibling_slots"] == 30
    assert all(r["status"] == "ATTEMPT_RESERVED" for r in initial["rows"])
    for city in sorted(CITY_SERIES):
        classify_negative(
            path, TARGET, city, reason="NO_MODEL", stamped_at=NOW + timedelta(seconds=1)
        )
    final = read_negative_date(path, TARGET)
    assert all(r["status"] == "NO_MODEL" for r in final["rows"])
    assert final["source_attested"] is False
    assert final["market_economics_issued"] is False
    assert final["model_probability_authorized"] is False
    assert final["paper_authorized"] is False
    assert final["trading_authority"] == "NONE"
    assert final["production_influence"] == "0"
    assert final["local_storage_not_external_custody"] is True


def test_read_unreserved_path_never_creates_database(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    with pytest.raises(NegativeJournalError):
        read_negative_date(path, TARGET)
    assert not path.exists()


def test_reservation_duplicate_rejected_without_existing_record_mutation(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    first = read_negative_date(path, TARGET)
    with pytest.raises(NegativeJournalError):
        reserve_date(path, TARGET, stamped_at=NOW + timedelta(seconds=1))
    assert read_negative_date(path, TARGET) == first


def test_replayed_abstention_cannot_overwrite_record(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    classify_negative(path, TARGET, SERIES, reason="NO_MODEL", stamped_at=NOW)
    old = read_negative_date(path, TARGET)
    with pytest.raises(NegativeJournalError):
        classify_negative(
            path, TARGET, SERIES, reason="COLLECTOR_FAILURE", stamped_at=NOW
        )
    assert read_negative_date(path, TARGET) == old


def test_unreserved_attempt_does_not_create_terminal_or_new_date(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    with pytest.raises(NegativeJournalError):
        classify_negative(path, TARGET, SERIES, reason="NO_MODEL", stamped_at=NOW)
    reserve_date(path, TARGET, stamped_at=NOW)
    with pytest.raises(NegativeJournalError):
        classify_negative(
            path, TARGET + timedelta(days=1), SERIES, reason="NO_MODEL", stamped_at=NOW
        )
    assert read_negative_date(path, TARGET)["denominator_city_attempts"] == 5


@pytest.mark.parametrize(
    "reason",
    ("FINALIZED", "PAPER_DIAGNOSTIC_ONLY", "SOURCE_ATTESTED", "TRADE", "", "no_model"),
)
def test_positive_unknown_state_cannot_be_inserted(tmp_path: Path, reason: str) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    with pytest.raises(NegativeJournalError):
        classify_negative(path, TARGET, SERIES, reason=reason, stamped_at=NOW)


@pytest.mark.parametrize(
    "stamp",
    (
        NOW.replace(tzinfo=None),
        datetime(1, 1, 1, 0, 0, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, tzinfo=UTC),
        NOW + timedelta(minutes=5),
    ),
)
def test_bad_reservation_clock_rejected(tmp_path: Path, stamp: datetime) -> None:
    with pytest.raises(NegativeJournalError):
        reserve_date(db_path(tmp_path), TARGET, stamped_at=stamp)


def test_early_and_naive_classification_fail_without_replacing_claim(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    for stamp in (NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)):
        with pytest.raises(NegativeJournalError):
            classify_negative(path, TARGET, SERIES, reason="NO_MODEL", stamped_at=stamp)
    assert read_negative_date(path, TARGET)["rows"][0]["status"] == "ATTEMPT_RESERVED"


def test_sqlite_update_delete_triggers_refuse_mutation(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    classify_negative(path, TARGET, SERIES, reason="NO_MODEL", stamped_at=NOW)
    with sqlite3.connect(path) as connection:
        for table in ("dates", "attempts", "negatives"):
            for statement in (
                f"UPDATE {table} SET rowid = rowid",
                f"DELETE FROM {table}",
            ):
                with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                    connection.execute(statement)
    assert read_negative_date(path, TARGET)["denominator_city_attempts"] == 5


def test_corrupt_missing_city_and_policy_fail_closed(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    reserve_date(path, TARGET, stamped_at=NOW)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER no_delete_attempts")
        connection.execute(
            "DELETE FROM attempts WHERE target_date = ? AND city_series = ?",
            (TARGET.isoformat(), SERIES),
        )
    with pytest.raises(NegativeJournalError):
        read_negative_date(path, TARGET)


def _wrap(path: str, payload: dict[str, Any], stamp: datetime = NOW) -> dict[str, object]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "path": path,
        "observed_at": stamp.isoformat(),
        "classification": "SUCCESS",
        "status": 200,
        "bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "payload": payload,
    }


def _envelopes() -> dict[str, dict[str, object]]:
    event = event_ticker_for(SERIES, TARGET)
    markets = [
        {
            "ticker": f"{event}-{suffix}",
            "event_ticker": event,
            "status": "active",
            "market_type": "binary",
            "rules_primary": "SYNTHETIC_NO_EXCHANGE_OR_TWC_AUTHORITY",
            "strike_type": kind,
            "floor_strike": floor,
            "cap_strike": cap,
        }
        for suffix, kind, floor, cap in SPECS
    ]
    responses = {
        public_read.BASE + "/events/" + event + "?with_nested_markets=true": _wrap(
            public_read.BASE + "/events/" + event + "?with_nested_markets=true",
            {"event": {"event_ticker": event, "series_ticker": SERIES, "markets": markets}},
        ),
        public_read.BASE + "/series/" + SERIES: _wrap(
            public_read.BASE + "/series/" + SERIES,
            {"series": {"ticker": SERIES}},
        ),
    }
    for market in markets:
        ticker = market["ticker"]
        market_path = public_read.BASE + "/markets/" + ticker
        book_path = public_read.BASE + "/markets/orderbooks?tickers=" + ticker
        responses[market_path] = _wrap(market_path, {"market": market})
        responses[book_path] = _wrap(
            book_path,
            {"orderbooks": [{"ticker": ticker, "orderbook_fp": {}}]},
        )
    return responses


def test_bounded_one_pass_all_six_no_economics_authority() -> None:
    responses = _envelopes()
    requested: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        requested.append(path)
        return responses[path]

    result = capture_untrusted_six_sibling_bundle(
        series=SERIES, target=TARGET, evaluated_at=NOW, get=fake_get
    )
    assert len(requested) == 14
    assert len(set(requested)) == 14
    assert len(result.markets) == 6
    assert len(result.books) == 6
    assert len(result.replay.responses) == 14
    assert result.source_attested is False
    assert result.rules_and_station_authorized is False
    assert result.fee_depth_authorized is False
    assert result.model_probability_authorized is False
    assert result.paper_authorized is False
    assert result.trading_authority == "NONE"


def test_missing_or_unauthorized_event_has_one_request_only() -> None:
    requested: list[str] = []

    def unauthorized(path: str) -> dict[str, object]:
        requested.append(path)
        return {
            "path": path,
            "status": 401,
            "classification": "HTTP_OR_NETWORK_FAILURE",
        }

    with pytest.raises(CaptureError):
        capture_untrusted_six_sibling_bundle(
            series=SERIES, target=TARGET, evaluated_at=NOW, get=unauthorized
        )
    assert len(requested) == 1


def test_missing_book_abstains_without_retry_or_fabrication() -> None:
    responses = _envelopes()
    book_path = next(p for p in responses if "/markets/orderbooks?" in p)
    del responses[book_path]
    requested: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        requested.append(path)
        return responses[path]

    with pytest.raises(KeyError):
        capture_untrusted_six_sibling_bundle(
            series=SERIES, target=TARGET, evaluated_at=NOW, get=fake_get
        )
    assert len(requested) <= 14
    assert len(requested) == len(set(requested))


def test_forged_standalone_rule_and_stale_clock_fail_closed() -> None:
    for stale in (False, True):
        responses = _envelopes()
        market_path = next(
            p for p in responses if "/markets/" in p and "/orderbooks" not in p
        )
        if stale:
            responses[market_path]["observed_at"] = (
                NOW - timedelta(seconds=121)
            ).isoformat()
        else:
            responses[market_path]["body_sha256"] = "0" * 64
        with pytest.raises(CaptureError):
            capture_untrusted_six_sibling_bundle(
                series=SERIES,
                target=TARGET,
                evaluated_at=NOW,
                get=responses.__getitem__,
            )


def test_invented_market_family_stops_before_books() -> None:
    responses = _envelopes()
    event_path = next(p for p in responses if "/events/" in p)
    envelope = responses[event_path]
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    raw = payload["event"]
    assert isinstance(raw, dict)
    markets = raw["markets"]
    assert isinstance(markets, list)
    markets.pop()
    responses[event_path] = _wrap(event_path, payload)
    calls: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        calls.append(path)
        return responses[path]

    with pytest.raises(CaptureError):
        capture_untrusted_six_sibling_bundle(
            series=SERIES, target=TARGET, evaluated_at=NOW, get=fake_get
        )
    assert len(calls) == 1
