"""Independently runnable five-minute account refresh worker."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from services.web_dashboard.security import SecretBox
from services.web_dashboard.store import StateStore

from .auth import RequestSigner
from .client import KalshiAccountClient, UrllibReadTransport
from .models import AccountSnapshot


class Refresher(Protocol):
    def refresh(self, expected_key_id: str) -> AccountSnapshot: ...


def refresh_once(store: StateStore, client: Refresher, key_id: str) -> bool:
    store.refresh_started()
    try:
        snapshot = client.refresh(key_id)
    except Exception as exc:  # worker boundary persists failure; process remains independent
        store.refresh_failed(type(exc).__name__)
        store.audit("account_refresh_failed", "worker", type(exc).__name__)
        return False
    store.refresh_succeeded(asdict(snapshot))
    store.audit("account_refresh_succeeded", "worker")
    return True


def run_worker(store: StateStore, client: Refresher, key_id: str, interval: int = 300) -> None:
    if interval < 60:
        raise ValueError("refresh interval cannot be less than one minute")
    while True:
        refresh_once(store, client, key_id)
        time.sleep(interval)


def main() -> None:
    state_dir = Path(os.environ.get("KPV3_STATE_DIR", "/var/lib/kalshi-v3"))
    store, box = (
        StateStore(state_dir / "m1.sqlite3"),
        SecretBox((state_dir / "vault.key").read_bytes()),
    )
    while not store.configured():
        time.sleep(30)
    credential = json.loads(box.open(store.config("vault") or "").decode())
    client = KalshiAccountClient(
        RequestSigner(credential["key_id"], credential["pem"].encode()), UrllibReadTransport()
    )
    run_worker(store, client, credential["key_id"])


if __name__ == "__main__":
    main()
