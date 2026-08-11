"""WSGI entry point; bind privately behind Caddy."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, cast
from wsgiref.simple_server import make_server

from .app import DashboardApp
from .security import SecretBox
from .setup import SetupService, Validator
from .store import StateStore


def main() -> None:
    state_dir = Path(os.environ.get("KPV3_STATE_DIR", "/var/lib/kalshi-v3"))
    key = (state_dir / "vault.key").read_bytes()
    store, box = StateStore(state_dir / "m1.sqlite3"), SecretBox(key)
    if store.config("setup_token_hash") is None:
        token = (state_dir / "setup.token").read_text().strip()
        store.set_config("setup_token_hash", hashlib.sha256(token.encode()).hexdigest())

    class LiveValidator(Validator):
        def validate(self, signer: Any, key_id: str) -> Any:
            from services.kalshi_account_gateway.client import (
                KalshiAccountClient,
                UrllibReadTransport,
            )

            return KalshiAccountClient(signer, UrllibReadTransport()).refresh(key_id)

    app = DashboardApp(
        store, box, SetupService(store, LiveValidator(), box, state_dir / "setup.token")
    )
    with make_server("0.0.0.0", 8000, cast(Any, app)) as server:  # noqa: S104
        server.serve_forever()


if __name__ == "__main__":
    main()
