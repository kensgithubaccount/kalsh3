"""Only synthetic WN-P1-U2 claim/clock/receipt guard tests; no GCP/EE traffic."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from scripts.wn_p1_u2_offline_claim_guard import (
    CLAIM_KEY,
    EARLIEST,
    LAST_START_EXCLUSIVE,
    RECEIPT_KEY,
    WATCHDOG_DEADLINE_EXCLUSIVE,
    ClaimExists,
    GuardResult,
    offline_once,
)


class MemoryCreateOnly:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.claim_ambiguous = False
        self.receipt_ambiguous = False

    def create_if_absent(self, key: str, payload: bytes) -> None:
        self.calls.append(key)
        if (key == CLAIM_KEY and self.claim_ambiguous) or (
            key == RECEIPT_KEY and self.receipt_ambiguous
        ):
            raise OSError("INJECTED_NETWORK_FAILURE_SECRET_SHOULD_NOT_LEAK")
        if key in self.objects:
            raise ClaimExists(key)
        self.objects[key] = payload


START = EARLIEST + timedelta(minutes=1)


def clock(*values: datetime) -> Callable[[], datetime]:
    items = iter(values)
    return lambda: next(items)


def ticks(*values: int) -> Callable[[], int]:
    items = iter(values)
    return lambda: next(items)


def receipt(**overrides: object) -> bytes:
    data: dict[str, object] = {
        "record_type": "WN-P1-PROSPECTIVE-EE-UNATTESTED-METADATA-v1",
        "target_date_utc": "2026-11-01",
        "status": "METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED",
        "source_attestation": "NONE",
        "scientific_evidence_authorized": False,
        "probability_authorized": False,
        "paper_signal_authorized": False,
        "trading_authority": "NONE",
        "production_influence": "0",
        "returned_metadata": {"source": "SYNTHETIC_OFFLINE_ONLY"},
    }
    data.update(overrides)
    return json.dumps(data, sort_keys=True).encode()


def execute(
    store: MemoryCreateOnly,
    *,
    times: tuple[datetime, ...] | None = None,
    monotonic: tuple[int, ...] = (1, 2, 3),
    capture: Callable[[], bytes] = receipt,
) -> GuardResult:
    if times is None:
        times = (START, START + timedelta(seconds=1), START + timedelta(seconds=2))
    return offline_once(
        store=store,
        capture_receipt=capture,
        clock=clock(*times),
        monotonic_ns=ticks(*monotonic),
    )


def test_one_untrusted_receipt_copy_and_no_positive_authority() -> None:
    store = MemoryCreateOnly()
    calls: list[int] = []

    def capture() -> bytes:
        calls.append(1)
        return receipt()

    result = execute(store, capture=capture)
    assert result.status == "UNATTESTED_RECEIPT_COPIED"
    assert result.claim_attempted and result.claim_acknowledged
    assert result.source_callback_attempted and result.receipt_published
    assert not result.source_attested
    assert not result.probability_authorized
    assert not result.paper_authorized
    assert result.trading_authority == "NONE"
    assert calls == [1]
    assert store.calls == [CLAIM_KEY, RECEIPT_KEY]
    assert store.objects[RECEIPT_KEY] == receipt()
    assert json.loads(store.objects[CLAIM_KEY])["no_retry"] is True

    second = execute(store, capture=capture)
    assert second.status == "CLAIM_ALREADY_EXISTS"
    assert second.claim_attempted and not second.claim_acknowledged
    assert not second.source_callback_attempted and not second.receipt_published
    assert calls == [1]
    assert store.calls == [CLAIM_KEY, RECEIPT_KEY, CLAIM_KEY]


@pytest.mark.parametrize(
    "start",
    [
        EARLIEST - timedelta(microseconds=1),
        LAST_START_EXCLUSIVE,
        LAST_START_EXCLUSIVE + timedelta(hours=1),
        START + timedelta(days=1),
    ],
)
def test_outside_window_never_claims_or_calls(start: datetime) -> None:
    store = MemoryCreateOnly()
    calls: list[int] = []

    def capture() -> bytes:
        calls.append(1)
        return receipt()

    result = execute(store, times=(start,), monotonic=(1,), capture=capture)
    assert result.status == "SKIP_OUTSIDE_FROZEN_LAUNCH_WINDOW"
    assert not store.calls and not calls


def test_exact_0805_is_allowed_and_08010_is_rejected() -> None:
    store = MemoryCreateOnly()
    ok = execute(
        store,
        times=(
            EARLIEST,
            EARLIEST + timedelta(seconds=1),
            EARLIEST + timedelta(seconds=2),
        ),
    )
    assert ok.status == "UNATTESTED_RECEIPT_COPIED"
    too_late = execute(MemoryCreateOnly(), times=(LAST_START_EXCLUSIVE,), monotonic=(1,))
    assert too_late.status == "SKIP_OUTSIDE_FROZEN_LAUNCH_WINDOW"


@pytest.mark.parametrize(
    ("times", "monotonic", "status"),
    [
        ((START, LAST_START_EXCLUSIVE), (1, 2), "CLOCK_OR_DEADLINE_AFTER_CLAIM"),
        ((START, START - timedelta(seconds=1)), (1, 2), "CLOCK_OR_DEADLINE_AFTER_CLAIM"),
        ((START, START + timedelta(seconds=1)), (2, 1), "MONOTONIC_ROLLBACK_AFTER_CLAIM"),
        (
            (START, START + timedelta(seconds=1), WATCHDOG_DEADLINE_EXCLUSIVE),
            (1, 2, 3),
            "FINISHED_TOO_LATE_OR_CLOCK_INVALID_POISONED",
        ),
        (
            (START, START + timedelta(seconds=1), START),
            (1, 2, 3),
            "FINISHED_TOO_LATE_OR_CLOCK_INVALID_POISONED",
        ),
        (
            (START, START + timedelta(seconds=1), START + timedelta(seconds=2)),
            (1, 2, 1),
            "FINISHED_TOO_LATE_OR_CLOCK_INVALID_POISONED",
        ),
    ],
)
def test_clock_failures_poison_one_shot_without_any_retry(
    times: tuple[datetime, ...], monotonic: tuple[int, ...], status: str
) -> None:
    store = MemoryCreateOnly()
    calls: list[int] = []

    def capture() -> bytes:
        calls.append(1)
        return receipt()

    result = execute(store, times=times, monotonic=monotonic, capture=capture)
    assert result.status == status
    assert CLAIM_KEY in store.objects
    assert RECEIPT_KEY not in store.objects
    assert not result.receipt_published
    duplicate = execute(store, capture=capture)
    assert duplicate.status == "CLAIM_ALREADY_EXISTS"
    assert len(calls) <= 1


def test_malformed_clock_or_monotonic_skips_without_claim() -> None:
    store = MemoryCreateOnly()
    result = execute(store, times=(START.replace(tzinfo=None),), monotonic=(1,))
    assert result.status == "INVALID_PRELAUNCH_CLOCK"
    assert not store.calls
    negative = execute(store, monotonic=(-1,))
    assert negative.status == "INVALID_PRELAUNCH_CLOCK"
    assert not store.calls


def test_uncertain_external_claim_never_attempts_source() -> None:
    store = MemoryCreateOnly()
    store.claim_ambiguous = True
    calls: list[int] = []

    def capture() -> bytes:
        calls.append(1)
        return receipt()

    result = execute(store, capture=capture)
    assert result.status == "CLAIM_UNCERTAIN_NO_SOURCE"
    assert result.claim_attempted and not result.claim_acknowledged
    assert not result.source_callback_attempted
    assert not calls
    assert RECEIPT_KEY not in store.objects


def test_source_exception_poison_no_retry_and_no_success_receipt() -> None:
    store = MemoryCreateOnly()
    calls: list[int] = []

    def capture() -> bytes:
        calls.append(1)
        raise RuntimeError("FAKE_PRIVATE_TOKEN")

    result = execute(store, capture=capture)
    assert result.status == "CAPTURE_OR_RECEIPT_FAILED_POISONED"
    assert CLAIM_KEY in store.objects and RECEIPT_KEY not in store.objects
    assert "FAKE_PRIVATE_TOKEN" not in result.status
    assert execute(store, capture=capture).status == "CLAIM_ALREADY_EXISTS"
    assert calls == [1]


def test_abrupt_process_level_exception_propagates_but_claim_is_poisoned() -> None:
    store = MemoryCreateOnly()

    def interrupt() -> bytes:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(store, capture=interrupt)
    assert CLAIM_KEY in store.objects and RECEIPT_KEY not in store.objects
    assert execute(store).status == "CLAIM_ALREADY_EXISTS"


@pytest.mark.parametrize(
    "bad",
    [
        {"probability_authorized": True},
        {"scientific_evidence_authorized": 0},
        {"paper_signal_authorized": 0},
        {"source_attestation": "GCS_SOURCE_PROOF"},
        {"trading_authority": "YES"},
        {"target_date_utc": "2026-11-02"},
        {"production_influence": 0},
        {"record_type": "FAKE"},
        {"status": "SCIENTIFIC_PASS"},
    ],
)
def test_forged_receipt_never_published(bad: dict[str, object]) -> None:
    store = MemoryCreateOnly()
    result = execute(store, capture=lambda: receipt(**bad))
    assert result.status == "CAPTURE_OR_RECEIPT_FAILED_POISONED"
    assert CLAIM_KEY in store.objects and RECEIPT_KEY not in store.objects


def test_bad_json_or_oversize_receipt_is_not_published() -> None:
    for blob in (b"{bad", b"", b"x" * 131073):
        store = MemoryCreateOnly()
        result = execute(store, capture=lambda blob=blob: blob)
        assert result.status == "CAPTURE_OR_RECEIPT_FAILED_POISONED"
        assert CLAIM_KEY in store.objects and RECEIPT_KEY not in store.objects


def test_uncertain_remote_receipt_keeps_claim_and_abstains() -> None:
    store = MemoryCreateOnly()
    store.receipt_ambiguous = True
    result = execute(store)
    assert result.status == "REMOTE_RECEIPT_UNCERTAIN_POISONED"
    assert CLAIM_KEY in store.objects and RECEIPT_KEY not in store.objects
    assert execute(store).status == "CLAIM_ALREADY_EXISTS"


def test_straight_predeadline_metadata_failures_remain_unattested() -> None:
    store = MemoryCreateOnly()
    result = execute(store, capture=lambda: receipt(status="ABSTAIN_INCOMPLETE_OR_LATE"))
    assert result.status == "UNATTESTED_RECEIPT_COPIED"
    assert not result.source_attested
    assert json.loads(store.objects[RECEIPT_KEY])["status"] == "ABSTAIN_INCOMPLETE_OR_LATE"


def test_host_timestamp_is_timezone_normalized() -> None:
    store = MemoryCreateOnly()
    assert START.tzinfo is UTC
    assert execute(store).status == "UNATTESTED_RECEIPT_COPIED"
