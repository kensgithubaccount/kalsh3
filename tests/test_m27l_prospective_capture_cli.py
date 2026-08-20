from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import scripts.capture_m27l_prospective_forecast as cli
from scripts.capture_m27l_prospective_forecast import _write_create_only, capture
from services.forecasting.domain import ForecastError
from services.forecasting.weather_prospective_capture import deserialize_prospective_bundle
from tests.test_m27l_prospective_capture import _extraction

# Record 1's interval_start is reference (03:00Z) + 9h = 12:00Z; the fixed
# clock used throughout must land in [reference, interval_start).
_FIXED_NOW = datetime(2026, 9, 1, 4, 0, tzinfo=UTC)


def _fake_executable(tmp_path: Path) -> Path:
    path = tmp_path / "wgrib2"
    path.write_text("fake")
    path.chmod(0o755)
    return path


def _args(tmp_path: Path, **overrides: object) -> Namespace:
    raw_grib = tmp_path / "input.grib2"
    raw_grib.write_bytes(b"not-real-grib-bytes")
    values: dict[str, object] = {
        "raw_grib": raw_grib,
        "wgrib2_bin": str(_fake_executable(tmp_path)),
        "output": tmp_path / "out.json",
    }
    values.update(overrides)
    return Namespace(**values)


def _fake_run(extraction_text: str):
    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        return subprocess.CompletedProcess(command, 0, extraction_text, "")

    return run


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_now", lambda: _FIXED_NOW)


def test_capture_cli_produces_three_observations_and_writes_immutable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)
    payload = capture(args)
    assert len(payload["observations"]) == 3
    assert args.output.exists()
    on_disk = json.loads(args.output.read_text())
    assert len(on_disk["observations"]) == 3
    assert on_disk["observations"][0]["research_only"] is True
    assert on_disk["observations"][0]["production_influence"] == "0"
    assert on_disk["observations"][0]["collection_timestamp"] == _FIXED_NOW.isoformat()


def test_capture_cli_output_is_write_once_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)
    capture(args)
    original = args.output.read_text()
    capture(args)  # identical rerun must not fail and must not change content
    assert args.output.read_text() == original


def test_capture_cli_rejects_conflicting_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)
    capture(args)
    other = _args(tmp_path, output=args.output, raw_grib=args.raw_grib)
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction(values=("303", "307", "309.3"))))
    with pytest.raises(ForecastError, match="conflicting"):
        capture(other)


def test_capture_cli_fails_closed_on_wrong_wgrib2_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "3.7.0\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ForecastError, match=r"reviewed 3\.8\.0"):
        capture(_args(tmp_path))


def test_capture_cli_has_no_collected_at_or_version_override_flags(tmp_path: Path) -> None:
    args = _args(tmp_path)
    assert not hasattr(args, "collected_at")
    assert not hasattr(args, "extraction_policy_version")
    assert not hasattr(args, "wgrib2_version")


def test_capture_cli_round_trip_write_load_deserialize_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)
    capture(args)
    on_disk = json.loads(args.output.read_text())
    restored = deserialize_prospective_bundle(on_disk)
    assert len(restored) == 3
    assert on_disk["raw_grib_source"]["byte_length"] == len(args.raw_grib.read_bytes())
    assert on_disk["raw_grib_source"]["research_only"] is True


def test_capture_cli_records_wgrib2_executable_sha256_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)
    payload = capture(args)
    expected_sha256 = hashlib.sha256(Path(args.wgrib2_bin).read_bytes()).hexdigest()
    assert payload["raw_grib_source"]["wgrib2_executable_sha256"] == expected_sha256


def test_capture_cli_fails_closed_when_wgrib2_executable_digest_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_extraction()))
    args = _args(tmp_path)

    def fake_resolve(wgrib2_bin: object) -> tuple[str, None]:
        return (args.wgrib2_bin, None)

    monkeypatch.setattr(cli, "_resolve_wgrib2", fake_resolve)
    with pytest.raises(ForecastError, match="digest"):
        capture(args)


# ---------------------------------------------------------------------------
# RAW-BYTE / EXTRACTION TOCTOU
# ---------------------------------------------------------------------------


def test_raw_bytes_are_snapshotted_before_extraction_toctou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove wgrib2 is run against a private snapshot of the exact bytes this
    script hashed, not against the operator's original path re-read a second
    time — even when the original path is mutated immediately after the
    legitimate read returns."""
    raw_grib = tmp_path / "toctou_input.grib2"
    original_bytes = b"original-bytes-for-hash"
    raw_grib.write_bytes(original_bytes)
    invoked_grib_paths: list[Path] = []

    def fake_run(command: object, *a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        grib_path = Path(command[1])
        invoked_grib_paths.append(grib_path)
        # The snapshot must hold the ORIGINAL bytes, never the mutated ones,
        # and must not be the operator's own path.
        assert grib_path != raw_grib
        assert grib_path.read_bytes() == original_bytes
        return subprocess.CompletedProcess(command, 0, _extraction(), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    original_read_bytes = Path.read_bytes

    def read_then_mutate(self: Path) -> bytes:
        data = original_read_bytes(self)
        if self == raw_grib:
            self.write_bytes(b"attacker-mutated-bytes-after-read")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_mutate)

    args = _args(tmp_path, raw_grib=raw_grib)
    payload = capture(args)

    assert invoked_grib_paths, "wgrib2 must have been invoked against a snapshot"
    assert payload["raw_grib_source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    # The operator's original path really was mutated, proving the attack
    # window existed and the snapshot defended against it.
    assert raw_grib.read_bytes() == b"attacker-mutated-bytes-after-read"


# ---------------------------------------------------------------------------
# BLOCKER 3 — true create-only persistence
# ---------------------------------------------------------------------------


def test_write_create_only_rejects_conflicting_content_and_accepts_identical(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.json"
    _write_create_only(path, {"observations": ["A"]})
    original = path.read_text()
    _write_create_only(path, {"observations": ["A"]})  # idempotent
    assert path.read_text() == original
    with pytest.raises(ForecastError, match="conflicting"):
        _write_create_only(path, {"observations": ["B"]})
    assert path.read_text() == original


def test_write_create_only_uses_restrictive_mode(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    _write_create_only(path, {"observations": ["A"]})
    assert (path.stat().st_mode & 0o777) == 0o600


def test_concurrent_writers_cannot_both_commit_and_winner_is_not_overwritable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent.json"
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    payload_a: dict[str, Any] = {"observations": ["A"]}
    payload_b: dict[str, Any] = {"observations": ["B"]}

    def attempt(label: str, payload: dict[str, Any]) -> None:
        barrier.wait()
        try:
            _write_create_only(path, payload)
            results[label] = "committed"
        except ForecastError:
            results[label] = "rejected"

    t1 = threading.Thread(target=attempt, args=("a", payload_a))
    t2 = threading.Thread(target=attempt, args=("b", payload_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    on_disk = json.loads(path.read_text())
    assert on_disk in (payload_a, payload_b)
    winner_label = "a" if on_disk == payload_a else "b"
    loser_label = "b" if winner_label == "a" else "a"
    loser_payload = payload_b if winner_label == "a" else payload_a

    assert results[winner_label] == "committed"
    assert results[loser_label] == "rejected"

    # The winner cannot later be overwritten by the loser's content.
    with pytest.raises(ForecastError, match="conflicting"):
        _write_create_only(path, loser_payload)
    assert json.loads(path.read_text()) == on_disk


def test_write_create_only_never_opens_the_final_path_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    opened_paths: list[str] = []
    real_open = os.open

    def spying_open(target: object, *a: object, **kw: object) -> int:
        opened_paths.append(str(target))
        return real_open(target, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spying_open)
    _write_create_only(path, {"observations": ["A"]})
    assert str(path) not in opened_paths
    assert any(p.endswith(".tmp") for p in opened_paths)


def test_write_create_only_handles_injected_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    real_write = os.write
    call_count = 0

    def short_write(fd: int, data: bytes) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1 and len(data) > 1:
            return real_write(fd, data[: len(data) // 2])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_write)
    payload = {"observations": ["A", "B", "C"]}
    _write_create_only(path, payload)
    assert call_count >= 2, "the short-write loop must have made more than one os.write call"
    assert json.loads(path.read_text()) == payload


def test_write_create_only_injected_mid_write_failure_leaves_no_partial_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"

    def failing_write(fd: int, data: bytes) -> int:
        raise OSError("simulated disk failure mid-write")

    monkeypatch.setattr(os, "write", failing_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        _write_create_only(path, {"observations": ["A"]})
    assert not path.exists(), "the authoritative final path must never appear on a failed write"
    leftover_temp_files = list(tmp_path.glob(".*evidence.json*.tmp"))
    assert leftover_temp_files == [], "the temp file must be removed on every failure path"


def test_write_create_only_injected_fsync_failure_leaves_no_partial_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"

    def failing_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        _write_create_only(path, {"observations": ["A"]})
    assert not path.exists()
    assert list(tmp_path.glob(".*evidence.json*.tmp")) == []


def test_write_create_only_publishes_via_no_replace_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    real_link = os.link
    link_calls: list[tuple[str, str]] = []

    def spying_link(src: object, dst: object) -> None:
        link_calls.append((str(src), str(dst)))
        real_link(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", spying_link)
    _write_create_only(path, {"observations": ["A"]})
    assert len(link_calls) == 1
    assert link_calls[0][1] == str(path)
    assert link_calls[0][0] != str(path)


def test_write_create_only_rejects_symlink_at_final_path(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("not json content that matters")
    path.symlink_to(elsewhere)
    with pytest.raises(ForecastError, match="not a regular file"):
        _write_create_only(path, {"observations": ["A"]})
