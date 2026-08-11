"""One-use first-run setup transaction; credentials persist only after validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.models import AccountSnapshot

from .security import SecretBox, hash_password, recovery_codes
from .store import StateStore


class SetupError(ValueError):
    pass


class Validator:
    def validate(self, signer: RequestSigner, key_id: str) -> AccountSnapshot:
        raise NotImplementedError


@dataclass(slots=True)
class SetupService:
    store: StateStore
    validator: Validator
    box: SecretBox = field(repr=False)
    setup_token_path: Path | None = field(default=None, repr=False)

    def complete(
        self,
        *,
        setup_token: str,
        username: str,
        password: str,
        totp_secret: str,
        totp_code_valid: bool,
        key_id: str,
        private_key_pem: bytes,
    ) -> tuple[str, ...]:
        expected_hash = self.store.config("setup_token_hash")
        actual_hash = hashlib.sha256(setup_token.encode()).hexdigest()
        if expected_hash is None or not hmac.compare_digest(expected_hash, actual_hash):
            raise SetupError("setup token is invalid or already used")
        if self.store.configured() or not totp_code_valid or not username.strip():
            raise SetupError("owner or TOTP enrollment is invalid")
        password_hash = hash_password(password)
        signer = RequestSigner(key_id, private_key_pem)
        snapshot = self.validator.validate(signer, key_id)
        if snapshot.subaccount != 0 or not snapshot.reconciled:
            raise SetupError("primary account 0 could not be reconciled")
        clear_codes, code_hashes = recovery_codes()
        vault = self.box.seal(
            json.dumps({"key_id": key_id, "pem": private_key_pem.decode()}).encode()
        )
        self.store.set_config("owner", username.strip())
        self.store.set_config("password_hash", password_hash)
        self.store.set_config("totp_secret", self.box.seal(totp_secret.encode()))
        self.store.set_config("recovery_hashes", json.dumps(code_hashes))
        self.store.set_config("vault", vault)
        self.store.set_config("setup_token_hash", "used")
        if self.setup_token_path is not None:
            self.setup_token_path.unlink(missing_ok=True)
        self.store.audit("setup_completed", username.strip(), "read-only credential validated")
        return clear_codes
