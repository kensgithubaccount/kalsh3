"""Deterministic, non-secret review artifact for M27R read-only operator runs.

The artifact is intentionally downstream of :mod:`m27r_operator_runner` and carries no
networking, credential, signer, approval, authorization, burn, sender, or exchange-mutation
capability. It serializes only the already-reduced M27R/M27Q/M27I review result.

The content hash is a deterministic mutation-detection identity over the retained review
payload. It is not a signature, not execution authority, and not proof that upstream evidence
was authentic; those claims remain owned by the reviewed validators that produced the run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .m27r_operator_runner import M27ROperatorRun

SCHEMA = "kalsh3.m27r.readonly-review-artifact.v1"
SOFTWARE_VERSION = "kalsh3.m27r.readonly-review-artifact/1"

_FORBIDDEN_KEY_FRAGMENTS: tuple[str, ...] = (
    "private_key",
    "privatekey",
    "access_token",
    "refresh_token",
    "api_secret",
    "client_secret",
    "authorization_header",
    "cookie",
    "set_cookie",
    "password",
    "pem_contents",
)

_FORBIDDEN_VALUE_MARKERS: tuple[str, ...] = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "Bearer ",
)


class M27RArtifactError(ValueError):
    """A proposed M27R review artifact violated the non-secret output contract."""


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27RArtifactError(f"{field} must be timezone-aware")


def _assert_nonsecret(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                raise M27RArtifactError(f"forbidden secret-bearing field at {path}.{key}")
            _assert_nonsecret(item, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_nonsecret(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and any(marker in value for marker in _FORBIDDEN_VALUE_MARKERS):
        raise M27RArtifactError(f"forbidden secret-like value at {path}")


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class M27RReviewArtifact:
    schema: str
    software_version: str
    created_at: datetime
    read_only: bool
    execution_authorized: bool
    result: dict[str, object]
    content_hash: str

    def __post_init__(self) -> None:
        _require_aware(self.created_at, field="artifact created_at")
        if self.schema != SCHEMA:
            raise M27RArtifactError("artifact schema mismatch")
        if self.software_version != SOFTWARE_VERSION:
            raise M27RArtifactError("artifact software version mismatch")
        if not self.read_only:
            raise M27RArtifactError("M27R review artifact must remain read-only")
        if self.execution_authorized:
            raise M27RArtifactError("M27R review artifact can never authorize execution")
        _assert_nonsecret(self.result)
        expected = _canonical_hash(self._payload_for_hash())
        if self.content_hash != expected:
            raise M27RArtifactError("artifact content hash does not match retained payload")

    def _payload_for_hash(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "created_at": self.created_at.isoformat(),
            "read_only": self.read_only,
            "execution_authorized": self.execution_authorized,
            "result": self.result,
        }

    def to_json(self) -> dict[str, Any]:
        return {**self._payload_for_hash(), "content_hash": self.content_hash}


def build_review_artifact(*, run: M27ROperatorRun, created_at: datetime) -> M27RReviewArtifact:
    """Build a deterministic, non-secret artifact from one completed M27R run."""

    _require_aware(created_at, field="artifact created_at")
    if not run.read_only or run.execution_authorized:
        raise M27RArtifactError("source M27R run violates the read-only authority boundary")
    result = run.to_json()
    _assert_nonsecret(result)
    material: dict[str, Any] = {
        "schema": SCHEMA,
        "software_version": SOFTWARE_VERSION,
        "created_at": created_at.isoformat(),
        "read_only": True,
        "execution_authorized": False,
        "result": result,
    }
    return M27RReviewArtifact(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        created_at=created_at,
        read_only=True,
        execution_authorized=False,
        result=result,
        content_hash=_canonical_hash(material),
    )


def write_review_artifact(*, artifact: M27RReviewArtifact, path: Path) -> None:
    """Persist only the non-secret review artifact; never mutate canary/shared authority state."""

    payload = artifact.to_json()
    _assert_nonsecret(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
