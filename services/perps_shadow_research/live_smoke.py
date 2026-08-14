"""Explicit manual M25B2 read-only Perps evidence smoke; never autostarted."""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from services.kalshi_account_gateway.auth import RequestSigner

from .domain import ShadowResearchError
from .exact_read_credentials import (
    CredentialBoundaryError,
    ExactReadCredentialStore,
    VerifiedDemoCredentialProvider,
    default_store_directory,
)
from .live_boundary import (
    ExactReadCredentialProvider,
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


class SmokeOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    INCONCLUSIVE = "INCONCLUSIVE"
    NO_GO = "NO_GO"


@dataclass(frozen=True, slots=True)
class LiveSmokeConfig:
    environment: MarginEnvironment
    ticker: str
    evidence_db: Path
    live_readonly: bool = False
    confirm_production_readonly: bool = False
    window_seconds: float = 60
    max_reconnects: int = 1

    def __post_init__(self) -> None:
        if not self.live_readonly:
            raise ShadowResearchError("live smoke requires --live-readonly")
        if not self.ticker or self.ticker.strip() != self.ticker:
            raise ShadowResearchError("an explicit ticker is required")
        if (
            self.environment is MarginEnvironment.PRODUCTION
            and not self.confirm_production_readonly
        ):
            raise ShadowResearchError("production requires --confirm-production-readonly")
        if not 0 < self.window_seconds <= 300 or not 0 <= self.max_reconnects <= 3:
            raise ShadowResearchError("unsafe smoke time or reconnect bound")
        forbidden_parts = {"fixtures", "testdata", "data"}
        lexical_parts = {part.lower() for part in self.evidence_db.parts}
        resolved_parts = {
            part.lower() for part in self.evidence_db.parent.resolve(strict=False).parts
        }
        if forbidden_parts & (lexical_parts | resolved_parts):
            raise ShadowResearchError("evidence DB must not be inside tracked fixture/data paths")


@dataclass(frozen=True, slots=True)
class SmokeSummary:
    outcome: SmokeOutcome
    environment: MarginEnvironment
    ticker: str
    entitled: bool
    exchange_index: int
    epochs: tuple[str, ...]
    snapshots: int
    deltas: int
    tickers: int
    book_rows: int
    market_state_rows: int


async def collect_live_evidence(
    config: LiveSmokeConfig,
    runtime: OfflinePerpsEvidenceRuntime,
    signer: RequestSigner,
    *,
    connector: WebSocketConnector = websockets_connector,
) -> tuple[tuple[str, ...], int, int, int]:
    epochs: list[str] = []
    snapshots = deltas = tickers = 0
    sessions = config.max_reconnects + 1
    for session_number in range(sessions):
        transport = AsyncMarginTransport(config.environment, signer, connector)
        epoch = await transport.connect()
        epochs.append(str(epoch))
        protocol = transport.protocol
        if protocol is None:
            raise ShadowResearchError("missing connected margin protocol")
        runtime.bind_live_connection(epoch, protocol)
        session_baseline = runtime.health()
        await transport.send_protocol_command(
            protocol.subscribe(MarginChannel.ORDERBOOK, (config.ticker,))
        )
        await transport.send_protocol_command(
            protocol.subscribe(MarginChannel.TICKER, (config.ticker,))
        )
        deadline = asyncio.get_running_loop().time() + config.window_seconds
        try:
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                frame = await asyncio.wait_for(transport.receive(), timeout=remaining)
                runtime.process(frame, connection_epoch=epoch)
                after = runtime.health()
                if runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED:
                    break
                session_snapshots = (
                    after.accepted_snapshot_count - session_baseline.accepted_snapshot_count
                )
                session_deltas = after.accepted_delta_count - session_baseline.accepted_delta_count
                session_tickers = (
                    after.accepted_ticker_count - session_baseline.accepted_ticker_count
                )
                session_ready = session_snapshots >= 1
                if session_number == 0:
                    session_ready = session_ready and session_deltas >= 1 and session_tickers >= 1
                if session_ready:
                    break
        except TimeoutError:
            pass
        finally:
            protocol = transport.protocol
            if protocol is not None:
                for sid in tuple(protocol.subscriptions):
                    await transport.send_protocol_command(protocol.unsubscribe(sid))
            runtime.invalidate_live_connection(epoch)
            await transport.close()
        after_session = runtime.health()
        snapshots += (
            after_session.accepted_snapshot_count - session_baseline.accepted_snapshot_count
        )
        deltas += after_session.accepted_delta_count - session_baseline.accepted_delta_count
        tickers += after_session.accepted_ticker_count - session_baseline.accepted_ticker_count
    return tuple(epochs), snapshots, deltas, tickers


async def run_live_smoke(
    config: LiveSmokeConfig,
    provider: ExactReadCredentialProvider,
    *,
    http_transport: UrllibMarginHttpTransport | None = None,
    connector: WebSocketConnector = websockets_connector,
) -> SmokeSummary:
    http = http_transport or UrllibMarginHttpTransport()
    observed_at = datetime.now(UTC)
    market = PerpsMarketClient(config.environment, http).get_market(
        config.ticker, observed_at=observed_at
    )
    store = PerpsEvidenceStore(config.evidence_db)
    store.append_metadata(market)
    book_rows_before = store.count("perps_book_evidence")
    state_rows_before = store.count("perps_market_state")
    signer = resolve_signer(provider, config.environment)
    entitled = MarginEnabledClient(config.environment, signer, http).enabled(
        timestamp_ms=time.time_ns() // 1_000_000
    )
    if not entitled:
        return SmokeSummary(
            SmokeOutcome.NO_GO,
            config.environment,
            config.ticker,
            False,
            market.exchange_index,
            (),
            0,
            0,
            0,
            0,
            0,
        )
    runtime = OfflinePerpsEvidenceRuntime(
        market,
        store,
        ScriptedPerpsTransport(),
        lambda: datetime.now(UTC),
        time.monotonic_ns,
        enabled=True,
    )
    epochs, snapshots, deltas, tickers = await collect_live_evidence(
        config, runtime, signer, connector=connector
    )
    book_rows = store.count("perps_book_evidence") - book_rows_before
    state_rows = store.count("perps_market_state") - state_rows_before
    accepted = (
        len(epochs) == config.max_reconnects + 1
        and len(set(epochs)) == len(epochs)
        and snapshots >= config.max_reconnects + 1
        and deltas >= 1
        and tickers >= 1
        and book_rows >= 2
        and state_rows >= 1
    )
    outcome = SmokeOutcome.SUCCESS if accepted else SmokeOutcome.INCONCLUSIVE
    return SmokeSummary(
        outcome,
        config.environment,
        config.ticker,
        True,
        market.exchange_index,
        epochs,
        snapshots,
        deltas,
        tickers,
        book_rows,
        state_rows,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manual read-only Kalshi Perps evidence smoke")
    parser.add_argument(
        "--environment", required=True, choices=[item.value for item in MarginEnvironment]
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--evidence-db", required=True, type=Path)
    parser.add_argument("--live-readonly", action="store_true")
    parser.add_argument("--confirm-production-readonly", action="store_true")
    parser.add_argument("--credential-store", type=Path, default=default_store_directory())
    return parser


def main() -> int:
    args = _parser().parse_args()
    environment = MarginEnvironment(args.environment)
    if environment is MarginEnvironment.PRODUCTION:
        print("BLOCKER: M25B3 production credential composition unavailable")
        return 2
    try:
        config = LiveSmokeConfig(
            environment,
            args.ticker,
            args.evidence_db,
            args.live_readonly,
            args.confirm_production_readonly,
        )
        provider = VerifiedDemoCredentialProvider(ExactReadCredentialStore(args.credential_store))
        provider.resolve(MarginEnvironment.DEMO)
    except (ShadowResearchError, CredentialBoundaryError):
        print("BLOCKER: verified DEMO credential unavailable")
        return 2
    summary = asyncio.run(run_live_smoke(config, provider))
    print(f"DEMO read-only smoke outcome: {summary.outcome.value}")
    return 0 if summary.outcome is SmokeOutcome.SUCCESS else 2


if __name__ == "__main__":
    raise SystemExit(main())
