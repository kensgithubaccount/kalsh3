"""M27G -- non-network real-signer self-test boundary.

Proves the installed production write-credential's private key works with the exact
RSA-PSS-SHA256 primitive the production signer (:class:`services.production_execution.
security_boundary.SignAndSendBoundary`) uses, without ever constructing a valid Kalshi
request signature, calling transport, touching the production journal, or arming production.

Kalshi's own request-signature message is exactly ``f"{timestamp_ms}{METHOD}{PATH}"`` --
digits, then an HTTP method, then an absolute path (see ``security_boundary.py`` and
``kalshi_account_gateway/auth.py``). The self-test challenge below is a fixed,
domain-separated string that can never collide with that shape: it always starts with a
non-numeric schema identifier, so even if a challenge signature were somehow replayed against
the real Kalshi API, it could not be interpreted as a valid signed request for any
method/path/timestamp -- this boundary structurally never produces a mutating-request
signature.

This module reuses ``security_boundary._rsa_pss_sha256`` unmodified rather than
reimplementing signing: that function is exactly what ``SignAndSendBoundary`` calls, so a
passing self-test proves the installed key works with the real production signing runtime,
not a parallel copy of it. ``SignAndSendBoundary.production_execute`` /
``offline_fixture_execute`` themselves are untouched by this module.
"""

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .credentials import ProductionWriteCredential
from .security_boundary import BoundaryError, _rsa_pss_sha256

SIGNER_SELF_TEST_DOMAIN = "kalsh3.m27g.signer-self-test.v1"
_OPENSSL = "/usr/bin/openssl"


def _self_test_challenge(key_id_hash: str, at: datetime) -> bytes:
    """A fixed, domain-separated, structurally non-transmittable challenge.

    Never resembles ``{timestamp_ms}{METHOD}{PATH}`` -- it begins with a non-numeric schema
    string, so it can never be parsed as a real Kalshi request signature message.
    """
    return f"{SIGNER_SELF_TEST_DOMAIN}|{key_id_hash}|{at.astimezone(UTC).isoformat()}".encode()


def _derive_public_key_pem(private_key_pem: bytes) -> bytes:
    """Derive the public key from a PKCS#8 private key PEM without touching disk."""
    result = subprocess.run(  # noqa: S603 - fixed executable and arguments
        [_OPENSSL, "pkey", "-pubout"],
        input=private_key_pem,
        capture_output=True,
        check=False,
    )
    if result.returncode or not result.stdout:
        raise BoundaryError("production signer public-key derivation failed")
    return result.stdout


def _verify_rsa_pss_sha256(public_key_pem: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a signature locally. The public key and signature are never secret."""
    with tempfile.TemporaryDirectory() as directory:
        public_key_path = Path(directory) / "self-test-pub.pem"
        signature_path = Path(directory) / "self-test-sig.bin"
        public_key_path.write_bytes(public_key_pem)
        signature_path.write_bytes(signature)
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [
                _OPENSSL,
                "dgst",
                "-sha256",
                "-sigopt",
                "rsa_padding_mode:pss",
                "-sigopt",
                "rsa_pss_saltlen:digest",
                "-verify",
                str(public_key_path),
                "-signature",
                str(signature_path),
            ],
            input=message,
            capture_output=True,
            check=False,
        )
    return result.returncode == 0


@dataclass(frozen=True, slots=True)
class SignerSelfTestResult:
    """Secret-free proof that the real signer primitive works with this exact key.

    Never carries the private key, public key, signature, or challenge bytes themselves --
    only hashes/classification, matching every other M27 evidence artifact.
    """

    classification: str
    key_id_hash: str
    challenge_domain: str
    signature_sha256: str | None
    public_key_sha256: str | None
    completed_at: datetime
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"

    def to_json(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "key_id_hash": self.key_id_hash,
            "challenge_domain": self.challenge_domain,
            "signature_sha256": self.signature_sha256,
            "public_key_sha256": self.public_key_sha256,
            "completed_at": self.completed_at.isoformat(),
            "reason": self.reason,
            "network_call": "NONE",
            "mutating_request_constructed": False,
        }


def run_real_signer_self_test(
    *, credential: ProductionWriteCredential, now: datetime
) -> SignerSelfTestResult:
    """Sign and locally verify a non-request challenge with the real production primitive.

    Never calls transport, never touches the production journal, never arms production, and
    the challenge it signs can never be replayed as a valid mutating Kalshi request (see
    module docstring).
    """
    completed_at = now.astimezone(UTC)
    key_id_hash = hashlib.sha256(credential.key_id.encode()).hexdigest()
    challenge = _self_test_challenge(key_id_hash, completed_at)
    try:
        signature = _rsa_pss_sha256(credential.private_key_pem, challenge)
        public_key_pem = _derive_public_key_pem(credential.private_key_pem)
    except BoundaryError as exc:
        return SignerSelfTestResult(
            "FAIL",
            key_id_hash,
            SIGNER_SELF_TEST_DOMAIN,
            None,
            None,
            completed_at,
            reason=str(exc),
        )
    if not _verify_rsa_pss_sha256(public_key_pem, challenge, signature):
        return SignerSelfTestResult(
            "FAIL",
            key_id_hash,
            SIGNER_SELF_TEST_DOMAIN,
            hashlib.sha256(signature).hexdigest(),
            hashlib.sha256(public_key_pem).hexdigest(),
            completed_at,
            reason="local signature verification against the derived public key failed",
        )
    return SignerSelfTestResult(
        "PASS",
        key_id_hash,
        SIGNER_SELF_TEST_DOMAIN,
        hashlib.sha256(signature).hexdigest(),
        hashlib.sha256(public_key_pem).hexdigest(),
        completed_at,
    )
