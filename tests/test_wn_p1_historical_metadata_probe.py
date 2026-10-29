"""Hostile offline probes for WN-P1 historical metadata only; no network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest

from scripts.audit_wn_p1_source_metadata import (
    REQUIRED_BANDS,
    SourceMetadataError,
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
