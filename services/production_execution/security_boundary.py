"""Isolated sign-and-send boundary. Raw signatures never leave this module."""

from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from services.risk_engine.authorization import RiskAuthorization

from .credentials import ProductionWriteCredential
from .domain import PRODUCTION_ORIGIN, ProductionArmState, ProductionRequestEnvelope, digest
from .store import ProductionJournal


class Clock(Protocol):
    def now(self) -> datetime: ...


class ExactByteSender(Protocol):
    offline_fixture: bool

    def send_exact(
        self,
        *,
        origin: str,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
        verify_tls: bool,
        follow_redirects: bool,
        timeout_seconds: int,
        maximum_response_bytes: int,
    ) -> tuple[int, bytes]: ...


@dataclass(frozen=True, slots=True)
class Preflight:
    intent_hash: str
    portfolio_hash: str
    reconciliation_hash: str
    rules_current: bool
    market_active: bool
    market_not_paused: bool
    reconciliation_fresh: bool
    global_halt_clear: bool
    compliance_clear: bool
    kills_clear: bool
    order_group_ready: bool
    client_order_id_unique: bool

    @property
    def valid(self) -> bool:
        return all(
            (
                self.rules_current,
                self.market_active,
                self.market_not_paused,
                self.reconciliation_fresh,
                self.global_halt_clear,
                self.compliance_clear,
                self.kills_clear,
                self.order_group_ready,
                self.client_order_id_unique,
            )
        )


@dataclass(frozen=True, slots=True)
class NormalizedOutcome:
    state: str
    status: int | None
    reconciliation_required: bool


class BoundaryError(PermissionError):
    pass


class SignAndSendBoundary:
    version = "m15-sign-and-send-v1"

    def __init__(self, journal: ProductionJournal, clock: Clock) -> None:
        self.journal = journal
        self.clock = clock
        self._last_timestamp_ms = 0

    def production_execute(self, envelope: ProductionRequestEnvelope) -> NormalizedOutcome:
        del envelope
        raise BoundaryError("production state is DISARMED; live transport is unreachable")

    def offline_fixture_execute(
        self,
        *,
        envelope: ProductionRequestEnvelope,
        authorization: RiskAuthorization,
        preflight: Preflight,
        credential: ProductionWriteCredential,
        sender: ExactByteSender,
    ) -> NormalizedOutcome:
        """Exercises exact architecture only with an explicitly synthetic sender and key."""
        now = self.clock.now().astimezone(UTC)
        if (
            not isinstance(credential, ProductionWriteCredential)
            or not credential.fixture_only
            or not sender.offline_fixture
        ):
            raise BoundaryError("M15 permits only ephemeral offline signer fixtures")
        self._validate(envelope, authorization, preflight, now)
        if not self.journal.claim(envelope, version=self.version):
            raise BoundaryError("single-use production claim already exists")
        timestamp_ms = self._timestamp_ms(now)
        message = (
            f"{timestamp_ms}{envelope.method.upper()}{envelope.path.split('?', 1)[0]}".encode()
        )
        signature = _rsa_pss_sha256(credential.private_key_pem, message)
        headers = {
            "KALSHI-ACCESS-KEY": credential.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }
        self.journal.transition(envelope.execution_id, "BOUNDARY_ENTERED", possibly_sent=True)
        try:
            status, response = sender.send_exact(
                origin=PRODUCTION_ORIGIN,
                method=envelope.method,
                path=envelope.path,
                body=envelope.canonical_body,
                headers=headers,
                verify_tls=True,
                follow_redirects=False,
                timeout_seconds=3,
                maximum_response_bytes=1_000_000,
            )
        except TimeoutError:
            self.journal.transition(
                envelope.execution_id, "UNKNOWN_RECONCILIATION_REQUIRED", possibly_sent=True
            )
            return NormalizedOutcome("UNKNOWN_RECONCILIATION_REQUIRED", None, True)
        finally:
            headers.clear()
        if len(response) > 1_000_000 or status == 429 or status >= 500:
            self.journal.transition(
                envelope.execution_id, "UNKNOWN_RECONCILIATION_REQUIRED", possibly_sent=True
            )
            return NormalizedOutcome("UNKNOWN_RECONCILIATION_REQUIRED", status, True)
        self.journal.transition(
            envelope.execution_id, "ACKNOWLEDGED_RECONCILIATION_REQUIRED", possibly_sent=True
        )
        return NormalizedOutcome("ACKNOWLEDGED_RECONCILIATION_REQUIRED", status, True)

    def _validate(
        self,
        envelope: ProductionRequestEnvelope,
        authorization: RiskAuthorization,
        preflight: Preflight,
        now: datetime,
    ) -> None:
        if self.journal.state() != ProductionArmState.DISARMED:
            raise BoundaryError("invalid production state")
        if not preflight.valid or envelope.expires_at <= now or authorization.expires_at <= now:
            raise BoundaryError("stale authorization or failed last-millisecond safety state")
        if (
            authorization.intent_hash != envelope.intent_hash
            or preflight.intent_hash != envelope.intent_hash
        ):
            raise BoundaryError("INTENT_CHANGED")
        if authorization.authorization_id != envelope.risk_authorization_id:
            raise BoundaryError("M13 authorization mismatch")
        if authorization.portfolio_state_hash != preflight.portfolio_hash:
            raise BoundaryError("portfolio state changed")
        if (
            envelope.portfolio_state_hash != preflight.portfolio_hash
            or envelope.reconciliation_state_hash != preflight.reconciliation_hash
        ):
            raise BoundaryError("reconciliation state changed")
        if (
            digest(envelope.canonical_body) != envelope.body_hash
            or envelope.origin != PRODUCTION_ORIGIN
        ):
            raise BoundaryError("body or host changed")

    def _timestamp_ms(self, now: datetime) -> int:
        value = int(now.timestamp() * 1000)
        if value <= self._last_timestamp_ms:
            raise BoundaryError("signer clock regression")
        self._last_timestamp_ms = value
        return value


def _rsa_pss_sha256(private_key_pem: bytes, message: bytes) -> bytes:
    """RSA key stays in an anonymous in-memory file descriptor, never a filesystem path."""
    descriptor = os.memfd_create("m15-ephemeral-key", flags=0)
    try:
        os.write(descriptor, private_key_pem)
        os.lseek(descriptor, 0, os.SEEK_SET)
        result = subprocess.run(
            [
                "/usr/bin/openssl",
                "dgst",
                "-sha256",
                "-sigopt",
                "rsa_padding_mode:pss",
                "-sigopt",
                "rsa_pss_saltlen:digest",
                "-sign",
                f"/proc/self/fd/{descriptor}",
            ],
            input=message,
            capture_output=True,
            check=False,
            pass_fds=(descriptor,),
        )
        if result.returncode:
            raise BoundaryError("production signer cryptographic operation failed")
        return result.stdout
    finally:
        os.close(descriptor)
