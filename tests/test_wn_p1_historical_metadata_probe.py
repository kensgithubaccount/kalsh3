"""Hostile offline probes for WN-P1 historical metadata only; no network."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

from scripts.audit_wn_p1_source_metadata import (
    MAX_METADATA_IMAGES,
    REQUIRED_BANDS,
    SourceMetadataError,
    acquire_metadata,
    main,
    validate,
)

TARGET = date(2026, 10, 27)
INIT = datetime(2026, 10, 27, tzinfo=UTC)


def specimen() -> dict[str, object]:
    hours = list(range(1, 32))
    return {
        "forecast_hours": hours,
        "start_times": [INIT.isoformat().replace("+00:00", "Z")] * len(hours),
        "end_times": [
            (INIT + timedelta(hours=h)).isoformat().replace("+00:00", "Z") for h in hours
        ],
        "ingestion_times": [(INIT + timedelta(hours=8)).timestamp()] * len(hours),
        "band_names": sorted(REQUIRED_BANDS),
    }


def cities(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], report["cities"])


def test_six_band_historical_metadata_never_creates_probability() -> None:
    report = validate(TARGET, specimen())
    assert len(cities(report)) == 5
    assert all(
        row["status"] == "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF"
        for row in cities(report).values()
    )
    assert report["probability_authorized"] is False
    assert report["paper_signal_authorized"] is False
    assert report["hourly_marginals_not_joint_daily_max_distribution"] is True
    assert report["trading_authority"] == "NONE"


def test_late_hour_abstains_in_exact_affected_windows() -> None:
    data = specimen()
    cast(list[object], data["ingestion_times"])[29] = (INIT + timedelta(hours=9)).timestamp()
    report = validate(TARGET, data)
    assert cities(report)["denver"]["status"] == "ABSTAIN_INCOMPLETE_OR_LATE"
    assert cities(report)["seattle"]["status"] == "ABSTAIN_INCOMPLETE_OR_LATE"
    assert cities(report)["boston"]["status"] == "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF"


def test_missing_hour_preserves_full_24h_denominator() -> None:
    data = specimen()
    for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
        del cast(list[object], data[key])[27]
    report = validate(TARGET, data)
    for city in ("boston", "miami", "denver", "los_angeles", "seattle"):
        assert cities(report)[city]["missing_hours"] == [28]
        assert cities(report)[city]["expected_hours"] == 24


def test_sha_identity_changes_when_metadata_changes() -> None:
    original = specimen()
    variant = specimen()
    cast(list[object], variant["ingestion_times"])[5] = (
        INIT + timedelta(hours=8, minutes=1)
    ).timestamp()
    assert (
        validate(TARGET, original)["metadata_sha256"]
        != validate(TARGET, variant)["metadata_sha256"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_start",
        "wrong_end",
        "duplicate_hour",
        "missing_band",
        "duplicate_band",
        "invalid_epoch",
        "nan_epoch",
        "wrong_hour_type",
        "different_length",
        "unknown_field",
        "not_utc",
        "pre_init",
        "overrange_hour",
        "invalid_band_type",
    ],
)
def test_bad_metadata_fails_closed(mutation: str) -> None:
    data = specimen()
    if mutation == "wrong_start":
        cast(list[object], data["start_times"])[0] = "2026-10-26T00:00:00Z"
    elif mutation == "wrong_end":
        cast(list[object], data["end_times"])[0] = "2026-10-29T01:00:00Z"
    elif mutation == "duplicate_hour":
        cast(list[object], data["forecast_hours"])[0] = 2
    elif mutation == "missing_band":
        cast(list[object], data["band_names"]).pop()
    elif mutation == "duplicate_band":
        cast(list[object], data["band_names"]).append(next(iter(REQUIRED_BANDS)))
    elif mutation == "invalid_epoch":
        cast(list[object], data["ingestion_times"])[0] = -1
    elif mutation == "nan_epoch":
        cast(list[object], data["ingestion_times"])[0] = float("nan")
    elif mutation == "wrong_hour_type":
        cast(list[object], data["forecast_hours"])[0] = True
    elif mutation == "different_length":
        cast(list[object], data["start_times"]).pop()
    elif mutation == "unknown_field":
        data["not_a_field"] = "unverified"
    elif mutation == "not_utc":
        cast(list[object], data["start_times"])[0] = "2026-10-27T00:00:00+00:00"
    elif mutation == "pre_init":
        cast(list[object], data["ingestion_times"])[0] = (INIT - timedelta(seconds=1)).timestamp()
    elif mutation == "overrange_hour":
        cast(list[object], data["forecast_hours"])[0] = 32
    elif mutation == "invalid_band_type":
        cast(list[object], data["band_names"])[0] = ["unhashable"]
    with pytest.raises(SourceMetadataError):
        validate(TARGET, data)


def test_invalid_target_date_is_not_admitted() -> None:
    with pytest.raises(SourceMetadataError):
        validate(date(2026, 6, 1), specimen())


class _MockEarthEngineFailure(Exception):
    """Simulate the SDK's EEException (an Exception, not ValueError)."""


def _install_fake_ee(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: dict[str, object],
    fail_at: str | None = None,
) -> list[str]:
    """No network. Require all arrays to flow through a 32-item server-side limit."""
    calls: list[str] = []
    fake = ModuleType("ee")

    def initialize(*, project: str) -> None:
        calls.append(f"initialize:{project}")
        if fail_at == "initialize":
            raise _MockEarthEngineFailure("unauthorized (test)")

    class Image:
        def bandNames(self) -> object:
            return source["band_names"]

    class Collection:
        def __init__(self) -> None:
            self.limit_value: int | None = None

        def filter(self, _filter: object) -> Collection:
            return self

        def sort(self, field: str) -> Collection:
            calls.append(f"sort:{field}")
            return self

        def limit(self, n: int) -> Collection:
            calls.append(f"limit:{n}")
            self.limit_value = n
            return self

        def aggregate_array(self, name: str) -> list[object]:
            assert self.limit_value is not None, "unbounded metadata source query"
            fields = {
                "forecast_hour": "forecast_hours",
                "start_time": "start_times",
                "end_time": "end_times",
                "ingestion_time_utc": "ingestion_times",
            }
            return cast(list[object], source[fields[name]])[: self.limit_value]

        def first(self) -> Image:
            if not source["forecast_hours"]:
                raise _MockEarthEngineFailure("empty collection")
            assert self.limit_value is not None
            return Image()

    class Dictionary:
        def __init__(self, fields: dict[str, object]) -> None:
            self.fields = fields

        def getInfo(self) -> object:
            calls.append("getInfo")
            if fail_at == "getInfo":
                raise _MockEarthEngineFailure("Earth Engine server error")
            if fail_at == "malformed":
                return ["not an EE dictionary"]
            return self.fields

    fake.__dict__.update(
        {
            "Initialize": initialize,
            "ImageCollection": lambda _asset: Collection(),
            "Filter": SimpleNamespace(
                eq=lambda *_args: None, gte=lambda *_args: None, lte=lambda *_args: None
            ),
            "Image": lambda image: image,
            "Dictionary": Dictionary,
            "EEException": _MockEarthEngineFailure,
        }
    )
    monkeypatch.setitem(sys.modules, "ee", fake)
    return calls


def test_source_request_is_bounded_and_one_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_ee(monkeypatch, source=specimen())
    result = acquire_metadata("research-project", TARGET)
    assert result == specimen()
    assert calls.count("getInfo") == 1
    assert calls.count(f"limit:{MAX_METADATA_IMAGES}") == 1
    assert calls.index("sort:forecast_hour") < calls.index(f"limit:{MAX_METADATA_IMAGES}")
    assert all(
        x["status"] == "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF"
        for x in cities(validate(TARGET, result)).values()
    )


def test_oversized_server_response_cannot_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    data = specimen()
    for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
        cast(list[object], data[key]).extend(cast(list[object], data[key])[:9])
    calls = _install_fake_ee(monkeypatch, source=data)
    bounded = acquire_metadata("research-project", TARGET)
    assert len(cast(list[object], bounded["forecast_hours"])) == MAX_METADATA_IMAGES
    assert calls.count(f"limit:{MAX_METADATA_IMAGES}") == 1
    with pytest.raises(SourceMetadataError, match="exceeds 31-hour bound"):
        validate(TARGET, bounded)


@pytest.mark.parametrize("fail_at", ["initialize", "getInfo", "malformed", "empty"])
def test_earth_engine_failures_exit_two_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fail_at: str,
) -> None:
    data = specimen()
    if fail_at == "empty":
        for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
            cast(list[object], data[key]).clear()
    calls = _install_fake_ee(
        monkeypatch,
        source=data,
        fail_at=fail_at if fail_at != "empty" else None,
    )
    status = main(["--historical-target", TARGET.isoformat(), "--project", "research-project"])
    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert "FAIL_CLOSED:" in captured.err
    assert "Traceback" not in captured.err
    if fail_at == "initialize":
        assert "getInfo" not in calls
    elif fail_at == "getInfo":
        assert calls.count("getInfo") == 1


def test_empty_raw_metadata_offline_never_returns_pass() -> None:
    data = specimen()
    for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
        cast(list[object], data[key]).clear()
    report = validate(TARGET, data)
    assert all(row["status"] == "ABSTAIN_INCOMPLETE_OR_LATE" for row in cities(report).values())
