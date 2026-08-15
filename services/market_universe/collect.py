"""Operator-triggered collection of one reviewed public evidence scope.

Nothing runs on import. Markets and Events are fetched sequentially, so even a
complete receipt is not an atomic exchange snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit

from .archive import UniverseObservationArchive
from .sync import (
    Completeness,
    MemoryUniverseRepository,
    PublicTransport,
    SyncProgress,
    SyncRun,
    UniverseSynchronizer,
)

PUBLIC_ORIGIN = "https://external-api.kalshi.com"
ALLOWED_RESOURCES = frozenset({"markets", "events"})
MAX_RESPONSE_BYTES = 8_000_000
DEFAULT_MAX_PAGES = 250
ZERO_INFLUENCE = Decimal("0")
M26H1_SCOPE_POLICY_VERSION = "m26h1-reviewed-public-scope-v1"


@dataclass(frozen=True, slots=True)
class CollectionScope:
    name: str
    markets_endpoint: str
    markets_parameters: tuple[tuple[str, str], ...]
    events_endpoint: str
    events_parameters: tuple[tuple[str, str], ...]
    policy_version: str = M26H1_SCOPE_POLICY_VERSION
    production_influence: Decimal = ZERO_INFLUENCE

    @property
    def scope_id(self) -> str:
        material = {
            "events_endpoint": self.events_endpoint,
            "events_parameters": dict(self.events_parameters),
            "markets_endpoint": self.markets_endpoint,
            "markets_parameters": dict(self.markets_parameters),
            "name": self.name,
            "policy_version": self.policy_version,
            "production_influence": "0",
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


OPEN_NON_MVE_V1 = CollectionScope(
    name="open-non-mve-v1",
    markets_endpoint="/trade-api/v2/markets",
    markets_parameters=(("status", "open"), ("mve_filter", "exclude"), ("limit", "1000")),
    events_endpoint="/trade-api/v2/events",
    events_parameters=(("status", "open"), ("limit", "200")),
)
REVIEWED_SCOPES = MappingProxyType({OPEN_NON_MVE_V1.name: OPEN_NON_MVE_V1})


class CollectionError(RuntimeError):
    """The public read boundary rejected or could not complete a request."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class PublicUniverseTransport:
    """Unauthenticated GET transport restricted to one reviewed scope."""

    def __init__(self, scope: CollectionScope = OPEN_NON_MVE_V1) -> None:
        if scope is not OPEN_NON_MVE_V1:
            raise CollectionError("public universe scope rejected")
        self._scope = scope

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        try:
            parsed = urlsplit(path)
            pairs = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeError, ValueError):
            raise CollectionError("public universe resource rejected") from None
        expected = {
            self._scope.markets_endpoint: dict(self._scope.markets_parameters),
            self._scope.events_endpoint: dict(self._scope.events_parameters),
        }
        keys = [key for key, _ in pairs]
        semantic = dict(pairs)
        required = expected.get(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or required is None
            or len(keys) != len(set(keys))
            or set(semantic) != set(required) | ({"cursor"} if "cursor" in semantic else set())
            or any(semantic.get(key) != value for key, value in required.items())
            or any(not value or _has_control(value) for _, value in pairs)
        ):
            raise CollectionError("public universe resource rejected")
        request = urllib.request.Request(PUBLIC_ORIGIN + path, method="GET")  # noqa: S310
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                    raise CollectionError("public response exceeds size limit")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
                content_type = response.headers.get_content_type()
                location = response.headers.get("Location")
        except urllib.error.HTTPError as exc:
            raise CollectionError("public endpoint returned an error status") from exc
        except (urllib.error.URLError, http.client.InvalidURL, TimeoutError, OSError):
            raise CollectionError("public endpoint request failed") from None
        except ValueError:
            raise CollectionError("public response metadata is invalid") from None
        if location is not None or 300 <= status < 400:
            raise CollectionError("public endpoint redirect rejected")
        if status != 200 or content_type != "application/json" or len(raw) > MAX_RESPONSE_BYTES:
            raise CollectionError("public endpoint response rejected")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CollectionError("public endpoint returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise CollectionError("public endpoint response is not an object")
        return payload


@dataclass(frozen=True, slots=True)
class CollectionReceipt:
    archive_path: Path
    archive_authority_id: str
    market_run: SyncRun
    event_run: SyncRun
    started_at: datetime
    finished_at: datetime
    scope: str
    scope_policy_version: str
    scope_id: str
    market_event_ticker_count: int
    matched_event_ticker_count: int
    missing_event_tickers: tuple[str, ...]
    production_influence: Decimal = ZERO_INFLUENCE

    @property
    def complete(self) -> bool:
        return (
            self.market_run.completeness is Completeness.COMPLETE
            and self.event_run.completeness is Completeness.COMPLETE
            and not self.missing_event_tickers
        )


class ArchiveFactory(Protocol):
    def __call__(self, path: str | Path) -> UniverseObservationArchive: ...


ProgressCallback = Callable[[SyncProgress], None]


def collect_evidence(
    archive_path: str | Path,
    transport: PublicTransport,
    *,
    scope: CollectionScope = OPEN_NON_MVE_V1,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_seconds: float = 15,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    archive_factory: ArchiveFactory = UniverseObservationArchive,
    progress: ProgressCallback | None = None,
) -> CollectionReceipt:
    """Collect the fixed scope sequentially through one archive authority."""
    if scope is not OPEN_NON_MVE_V1:
        raise CollectionError("public universe scope rejected")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    started = clock()
    archive = archive_factory(archive_path)
    repo = MemoryUniverseRepository()
    synchronizer = UniverseSynchronizer(
        transport,
        repo,
        archive=archive,
        clock=clock,
        timeout=timeout_seconds,
        max_pages=max_pages,
        progress=progress,
    )
    if progress is not None:
        progress(SyncProgress("markets", 0, 0))
    market_run = synchronizer.sync("markets", parameters=dict(scope.markets_parameters))
    if progress is not None:
        progress(SyncProgress("events", 0, 0))
    event_run = synchronizer.sync("events", parameters=dict(scope.events_parameters))
    market_events = {item.event_ticker for item in repo.markets.values()}
    matched = market_events & set(repo.events)
    missing = tuple(sorted(market_events - matched))
    return CollectionReceipt(
        Path(archive_path),
        archive.authority_id,
        market_run,
        event_run,
        started,
        clock(),
        scope.name,
        scope.policy_version,
        scope.scope_id,
        len(market_events),
        len(matched),
        missing,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect read-only Market/Event evidence")
    parser.add_argument("--archive", required=True, type=Path, help="local SQLite archive path")
    parser.add_argument(
        "--live-public-read",
        action="store_true",
        help="explicitly permit unauthenticated public Kalshi GET requests",
    )
    parser.add_argument("--scope", choices=tuple(REVIEWED_SCOPES))
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    return parser


def _reason(receipt: CollectionReceipt) -> str:
    failures = []
    for label, run in (("market", receipt.market_run), ("event", receipt.event_run)):
        if run.completeness is not Completeness.COMPLETE:
            failures.append(f"{label} acquisition did not complete ({run.failure or 'partial'})")
    if receipt.missing_event_tickers:
        failures.append("market-to-event coverage is incomplete")
    return "; ".join(failures)


def _print_receipt(receipt: CollectionReceipt) -> None:
    market_finished = receipt.market_run.finished_at
    event_finished = receipt.event_run.finished_at
    if market_finished is None or event_finished is None:
        raise CollectionError("collection receipt has unfinished resource run")
    print(f"Evidence collection: {'COMPLETE' if receipt.complete else 'INCOMPLETE'}")
    print(f"Scope: {receipt.scope}")
    print(f"Scope policy: {receipt.scope_policy_version}")
    print(f"Scope ID: {receipt.scope_id}")
    print(
        f"Markets: {receipt.market_run.pages} pages / "
        f"{receipt.market_run.records_received:,} observed"
    )
    print(
        f"Events: {receipt.event_run.pages} pages / {receipt.event_run.records_received:,} observed"
    )
    print(f"Market event tickers: {receipt.market_event_ticker_count}")
    print(f"Matched event tickers: {receipt.matched_event_ticker_count}")
    print(f"Missing event tickers: {', '.join(receipt.missing_event_tickers) or 'none'}")
    print(f"Archive: {receipt.archive_path}")
    print(f"Archive authority: {receipt.archive_authority_id}")
    print(f"Market run: {receipt.market_run.run_id}")
    print(
        f"Market window: {receipt.market_run.started_at.isoformat()} to "
        f"{market_finished.isoformat()}"
    )
    print(f"Event run: {receipt.event_run.run_id}")
    print(
        f"Event window: {receipt.event_run.started_at.isoformat()} to {event_finished.isoformat()}"
    )
    print(f"Collection started: {receipt.started_at.isoformat()}")
    print(f"Collection finished: {receipt.finished_at.isoformat()}")
    print("Production influence: 0")
    if not receipt.complete:
        print(f"Reason: {_reason(receipt)}")
        print("No reviewed evidence-unit authority changed.")


def _cli_progress(progress: SyncProgress) -> None:
    resource = progress.resource.title()
    if progress.pages == 0:
        print(f"Collecting {resource}...")
    else:
        print(f"{resource}: {progress.pages} pages / {progress.records_received:,} observed")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live_public_read or args.scope is None:
        print("Evidence collection: NOT STARTED")
        reason = (
            "explicit --live-public-read permission is required"
            if not args.live_public_read
            else "explicit reviewed --scope is required"
        )
        print(f"Reason: {reason}")
        return 2
    scope = REVIEWED_SCOPES[args.scope]
    print("Starting evidence collection")
    print(f"Scope: {scope.name}")
    try:
        receipt = collect_evidence(
            args.archive,
            PublicUniverseTransport(scope),
            scope=scope,
            max_pages=args.max_pages,
            timeout_seconds=args.timeout_seconds,
            progress=_cli_progress,
        )
    except Exception as exc:
        print("Evidence collection: FAILED")
        print(f"Reason: {type(exc).__name__}")
        print("No reviewed evidence-unit authority changed.")
        return 1
    _print_receipt(receipt)
    return 0 if receipt.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
