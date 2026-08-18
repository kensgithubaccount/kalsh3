from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    ProductionReadReply,
    UrllibProductionReadTransport,
)
from services.supervised_canary import authority_attestation as attestation_mod
from services.supervised_canary.authority_attestation import (
    generate_candidate_authority_attestation,
    validate_attestation_for_candidate,
)


class FakeSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class FakeManagementTransport:
    """Fake ``GET /trade-api/v2/api_keys`` transport; records exactly what was called."""

    def __init__(self, reply: ProductionReadReply | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, Mapping[str, str]]] = []

    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> ProductionReadReply:
        self.calls.append((origin, path, dict(headers)))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def api_keys_reply(records: Any, *, status: int = 200) -> ProductionReadReply:
    return ProductionReadReply(status, json.dumps({"api_keys": records}).encode())


VALID_CANDIDATE_RECORD = {
    "api_key_id": "candidate-key-id",
    "scopes": ["read", "write::trade"],
    "subaccount": 0,
}

FIXED_CLOCK = lambda: datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)  # noqa: E731


def generate(
    reply: ProductionReadReply | Exception,
    *,
    candidate_key_id: str = "candidate-key-id",
) -> attestation_mod.CandidateAuthorityAttestation:
    return generate_candidate_authority_attestation(
        management_key_id="bootstrap-management-key",
        management_private_key_pem=b"synthetic-management-pem-not-real",
        candidate_key_id=candidate_key_id,
        transport=FakeManagementTransport(reply),
        timestamp_ms=123,
        clock=FIXED_CLOCK,
        signer_factory=FakeSigner,
    )


# --------------------------------------------------------------------------------------
# 1. Happy path
# --------------------------------------------------------------------------------------


def test_unique_exact_candidate_match_produces_pass_artifact() -> None:
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    assert attestation.classification == "PASS"
    assert attestation.schema == "kalsh3.m27f.candidate-authority.v1"
    assert attestation.environment == "PRODUCTION"
    assert attestation.source_origin == PRODUCTION_ORIGIN
    assert attestation.source_path == API_KEYS_PATH
    assert attestation.server_scopes == ("read", "write::trade")
    assert attestation.server_subaccount == 0
    assert attestation.unique_matches == 1
    assert attestation.key_id_hash == hashlib.sha256(b"candidate-key-id").hexdigest()
    assert attestation.reason is None


# --------------------------------------------------------------------------------------
# 2-8. Adversarial matrix
# --------------------------------------------------------------------------------------


def test_zero_candidate_matches_fails() -> None:
    attestation = generate(api_keys_reply([{"api_key_id": "someone-else", "scopes": ["read"]}]))
    assert attestation.classification == "FAIL"
    assert attestation.unique_matches == 0


def test_duplicate_candidate_matches_fails() -> None:
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD, VALID_CANDIDATE_RECORD]))
    assert attestation.classification == "FAIL"
    assert attestation.unique_matches == 2


@pytest.mark.parametrize(
    "scopes",
    [["read"], ["read", "write"], ["read", "write::trade", "write::transfer"]],
    ids=["missing_write_trade", "broad_write", "extra_scope"],
)
def test_wrong_scopes_fails(scopes: list[str]) -> None:
    record = {**VALID_CANDIDATE_RECORD, "scopes": scopes}
    attestation = generate(api_keys_reply([record]))
    assert attestation.classification == "FAIL"
    assert attestation.unique_matches == 1


@pytest.mark.parametrize("subaccount", [None, 1, 63], ids=["null", "one", "max_nonzero"])
def test_wrong_subaccount_fails(subaccount: int | None) -> None:
    record = {**VALID_CANDIDATE_RECORD, "subaccount": subaccount}
    attestation = generate(api_keys_reply([record]))
    assert attestation.classification == "FAIL"
    assert attestation.unique_matches == 1


@pytest.mark.parametrize("status", [401, 403], ids=["http_401", "http_403"])
def test_management_authentication_failure_fails_closed(status: int) -> None:
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD], status=status))
    assert attestation.classification == "FAIL"
    assert attestation.server_scopes is None
    assert attestation.unique_matches == 0


def test_redirect_fails_closed() -> None:
    attestation = generate(ProductionReadReply(302, b"", location="https://attacker.example"))
    assert attestation.classification == "FAIL"


def test_transport_exception_fails_closed() -> None:
    attestation = generate(TimeoutError("synthetic timeout"))
    assert attestation.classification == "FAIL"


@pytest.mark.parametrize(
    "reply",
    [
        ProductionReadReply(200, b"not-json"),
        ProductionReadReply(200, json.dumps({"not_api_keys": []}).encode()),
        ProductionReadReply(200, json.dumps({"api_keys": "not-a-list"}).encode()),
        ProductionReadReply(200, json.dumps({"api_keys": ["not-a-dict"]}).encode()),
        ProductionReadReply(200, b'{"api_keys": []}', content_type="text/plain"),
    ],
    ids=[
        "malformed_json",
        "missing_api_keys_key",
        "api_keys_not_a_list",
        "record_not_a_dict",
        "wrong_content_type",
    ],
)
def test_malformed_response_fails_closed(reply: ProductionReadReply) -> None:
    attestation = generate(reply)
    assert attestation.classification == "FAIL"


# --------------------------------------------------------------------------------------
# 9. Only GET is structurally possible
# --------------------------------------------------------------------------------------


def test_transport_protocol_is_get_only() -> None:
    """The reused production transport (and the fake used above) expose only ``get``."""
    real_transport = UrllibProductionReadTransport()
    for mutating in ("post", "put", "patch", "delete"):
        assert not hasattr(real_transport, mutating)
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    assert attestation.classification == "PASS"


def test_generator_calls_transport_get_exactly_once() -> None:
    transport = FakeManagementTransport(api_keys_reply([VALID_CANDIDATE_RECORD]))
    generate_candidate_authority_attestation(
        management_key_id="bootstrap-management-key",
        management_private_key_pem=b"synthetic-management-pem-not-real",
        candidate_key_id="candidate-key-id",
        transport=transport,
        timestamp_ms=123,
        clock=FIXED_CLOCK,
        signer_factory=FakeSigner,
    )
    expected_headers = {"synthetic-auth": "bootstrap-management-key:123:GET:/trade-api/v2/api_keys"}
    assert transport.calls == [(PRODUCTION_ORIGIN, API_KEYS_PATH, expected_headers)]


# --------------------------------------------------------------------------------------
# 10. Secret material absent from output/exceptions
# --------------------------------------------------------------------------------------


def test_attestation_json_is_secret_free() -> None:
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    dumped = json.dumps(attestation.to_json())
    assert "bootstrap-management-key" not in dumped
    assert "synthetic-management-pem-not-real" not in dumped
    assert "candidate-key-id" not in dumped
    assert "PRIVATE KEY" not in dumped


def test_failure_reasons_are_secret_free() -> None:
    for attestation in (
        generate(api_keys_reply([VALID_CANDIDATE_RECORD], status=401)),
        generate(TimeoutError("synthetic timeout")),
        generate(ProductionReadReply(200, b"not-json")),
    ):
        assert attestation.reason is not None
        assert "bootstrap-management-key" not in attestation.reason
        assert "synthetic-management-pem-not-real" not in attestation.reason
        assert "candidate-key-id" not in attestation.reason


# --------------------------------------------------------------------------------------
# Consumer-side independent re-validation (defense in depth: the artifact's own
# classification is never merely trusted).
# --------------------------------------------------------------------------------------


def test_validate_accepts_a_genuinely_passing_attestation() -> None:
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    validation = validate_attestation_for_candidate(
        attestation.to_json(), candidate_key_id="candidate-key-id"
    )
    assert validation.classification == "PASS"


def test_validate_rejects_attestation_bound_to_a_different_key_id() -> None:
    """An attestation remains valid only for the exact key ID hash it names.

    Deletion/replacement of the underlying key cannot silently transfer authority to a
    different key ID: validating the same artifact against any other candidate key ID must
    fail, because the hash comparison is exact.
    """
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    validation = validate_attestation_for_candidate(
        attestation.to_json(), candidate_key_id="a-different-key-id"
    )
    assert validation.classification == "FAIL"


def test_validate_rejects_tampered_classification() -> None:
    payload = generate(api_keys_reply([VALID_CANDIDATE_RECORD])).to_json()
    payload["classification"] = "PASS"
    payload["candidate"]["unique_matches"] = 2  # tampered: claims a duplicate match too
    validation = validate_attestation_for_candidate(payload, candidate_key_id="candidate-key-id")
    assert validation.classification == "FAIL"


def test_validate_applies_no_time_based_expiry() -> None:
    """No TTL is invented: an attestation with an arbitrarily old ``observed_at`` still
    validates, because validity is scoped to the exact candidate key ID hash, not to time.
    """
    attestation = generate(api_keys_reply([VALID_CANDIDATE_RECORD]))
    payload = attestation.to_json()
    ancient = (datetime(2020, 1, 1, tzinfo=UTC)).isoformat()
    payload["observed_at"] = ancient
    validation = validate_attestation_for_candidate(payload, candidate_key_id="candidate-key-id")
    assert validation.classification == "PASS"


# --------------------------------------------------------------------------------------
# CLI: management credential handling
# --------------------------------------------------------------------------------------


def test_cli_never_touches_a_candidate_private_key() -> None:
    """The generator CLI has no flag through which a candidate private key could flow.

    Only ``--candidate-key-id-file`` (a non-secret identifier) names the candidate at all;
    the only private-key-shaped input is the management credential's inherited fd.
    """
    option_strings = {
        option for action in attestation_mod._parser()._actions for option in action.option_strings
    }
    assert option_strings == {
        "-h",
        "--help",
        "--management-key-id-file",
        "--management-private-key-fd",
        "--candidate-key-id-file",
        "--output",
    }
    assert not any("candidate" in option and "key-id" not in option for option in option_strings)


def test_cli_reads_management_key_only_from_file_and_fd_never_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    management_key_id_file = tmp_path / "management_key_id.txt"
    management_key_id_file.write_text("bootstrap-management-key\n")
    candidate_key_id_file = tmp_path / "candidate_key_id.txt"
    candidate_key_id_file.write_text("candidate-key-id\n")
    output = tmp_path / "attestation.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"synthetic-management-pem-bytes")
    os.close(write_fd)

    calls: dict[str, Any] = {}

    def fake_generate(**kwargs: Any) -> attestation_mod.CandidateAuthorityAttestation:
        calls.update(kwargs)
        return generate(api_keys_reply([VALID_CANDIDATE_RECORD]))

    monkeypatch.setattr(attestation_mod, "generate_candidate_authority_attestation", fake_generate)
    exit_code = attestation_mod.main(
        [
            "--management-key-id-file",
            str(management_key_id_file),
            "--management-private-key-fd",
            str(read_fd),
            "--candidate-key-id-file",
            str(candidate_key_id_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert calls["management_key_id"] == "bootstrap-management-key"
    assert calls["management_private_key_pem"] == b"synthetic-management-pem-bytes"
    assert calls["candidate_key_id"] == "candidate-key-id"
    written = json.loads(output.read_text())
    dumped = json.dumps(written)
    assert "synthetic-management-pem-bytes" not in dumped
    assert "bootstrap-management-key" not in dumped
    assert "candidate-key-id" not in dumped
    captured = capsys.readouterr()
    assert "synthetic-management-pem-bytes" not in captured.out
    assert "candidate_authority_attestation=PASS" in captured.out
    os.close(read_fd)


def test_cli_empty_management_key_id_fails_closed_and_writes_no_output(tmp_path: Path) -> None:
    management_key_id_file = tmp_path / "management_key_id.txt"
    management_key_id_file.write_text("  \n")
    candidate_key_id_file = tmp_path / "candidate_key_id.txt"
    candidate_key_id_file.write_text("candidate-key-id")
    output = tmp_path / "attestation.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    exit_code = attestation_mod.main(
        [
            "--management-key-id-file",
            str(management_key_id_file),
            "--management-private-key-fd",
            str(read_fd),
            "--candidate-key-id-file",
            str(candidate_key_id_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    assert not output.exists()
    os.close(read_fd)


def test_cli_failure_writes_sanitized_failing_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    management_key_id_file = tmp_path / "management_key_id.txt"
    management_key_id_file.write_text("bootstrap-management-key")
    candidate_key_id_file = tmp_path / "candidate_key_id.txt"
    candidate_key_id_file.write_text("candidate-key-id")
    output = tmp_path / "attestation.json"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"pem-bytes")
    os.close(write_fd)
    failing = generate(api_keys_reply([VALID_CANDIDATE_RECORD], status=401))
    monkeypatch.setattr(
        attestation_mod, "generate_candidate_authority_attestation", lambda **kwargs: failing
    )
    exit_code = attestation_mod.main(
        [
            "--management-key-id-file",
            str(management_key_id_file),
            "--management-private-key-fd",
            str(read_fd),
            "--candidate-key-id-file",
            str(candidate_key_id_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2
    written = json.loads(output.read_text())
    assert written["classification"] == "FAIL"
    os.close(read_fd)
