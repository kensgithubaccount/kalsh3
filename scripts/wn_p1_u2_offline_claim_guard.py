"""WN-P1-U2 offline one-date guard contract; NOT a deployable Cloud Run runner.

This research module has no network, credential, scheduler, source-import, CLI,
or top-level I/O. Its callbacks are injected by offline tests. In particular,
an arbitrary implementation of CreateOnlyStore is NOT proof that GCS enforces
if_generation_match=0. Nothing here authorizes Earth Engine or paper trading.

A separately reviewed executor must add provider-enforced durable GCS claims,
a strict real pre-launch gate, process-killing watchdog, hardened source
invocation, credential scope, output custody and actual scheduler expiry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

POLICY = "wn-p1-u2-offline-guard-v1"
TARGET = date(2026, 11, 1)
EARLIEST = datetime(2026, 11, 1, 8, 5, tzinfo=UTC)
LAST_START_EXCLUSIVE = datetime(2026, 11, 1, 8, 10, tzinfo=UTC)
WATCHDOG_DEADLINE_EXCLUSIVE = datetime(2026, 11, 1, 8, 55, tzinfo=UTC)
CANARY_CUTOFF_EXCLUSIVE = datetime(2026, 11, 1, 9, tzinfo=UTC)
CLAIM_KEY = "wn_p1_u2/claims/2026-11-01.json"
RECEIPT_KEY = "wn_p1_u2/receipts/2026-11-01.json"
MAX_RECEIPT_BYTES = 131072


class ClaimExists(ValueError):
    """The independent provider-enforced create-only claim already exists."""


class GuardFailure(ValueError):
    """A request or outcome cannot be relied on for prospective source evidence."""


class CreateOnlyStore(Protocol):
    """Synthetic interface only; production GCS preconditions are NOT implemented."""

    def create_if_absent(self, key: str, payload: bytes) -> None:
        """Must atomically create once, never overwrite/delete or silently retry."""


@dataclass(frozen=True, slots=True)
class GuardResult:
    status: str
    claim_attempted: bool
    claim_acknowledged: bool
    source_callback_attempted: bool
    receipt_published: bool

    @property
    def source_attested(self) -> bool:
        return False

    @property
    def probability_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise GuardFailure("invalid untrusted host clock")
    return value.astimezone(UTC)


def _ticks(value: int) -> int:
    if type(value) is not int or value < 0:
        raise GuardFailure("invalid monotonic time")
    return value


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _validate_canary_receipt(blob: bytes) -> bytes:
    """Require original manual canary's DENIED authorities; never promote receipt."""
    if type(blob) is not bytes or not 0 < len(blob) <= MAX_RECEIPT_BYTES:
        raise GuardFailure("invalid local receipt size or type")
    try:
        record = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardFailure("invalid local receipt JSON") from exc
    if (
        type(record) is not dict
        or record.get("record_type") != "WN-P1-PROSPECTIVE-EE-UNATTESTED-METADATA-v1"
        or record.get("target_date_utc") != TARGET.isoformat()
        or record.get("source_attestation") != "NONE"
        or record.get("scientific_evidence_authorized") is not False
        or record.get("probability_authorized") is not False
        or record.get("paper_signal_authorized") is not False
        or record.get("trading_authority") != "NONE"
        or record.get("production_influence") != "0"
        or record.get("status") not in {
            "METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED",
            "ABSTAIN_INCOMPLETE_OR_LATE",
            "FAIL_CLOSED_NO_SOURCE_CLAIM",
            "FAIL_CLOSED_CLOCK_OR_DEADLINE",
        }
    ):
        raise GuardFailure("local receipt cannot grant source/model/paper/trade authority")
    return blob


def offline_once(
    *,
    store: CreateOnlyStore,
    capture_receipt: Callable[[], bytes],
    clock: Callable[[], datetime],
    monotonic_ns: Callable[[], int],
) -> GuardResult:
    """Simulate a claim-gated single attempt without providing a live entrypoint.

    All remote effects are supplied by test doubles. Failures after claim may
    leave a missing receipt, but MUST NEVER trigger a second acquisition.
    Wall clocks and callback timeouts need separate independently tested
    runtime enforcement; this function cannot kill a blocked source call.
    """
    def no(status: str, claimed: bool = False, called: bool = False) -> GuardResult:
        return GuardResult(status, claimed, claimed, called, False)

    # Do not even attempt a provider claim outside the tighter launch window.
    try:
        started = _utc(clock())
        tick_start = _ticks(monotonic_ns())
    except Exception:
        return no("INVALID_PRELAUNCH_CLOCK")
    if not EARLIEST <= started < LAST_START_EXCLUSIVE:
        return no("SKIP_OUTSIDE_FROZEN_LAUNCH_WINDOW")

    claim = _json_bytes(
        {
            "record_type": "WN-P1-U2-EXTERNAL-ONE-DATE-CLAIM-v1",
            "policy": POLICY,
            "target_date_utc": TARGET.isoformat(),
            "started_host_utc": started.isoformat(),
            "no_retry": True,
            "source_authority": "NONE",
            "trading_authority": "NONE",
        }
    )
    try:
        # The store implementation MUST map provider if_generation_match=0
        # preconditions to ClaimExists and treat uncertain errors as failures.
        store.create_if_absent(CLAIM_KEY, claim)
    except ClaimExists:
        return GuardResult("CLAIM_ALREADY_EXISTS", True, False, False, False)
    except Exception:
        return GuardResult("CLAIM_UNCERTAIN_NO_SOURCE", True, False, False, False)

    try:
        after_claim = _utc(clock())
        tick_after_claim = _ticks(monotonic_ns())
        if not started <= after_claim < LAST_START_EXCLUSIVE:
            return no("CLOCK_OR_DEADLINE_AFTER_CLAIM", True)
        if tick_after_claim < tick_start:
            return no("MONOTONIC_ROLLBACK_AFTER_CLAIM", True)
    except Exception:
        return no("CLOCK_FAILED_AFTER_CLAIM", True)

    # There is intentionally no GCP SDK and no direct EE read in this module.
    try:
        blob = _validate_canary_receipt(capture_receipt())
    except Exception:
        return no("CAPTURE_OR_RECEIPT_FAILED_POISONED", True, True)

    try:
        finished = _utc(clock())
        tick_end = _ticks(monotonic_ns())
        if (
            finished < after_claim
            or finished >= WATCHDOG_DEADLINE_EXCLUSIVE
            or finished >= CANARY_CUTOFF_EXCLUSIVE
            or tick_end < tick_after_claim
        ):
            return no("FINISHED_TOO_LATE_OR_CLOCK_INVALID_POISONED", True, True)
    except Exception:
        return no("FINISH_CLOCK_UNVERIFIABLE_POISONED", True, True)

    # This fixture interface is not a guarantee of provider-verified custody.
    # A failed or ambiguous receipt commit is terminal. Never rerun the capture.
    try:
        store.create_if_absent(RECEIPT_KEY, blob)
    except Exception:
        return no("REMOTE_RECEIPT_UNCERTAIN_POISONED", True, True)
    return GuardResult("UNATTESTED_RECEIPT_COPIED", True, True, True, True)
