"""Bounded prospective collector for the frozen Perps Phase-0 protocol.

One invocation may claim and collect exactly one predeclared session. The
collector is production-read-only, single-connection, no-retry/no-backfill, and
writes a fresh append-only evidence database plus an exclusive result/failure
receipt. It never evaluates the mechanism and has no order capability.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.production_read_credentials import (
    ProductionCredentialError,
    ProductionReadCredentialStore,
    VerifiedProductionReadCredentialProvider,
    default_production_store_directory,
)

from .domain import ShadowResearchError
from .live_boundary import (
    MarginEnabledClient,
    MarginEnvironment,
    PerpsMarketClient,
    UrllibMarginHttpTransport,
    resolve_signer,
)
from .live_transport import AsyncMarginTransport, WebSocketConnector, websockets_connector
from .margin_protocol import MarginChannel
from .perps_runtime import OfflinePerpsEvidenceRuntime, PerpsRuntimeState, ScriptedPerpsTransport
from .perps_store import PerpsEvidenceStore
from .phase0_prospective import (
    PRODUCTION_INFLUENCE,
    PROTOCOL_SHA256,
    SESSION_DURATION_SECONDS,
    SESSION_START_LATE_TOLERANCE_SECONDS,
    TICKER,
    expected_session_ids,
    scheduled_at,
)


class ProspectiveCollectionError(ShadowResearchError):
    """Frozen prospective collection violation or fail-closed runtime failure."""


@dataclass(frozen=True, slots=True)
class SessionPaths:
    root: Path
    session_id: str

    @property
    def directory(self) -> Path:
        return self.root / self.session_id

    @property
    def claim(self) -> Path:
        return self.directory / "claim.json"

    @property
    def evidence_db(self) -> Path:
        return self.directory / "evidence.sqlite3"

    @property
    def result(self) -> Path:
        return self.directory / "result.json"

    @property
    def failure(self) -> Path:
        return self.directory / "failure.json"


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_invocation_time(session_id: str, now: datetime) -> datetime:
    if session_id not in expected_session_ids():
        raise ProspectiveCollectionError("unknown prospective session id")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ProspectiveCollectionError("invocation time must be timezone-aware")
    now_utc = now.astimezone(UTC)
    scheduled = scheduled_at(session_id)
    if now_utc < scheduled:
        raise ProspectiveCollectionError("prospective session invoked before frozen start")
    if now_utc >= scheduled + timedelta(seconds=SESSION_START_LATE_TOLERANCE_SECONDS):
        raise ProspectiveCollectionError("prospective session invoked after frozen start window")
    return scheduled


def claim_session(paths: SessionPaths, *, now: datetime) -> None:
    validate_invocation_time(paths.session_id, now)
    try:
        paths.directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ProspectiveCollectionError(
            "prospective session already claimed; retry forbidden"
        ) from exc
    _write_json_exclusive(
        paths.claim,
        {
            "record_type": "PERPS-REFERENCE-LAG-P0-CLAIM-v1",
            "protocol_sha256": PROTOCOL_SHA256,
            "session_id": paths.session_id,
            "scheduled_at_utc": _iso_z(scheduled_at(paths.session_id)),
            "claimed_at_utc": _iso_z(now),
            "ticker": TICKER,
            "session_duration_seconds": SESSION_DURATION_SECONDS,
            "no_retry": True,
            "no_backfill": True,
            "production_influence": str(PRODUCTION_INFLUENCE),
        },
    )


def seal_evidence_db(path: Path) -> str:
    if not path.is_file():
        raise ProspectiveCollectionError("session evidence database is missing")
    try:
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            foreign = db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity is None or integrity[0] != "ok" or foreign:
                raise ProspectiveCollectionError("session evidence database integrity failed")
            db.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        raise ProspectiveCollectionError("session evidence database sealing failed") from exc
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ProspectiveCollectionError("nonempty WAL remains after evidence seal")
    return _sha256(path)


async def _collect_full_window(
    *,
    signer: RequestSigner,
    runtime: OfflinePerpsEvidenceRuntime,
    connector: WebSocketConnector,
    duration_seconds: float,
) -> tuple[str, datetime, datetime]:
    if duration_seconds <= 0 or duration_seconds > SESSION_DURATION_SECONDS:
        raise ProspectiveCollectionError("unsafe prospective duration")
    transport = AsyncMarginTransport(MarginEnvironment.PRODUCTION, signer, connector)
    epoch = await transport.connect()
    protocol = transport.protocol
    if protocol is None:
        raise ProspectiveCollectionError("missing connected Margin protocol")
    runtime.bind_live_connection(epoch, protocol)
    await transport.send_protocol_command(protocol.subscribe(MarginChannel.ORDERBOOK, (TICKER,)))
    await transport.send_protocol_command(protocol.subscribe(MarginChannel.TICKER, (TICKER,)))

    started_at = datetime.now(UTC)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                frame = await asyncio.wait_for(transport.receive(), timeout=remaining)
            except TimeoutError:
                if loop.time() + 0.01 < deadline:
                    raise ProspectiveCollectionError(
                        "prospective websocket timed out early"
                    ) from None
                break
            runtime.process(frame, connection_epoch=epoch)
            if runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED:
                raise ProspectiveCollectionError(
                    "prospective stream requires reconnect; retry forbidden"
                )
    finally:
        runtime.invalidate_live_connection(epoch)
        await transport.close()
    ended_at = datetime.now(UTC)
    return str(epoch), started_at, ended_at


async def run_session(
    paths: SessionPaths,
    provider: VerifiedProductionReadCredentialProvider,
    *,
    now: datetime | None = None,
    http_transport: UrllibMarginHttpTransport | None = None,
    connector: WebSocketConnector = websockets_connector,
    duration_seconds: float = SESSION_DURATION_SECONDS,
) -> dict[str, Any]:
    invocation = (now or datetime.now(UTC)).astimezone(UTC)
    claim_session(paths, now=invocation)

    if type(provider) is not VerifiedProductionReadCredentialProvider:
        raise ProspectiveCollectionError("verified production read credential boundary required")
    if type(provider.store) is not ProductionReadCredentialStore:
        raise ProspectiveCollectionError("verified production read credential store required")

    signer = resolve_signer(provider, MarginEnvironment.PRODUCTION)
    http = http_transport or UrllibMarginHttpTransport()
    observed_at = datetime.now(UTC)
    market = PerpsMarketClient(
        MarginEnvironment.PRODUCTION,
        http,
        max_retries=0,
    ).get_market(TICKER, observed_at=observed_at)
    if market.ticker != TICKER:
        raise ProspectiveCollectionError("frozen prospective ticker mismatch")

    entitled = MarginEnabledClient(
        MarginEnvironment.PRODUCTION,
        signer,
        http,
        max_retries=0,
    ).enabled(timestamp_ms=time.time_ns() // 1_000_000)
    if not entitled:
        raise ProspectiveCollectionError("production Margin read access is not enabled")

    store = PerpsEvidenceStore(paths.evidence_db)
    if not store.append_metadata(market):
        raise ProspectiveCollectionError("fresh prospective DB unexpectedly contains metadata")
    runtime = OfflinePerpsEvidenceRuntime(
        market,
        store,
        ScriptedPerpsTransport(),
        lambda: datetime.now(UTC),
        time.monotonic_ns,
        enabled=True,
    )
    epoch, started_at, ended_at = await _collect_full_window(
        signer=signer,
        runtime=runtime,
        connector=connector,
        duration_seconds=duration_seconds,
    )
    health = runtime.health()
    if health.accepted_snapshot_count < 1 or health.accepted_ticker_count < 1:
        raise ProspectiveCollectionError("prospective session lacks minimum source evidence")
    if health.gap_count or health.collision_count or health.persistence_failure_count:
        raise ProspectiveCollectionError("prospective runtime health is not clean")

    book_rows = store.count("perps_book_evidence")
    market_state_rows = store.count("perps_market_state")
    if book_rows < 1 or market_state_rows < 1:
        raise ProspectiveCollectionError("prospective session persisted insufficient evidence")

    db_sha256 = seal_evidence_db(paths.evidence_db)
    result = {
        "record_type": "PERPS-REFERENCE-LAG-P0-RESULT-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "session_id": paths.session_id,
        "scheduled_at_utc": _iso_z(scheduled_at(paths.session_id)),
        "claimed_at_utc": _iso_z(invocation),
        "collection_started_at_utc": _iso_z(started_at),
        "collection_ended_at_utc": _iso_z(ended_at),
        "ticker": TICKER,
        "connection_epoch": epoch,
        "exchange_index": market.exchange_index,
        "market_version": market.market_version,
        "underlying_multiplier": str(market.underlying_multiplier),
        "market_metadata_hash": market.market_metadata_hash,
        "perps_contract_hash": market.perps_contract_hash,
        "accepted_snapshots": health.accepted_snapshot_count,
        "accepted_deltas": health.accepted_delta_count,
        "accepted_tickers": health.accepted_ticker_count,
        "book_rows": book_rows,
        "market_state_rows": market_state_rows,
        "evidence_db_sha256": db_sha256,
        "production_influence": str(PRODUCTION_INFLUENCE),
        "no_trade": True,
    }
    _write_json_exclusive(paths.result, result)
    return result


def _write_failure(paths: SessionPaths, exc: BaseException, *, now: datetime) -> None:
    if not paths.directory.exists() or paths.result.exists() or paths.failure.exists():
        return
    payload: dict[str, Any] = {
        "record_type": "PERPS-REFERENCE-LAG-P0-FAILURE-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "session_id": paths.session_id,
        "failed_at_utc": _iso_z(now),
        "failure_type": type(exc).__name__,
        "ticker": TICKER,
        "no_retry": True,
        "no_backfill": True,
        "production_influence": str(PRODUCTION_INFLUENCE),
    }
    if paths.evidence_db.exists():
        try:
            payload["evidence_db_sha256"] = seal_evidence_db(paths.evidence_db)
        except ProspectiveCollectionError:
            payload["evidence_db_seal"] = "FAILED"
    _write_json_exclusive(paths.failure, payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen Perps Phase-0 prospective collector")
    parser.add_argument("--session-id", required=True, choices=expected_session_ids())
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--confirm-production-readonly", action="store_true")
    parser.add_argument(
        "--production-credential-store",
        type=Path,
        default=default_production_store_directory(),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.confirm_production_readonly:
        print("BLOCKER: explicit production-readonly confirmation required")
        return 2
    paths = SessionPaths(args.root, args.session_id)
    provider = VerifiedProductionReadCredentialProvider(
        ProductionReadCredentialStore(args.production_credential_store)
    )
    try:
        asyncio.run(run_session(paths, provider))
    except ProspectiveCollectionError as exc:
        _write_failure(paths, exc, now=datetime.now(UTC))
        print(f"FAILED: {args.session_id}")
        return 2
    except (ShadowResearchError, ProductionCredentialError, ValueError) as exc:
        _write_failure(paths, exc, now=datetime.now(UTC))
        print(f"FAILED: {args.session_id}")
        return 2
    print(f"CAPTURED: {args.session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
