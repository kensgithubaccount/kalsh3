"""Offline-only frozen October C1 packet gate tests; synthetic local bytes."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import wn_multicity_october_offline_gate as gate
from scripts.wn_multicity_october_offline_gate import (
    OctoberAuditError,
    _object,
    audit_local_snapshot,
    main,
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


def test_unreadable_unexpected_city_can_never_disappear_from_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    complete_synthetic_october(tmp_path)
    put(tmp_path, "city_days/chicago/20261003/packet.json", b"{}")
    visible = audit_local_snapshot(tmp_path)
    assert visible["audit_structurally_complete"] is False
    assert visible["coverage"]["unexpected_paths"]

    blocked_ino = (tmp_path / "city_days/chicago").stat().st_ino
    actual_scandir = os.scandir

    def deny_scandir(fd: int) -> Any:
        if isinstance(fd, int) and os.fstat(fd).st_ino == blocked_ino:
            raise PermissionError("synthetic unreadable unexpected directory")
        return actual_scandir(fd)

    monkeypatch.setattr(os, "scandir", deny_scandir)
    with pytest.raises(OctoberAuditError, match="filesystem"):
        audit_local_snapshot(tmp_path)
    monkeypatch.setattr(sys, "argv", ["wn-audit", "--root", str(tmp_path)])
    assert main() == 4
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "AUDIT_UNVERIFIED"
    assert receipt["paper_or_trading_authority"] == "NONE"


def test_symlink_swap_exactly_before_file_open_cannot_escape_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete_synthetic_october(tmp_path)
    target = tmp_path / "city_days/boston/20261003/weather.json"
    outside = tmp_path.parent / (tmp_path.name + "-outside-weather.json")
    outside.write_bytes(target.read_bytes())
    original_open = os.open
    swapped = False

    def replace_on_open(
        path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal swapped
        if path == "weather.json" and dir_fd is not None and not swapped:
            swapped = True
            target.rename(target.with_name("weather.original"))
            target.symlink_to(outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_on_open)
    with pytest.raises(OctoberAuditError, match="symlink"):
        audit_local_snapshot(tmp_path)
    assert swapped is True


def test_unreadable_component_emits_json_and_exit_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    complete_synthetic_october(tmp_path)
    original_read = os.read
    denied = False

    def interrupted_read(fd: int, count: int) -> bytes:
        nonlocal denied
        if not denied:
            denied = True
            raise PermissionError("synthetic component read error")
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", interrupted_read)
    monkeypatch.setattr(sys, "argv", ["wn-audit", "--root", str(tmp_path)])
    assert main() == 4
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "AUDIT_UNVERIFIED"
    assert receipt["audit_structurally_complete"] is False
    assert receipt["invalid_city_days"]
    assert receipt["paper_or_trading_authority"] == "NONE"


def test_absolute_symlink_ancestor_is_never_followed(
    tmp_path: Path,
) -> None:
    complete_synthetic_october(tmp_path)
    link = tmp_path.parent / (tmp_path.name + "-linked-root")
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OctoberAuditError, match="symlink"):
        audit_local_snapshot(link)


def test_two_actual_writers_after_inventory_cannot_get_false_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    complete_synthetic_october(tmp_path)
    staging = tmp_path.parent / (tmp_path.name + "-race-staging")
    put(staging, "failure.json", b"{}")
    put(staging, "chicago/20261003/packet.json", b"{}")
    packet_conflict = tmp_path / "city_days/boston/20261003/failure.json"
    unexpected_city = tmp_path / "city_days/chicago"
    trigger = threading.Event()
    failures: list[BaseException] = []

    def write_conflict() -> None:
        try:
            assert trigger.wait(timeout=15)
            os.replace(staging / "failure.json", packet_conflict)
        except BaseException as exc:
            failures.append(exc)

    def write_unexpected() -> None:
        try:
            assert trigger.wait(timeout=15)
            os.replace(staging / "chicago", unexpected_city)
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=write_conflict), threading.Thread(target=write_unexpected)]
    for thread in threads:
        thread.start()
    actual_open = os.open
    injected = False

    def swap_before_first_packet(
        path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal injected
        if path == "packet.json" and dir_fd is not None and not injected:
            injected = True
            trigger.set()
            for thread in threads:
                thread.join(timeout=15)
            assert not any(thread.is_alive() for thread in threads)
            assert not failures
            assert packet_conflict.is_file()
            assert (unexpected_city / "20261003/packet.json").is_file()
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_first_packet)
    monkeypatch.setattr(sys, "argv", ["wn-audit", "--root", str(tmp_path)])
    assert main() == 4
    assert injected
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "AUDIT_UNVERIFIED"
    assert receipt["paper_or_trading_authority"] == "NONE"

    monkeypatch.setattr(os, "open", actual_open)
    rerun = audit_local_snapshot(tmp_path)
    assert rerun["audit_structurally_complete"] is False
    assert rerun["coverage"]["conflicted_city_days"] == 1
    assert any("chicago" in path for path in rerun["coverage"]["unexpected_paths"])


def test_transient_add_then_remove_is_detected_by_directory_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete_synthetic_october(tmp_path)
    actual_open = os.open
    injected = False
    transient = tmp_path / "city_days/boston/20261003/transient.json"

    def inject_then_open(
        path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal injected
        if path == "packet.json" and dir_fd is not None and not injected:
            injected = True
            transient.write_bytes(b"{}")
            transient.unlink()
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", inject_then_open)
    with pytest.raises(OctoberAuditError, match="snapshot changed during audit"):
        audit_local_snapshot(tmp_path)
    assert injected


def test_same_name_component_metadata_change_after_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete_synthetic_october(tmp_path)
    original_read = gate._read
    target = tmp_path / "city_days/boston/20261003/packet.json"
    changed = False

    def change_after_read(root_fd: int, name: str) -> bytes:
        nonlocal changed
        body = original_read(root_fd, name)
        if name == "city_days/boston/20261003/packet.json" and not changed:
            changed = True
            os.utime(target, (1_500_000_000, 1_500_000_000))
        return body

    monkeypatch.setattr(gate, "_read", change_after_read)
    with pytest.raises(OctoberAuditError, match="snapshot changed during audit"):
        audit_local_snapshot(tmp_path)
    assert changed


@pytest.mark.parametrize(
    "raw",
    (
        b'{"payload":' + b"[" * 20000 + b"0" + b"]" * 20000 + b"}",
        b'{"payload":' + b'{"x":' * 20000 + b"0" + b"}" * 20000 + b"}",
    ),
)
def test_deeply_nested_json_fails_with_audit_error_not_recursion(
    raw: bytes,
) -> None:
    with pytest.raises(OctoberAuditError, match="JSON nesting exceeds audit bound"):
        _object(raw)


@pytest.mark.parametrize(
    "name",
    ("packet.json", "forecast.json", "SHA256SUMS.json", "failure.json"),
)
def test_deep_json_cli_emits_single_structured_failure_and_exit_four(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    complete_synthetic_october(tmp_path)
    day = "20261004" if name == "failure.json" else "20261003"
    malformed = b'{"deep":' + b"[" * 20000 + b"0" + b"]" * 20000 + b"}"
    destination = tmp_path / "city_days" / "boston" / day / name
    destination.write_bytes(malformed)
    monkeypatch.setattr(sys, "argv", ["wn-audit", "--root", str(tmp_path)])
    assert main() == 4
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert receipt["status"] == "AUDIT_UNVERIFIED"
    assert receipt["paper_or_trading_authority"] == "NONE"
    assert "RecursionError" not in output.err
    assert "Traceback" not in output.err
    assert "STRUCTURAL_PASS_NOT_ATTESTED" not in output.out


def test_directory_recursion_failure_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def poisoned_inventory(_: int) -> tuple[list[str], tuple[tuple[str, tuple[int, ...]], ...]]:
        raise RecursionError("synthetic excessive directory nesting")

    monkeypatch.setattr(gate, "_inventory", poisoned_inventory)
    monkeypatch.setattr(sys, "argv", ["wn-audit", "--root", str(tmp_path)])
    assert main() == 4
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert receipt["status"] == "AUDIT_UNVERIFIED"
    assert receipt["reason"] == "OctoberAuditError"
    assert receipt["paper_or_trading_authority"] == "NONE"
    assert output.err == ""


def test_json_nesting_limit_ignores_quoted_brackets_and_escaped_quotes() -> None:
    raw = encoded({"quoted": "[{]}" * 25000, "escaped_quote": '\\"' * 100})
    decoded = _object(raw)
    assert decoded["quoted"] == "[{]}" * 25000


def test_json_depth_boundary_accepts_at_limit_and_rejects_above() -> None:
    inside = b"[" * 255 + b"0" + b"]" * 255
    assert isinstance(_object(b'{"safe":' + inside + b"}"), dict)
    too_deep = b'{"unsafe":' + b"[" * 256 + b"0" + b"]" * 256 + b"}"
    with pytest.raises(OctoberAuditError, match="JSON nesting exceeds audit bound"):
        _object(too_deep)
