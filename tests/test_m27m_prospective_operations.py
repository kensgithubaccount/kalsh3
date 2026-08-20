from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import services.forecasting.weather_prospective_operations as ops
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.forecasting.weather_prospective import PROSPECTIVE_END, PROSPECTIVE_START
from services.forecasting.weather_prospective_capture import (
    capture_prospective_forecast_evidence,
    serialize_prospective_bundle,
)
from services.forecasting.weather_prospective_operations import (
    CycleStatus,
    archive_layout,
    canonical_cycle_key,
    classify_cycle,
    derive_receipt,
    expected_reference_cycles,
    parse_and_validate_bundle,
    register_prospective_capture,
    verify_archive,
)
from tests.test_m27l_prospective_capture import _RAW_GRIB_SOURCE, _extraction

# ---------------------------------------------------------------------------
# Shared bundle-construction fixture (reuses the reviewed M27L extraction
# fixture text; builds real bytes through the frozen capture pipeline rather
# than hand-rolling JSON, so every test bundle is a genuine validated M27L
# artifact).
# ---------------------------------------------------------------------------


def _collected_at_for(reference: str) -> datetime:
    ref_dt = datetime.strptime(reference, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    return ref_dt + timedelta(hours=1)


def _bundle_bytes(
    *,
    reference: str = "20260901030000",
    values: tuple[str, str, str] = ("302", "307", "309.3"),
    raw_grib_sha256: str = "raw-sha-1",
    extraction_sha256: str = "extraction-sha-1",
    collected_at: datetime | None = None,
    raw_grib_source: dict[str, Any] | None = None,
) -> bytes:
    text = _extraction(reference=reference, values=values)
    evidence = parse_wgrib2_max_t_evidence(
        text, raw_grib_sha256=raw_grib_sha256, extraction_sha256=extraction_sha256
    )
    collected = collected_at if collected_at is not None else _collected_at_for(reference)
    observations = capture_prospective_forecast_evidence(evidence, collected_at=collected)
    bundle = serialize_prospective_bundle(
        observations, raw_grib_source=dict(raw_grib_source or _RAW_GRIB_SOURCE)
    )
    return (json.dumps(bundle, sort_keys=True, indent=2, default=str) + "\n").encode()


_SEPT1_CYCLE_KEY = "20260901T030000Z"
_SEPT1_REFERENCE = datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_and_validate_bundle
# ---------------------------------------------------------------------------


def test_parse_and_validate_bundle_accepts_valid_bytes() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    assert len(observations) == 3
    assert parsed["protocol_version"]
    assert len(bundle_sha256) == 64


def test_parse_and_validate_bundle_requires_bytes_input() -> None:
    with pytest.raises(ForecastError, match="raw bytes"):
        parse_and_validate_bundle("not-bytes")  # type: ignore[arg-type]


def test_parse_and_validate_bundle_rejects_non_utf8() -> None:
    with pytest.raises(ForecastError, match="UTF-8"):
        parse_and_validate_bundle(b"\xff\xfe\x00\x01")


def test_parse_and_validate_bundle_rejects_invalid_json() -> None:
    with pytest.raises(ForecastError, match="JSON"):
        parse_and_validate_bundle(b"{not valid json")


def test_parse_and_validate_bundle_rejects_non_object_json() -> None:
    with pytest.raises(ForecastError, match="object"):
        parse_and_validate_bundle(b"[1, 2, 3]")


def test_parse_and_validate_bundle_rejects_tampered_protocol_identity() -> None:
    _observations, parsed, _sha = parse_and_validate_bundle(_bundle_bytes())
    parsed["protocol_identity"] = "tampered"
    raw = json.dumps(parsed).encode()
    with pytest.raises(ForecastError, match="protocol identity"):
        parse_and_validate_bundle(raw)


# ---------------------------------------------------------------------------
# canonical_cycle_key
# ---------------------------------------------------------------------------


def test_canonical_cycle_key_is_compact_and_filename_safe() -> None:
    key = canonical_cycle_key(_SEPT1_REFERENCE)
    assert key == _SEPT1_CYCLE_KEY
    assert ":" not in key
    assert "/" not in key


def test_canonical_cycle_key_rejects_naive_datetime() -> None:
    with pytest.raises(ForecastError, match="UTC"):
        canonical_cycle_key(datetime(2026, 9, 1, 3, 0, 0))


def test_canonical_cycle_key_rejects_non_utc_offset() -> None:
    from zoneinfo import ZoneInfo

    with pytest.raises(ForecastError, match="UTC"):
        canonical_cycle_key(datetime(2026, 9, 1, 3, 0, 0, tzinfo=ZoneInfo("America/Chicago")))


def test_canonical_cycle_key_rejects_non_03z_reference() -> None:
    with pytest.raises(ForecastError, match="03Z"):
        canonical_cycle_key(datetime(2026, 9, 1, 4, 0, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# derive_receipt
# ---------------------------------------------------------------------------


def test_derive_receipt_is_deterministic() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    first = derive_receipt(
        observations, bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
    )
    second = derive_receipt(
        observations, bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
    )
    assert first == second
    assert first["receipt_id"]


def test_derive_receipt_id_changes_with_different_bundle_content() -> None:
    obs_a, parsed_a, sha_a = parse_and_validate_bundle(
        _bundle_bytes(values=("302", "307", "309.3"))
    )
    obs_b, parsed_b, sha_b = parse_and_validate_bundle(
        _bundle_bytes(values=("303", "307", "309.3"))
    )
    receipt_a = derive_receipt(
        obs_a, bundle_sha256=sha_a, raw_grib_source=parsed_a["raw_grib_source"]
    )
    receipt_b = derive_receipt(
        obs_b, bundle_sha256=sha_b, raw_grib_source=parsed_b["raw_grib_source"]
    )
    assert receipt_a["receipt_id"] != receipt_b["receipt_id"]


def test_derive_receipt_is_research_only_zero_influence() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    receipt = derive_receipt(
        observations, bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
    )
    assert receipt["research_only"] is True
    assert receipt["production_influence"] == "0"


def test_derive_receipt_target_dates_match_observations() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    receipt = derive_receipt(
        observations, bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
    )
    assert receipt["target_dates"] == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_derive_receipt_rejects_missing_executable_provenance() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    source = dict(parsed["raw_grib_source"])
    del source["wgrib2_executable_sha256"]
    with pytest.raises(ForecastError, match="executable"):
        derive_receipt(observations, bundle_sha256=bundle_sha256, raw_grib_source=source)


def test_derive_receipt_rejects_wrong_observation_count() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    with pytest.raises(ForecastError, match="exactly three"):
        derive_receipt(
            observations[:2], bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
        )


def test_derive_receipt_rejects_duplicate_midpoint() -> None:
    observations, parsed, bundle_sha256 = parse_and_validate_bundle(_bundle_bytes())
    duplicated = (observations[0], observations[0], observations[2])
    with pytest.raises(ForecastError, match="one observation per frozen midpoint"):
        derive_receipt(
            duplicated, bundle_sha256=bundle_sha256, raw_grib_source=parsed["raw_grib_source"]
        )


# ---------------------------------------------------------------------------
# register_prospective_capture -- core archival behavior
# ---------------------------------------------------------------------------


def test_register_writes_bundle_receipt_and_cycle_files(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    result = register_prospective_capture(raw, root)
    layout = archive_layout(root)
    assert (layout.bundles_dir / f"{result.bundle_sha256}.json").exists()
    assert (layout.receipts_dir / f"{result.receipt_id}.json").exists()
    assert (layout.cycles_dir / f"{result.cycle_key}.json").exists()
    assert result.cycle_key == _SEPT1_CYCLE_KEY


def test_register_bundle_content_is_byte_identical_to_input(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    result = register_prospective_capture(raw, root)
    layout = archive_layout(root)
    on_disk = (layout.bundles_dir / f"{result.bundle_sha256}.json").read_bytes()
    assert on_disk == raw


def test_register_filenames_match_their_own_content_identity(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), root)
    layout = archive_layout(root)
    bundle_bytes = (layout.bundles_dir / f"{result.bundle_sha256}.json").read_bytes()
    import hashlib

    assert hashlib.sha256(bundle_bytes).hexdigest() == result.bundle_sha256
    receipt = json.loads((layout.receipts_dir / f"{result.receipt_id}.json").read_bytes())
    assert receipt["receipt_id"] == result.receipt_id
    cycle = json.loads((layout.cycles_dir / f"{result.cycle_key}.json").read_bytes())
    assert cycle["cycle_key"] == result.cycle_key
    assert (
        canonical_cycle_key(datetime.fromisoformat(cycle["cycle_reference_time"]))
        == result.cycle_key
    )


def test_register_created_flags_true_on_first_registration(tmp_path: Path) -> None:
    result = register_prospective_capture(_bundle_bytes(), tmp_path / "archive")
    assert result.bundle_created is True
    assert result.receipt_created is True
    assert result.cycle_created is True


def test_register_rejects_invalid_bundle_without_writing_anything(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    with pytest.raises(ForecastError):
        register_prospective_capture(b"{not valid json", root)
    assert not root.exists()


# ---------------------------------------------------------------------------
# Exact re-registration idempotency
# ---------------------------------------------------------------------------


def test_exact_reregistration_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    first = register_prospective_capture(raw, root)
    second = register_prospective_capture(raw, root)
    assert first.bundle_sha256 == second.bundle_sha256
    assert first.receipt_id == second.receipt_id
    assert first.cycle_key == second.cycle_key
    assert second.bundle_created is False
    assert second.receipt_created is False
    assert second.cycle_created is False


def test_exact_reregistration_leaves_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    result = register_prospective_capture(raw, root)
    layout = archive_layout(root)
    cycle_path = layout.cycles_dir / f"{result.cycle_key}.json"
    before = cycle_path.read_bytes()
    register_prospective_capture(raw, root)
    assert cycle_path.read_bytes() == before


# ---------------------------------------------------------------------------
# A different valid bundle for the same reference cycle fails closed
# ---------------------------------------------------------------------------


def test_different_valid_bundle_same_cycle_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    original = register_prospective_capture(_bundle_bytes(values=("302", "307", "309.3")), root)
    with pytest.raises(ForecastError, match="conflicting content"):
        register_prospective_capture(_bundle_bytes(values=("303", "307", "309.3")), root)
    layout = archive_layout(root)
    cycle_path = layout.cycles_dir / f"{original.cycle_key}.json"
    cycle = json.loads(cycle_path.read_bytes())
    assert cycle["bundle_sha256"] == original.bundle_sha256


def test_rejected_cherry_pick_leaves_pre_commit_orphans_on_disk(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    register_prospective_capture(_bundle_bytes(values=("302", "307", "309.3")), root)
    cherry_pick_bytes = _bundle_bytes(values=("303", "307", "309.3"))
    _observations, _parsed, cherry_pick_sha256 = parse_and_validate_bundle(cherry_pick_bytes)
    with pytest.raises(ForecastError):
        register_prospective_capture(cherry_pick_bytes, root)
    layout = archive_layout(root)
    assert (layout.bundles_dir / f"{cherry_pick_sha256}.json").exists()


def test_cherry_pick_rejection_does_not_disturb_accepted_cycle_evidence(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    original = register_prospective_capture(_bundle_bytes(values=("302", "307", "309.3")), root)
    with pytest.raises(ForecastError):
        register_prospective_capture(_bundle_bytes(values=("303", "307", "309.3")), root)
    report = verify_archive(root, as_of=_SEPT1_REFERENCE + timedelta(hours=1))
    assert [c.bundle_sha256 for c in report.accepted_cycles] == [original.bundle_sha256]


# ---------------------------------------------------------------------------
# Crash between bundle, receipt, and cycle publication
# ---------------------------------------------------------------------------


def test_crash_between_bundle_and_receipt_leaves_only_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()

    def boom(*_args: object, **_kwargs: object) -> bool:
        raise ForecastError("simulated crash before receipt archive")

    monkeypatch.setattr(ops, "_archive_receipt", boom)
    with pytest.raises(ForecastError, match="simulated crash"):
        register_prospective_capture(raw, root)

    layout = archive_layout(root)
    assert list(layout.bundles_dir.glob("*.json"))
    assert not layout.receipts_dir.exists() or list(layout.receipts_dir.glob("*.json")) == []
    assert not layout.cycles_dir.exists() or list(layout.cycles_dir.glob("*.json")) == []

    monkeypatch.undo()
    resumed = register_prospective_capture(raw, root)
    assert resumed.cycle_created is True
    assert list(layout.cycles_dir.glob("*.json"))


def test_crash_between_receipt_and_cycle_leaves_bundle_and_receipt_but_no_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()

    def boom(*_args: object, **_kwargs: object) -> bool:
        raise ForecastError("simulated crash before cycle publication")

    monkeypatch.setattr(ops, "_publish_cycle", boom)
    with pytest.raises(ForecastError, match="simulated crash"):
        register_prospective_capture(raw, root)

    layout = archive_layout(root)
    assert list(layout.bundles_dir.glob("*.json"))
    assert list(layout.receipts_dir.glob("*.json"))
    assert not layout.cycles_dir.exists() or list(layout.cycles_dir.glob("*.json")) == []

    monkeypatch.undo()
    resumed = register_prospective_capture(raw, root)
    assert resumed.bundle_created is False
    assert resumed.receipt_created is False
    assert resumed.cycle_created is True


def test_resumed_registration_after_crash_produces_identical_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()

    def boom(*_args: object, **_kwargs: object) -> bool:
        raise ForecastError("simulated crash")

    monkeypatch.setattr(ops, "_publish_cycle", boom)
    with pytest.raises(ForecastError):
        register_prospective_capture(raw, root)
    monkeypatch.undo()

    resumed = register_prospective_capture(raw, root)
    # Compute what a from-scratch registration into a fresh archive would
    # produce, to prove the resumed identifiers are exactly the same as an
    # uninterrupted capture would have produced -- not merely internally
    # self-consistent.
    fresh = register_prospective_capture(raw, tmp_path / "fresh-archive")
    assert resumed.bundle_sha256 == fresh.bundle_sha256
    assert resumed.receipt_id == fresh.receipt_id
    assert resumed.cycle_key == fresh.cycle_key


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_identical_registrations_are_all_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    barrier = threading.Barrier(6)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        try:
            register_prospective_capture(raw, root)
            with lock:
                outcomes.append("ok")
        except ForecastError:
            with lock:
                outcomes.append("error")

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes == ["ok"] * 6
    layout = archive_layout(root)
    assert len(list(layout.cycles_dir.glob("*.json"))) == 1


def test_concurrent_competing_bundles_exactly_one_wins_cycle(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw_a = _bundle_bytes(values=("302", "307", "309.3"))
    raw_b = _bundle_bytes(values=("303", "307", "309.3"))
    _obs_a, _parsed_a, sha_a = parse_and_validate_bundle(raw_a)
    _obs_b, _parsed_b, sha_b = parse_and_validate_bundle(raw_b)
    assert sha_a != sha_b

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def attempt(label: str, raw: bytes) -> None:
        barrier.wait()
        try:
            register_prospective_capture(raw, root)
            results[label] = "committed"
        except ForecastError:
            results[label] = "rejected"

    t1 = threading.Thread(target=attempt, args=("a", raw_a))
    t2 = threading.Thread(target=attempt, args=("b", raw_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results.values()) == ["committed", "rejected"]
    layout = archive_layout(root)
    cycle_files = list(layout.cycles_dir.glob("*.json"))
    assert len(cycle_files) == 1
    cycle = json.loads(cycle_files[0].read_bytes())
    winner_sha = sha_a if results["a"] == "committed" else sha_b
    assert cycle["bundle_sha256"] == winner_sha

    # The winner cannot later be dislodged by the loser's content.
    loser_raw = raw_b if results["a"] == "committed" else raw_a
    with pytest.raises(ForecastError):
        register_prospective_capture(loser_raw, root)
    assert json.loads(cycle_files[0].read_bytes()) == cycle


# ---------------------------------------------------------------------------
# Low-level write mechanics: short write, fsync failure, symlink rejection,
# no-replace hard link publication, never opening the final path directly.
# ---------------------------------------------------------------------------


def test_register_handles_injected_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    real_write = os.write
    call_count = 0

    def short_write(fd: int, data: bytes) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1 and len(data) > 1:
            return real_write(fd, data[: len(data) // 2])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_write)
    result = register_prospective_capture(raw, root)
    assert call_count >= 2
    layout = archive_layout(root)
    on_disk = (layout.bundles_dir / f"{result.bundle_sha256}.json").read_bytes()
    assert on_disk == raw


def test_register_fsync_failure_leaves_no_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"

    def failing_fsync(_fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        register_prospective_capture(_bundle_bytes(), root)
    layout = archive_layout(root)
    assert not layout.bundles_dir.exists() or list(layout.bundles_dir.iterdir()) == []


def test_register_never_opens_final_bundle_path_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    _observations, _parsed, bundle_sha256 = parse_and_validate_bundle(raw)
    layout = archive_layout(root)
    final_bundle_path = str(layout.bundles_dir / f"{bundle_sha256}.json")
    opened: list[str] = []
    real_open = os.open

    def spying_open(target: object, *args: object, **kwargs: object) -> int:
        opened.append(str(target))
        return real_open(target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spying_open)
    register_prospective_capture(raw, root)
    assert final_bundle_path not in opened
    assert any(path.endswith(".tmp") for path in opened)


def test_register_publishes_via_no_replace_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    link_calls: list[tuple[str, str]] = []
    real_link = os.link

    def spying_link(src: object, dst: object) -> None:
        link_calls.append((str(src), str(dst)))
        real_link(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", spying_link)
    register_prospective_capture(_bundle_bytes(), root)
    assert len(link_calls) == 3  # bundle, receipt, cycle
    for src, dst in link_calls:
        assert src != dst
        assert src.endswith(".tmp")


def test_register_rejects_symlink_at_bundle_path(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    raw = _bundle_bytes()
    _observations, _parsed, bundle_sha256 = parse_and_validate_bundle(raw)
    layout = archive_layout(root)
    layout.bundles_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("not the real content")
    (layout.bundles_dir / f"{bundle_sha256}.json").symlink_to(elsewhere)
    with pytest.raises(ForecastError, match="not a regular file"):
        register_prospective_capture(raw, root)


def test_register_rejects_symlink_at_cycle_path(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    layout = archive_layout(root)
    layout.cycles_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("not the real content")
    (layout.cycles_dir / f"{_SEPT1_CYCLE_KEY}.json").symlink_to(elsewhere)
    with pytest.raises(ForecastError, match="not a regular file"):
        register_prospective_capture(_bundle_bytes(), root)


# ---------------------------------------------------------------------------
# Expected prospective 03Z coverage -- derived from frozen constants only
# ---------------------------------------------------------------------------


def test_expected_reference_cycles_first_eligible_day_is_september_1() -> None:
    cycles = expected_reference_cycles()
    assert cycles[0].reference_time == _SEPT1_REFERENCE
    assert cycles[0].target_dates[0].isoformat() == PROSPECTIVE_START.isoformat()


def test_expected_reference_cycles_last_eligible_day_is_march_29() -> None:
    cycles = expected_reference_cycles()
    assert cycles[-1].reference_time == datetime(2027, 3, 29, 3, 0, 0, tzinfo=UTC)
    assert cycles[-1].target_dates[-1].isoformat() == PROSPECTIVE_END.isoformat()


def test_expected_reference_cycles_all_target_dates_within_window() -> None:
    for cycle in expected_reference_cycles():
        for target in cycle.target_dates:
            assert PROSPECTIVE_START <= target <= PROSPECTIVE_END


def test_expected_reference_cycles_capture_deadline_is_earliest_interval_start() -> None:
    cycles = expected_reference_cycles()
    first = next(c for c in cycles if c.reference_time == _SEPT1_REFERENCE)
    assert first.capture_deadline == _SEPT1_REFERENCE + timedelta(hours=9)


def test_expected_reference_cycles_are_unique_and_sorted() -> None:
    cycles = expected_reference_cycles()
    reference_times = [c.reference_time for c in cycles]
    assert reference_times == sorted(reference_times)
    assert len(set(reference_times)) == len(reference_times)


# ---------------------------------------------------------------------------
# classify_cycle
# ---------------------------------------------------------------------------


def _expected_sept1_cycle() -> ops.ExpectedCycle:
    return next(c for c in expected_reference_cycles() if c.reference_time == _SEPT1_REFERENCE)


def test_classify_cycle_pending_before_deadline() -> None:
    cycle = _expected_sept1_cycle()
    status = classify_cycle(cycle, captured=False, as_of=_SEPT1_REFERENCE + timedelta(hours=1))
    assert status is CycleStatus.PENDING


def test_classify_cycle_missed_after_deadline_without_capture() -> None:
    cycle = _expected_sept1_cycle()
    status = classify_cycle(
        cycle, captured=False, as_of=cycle.capture_deadline + timedelta(seconds=1)
    )
    assert status is CycleStatus.MISSED


def test_classify_cycle_captured_overrides_deadline() -> None:
    cycle = _expected_sept1_cycle()
    status = classify_cycle(cycle, captured=True, as_of=cycle.capture_deadline + timedelta(days=30))
    assert status is CycleStatus.CAPTURED


def test_classify_cycle_at_exact_deadline_is_missed_not_pending() -> None:
    cycle = _expected_sept1_cycle()
    status = classify_cycle(cycle, captured=False, as_of=cycle.capture_deadline)
    assert status is CycleStatus.MISSED


def test_classify_cycle_requires_utc_as_of() -> None:
    cycle = _expected_sept1_cycle()
    with pytest.raises(ForecastError, match="UTC"):
        classify_cycle(cycle, captured=False, as_of=datetime(2026, 9, 1, 4, 0, 0))


# ---------------------------------------------------------------------------
# verify_archive -- core (never writes)
# ---------------------------------------------------------------------------


def test_verify_archive_ok_after_clean_registration(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), root)
    report = verify_archive(root, as_of=_SEPT1_REFERENCE + timedelta(hours=1))
    assert report.ok is True
    assert len(report.accepted_cycles) == 1
    accepted = report.accepted_cycles[0]
    assert accepted.cycle_key == result.cycle_key
    assert accepted.bundle_sha256 == result.bundle_sha256
    assert accepted.receipt_id == result.receipt_id


def test_verify_archive_classifies_accepted_cycle_as_captured(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    register_prospective_capture(_bundle_bytes(), root)
    report = verify_archive(root, as_of=_SEPT1_REFERENCE + timedelta(days=200))
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY] is CycleStatus.CAPTURED


def test_verify_archive_on_nonexistent_root_never_creates_it(tmp_path: Path) -> None:
    root = tmp_path / "does-not-exist"
    report = verify_archive(root, as_of=datetime.now(UTC))
    assert report.ok is True
    assert report.accepted_cycles == ()
    assert not root.exists()


def test_verify_archive_requires_utc_as_of(tmp_path: Path) -> None:
    with pytest.raises(ForecastError, match="UTC"):
        verify_archive(tmp_path / "archive", as_of=datetime(2026, 9, 1))
