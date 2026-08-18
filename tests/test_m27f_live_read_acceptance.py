from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.client import AccountGatewayError, HttpResponse
from services.kalshi_account_gateway.production_read_credentials import ProductionReadReply
from services.supervised_canary import live_read_acceptance as m27f
from services.supervised_canary.readiness_report import operator_evidence


class FakeSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class FakeAuthorityTransport:
    """Fake ``GET /trade-api/v2/api_keys`` boundary transport."""

    def __init__(self, reply: ProductionReadReply | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> ProductionReadReply:
        self.calls.append((origin, path))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def api_keys_reply(records: Any, *, status: int = 200) -> ProductionReadReply:
    return ProductionReadReply(status, json.dumps({"api_keys": records}).encode())


VALID_KEY_RECORD = {
    "api_key_id": "candidate",
    "scopes": ["read", "write::trade"],
    "subaccount": 0,
}


class FakeAccountTransport:
    """Fake account-read transport; overrides key by path substring, popped in order."""

    def __init__(self, overrides: dict[str, list[HttpResponse | Exception]] | None = None) -> None:
        self.overrides = overrides or {}
        self.paths: list[str] = []

    def get(self, path: str, headers: Mapping[str, str], *, timeout_seconds: float) -> HttpResponse:
        self.paths.append(path)
        for needle, values in self.overrides.items():
            if needle in path and values:
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
        if "balance" in path:
            return HttpResponse(
                200,
                {
                    "balance": 100000,
                    "portfolio_value": 100125,
                    "updated_ts": 1_700_000_000,
                    "balance_breakdown": [],
                },
            )
        if "limits" in path:
            return HttpResponse(
                200,
                {
                    "usage_tier": "basic",
                    "read": {"refill_rate": 10, "bucket_capacity": 20},
                    "write": {"refill_rate": 5, "bucket_capacity": 10},
                    "grants": [],
                },
            )
        field = (
            "market_positions"
            if "positions" in path
            else next(x for x in ("orders", "fills", "settlements") if x in path)
        )
        return HttpResponse(200, {field: [], "cursor": ""})


def _clock_sequence(*deltas_seconds: float) -> Any:
    """Returns each scheduled offset in turn, then holds the final offset thereafter.

    Holding the final value (rather than raising once exhausted) keeps this independent of
    the exact number of ``clock()`` calls the orchestration makes internally.
    """
    start = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    schedule = [start + timedelta(seconds=value) for value in deltas_seconds]
    state = {"calls": 0}

    def clock() -> datetime:
        index = min(state["calls"], len(schedule) - 1)
        state["calls"] += 1
        return schedule[index]

    return clock


# Captured once, before any test monkeypatches ``m27f.run_live_read_acceptance`` --
# this helper must always call the real implementation, never whatever the module
# attribute currently points at, or a monkeypatched fake calling ``run()`` would recurse
# into itself.
_real_run_live_read_acceptance = m27f.run_live_read_acceptance


def run(
    *,
    authority_reply: ProductionReadReply | Exception | None = None,
    account_overrides: dict[str, list[HttpResponse | Exception]] | None = None,
    clock: Any = None,
) -> m27f.LiveReadAcceptanceEvidence:
    if authority_reply is None:
        authority_reply = api_keys_reply([VALID_KEY_RECORD])
    kwargs: dict[str, Any] = {
        "key_id": "candidate",
        "private_key_pem": b"synthetic-pem-not-real",
        "authority_transport": FakeAuthorityTransport(authority_reply),
        "account_transport": FakeAccountTransport(account_overrides),
        "signer_factory": FakeSigner,
        "clock_ms": lambda: 123,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return _real_run_live_read_acceptance(**kwargs)


# --------------------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------------------


def test_correct_candidate_and_complete_reads_pass_every_gate() -> None:
    evidence = run()
    assert evidence.candidate_authority.classification == "PASS"
    assert evidence.candidate_authority.server_scopes == ("read", "write::trade")
    assert evidence.candidate_authority.server_subaccount == 0
    assert {read.name: read.classification for read in evidence.reads} == {
        "balance": "SUCCESS",
        "limits": "SUCCESS",
        "positions": "SUCCESS",
        "orders": "SUCCESS",
        "fills": "SUCCESS",
        "settlements": "SUCCESS",
    }
    assert evidence.reconciliation.classification == "PASS"
    assert evidence.subaccount == 0
    assert evidence.environment == "PRODUCTION"


def test_evidence_json_is_secret_free() -> None:
    evidence = run()
    dumped = json.dumps(evidence.to_json())
    assert "synthetic-pem-not-real" not in dumped
    assert "PRIVATE KEY" not in dumped
    # Only the key id *hash* may appear (e.g. inside the "candidate_authority" field name);
    # the raw key id must never appear as a quoted JSON string value.
    assert '"candidate"' not in dumped


# --------------------------------------------------------------------------------------
# Candidate authority adversarial matrix -- reused M27E boundary must fail closed and no
# read may ever be attempted when authority does not PASS.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authority_reply",
    [
        api_keys_reply([{"api_key_id": "someone-else", "scopes": ["read"], "subaccount": 0}]),
        api_keys_reply([VALID_KEY_RECORD, VALID_KEY_RECORD]),
        api_keys_reply([{"api_key_id": "candidate", "scopes": ["read", "write"], "subaccount": 0}]),
        api_keys_reply(
            [
                {
                    "api_key_id": "candidate",
                    "scopes": ["read", "write::trade", "write::transfer"],
                    "subaccount": 0,
                }
            ]
        ),
        api_keys_reply([{"api_key_id": "candidate", "scopes": ["read"], "subaccount": 0}]),
        api_keys_reply(
            [{"api_key_id": "candidate", "scopes": ["read", "write::trade"], "subaccount": None}]
        ),
        api_keys_reply(
            [{"api_key_id": "candidate", "scopes": ["read", "write::trade"], "subaccount": 1}]
        ),
        api_keys_reply([VALID_KEY_RECORD], status=401),
        api_keys_reply([VALID_KEY_RECORD], status=403),
        ProductionReadReply(302, b"", location="https://attacker.example"),
        TimeoutError("synthetic timeout"),
        ProductionReadReply(200, b"not-json"),
    ],
    ids=[
        "wrong_key_id",
        "duplicate_key_id",
        "broad_write",
        "extra_scope",
        "missing_write_trade",
        "null_subaccount",
        "wrong_subaccount",
        "http_401",
        "http_403",
        "redirect",
        "timeout",
        "malformed_json",
    ],
)
def test_authority_failure_never_reaches_account_reads(authority_reply: Any) -> None:
    evidence = run(authority_reply=authority_reply)
    assert evidence.candidate_authority.classification == "FAIL"
    assert evidence.reads == ()
    assert evidence.reconciliation.classification == "BLOCKED"


def test_oversized_authority_response_fails_closed() -> None:
    from services.kalshi_account_gateway.production_read_credentials import (
        ProductionCredentialError,
    )

    evidence = run(
        authority_reply=ProductionCredentialError("production verification response too large")
    )
    assert evidence.candidate_authority.classification == "FAIL"
    assert evidence.reads == ()
    assert evidence.reconciliation.classification == "BLOCKED"


def test_correct_candidate_metadata_is_recorded_on_pass() -> None:
    evidence = run()
    assert evidence.candidate_authority.reason is None
    assert evidence.candidate_authority.key_id_hash != "candidate"


# --------------------------------------------------------------------------------------
# Per-endpoint authenticated read adversarial matrix
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("needle", "override", "expected_classification"),
    [
        ("balance?", [HttpResponse(401, {})], "AUTH_FAILURE"),
        ("balance?", [HttpResponse(403, {})], "SCHEMA_OR_HTTP_FAILURE"),
        ("balance?", [HttpResponse(302, {})], "SCHEMA_OR_HTTP_FAILURE"),
        ("balance?", [TimeoutError()], "UPSTREAM_UNAVAILABLE"),
        (
            "balance?",
            [AccountGatewayError("upstream returned invalid JSON")],
            "SCHEMA_OR_HTTP_FAILURE",
        ),
        (
            "balance?",
            [AccountGatewayError("upstream response exceeds size limit")],
            "SCHEMA_OR_HTTP_FAILURE",
        ),
        ("orders?", [HttpResponse(200, {"orders": [], "cursor": ""})], "SUCCESS"),
        (
            "orders?",
            [
                HttpResponse(200, {"orders": [{}], "cursor": "next"}),
                HttpResponse(500, {}),
            ],
            "UPSTREAM_UNAVAILABLE",
        ),
        (
            "orders?",
            [
                HttpResponse(200, {"orders": [], "cursor": "same"}),
                HttpResponse(200, {"orders": [], "cursor": "same"}),
            ],
            "PAGINATION_FAILURE",
        ),
    ],
    ids=[
        "balance_401",
        "balance_403",
        "balance_redirect",
        "balance_timeout",
        "balance_malformed_json",
        "balance_oversized",
        "orders_successful_empty",
        "orders_incomplete_pagination",
        "orders_repeated_cursor",
    ],
)
def test_endpoint_failure_classification_never_becomes_empty_success(
    needle: str, override: list[HttpResponse | Exception], expected_classification: str
) -> None:
    evidence = run(account_overrides={needle: list(override)})
    target = needle.rstrip("?")
    read = next(item for item in evidence.reads if item.name == target)
    assert read.classification == expected_classification
    if expected_classification != "SUCCESS":
        assert evidence.reconciliation.classification == "BLOCKED"
    else:
        # a successful empty page must never be confused with a failure
        assert read.count == 0
        assert read.pagination_complete is True


def test_one_endpoint_failure_does_not_hide_other_successes() -> None:
    evidence = run(account_overrides={"orders?": [HttpResponse(401, {})]})
    by_name = {read.name: read.classification for read in evidence.reads}
    assert by_name["orders"] == "AUTH_FAILURE"
    assert by_name["balance"] == "SUCCESS"
    assert by_name["positions"] == "SUCCESS"
    assert by_name["fills"] == "SUCCESS"
    assert by_name["settlements"] == "SUCCESS"
    assert evidence.reconciliation.classification == "BLOCKED"
    assert evidence.reconciliation.balance_succeeded is True
    assert evidence.reconciliation.open_orders_complete is False


def test_paginated_collection_consumes_every_page() -> None:
    evidence = run(
        account_overrides={
            "fills?": [
                HttpResponse(200, {"fills": [{"a": 1}], "cursor": "p2"}),
                HttpResponse(200, {"fills": [{"a": 2}], "cursor": ""}),
            ]
        }
    )
    fills = next(item for item in evidence.reads if item.name == "fills")
    assert fills.classification == "SUCCESS"
    assert fills.count == 2
    assert fills.pagination_complete is True


# --------------------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------------------


def test_stale_evidence_fails_reconciliation_even_when_every_read_succeeds() -> None:
    # started_at, authority-check completion, then a 40s-later reads/reconciliation window
    clock = _clock_sequence(0, 40)
    evidence = run(clock=clock)
    assert evidence.candidate_authority.classification == "PASS"
    assert evidence.reconciliation.fresh is False
    assert evidence.reconciliation.classification == "FAIL"


# --------------------------------------------------------------------------------------
# Readiness report integration
# --------------------------------------------------------------------------------------


def test_readiness_report_default_blocks_without_evidence() -> None:
    statuses = operator_evidence()
    assert statuses["CANDIDATE_KEY_AUTHENTICATED_GET"][0] == "NOT TESTED"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] == "BLOCKED_BY_CREDENTIAL"
    assert statuses["ACCOUNT_RECONCILIATION"][0] == "BLOCKED_BY_CREDENTIAL"
    assert statuses["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert statuses["PRODUCTION_ARMED"][0] == "FAIL"


def test_readiness_report_partial_evidence_never_falsely_passes(tmp_path: Path) -> None:
    evidence = run(account_overrides={"orders?": [HttpResponse(401, {})]})
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["CANDIDATE_KEY_AUTHENTICATED_GET"][0] == "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] == "PASS"
    assert statuses["AUTHENTICATED_OPEN_ORDERS"][0] == "BLOCKED_BY_CREDENTIAL"
    assert statuses["ACCOUNT_RECONCILIATION"][0] == "BLOCKED_BY_CREDENTIAL"


def test_readiness_report_fresh_complete_evidence_unlocks_only_supported_gates(
    tmp_path: Path,
) -> None:
    evidence = run()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    for gate in (
        "CANDIDATE_KEY_AUTHENTICATED_GET",
        "AUTHENTICATED_PRODUCTION_BALANCE",
        "AUTHENTICATED_OPEN_ORDERS",
        "AUTHENTICATED_POSITIONS",
        "AUTHENTICATED_FILLS",
        "AUTHENTICATED_SETTLEMENTS",
        "ACCOUNT_RECONCILIATION",
    ):
        assert statuses[gate][0] == "PASS", gate
    # These must never be flipped by read-only evidence, regardless of how complete it is.
    assert statuses["PRODUCTION_WRITE_CREDENTIAL"] == (
        "NOT INSTALLED",
        "required M27E safety state",
    )
    assert statuses["PRODUCTION_ARMED"] == ("FAIL", "DISARMED is required in this milestone")
    assert statuses["REAL_MUTATION"][0] == "NOT TESTED"
    assert statuses["REAL_SIGNER_VALIDATION"][0] == "BLOCKED_BY_CREDENTIAL"


def test_readiness_report_stale_evidence_never_passes_reconciliation(tmp_path: Path) -> None:
    """Creation-time staleness (``completed_at - started_at`` > 30s) still blocks reconciliation.

    Consumed at (exactly) the moment of creation so this isolates the pre-existing
    creation-time freshness rule from the separate consumption-time rule covered below.
    """
    clock = _clock_sequence(0, 40)
    evidence = run(clock=clock)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] == "PASS"
    assert statuses["ACCOUNT_RECONCILIATION"][0] == "BLOCKED_BY_CREDENTIAL"


# --------------------------------------------------------------------------------------
# Consumption-time freshness -- loading a stored artifact later must re-check staleness,
# independently of whether the sweep itself was quick when it was created.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta_seconds",
    [0, 29, 30],
    ids=["consumed_immediately", "consumed_29s_later", "consumed_exactly_30s_later"],
)
def test_readiness_report_consumption_within_bound_unlocks_gates(
    tmp_path: Path, delta_seconds: float
) -> None:
    evidence = run()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    now = evidence.completed_at + timedelta(seconds=delta_seconds)
    statuses = operator_evidence(live_read_evidence=path, now=now)
    assert statuses["CANDIDATE_KEY_AUTHENTICATED_GET"][0] == "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] == "PASS"
    assert statuses["AUTHENTICATED_OPEN_ORDERS"][0] == "PASS"
    assert statuses["AUTHENTICATED_POSITIONS"][0] == "PASS"
    assert statuses["AUTHENTICATED_FILLS"][0] == "PASS"
    assert statuses["ACCOUNT_RECONCILIATION"][0] == "PASS"


@pytest.mark.parametrize(
    "now_factory",
    [
        lambda completed_at: completed_at + timedelta(seconds=30, milliseconds=1),
        lambda completed_at: completed_at + timedelta(minutes=5),
        lambda completed_at: completed_at - timedelta(seconds=1),
    ],
    ids=["consumed_30.001s_later", "consumed_5min_later", "future_completed_at"],
)
def test_readiness_report_consumption_outside_bound_blocks_gates(
    tmp_path: Path, now_factory: Any
) -> None:
    evidence = run()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    now = now_factory(evidence.completed_at)
    statuses = operator_evidence(live_read_evidence=path, now=now)
    assert statuses["CANDIDATE_KEY_AUTHENTICATED_GET"][0] != "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] != "PASS"
    assert statuses["AUTHENTICATED_OPEN_ORDERS"][0] != "PASS"
    assert statuses["AUTHENTICATED_POSITIONS"][0] != "PASS"
    assert statuses["AUTHENTICATED_FILLS"][0] != "PASS"
    assert statuses["ACCOUNT_RECONCILIATION"][0] != "PASS"


def test_readiness_report_missing_completed_at_fails_closed(tmp_path: Path) -> None:
    evidence = run()
    payload = evidence.to_json()
    del payload["completed_at"]
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["ACCOUNT_RECONCILIATION"][0] != "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] != "PASS"


@pytest.mark.parametrize(
    "malformed",
    ["not-a-timestamp", "2026-08-18T12:00:40", "2026-08-18", 12345, None],
    ids=["garbage_string", "naive_timestamp", "date_only_naive", "numeric", "null"],
)
def test_readiness_report_malformed_completed_at_fails_closed(
    tmp_path: Path, malformed: Any
) -> None:
    evidence = run()
    payload = evidence.to_json()
    payload["completed_at"] = malformed
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["ACCOUNT_RECONCILIATION"][0] != "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] != "PASS"


def test_readiness_report_creation_time_stale_artifact_still_fails_even_when_consumed_fresh(
    tmp_path: Path,
) -> None:
    """A sweep that itself took >30s must still fail, even consumed at the same instant."""
    clock = _clock_sequence(0, 40)
    evidence = run(clock=clock)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["ACCOUNT_RECONCILIATION"][0] != "PASS"


def test_readiness_report_partial_failed_artifact_never_passes_even_when_fresh(
    tmp_path: Path,
) -> None:
    """Freshness alone must never manufacture a PASS a failed read did not earn."""
    evidence = run(account_overrides={"orders?": [HttpResponse(401, {})]})
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence.to_json()))
    statuses = operator_evidence(live_read_evidence=path, now=evidence.completed_at)
    assert statuses["AUTHENTICATED_OPEN_ORDERS"][0] != "PASS"
    assert statuses["ACCOUNT_RECONCILIATION"][0] != "PASS"


# --------------------------------------------------------------------------------------
# CLI: private key handling
# --------------------------------------------------------------------------------------


def test_cli_reads_key_only_from_file_and_fd_never_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("candidate\n")
    output = tmp_path / "evidence.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"synthetic-pem-bytes")
    os.close(write_fd)

    calls: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> m27f.LiveReadAcceptanceEvidence:
        calls.update(kwargs)
        return run()

    monkeypatch.setattr(m27f, "run_live_read_acceptance", fake_run)
    exit_code = m27f.main(
        [
            "--key-id-file",
            str(key_id_file),
            "--private-key-fd",
            str(read_fd),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert calls["key_id"] == "candidate"
    assert calls["private_key_pem"] == b"synthetic-pem-bytes"
    written = json.loads(output.read_text())
    assert "synthetic-pem-bytes" not in json.dumps(written)
    captured = capsys.readouterr()
    assert "synthetic-pem-bytes" not in captured.out
    assert "PRODUCTION_WRITE_CREDENTIAL: NOT INSTALLED" in captured.out
    os.close(read_fd)


def test_cli_fd_is_fully_consumed_leaving_a_clean_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fd is drained to EOF, matching the reused M25 ``read_private_key_fd`` contract.

    Neither that helper nor this CLI closes the descriptor itself -- ownership belongs to
    the shell's ``3< file`` redirection, which closes it when the process exits. What must
    hold here is that every byte was consumed (no partial read left dangling) and reading
    again cleanly returns EOF rather than blocking or erroring.
    """
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("candidate")
    output = tmp_path / "evidence.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    monkeypatch.setattr(m27f, "run_live_read_acceptance", lambda **kwargs: run())
    exit_code = m27f.main(
        [
            "--key-id-file",
            str(key_id_file),
            "--private-key-fd",
            str(read_fd),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert os.read(read_fd, 1) == b""
    os.close(read_fd)


def test_cli_empty_key_id_file_fails_closed_and_writes_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("   \n")
    output = tmp_path / "evidence.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    exit_code = m27f.main(
        [
            "--key-id-file",
            str(key_id_file),
            "--private-key-fd",
            str(read_fd),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    assert not output.exists()
    captured = capsys.readouterr()
    assert "BLOCKER" in captured.err
    assert "pem-bytes" not in captured.err
    os.close(read_fd)


def test_cli_authority_failure_writes_sanitized_failing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("candidate")
    output = tmp_path / "evidence.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    failing = run(authority_reply=api_keys_reply([VALID_KEY_RECORD], status=401))
    monkeypatch.setattr(m27f, "run_live_read_acceptance", lambda **kwargs: failing)
    exit_code = m27f.main(
        [
            "--key-id-file",
            str(key_id_file),
            "--private-key-fd",
            str(read_fd),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    written = json.loads(output.read_text())
    assert written["candidate_authority"]["classification"] == "FAIL"
    assert written["reads"] == []
    os.close(read_fd)
