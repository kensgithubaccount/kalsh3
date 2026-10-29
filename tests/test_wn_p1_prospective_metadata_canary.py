"""Hostile offline tests: no live Earth Engine, pixel sampling, or Kalshi calls."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from scripts.audit_wn_p1_source_metadata import REQUIRED_BANDS
from scripts.wn_p1_prospective_metadata_canary import (
    ProspectiveCanaryError,
    capture_one,
    main,
    policy,
)

TARGET = date(2026, 11, 2)
INIT = datetime(2026, 11, 2, tzinfo=UTC)


def specimen(ingestion_minutes: int = 480) -> dict[str, object]:
    hours = list(range(1, 32))
    return {
        "forecast_hours": hours,
        "start_times": [INIT.isoformat().replace("+00:00", "Z")] * len(hours),
        "end_times": [
            (INIT + timedelta(hours=h)).isoformat().replace("+00:00", "Z") for h in hours
        ],
        "ingestion_times": [(INIT + timedelta(minutes=ingestion_minutes)).timestamp()]
        * len(hours),
        "band_names": sorted(REQUIRED_BANDS),
    }


def clock(*values: datetime) -> Callable[[], datetime]:
    sequence = iter(values)
    return lambda: next(sequence)


def ticks(*values: int) -> Callable[[], int]:
    sequence = iter(values)
    return lambda: next(sequence)


def run(
    root: Path,
    *,
    data: dict[str, object] | None = None,
    started: int = 15,
    finished: int = 16,
    fail: bool = False,
) -> tuple[int, dict[str, object], list[int]]:
    calls: list[int] = []

    def getter(_project: str, _target: date) -> dict[str, object]:
        calls.append(1)
        if fail:
            raise RuntimeError("SENSITIVE_FAKE_TOKEN")
        return specimen() if data is None else data

    code, receipt = capture_one(
        target=TARGET,
        project="research-project",
        receipt_root=root,
        getter=getter,
        clock=clock(
            INIT + timedelta(hours=8, minutes=started),
            INIT + timedelta(hours=8, minutes=finished),
        ),
        monotonic=ticks(7, 11),
    )
    return code, receipt, calls


def test_plan_is_inert_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([
        "--target", TARGET.isoformat(),
        "--project", "research-project",
        "--receipt-root", str(tmp_path),
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PLAN_ONLY_NO_SOURCE_RPC"
    assert list(tmp_path.iterdir()) == []


def test_complete_metadata_never_gets_source_or_model_authority(tmp_path: Path) -> None:
    code, receipt, calls = run(tmp_path)
    assert code == 0
    assert calls == [1]
    assert receipt["status"] == "METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED"
    assert receipt["acquisition_mode"] == "TEST_INJECTED_UNTRUSTED"
    assert receipt["source_attestation"] == "NONE"
    assert receipt["monotonic_elapsed_ns"] == 4
    assert receipt["source_request_attempted"] is True
    assert receipt["scientific_evidence_authorized"] is False
    assert receipt["probability_authorized"] is False
    assert receipt["paper_signal_authorized"] is False
    assert receipt["trading_authority"] == "NONE"
    assert policy()["rights_approved_by_program"] is False
    stored = json.loads((tmp_path / TARGET.isoformat() / "receipt.json").read_text())
    assert stored == receipt
    assert len(cast(dict[str, object], receipt["five_city_diagnostic"])) == 5


def test_duplicate_run_denied_even_when_final_receipt_deleted(tmp_path: Path) -> None:
    _, _, calls = run(tmp_path)
    (tmp_path / TARGET.isoformat() / "receipt.json").unlink()
    with pytest.raises(ProspectiveCanaryError, match="already claimed"):
        run(tmp_path)
    assert len(calls) == 1


def test_pre_window_no_source_request_but_failure_receipt(tmp_path: Path) -> None:
    calls: list[int] = []

    def get(_project: str, _target: date) -> dict[str, object]:
        calls.append(1)
        return specimen()

    code, receipt = capture_one(
        target=TARGET,
        receipt_root=tmp_path,
        project="research-project",
        getter=get,
        clock=clock(
            INIT + timedelta(hours=7, minutes=59),
            INIT + timedelta(hours=7, minutes=59),
        ),
        monotonic=ticks(1, 2),
    )
    assert code == 2 and not calls
    assert receipt["status"] == "FAIL_CLOSED_NO_SOURCE_CLAIM"
    assert receipt["source_request_attempted"] is False


def test_exact_nine_utc_completion_is_too_late(tmp_path: Path) -> None:
    code, receipt, calls = run(tmp_path, started=59, finished=60)
    assert calls == [1]
    assert code == 2
    assert receipt["status"] == "FAIL_CLOSED_CLOCK_OR_DEADLINE"


def test_missing_required_hour_abstains(tmp_path: Path) -> None:
    data = specimen()
    for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
        cast(list[object], data[key]).pop()
    code, receipt, _ = run(tmp_path, data=data)
    assert code == 1
    assert receipt["status"] == "ABSTAIN_INCOMPLETE_OR_LATE"


def test_source_ingestion_after_host_observation_rejected(tmp_path: Path) -> None:
    data = specimen()
    cast(list[object], data["ingestion_times"])[6] = (
        INIT + timedelta(hours=8, minutes=50)
    ).timestamp()
    code, receipt, _ = run(tmp_path, data=data)
    assert code == 2
    assert receipt["failure_class"] == "SOURCE_INGESTION_LATER_THAN_OBSERVATION"


def test_late_source_ingestion_never_passes(tmp_path: Path) -> None:
    code, receipt, _ = run(tmp_path, data=specimen(540))
    assert code == 2
    assert receipt["status"] == "FAIL_CLOSED_CLOCK_OR_DEADLINE"


def test_sdk_exception_is_sanitized_and_receipt_kept(tmp_path: Path) -> None:
    code, receipt, calls = run(tmp_path, fail=True)
    assert calls == [1] and code == 2
    assert receipt["status"] == "FAIL_CLOSED_NO_SOURCE_CLAIM"
    assert "SENSITIVE_FAKE_TOKEN" not in json.dumps(receipt)
    assert (tmp_path / TARGET.isoformat() / "receipt.json").exists()


@pytest.mark.parametrize("fault", ["duplicate", "overflow", "bad_band", "nan_epoch"])
def test_forged_metadata_does_not_generate_observed_status(
    tmp_path: Path, fault: str
) -> None:
    data = specimen()
    if fault == "duplicate":
        cast(list[object], data["forecast_hours"])[0] = 2
    if fault == "overflow":
        for key in ("forecast_hours", "start_times", "end_times", "ingestion_times"):
            cast(list[object], data[key]).extend(cast(list[object], data[key])[:10])
    if fault == "bad_band":
        cast(list[object], data["band_names"]).pop()
    if fault == "nan_epoch":
        cast(list[object], data["ingestion_times"])[0] = float("nan")
    code, receipt, _ = run(tmp_path, data=data)
    assert code == 2
    assert receipt["status"].startswith("FAIL_CLOSED")
    assert receipt["paper_signal_authorized"] is False


@pytest.mark.parametrize("reverse_monotonic", [True, False])
def test_clock_rollback_fails_closed(tmp_path: Path, reverse_monotonic: bool) -> None:
    times = (
        (INIT + timedelta(hours=8, minutes=15), INIT + timedelta(hours=8, minutes=16))
        if reverse_monotonic
        else (INIT + timedelta(hours=8, minutes=15), INIT + timedelta(hours=8, minutes=14))
    )
    code, receipt = capture_one(
        target=TARGET,
        project="research-project",
        receipt_root=tmp_path,
        getter=lambda _project, _target: specimen(),
        clock=clock(*times),
        monotonic=ticks(*( (5, 4) if reverse_monotonic else (5, 6) )),
    )
    assert code == 2
    assert receipt["status"] == "FAIL_CLOSED_CLOCK_OR_DEADLINE"


def test_fsync_error_poisoned_slot_cannot_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fsync
    calls = 0

    def fail_second(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected fsync failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail_second)
    with pytest.raises(ProspectiveCanaryError, match="slot permanently poisoned"):
        run(tmp_path)
    assert (tmp_path / TARGET.isoformat()).exists()
    with pytest.raises(ProspectiveCanaryError, match="already claimed"):
        run(tmp_path)


def test_october_target_cannot_claim_future_source(tmp_path: Path) -> None:
    with pytest.raises(ProspectiveCanaryError, match="date scope"):
        capture_one(target=date(2026, 10, 27), project="p", receipt_root=tmp_path)
    assert not list(tmp_path.iterdir())
