"""Offline integrated negative-cycle tests: no real source requests or paper rights."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from services.forecasting import wn_forward_offline_negative_cycle as cycle
from services.forecasting.wn_forward_negative_ledger import (
    NegativeJournalError,
    read_negative_date,
)
from services.forecasting.wn_forward_offline_negative_cycle import (
    OfflineCycleError,
    run_offline_negative_cycle,
)
from services.forecasting.wn_p3_sibling_preflight import CITY_SERIES, event_ticker_for
from services.market_universe import public_read

TARGET = date(2026, 11, 10)
RESERVED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
CLASSIFIED = RESERVED + timedelta(seconds=5)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "local-negative-cycle.sqlite3"


def _envelope(path: str, payload: dict[str, Any]) -> dict[str, object]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "path": path,
        "observed_at": CLASSIFIED.isoformat(),
        "classification": "SUCCESS",
        "status": 200,
        "bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "payload": payload,
    }


def _synthetic_city(series: str) -> dict[str, dict[str, object]]:
    event = event_ticker_for(series, TARGET)
    bins = (
        ("MOCKLT", "less", None, 70),
        ("MOCKB70", "between", 70, 71),
        ("MOCKB72", "between", 72, 73),
        ("MOCKB74", "between", 74, 75),
        ("MOCKB76", "between", 76, 77),
        ("MOCKGT", "greater", 77, None),
    )
    markets = [
        {
            "ticker": f"{event}-{suffix}",
            "event_ticker": event,
            "status": "active",
            "market_type": "binary",
            "rules_primary": "SYNTHETIC_NO_PROVIDER_SETTLEMENT_AUTHORITY",
            "strike_type": kind,
            "floor_strike": floor,
            "cap_strike": cap,
        }
        for suffix, kind, floor, cap in bins
    ]
    event_path = public_read.BASE + "/events/" + event + "?with_nested_markets=true"
    series_path = public_read.BASE + "/series/" + series
    responses = {
        event_path: _envelope(
            event_path,
            {"event": {"event_ticker": event, "series_ticker": series, "markets": markets}},
        ),
        series_path: _envelope(series_path, {"series": {"ticker": series}}),
    }
    for market in markets:
        ticker = str(market["ticker"])
        market_path = public_read.BASE + "/markets/" + ticker
        book_path = public_read.BASE + "/markets/orderbooks?tickers=" + ticker
        responses[market_path] = _envelope(market_path, {"market": market})
        responses[book_path] = _envelope(
            book_path,
            {"orderbooks": [{"ticker": ticker, "orderbook_fp": {}}]},
        )
    assert len(responses) == 14
    return responses


def test_absent_market_input_still_reserves_all_and_abstains(tmp_path: Path) -> None:
    path = _path(tmp_path)
    result = run_offline_negative_cycle(
        path, TARGET, reserved_at=RESERVED, classified_at=CLASSIFIED
    )
    assert result["status"] == "OFFLINE_NEGATIVE_ONLY_NOT_SOURCE_EVIDENCE"
    assert result["real_observation_count"] == 0
    assert result["synthetic_bundle_validation_count"] == 0
    assert result["synthetic_missing_or_invalid_bundle_count"] == 0
    assert result["live_kalshi_get_authorized"] is False
    assert result["real_source_attested"] is False
    assert result["external_immutable_custody"] is False
    assert result["independently_trusted_clock"] is False
    assert result["model_probability_authorized"] is False
    assert result["market_economics_issued"] is False
    assert result["paper_authorized"] is False
    assert result["trading_authority"] == "NONE"
    assert result["production_influence"] == "0"
    persisted = read_negative_date(path, TARGET)
    assert persisted == result["journal"]
    assert persisted["denominator_date_clusters"] == 1
    assert persisted["denominator_city_attempts"] == 5
    assert persisted["denominator_sibling_slots"] == 30
    rows = cast("list[dict[str, object]]", persisted["rows"])
    assert tuple(r["series"] for r in rows) == tuple(sorted(CITY_SERIES))
    assert all(row["status"] == "NO_MODEL" for row in rows)


def test_valid_invented_evidence_remains_no_model_and_no_economics(tmp_path: Path) -> None:
    bundles = {series: _synthetic_city(series) for series in sorted(CITY_SERIES)}
    path = _path(tmp_path)
    result = run_offline_negative_cycle(
        path,
        TARGET,
        reserved_at=RESERVED,
        classified_at=CLASSIFIED,
        synthetic_envelopes=bundles,
    )
    assert result["synthetic_bundle_validation_count"] == 5
    assert result["synthetic_missing_or_invalid_bundle_count"] == 0
    rows = cast("list[dict[str, object]]", read_negative_date(path, TARGET)["rows"])
    assert all(row["status"] == "NO_MODEL" for row in rows)
    assert result["real_source_attested"] is False
    assert result["paper_authorized"] is False


def test_missing_or_corrupt_synthetic_city_abstains_without_refetch(tmp_path: Path) -> None:
    bundles = {sorted(CITY_SERIES)[0]: _synthetic_city(sorted(CITY_SERIES)[0])}
    one = bundles[sorted(CITY_SERIES)[0]]
    book = next(k for k in one if "/markets/orderbooks?" in k)
    one[book]["body_sha256"] = "0" * 64
    path = _path(tmp_path)
    result = run_offline_negative_cycle(
        path,
        TARGET,
        reserved_at=RESERVED,
        classified_at=CLASSIFIED,
        synthetic_envelopes=bundles,
    )
    assert result["synthetic_bundle_validation_count"] == 0
    assert result["synthetic_missing_or_invalid_bundle_count"] == 5
    rows = cast("list[dict[str, object]]", cast("dict[str, object]", result["journal"])["rows"])
    assert all(row["status"] == "MARKET_MISSING" for row in rows)
    assert all(row["local_classification_utc"] == CLASSIFIED.isoformat() for row in rows)


def test_partial_valid_synthetic_family_keeps_full_denominator(tmp_path: Path) -> None:
    city = sorted(CITY_SERIES)[0]
    result = run_offline_negative_cycle(
        _path(tmp_path),
        TARGET,
        reserved_at=RESERVED,
        classified_at=CLASSIFIED,
        synthetic_envelopes={city: _synthetic_city(city)},
    )
    assert result["synthetic_bundle_validation_count"] == 1
    assert result["synthetic_missing_or_invalid_bundle_count"] == 4
    rows = cast("list[dict[str, object]]", cast("dict[str, object]", result["journal"])["rows"])
    assert len(rows) == 5
    assert sum(r["status"] == "NO_MODEL" for r in rows) == 1
    assert sum(r["status"] == "MARKET_MISSING" for r in rows) == 4


def test_no_network_transport_even_with_all_synthetic_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline runner must never use public GET transport")

    monkeypatch.setattr(public_read, "get_with_timeout", forbidden)
    bundles = {series: _synthetic_city(series) for series in sorted(CITY_SERIES)}
    result = run_offline_negative_cycle(
        _path(tmp_path),
        TARGET,
        reserved_at=RESERVED,
        classified_at=CLASSIFIED,
        synthetic_envelopes=bundles,
    )
    assert result["synthetic_bundle_validation_count"] == 5
    assert result["live_kalshi_get_authorized"] is False


@pytest.mark.parametrize("kind", ("unknown_city", "callable", "dict_subclass", "non_dict_city"))
def test_invalid_synthetic_input_refused_before_reservation(
    tmp_path: Path, kind: str
) -> None:
    path = _path(tmp_path)
    if kind == "unknown_city":
        source: Any = {"FORGED": {}}
    elif kind == "callable":
        def synthetic_callable(url: str) -> dict[str, str]:
            return {"path": url}

        source = synthetic_callable
    elif kind == "dict_subclass":
        class NetworkLikeDict(dict[str, dict[str, dict[str, object]]]):
            pass

        source = NetworkLikeDict()
    else:
        source = {sorted(CITY_SERIES)[0]: object()}
    with pytest.raises(OfflineCycleError):
        run_offline_negative_cycle(
            path,
            TARGET,
            reserved_at=RESERVED,
            classified_at=CLASSIFIED,
            synthetic_envelopes=source,
        )
    assert not path.exists()


def test_duplicate_date_cannot_replay_or_mutate_previously_saved_rows(tmp_path: Path) -> None:
    path = _path(tmp_path)
    run_offline_negative_cycle(path, TARGET, reserved_at=RESERVED, classified_at=CLASSIFIED)
    prior = read_negative_date(path, TARGET)
    with pytest.raises(NegativeJournalError):
        run_offline_negative_cycle(
            path,
            TARGET,
            reserved_at=RESERVED + timedelta(seconds=1),
            classified_at=CLASSIFIED + timedelta(seconds=1),
        )
    assert read_negative_date(path, TARGET) == prior


def test_early_classification_fails_but_preserves_all_five_reservations(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path)
    with pytest.raises(NegativeJournalError):
        run_offline_negative_cycle(
            path,
            TARGET,
            reserved_at=RESERVED,
            classified_at=RESERVED - timedelta(seconds=1),
        )
    rows = cast("list[dict[str, object]]", read_negative_date(path, TARGET)["rows"])
    assert len(rows) == 5
    assert all(row["status"] == "ATTEMPT_RESERVED" for row in rows)


def test_interrupted_classification_does_not_claim_complete_or_auto_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(tmp_path)
    original = cycle.classify_negative
    attempted: list[str] = []

    def partial(
        journal_path: Path,
        target: date,
        series: str,
        *,
        reason: str,
        stamped_at: datetime,
    ) -> None:
        attempted.append(series)
        if len(attempted) == 2:
            raise NegativeJournalError("synthetic mid-cycle crash")
        original(journal_path, target, series, reason=reason, stamped_at=stamped_at)

    monkeypatch.setattr(cycle, "classify_negative", partial)
    with pytest.raises(NegativeJournalError, match="mid-cycle crash"):
        run_offline_negative_cycle(
            path, TARGET, reserved_at=RESERVED, classified_at=CLASSIFIED
        )
    assert len(attempted) == 2
    rows = cast("list[dict[str, object]]", read_negative_date(path, TARGET)["rows"])
    assert sum(row["status"] == "NO_MODEL" for row in rows) == 1
    assert sum(row["status"] == "ATTEMPT_RESERVED" for row in rows) == 4
    with pytest.raises(NegativeJournalError):
        run_offline_negative_cycle(
            path, TARGET, reserved_at=RESERVED, classified_at=CLASSIFIED
        )
