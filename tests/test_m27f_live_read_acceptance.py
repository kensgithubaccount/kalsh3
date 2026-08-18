from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.client import AccountGatewayError, HttpResponse
from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    ProductionReadReply,
)
from services.supervised_canary import authority_attestation as attestation_mod
from services.supervised_canary import live_read_acceptance as m27f
from services.supervised_canary.readiness_report import operator_evidence


class FakeSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class FakeAuthorityTransport:
    """Fake ``GET /trade-api/v2/api_keys`` boundary transport (management side only)."""

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


def build_attestation(
    *,
    candidate_key_id: str = "candidate",
    schema: str = attestation_mod.SCHEMA,
    software_version: str | None = attestation_mod.SOFTWARE_VERSION,
    environment: str = "PRODUCTION",
    observed_at: str | None = "2026-08-18T12:00:00+00:00",
    source_origin: str = PRODUCTION_ORIGIN,
    source_path: str = API_KEYS_PATH,
    classification: str = "PASS",
    key_id_hash: str | None = None,
    scopes: list[str] | None = ("read", "write::trade"),
    subaccount: int | None = 0,
    unique_matches: int | None = 1,
) -> dict[str, Any]:
    """A valid-by-default candidate authority attestation payload for the M27F consumer.

    Every keyword lets a single test knock exactly one field off the happy path.
    """
    if key_id_hash is None:
        key_id_hash = hashlib.sha256(candidate_key_id.encode()).hexdigest()
    return {
        "schema": schema,
        "software_version": software_version,
        "environment": environment,
        "observed_at": observed_at,
        "source": {"origin": source_origin, "path": source_path},
        "classification": classification,
        "candidate": {
            "key_id_hash": key_id_hash,
            "server_scopes": list(scopes) if scopes is not None else None,
            "server_subaccount": subaccount,
            "unique_matches": unique_matches,
        },
        "reason": None,
    }


# Captured once, before any test monkeypatches ``m27f.run_live_read_acceptance`` --
# this helper must always call the real implementation, never whatever the module
# attribute currently points at, or a monkeypatched fake calling ``run()`` would recurse
# into itself.
_real_run_live_read_acceptance = m27f.run_live_read_acceptance


def run(
    *,
    authority_attestation: Any = "__default__",
    account_overrides: dict[str, list[HttpResponse | Exception]] | None = None,
    clock: Any = None,
) -> m27f.LiveReadAcceptanceEvidence:
    if authority_attestation == "__default__":
        authority_attestation = build_attestation()
    kwargs: dict[str, Any] = {
        "key_id": "candidate",
        "private_key_pem": b"synthetic-pem-not-real",
        "authority_attestation": authority_attestation,
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
    assert evidence.candidate_authority.source == "EXTERNAL_SERVER_ATTESTATION"
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
# Candidate authority attestation adversarial matrix (M27F consumer side) -- a stored
# attestation is never merely trusted; every field is independently re-checked, and no
# read may ever be attempted unless every check passes.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attestation",
    [
        None,
        "not-a-dict",
        build_attestation(schema="kalsh3.m27f.candidate-authority.v0"),
        build_attestation(environment="SANDBOX"),
        build_attestation(source_origin="https://attacker.example"),
        build_attestation(source_path="/trade-api/v2/other"),
        build_attestation(key_id_hash="0" * 64),
        build_attestation(scopes=["read"]),
        build_attestation(scopes=["read", "write"]),
        build_attestation(scopes=["read", "write::trade", "write::transfer"]),
        build_attestation(subaccount=None),
        build_attestation(subaccount=1),
        build_attestation(unique_matches=0),
        build_attestation(unique_matches=2),
        build_attestation(classification="FAIL"),
        build_attestation(software_version=None),
        build_attestation(observed_at=None),
    ],
    ids=[
        "missing_attestation",
        "malformed_not_a_dict",
        "wrong_schema",
        "wrong_environment",
        "wrong_source_origin",
        "wrong_source_path",
        "wrong_candidate_key_id_hash",
        "missing_write_trade",
        "broad_write",
        "extra_scope",
        "null_subaccount",
        "wrong_subaccount",
        "zero_matches",
        "duplicate_matches",
        "attestation_itself_failed",
        "malformed_software_version",
        "malformed_observed_at",
    ],
)
def test_invalid_attestation_never_reaches_account_reads(attestation: Any) -> None:
    evidence = run(authority_attestation=attestation)
    assert evidence.candidate_authority.classification == "FAIL"
    assert evidence.reads == ()
    assert evidence.reconciliation.classification == "BLOCKED"


def test_valid_attestation_metadata_is_recorded_on_pass() -> None:
    evidence = run()
    assert evidence.candidate_authority.reason is None
    assert evidence.candidate_authority.key_id_hash != "candidate"


def test_candidate_balance_failure_still_fails_even_with_valid_attestation() -> None:
    evidence = run(account_overrides={"balance?": [HttpResponse(401, {})]})
    assert evidence.candidate_authority.classification == "PASS"
    balance = next(item for item in evidence.reads if item.name == "balance")
    assert balance.classification == "AUTH_FAILURE"
    assert evidence.reconciliation.classification == "BLOCKED"


def test_candidate_no_longer_calls_api_keys() -> None:
    """Structural regression: the consumer has no transport capable of calling /api_keys."""
    parameters = inspect.signature(m27f.run_live_read_acceptance).parameters
    assert "authority_transport" not in parameters
    assert "authority_attestation" in parameters
    account_transport = FakeAccountTransport()
    evidence = _real_run_live_read_acceptance(
        key_id="candidate",
        private_key_pem=b"synthetic-pem-not-real",
        authority_attestation=build_attestation(),
        account_transport=account_transport,
        signer_factory=FakeSigner,
        clock_ms=lambda: 123,
    )
    assert evidence.reconciliation.classification == "PASS"
    assert all(API_KEYS_PATH not in path for path in account_transport.paths)


# --------------------------------------------------------------------------------------
# Regression matching the real M27F live discovery: a broad management credential can
# list /api_keys and produce a PASS attestation, the least-privilege candidate itself
# would be rejected (401) if it tried the same call, and M27F still passes end-to-end
# using only the attestation plus the candidate's own permitted account GETs.
# --------------------------------------------------------------------------------------


def test_regression_candidate_401_on_api_keys_but_passes_via_management_attestation() -> None:
    management_transport = FakeAuthorityTransport(api_keys_reply([VALID_KEY_RECORD]))
    attestation = attestation_mod.generate_candidate_authority_attestation(
        management_key_id="bootstrap-management-key",
        management_private_key_pem=b"synthetic-management-pem",
        candidate_key_id="candidate",
        transport=management_transport,
        timestamp_ms=123,
        clock=lambda: datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC),
        signer_factory=FakeSigner,
    )
    assert attestation.classification == "PASS"
    assert management_transport.calls == [(PRODUCTION_ORIGIN, API_KEYS_PATH)]

    # Real discovery: the candidate itself receives HTTP 401 from the same endpoint.
    candidate_transport = FakeAuthorityTransport(api_keys_reply([VALID_KEY_RECORD], status=401))
    candidate_reply = candidate_transport.get(
        PRODUCTION_ORIGIN, API_KEYS_PATH, {}, timeout_seconds=10
    )
    assert candidate_reply.status == 401

    evidence = run(authority_attestation=attestation.to_json())
    assert evidence.candidate_authority.classification == "PASS"
    assert evidence.candidate_authority.source == "EXTERNAL_SERVER_ATTESTATION"
    assert evidence.reconciliation.classification == "PASS"


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
# independently of whether the sweep itself was quick when it was created. This bound
# applies to the M27F account-read evidence itself, not to the authority attestation (see
# the authority-attestation lifetime tests in test_m27f_candidate_authority_attestation.py).
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


def test_readiness_report_and_final_evidence_never_reveal_production_gates_prematurely() -> None:
    """Production installation/arming/mutation gates are never touched by read evidence."""
    evidence = run()
    dumped = json.dumps(evidence.to_json())
    assert "PRODUCTION_WRITE_CREDENTIAL" not in dumped
    statuses = operator_evidence()
    assert statuses["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert statuses["PRODUCTION_ARMED"][0] == "FAIL"
    assert statuses["REAL_MUTATION"][0] == "NOT TESTED"


# --------------------------------------------------------------------------------------
# CLI: private key handling
# --------------------------------------------------------------------------------------


def _write_attestation(tmp_path: Path) -> Path:
    path = tmp_path / "attestation.json"
    path.write_text(json.dumps(build_attestation()))
    return path


def test_cli_reads_key_only_from_file_and_fd_never_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("candidate\n")
    attestation_file = _write_attestation(tmp_path)
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
            "--authority-attestation",
            str(attestation_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert calls["key_id"] == "candidate"
    assert calls["private_key_pem"] == b"synthetic-pem-bytes"
    assert calls["authority_attestation"] == build_attestation()
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
    attestation_file = _write_attestation(tmp_path)
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
            "--authority-attestation",
            str(attestation_file),
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
    attestation_file = _write_attestation(tmp_path)
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
            "--authority-attestation",
            str(attestation_file),
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
    attestation_file = _write_attestation(tmp_path)
    output = tmp_path / "evidence.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    failing = run(authority_attestation=build_attestation(classification="FAIL"))
    monkeypatch.setattr(m27f, "run_live_read_acceptance", lambda **kwargs: failing)
    exit_code = m27f.main(
        [
            "--key-id-file",
            str(key_id_file),
            "--private-key-fd",
            str(read_fd),
            "--authority-attestation",
            str(attestation_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    written = json.loads(output.read_text())
    assert written["candidate_authority"]["classification"] == "FAIL"
    assert written["reads"] == []
    os.close(read_fd)


def test_cli_malformed_attestation_json_fails_closed(tmp_path: Path) -> None:
    key_id_file = tmp_path / "key_id.txt"
    key_id_file.write_text("candidate")
    attestation_file = tmp_path / "attestation.json"
    attestation_file.write_text("not-json")
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
            "--authority-attestation",
            str(attestation_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    assert not output.exists()
    os.close(read_fd)
