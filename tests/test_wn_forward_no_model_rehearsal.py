"""Integrated all-abstention rehearsal: INVENTED data only, no source or paper authority."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from scripts import wn_forward_no_model_rehearsal as rehearsal
from scripts.wn_forward_no_model_rehearsal import (
    NoModelRehearsal,
    main,
    rehearse_all_no_model,
)
from services.forecasting.wn_p4_mock_attempt_protocol import AttemptState

TARGET = date(2026, 11, 10)
RESERVED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
CLASSIFIED = RESERVED + timedelta(seconds=5)


def run(target: date = TARGET) -> NoModelRehearsal:
    return rehearse_all_no_model(
        target,
        simulated_reserved_at=RESERVED,
        simulated_classified_at=CLASSIFIED,
    )


def test_integrates_reviewed_source_no_go_p3_roster_and_p4_all_abstention() -> None:
    result = run()
    receipt = result.receipt()
    assert receipt["status"] == "MOCK_ALL_ABSTAIN_NO_MODEL_NOT_EVIDENCE"
    assert receipt["source_decision"] == "NO_EXECUTABLE_MEMBER_SOURCE_DEMONSTRATED"
    assert receipt["geometry_global_chunks"] == 384
    assert receipt["geometry_global_decoded_bytes_not_network"] == 238_945_075_200
    assert receipt["inherited_reader_network_cap_bytes"] == 2 * 1024**3
    assert receipt["observed_network_bytes"] is None
    assert receipt["observed_source_generation"] is None
    assert receipt["denominator_date_clusters"] == 1
    assert receipt["denominator_city_attempts"] == 5
    assert receipt["denominator_sibling_slots"] == 30
    assert receipt["real_observation_count"] == 0
    assert receipt["gcs_ee_kalshi_requests_made"] == 0
    rows = receipt["city_attempts"]
    assert isinstance(rows, list) and len(rows) == 5
    assert all(row["state"] == "NO_MODEL" for row in rows)
    assert all(row["invented_sibling_slots"] == 6 for row in rows)
    assert all(row["simulated_reservation_utc"] == RESERVED.isoformat() for row in rows)
    assert all(row["simulated_classification_utc"] == CLASSIFIED.isoformat() for row in rows)
    assert {row["series_ticker"] for row in rows} == {
        "KXHIGHTBOS",
        "KXHIGHMIA",
        "KXHIGHDEN",
        "KXHIGHLAX",
        "KXHIGHTSEA",
    }


def test_all_authority_negative_even_for_successful_rehearsal() -> None:
    result = run()
    assert result.research_only
    assert not result.source_access_demonstrated
    assert not result.market_source_attested
    assert not result.probability_authorized
    assert not result.paper_authorized
    assert result.trading_authority == "NONE"
    receipt = result.receipt()
    for key in (
        "real_source_attested",
        "real_market_economics_issued",
        "source_probability_issued",
        "forecast_skill_scored",
        "paper_authorized",
    ):
        assert receipt[key] is False
    assert receipt["trading_authority"] == "NONE"
    assert receipt["production_influence"] == "0"
    assert receipt["simulated_only"] is True


@pytest.mark.parametrize(
    "attribute",
    (
        "source_access_demonstrated",
        "market_source_attested",
        "probability_authorized",
        "paper_authorized",
        "trading_authority",
    ),
)
def test_positive_authority_cannot_be_overridden(attribute: str) -> None:
    result = run()
    with pytest.raises(AttributeError):
        object.__setattr__(result, attribute, True)


def test_injected_positive_or_dropped_city_invalidates_receipt() -> None:
    result = run()
    original = result.mock_ledger.clusters[0]
    modified = replace(original.attempts[0], state=AttemptState.PAPER_DIAGNOSTIC_ONLY)
    forged = replace(original, attempts=(modified, *original.attempts[1:]))
    changed = replace(result.mock_ledger, clusters=(forged,))
    with pytest.raises(ValueError):
        replace(result, mock_ledger=changed).receipt()

    incomplete = replace(original, attempts=original.attempts[:-1])
    missing = replace(result.mock_ledger, clusters=(incomplete,))
    with pytest.raises(ValueError):
        replace(result, mock_ledger=missing).receipt()


def test_source_geometry_mismatch_fails_without_issuing_mock_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rehearsal, "NO_GO", "FRAGMENT_CHANGED")
    with pytest.raises(ValueError):
        run()


@pytest.mark.parametrize(
    "target",
    (date(2026, 10, 31), date(2100, 1, 1)),
)
def test_dates_outside_reviewed_geometry_scope_fail_closed(target: date) -> None:
    with pytest.raises(ValueError):
        run(target)


def test_classification_before_reservation_rejected() -> None:
    with pytest.raises(ValueError):
        rehearse_all_no_model(
            TARGET,
            simulated_reserved_at=RESERVED,
            simulated_classified_at=RESERVED - timedelta(seconds=1),
        )


def test_naive_or_wrong_type_clock_rejected() -> None:
    with pytest.raises(ValueError):
        rehearse_all_no_model(
            TARGET,
            simulated_reserved_at=RESERVED.replace(tzinfo=None),
            simulated_classified_at=CLASSIFIED,
        )
    with pytest.raises(ValueError):
        rehearse_all_no_model(
            TARGET,
            simulated_reserved_at=RESERVED,
            simulated_classified_at=CLASSIFIED.replace(tzinfo=None),
        )


def test_source_and_exchange_transport_never_invoked(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.market_universe import public_read

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mock rehearsal must not request external sources")

    monkeypatch.setattr(public_read, "get", forbidden)
    monkeypatch.setattr(public_read, "get_with_timeout", forbidden)
    assert run().receipt()["gcs_ee_kalshi_requests_made"] == 0


def test_cli_prints_one_parseable_json_and_returns_zero_for_mock_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "--target-local-date",
        "2026-11-10",
        "--simulated-reserved-at",
        "2026-11-10T09:00:00Z",
        "--simulated-classified-at",
        "2026-11-10T09:00:05Z",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    receipt = json.loads(captured.out)
    assert receipt["status"] == "MOCK_ALL_ABSTAIN_NO_MODEL_NOT_EVIDENCE"
    assert receipt["simulated_only"] is True
    assert receipt["paper_authorized"] is False
    assert receipt["trading_authority"] == "NONE"
    assert receipt["denominator_city_attempts"] == 5
    assert receipt["denominator_sibling_slots"] == 30


@pytest.mark.parametrize(
    ("target", "reserved", "classified"),
    [
        ("not-a-date", "2026-11-10T09:00:00Z", "2026-11-10T09:00:05Z"),
        ("2026-10-30", "2026-10-30T09:00:00Z", "2026-10-30T09:00:05Z"),
        ("2026-11-10", "not-a-clock", "2026-11-10T09:00:05Z"),
        ("2026-11-10", "2026-11-10T09:00:00", "2026-11-10T09:00:05Z"),
        ("2026-11-10", "2026-11-10T09:00:00Z", "2026-11-10T09:00:05"),
        ("2026-11-10", "2026-11-10T09:00:10Z", "2026-11-10T09:00:05Z"),
    ],
)
def test_invalid_cli_produces_only_negative_failure(
    target: str,
    reserved: str,
    classified: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "--target-local-date",
        target,
        "--simulated-reserved-at",
        reserved,
        "--simulated-classified-at",
        classified,
    ]
    assert main(argv) == 4
    output = capsys.readouterr()
    assert output.err == ""
    receipt = json.loads(output.out)
    assert receipt["status"] == "MOCK_REHEARSAL_UNVERIFIED"
    assert receipt["simulated_only"] is True
    assert receipt["paper_authorized"] is False
    assert receipt["trading_authority"] == "NONE"
    assert "Traceback" not in output.out
