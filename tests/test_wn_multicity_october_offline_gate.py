"""Offline-only frozen October C1 packet gate tests; synthetic local bytes."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.wn_multicity_october_offline_gate import (
    OctoberAuditError,
    _object,
    audit_local_snapshot,
)
from services.forecasting.wn_multicity_evaluation_spec import (
    D1_SHA256SUMS_SHA256,
    EXPECTED_CITY_IDS,
    FROZEN_FORECAST_RULE_IDENTITY,
    FROZEN_PROTOCOL_SHA256,
    PRE_FIRST_EVENT_AMENDMENT_SHA256,
)


def encoded(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def put(root: Path, path: str, data: bytes) -> None:
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def write_capture(root: Path, city: str, target: date) -> None:
    iso = target.isoformat()
    stem = f"city_days/{city}/{target:%Y%m%d}"
    common = {
        "city_id": city,
        "target_date_utc": iso,
        "same_date_cluster": iso,
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "development_only": True,
        "protocol_sha256": FROZEN_PROTOCOL_SHA256,
        "pre_first_event_amendment_sha256": PRE_FIRST_EVENT_AMENDMENT_SHA256,
        "d1_sha256sums_sha256": D1_SHA256SUMS_SHA256,
    }
    weather = encoded({"record_type": "WN-MULTICITY-C1-CLOUD-WEATHER-v1", **common})
    market = encoded({"record_type": "WN-MULTICITY-C1-CLOUD-MARKET-v1", **common})
    forecast = encoded(
        {
            "record_type": "WN-MULTICITY-C1-CLOUD-FORECAST-RECEIPT-v1",
            **common,
            "forecast_status": "PRE_OUTCOME_FROZEN",
            "outcome_not_consulted": True,
            "no_probability": True,
            "no_trade": True,
            "no_bias_correction": True,
            "no_city_specific_tuning": True,
            "frozen_rule_identity": FROZEN_FORECAST_RULE_IDENTITY,
            "forecast_p50_proxy_fahrenheit": "67.125",
            "market_sha256": hashlib.sha256(market).hexdigest(),
            "weather_sha256": hashlib.sha256(weather).hexdigest(),
            "decision_at_utc": iso + "T09:00:08Z",
        }
    )
    parts = {"forecast.json": forecast, "market.json": market, "weather.json": weather}
    packet = encoded(
        {
            "record_type": "WN-MULTICITY-C1-CLOUD-PACKET-v1",
            **common,
            "no_trade": True,
            "no_alert": True,
            "no_probability_or_edge_calculation": True,
            "weather_development_observation": "VALID_PRE_OUTCOME_SEALED",
            "forecast_p50_proxy_fahrenheit": "67.125",
            "decision_at_utc": iso + "T09:00:08Z",
            "component_sha256": {
                name: hashlib.sha256(blob).hexdigest() for name, blob in parts.items()
            },
        }
    )
    parts["packet.json"] = packet
    for name, blob in parts.items():
        put(root, stem + "/" + name, blob)
    put(
        root,
        stem + "/SHA256SUMS.json",
        encoded(
            {f"{stem}/{name}": hashlib.sha256(blob).hexdigest() for name, blob in parts.items()}
        ),
    )


def write_failure(root: Path, city: str, target: date) -> None:
    put(
        root,
        f"city_days/{city}/{target:%Y%m%d}/failure.json",
        encoded(
            {
                "record_type": "WN-MULTICITY-C1-CLOUD-FAILURE-v1",
                "city_id": city,
                "target_date_utc": target.isoformat(),
                "same_date_cluster": target.isoformat(),
                "observational_unit": "CITY_DAY",
                "city_days_independent": False,
                "development_only": True,
                "protocol_sha256": FROZEN_PROTOCOL_SHA256,
                "pre_first_event_amendment_sha256": PRE_FIRST_EVENT_AMENDMENT_SHA256,
                "d1_sha256sums_sha256": D1_SHA256SUMS_SHA256,
                "status": "DATA_NOT_READY",
                "error_code": "DATA_NOT_READY_IMAGE_COUNT",
                "stage": "weather",
                "no_retry": True,
                "no_backfill": True,
                "no_trade": True,
            }
        ),
    )


def complete_synthetic_october(root: Path) -> None:
    for day in range(3, 15):
        target = date(2026, 10, day)
        for city in EXPECTED_CITY_IDS:
            if day == 4:
                write_failure(root, city, target)
            else:
                write_capture(root, city, target)


def test_fifty_five_sealed_and_five_failures_are_structurally_accounted(
    tmp_path: Path,
) -> None:
    complete_synthetic_october(tmp_path)
    report = audit_local_snapshot(tmp_path)
    assert report["audit_structurally_complete"] is True
    assert report["sealed_packets_validated"] == 55
    assert report["coverage"]["expected_city_days"] == 60
    assert report["coverage"]["captured_city_days"] == 55
    assert report["coverage"]["failed_city_days"] == 5
    assert report["coverage"]["complete_date_clusters"] == 11
    assert report["coverage"]["operationally_clean"] is False
    assert report["invalid_city_days"] == []
    assert report["outcome_data_read"] is False
    assert report["probability_or_edge_calculated"] is False
    assert report["source_provenance_attested"] is False
    assert report["paper_or_trading_authority"] == "NONE"


def test_corrupt_component_is_not_counted_as_valid_seal(tmp_path: Path) -> None:
    write_capture(tmp_path, "boston", date(2026, 10, 3))
    original = tmp_path / "city_days/boston/20261003/forecast.json"
    original.write_bytes(original.read_bytes() + b" ")
    report = audit_local_snapshot(tmp_path)
    assert report["sealed_packets_validated"] == 0
    assert "boston/20261003:OctoberAuditError" in report["invalid_city_days"]
    assert report["audit_structurally_complete"] is False


def test_preserved_failure_rejects_forged_boolean(tmp_path: Path) -> None:
    write_failure(tmp_path, "boston", date(2026, 10, 4))
    path = tmp_path / "city_days/boston/20261004/failure.json"
    data = json.loads(path.read_bytes())
    data["no_trade"] = 1
    path.write_bytes(encoded(data))
    report = audit_local_snapshot(tmp_path)
    assert "boston/20261004:OctoberAuditError" in report["invalid_city_days"]


@pytest.mark.parametrize(
    "raw",
    (
        b'{"x":1,"x":2}',
        b'{"x":{"y":1,"y":2}}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":NaN}',
        b'{"x":1e999}',
    ),
)
def test_strict_json_fails_closed_on_ambiguous_bytes(raw: bytes) -> None:
    with pytest.raises(OctoberAuditError):
        _object(raw)


def test_unexpected_city_day_remains_in_denominator_report(tmp_path: Path) -> None:
    write_capture(tmp_path, "boston", date(2026, 10, 3))
    put(tmp_path, "city_days/chicago/20261003/packet.json", b"{}")
    report = audit_local_snapshot(tmp_path)
    assert report["audit_structurally_complete"] is False
    assert report["coverage"]["unexpected_paths"]


def test_symlink_is_rejected_before_reading(tmp_path: Path) -> None:
    (tmp_path / "city_days").symlink_to(tmp_path)
    with pytest.raises(OctoberAuditError, match="symlink"):
        audit_local_snapshot(tmp_path)


def test_all_failure_export_is_not_structurally_accepted(tmp_path: Path) -> None:
    for day in range(3, 15):
        for city in EXPECTED_CITY_IDS:
            write_failure(tmp_path, city, date(2026, 10, day))
    report = audit_local_snapshot(tmp_path)
    assert report["sealed_packets_validated"] == 0
    assert report["audit_structurally_complete"] is False
    assert report["coverage"]["failed_city_days"] == 60


def test_october_four_capture_is_not_a_valid_frozen_profile(tmp_path: Path) -> None:
    complete_synthetic_october(tmp_path)
    path = tmp_path / "city_days/boston/20261004/failure.json"
    path.unlink()
    write_capture(tmp_path, "boston", date(2026, 10, 4))
    report = audit_local_snapshot(tmp_path)
    assert report["audit_structurally_complete"] is False
    assert report["coverage"]["captured_city_days"] == 56
    assert report["coverage"]["failed_city_days"] == 4


def test_false_zero_frozen_identity_fails_closed(tmp_path: Path) -> None:
    write_failure(tmp_path, "boston", date(2026, 10, 4))
    path = tmp_path / "city_days/boston/20261004/failure.json"
    data = json.loads(path.read_bytes())
    data["city_days_independent"] = 0
    path.write_bytes(encoded(data))
    report = audit_local_snapshot(tmp_path)
    assert "boston/20261004:OctoberAuditError" in report["invalid_city_days"]
