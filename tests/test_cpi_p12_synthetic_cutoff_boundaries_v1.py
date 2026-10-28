"""Offline P12 transport-cutoff regressions using only invented data and injected clocks.

No Reuters or Kalshi network access, source selection, release timing, file
persistence, or scientific authority. These are transport-contract tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services.forecasting import cpi_p12_raw_market_snapshots as kalshi
from services.forecasting import cpi_p12_reuters_raw_acquisition as reuters
from services.market_universe import public_read

OBS = datetime(2026, 1, 2, 11, 1, tzinfo=UTC)
START = OBS - timedelta(minutes=1)
CUTOFF = OBS + timedelta(minutes=4)
EVENT = "KXCPI-SYNTHETICONLY"
MARKET = EVENT + "-T0.2"
URL = "https://www.reuters.com/world/invented-p12-fixture-only"
RAW = b"Entirely invented publisher text; no forecast or market data."


def _clock(*values: datetime) -> Callable[[], datetime]:
    iterator = iter(values)
    return lambda: next(iterator)


def _market_envelope(path: str, *, observed: datetime = OBS) -> dict[str, object]:
    body = json.dumps(
        {
            "market": {
                "ticker": MARKET,
                "event_ticker": EVENT,
                "close_time": "2026-01-02T10:59:00Z",
                "yes_ask_dollars": "0.45",
            }
        },
        sort_keys=True,
    ).encode("utf-8")
    return public_read._evidence_from_body(path, body, 200, observed)


def _raw_getter(_url: str) -> tuple[int, bytes, dict[str, str]]:
    return 200, RAW, {"content-type": "text/plain"}


def _capture(
    lane: str, times: tuple[datetime, ...], *, deadline: datetime = CUTOFF
) -> dict[str, Any]:
    clock = _clock(*times)
    if lane == "kalshi":
        return kalshi.capture_kalshi_raw_market_snapshots(
            event_ticker=EVENT,
            market_tickers=(MARKET,),
            window_start=START,
            deadline=deadline,
            getter=_market_envelope,
            clock=clock,
        )
    assert lane == "reuters"
    return reuters.capture_reuters_raw_sources(
        source_urls=(URL,),
        window_start=START,
        deadline=deadline,
        getter=_raw_getter,
        clock=clock,
    )


VALID_TIMES = (
    START,
    START + timedelta(seconds=30),
    OBS + timedelta(seconds=10),
    OBS + timedelta(seconds=20),
)


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_injected_success_is_raw_only_and_never_scientific(lane: str) -> None:
    record = _capture(lane, VALID_TIMES)
    assert record["scientific_evidence_authorized"] is False
    assert record["phase1_economic_test_authorized"] is False
    assert record["promotion_authority"] == "NONE"
    assert record["production_influence"] == "0"
    assert record["deadline_utc"] == "2026-01-02T11:05:00Z"
    if lane == "kalshi":
        assert record["cohort_authority"] == "CALLER_PROVIDED_NOT_FROZEN"
        assert record["acquisition_mode"] == "INJECTED_TEST_GETTER"
    else:
        assert record["source_publication_verified"] is False
        assert record["reuters_forecast_authorized"] is False
        assert record["transport_mode"] == "INJECTED_TEST_GETTER"


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_capture_opened_exactly_at_deadline_does_not_call_getter(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(lane, (CUTOFF,))


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_request_beginning_at_deadline_fails_before_get(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(lane, (START, CUTOFF))


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_response_completed_exactly_at_deadline_is_rejected(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(lane, (START, START + timedelta(seconds=30), CUTOFF))


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_capture_completion_exactly_at_deadline_is_rejected(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(
            lane,
            (
                START,
                START + timedelta(seconds=30),
                OBS + timedelta(seconds=10),
                CUTOFF,
            ),
        )


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_invalid_empty_window_fails_closed(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(lane, (START,), deadline=START)


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_naive_injected_clock_fails_closed(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(lane, (datetime(2026, 1, 2, 11, 0),))


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_backward_response_clock_cannot_produce_raw_success(lane: str) -> None:
    with pytest.raises(
        kalshi.P12SnapshotCaptureError
        if lane == "kalshi"
        else reuters.P12ReutersRawAcquisitionError
    ):
        _capture(
            lane,
            (
                START,
                OBS + timedelta(seconds=20),
                OBS - timedelta(seconds=1),
            ),
        )


def test_kalshi_observed_time_after_response_completion_is_rejected() -> None:
    def late_observed(path: str) -> dict[str, object]:
        return _market_envelope(path, observed=OBS + timedelta(seconds=30))

    with pytest.raises(kalshi.P12SnapshotCaptureError, match=r"timestamp|window"):
        kalshi.capture_kalshi_raw_market_snapshots(
            event_ticker=EVENT,
            market_tickers=(MARKET,),
            window_start=START,
            deadline=CUTOFF,
            getter=late_observed,
            clock=_clock(
                START,
                START + timedelta(seconds=30),
                OBS + timedelta(seconds=10),
            ),
        )


def test_reuters_completion_clock_regression_is_rejected() -> None:
    with pytest.raises(
        reuters.P12ReutersRawAcquisitionError, match="capture finished"
    ):
        _capture(
            "reuters",
            (
                START,
                START + timedelta(seconds=30),
                OBS + timedelta(seconds=10),
                OBS - timedelta(seconds=1),
            ),
        )


@pytest.mark.parametrize("lane", ["kalshi", "reuters"])
def test_caller_can_extend_transport_deadline_but_not_authority(lane: str) -> None:
    record = _capture(lane, VALID_TIMES, deadline=CUTOFF + timedelta(hours=1))
    assert record["deadline_utc"] == "2026-01-02T12:05:00Z"
    assert record["scientific_evidence_authorized"] is False
    assert record["phase1_economic_test_authorized"] is False
    assert record["promotion_authority"] == "NONE"


def test_unverified_early_market_close_is_not_transport_cutoff_authority() -> None:
    record = _capture("kalshi", VALID_TIMES)
    # The invented market's purported close time predates the fake response.
    # Raw transport can still succeed because close/rule authority is NOT
    # decided by this stage; scientific adjudication must remain elsewhere.
    assert record["market_count"] == 1
    assert record["cohort_authority"] == "CALLER_PROVIDED_NOT_FROZEN"
    assert record["scientific_evidence_authorized"] is False


def test_invented_reuters_late_publication_claim_is_not_verified() -> None:
    def invented_page(_url: str) -> tuple[int, bytes, dict[str, str]]:
        return 200, b"Invented datePublished: 2026-01-02T11:06:00Z", {}

    record = reuters.capture_reuters_raw_sources(
        source_urls=(URL,),
        window_start=START,
        deadline=CUTOFF,
        getter=invented_page,
        clock=_clock(*VALID_TIMES),
    )
    observation = record["observations"][0]
    assert observation["publication_time_verified"] is False
    assert observation["reuters_attribution_verified"] is False
    assert observation["forecast_value_extracted"] is False
    assert record["scientific_evidence_authorized"] is False
    assert record["source_publication_verified"] is False
