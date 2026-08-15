"""Explicit, bounded collection of public Market/Event archive evidence.

Nothing in this module runs on import.  The network-backed path is available
only through ``main`` after the operator supplies ``--live-public-read``.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

from .archive import UniverseObservationArchive
from .sync import (
    Completeness,
    MemoryUniverseRepository,
    PublicTransport,
    SyncRun,
    UniverseSynchronizer,
)

PUBLIC_ORIGIN = "https://external-api.kalshi.com"
ALLOWED_RESOURCES = frozenset({"markets", "events"})
MAX_RESPONSE_BYTES = 8_000_000
DEFAULT_MAX_PAGES = 250
ZERO_INFLUENCE = Decimal("0")


class CollectionError(RuntimeError):
    """The public read boundary rejected or could not complete a request."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class PublicUniverseTransport:
    """Unauthenticated GET-only transport for exactly markets and events."""

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        parsed = urlsplit(path)
        decoded = unquote(parsed.path)
        expected_prefix = "/trade-api/v2/"
        resource = decoded.removeprefix(expected_prefix)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not decoded.startswith(expected_prefix)
            or resource not in ALLOWED_RESOURCES
            or set(query) - {"cursor"}
            or any(len(values) != 1 for values in query.values())
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
        except (urllib.error.URLError, TimeoutError, OSError):
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
    production_influence: Decimal = ZERO_INFLUENCE

    @property
    def complete(self) -> bool:
        return (
            self.market_run.completeness is Completeness.COMPLETE
            and self.event_run.completeness is Completeness.COMPLETE
        )


class ArchiveFactory(Protocol):
    def __call__(self, path: str | Path) -> UniverseObservationArchive: ...


def collect_evidence(
    archive_path: str | Path,
    transport: PublicTransport,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_seconds: float = 15,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    archive_factory: ArchiveFactory = UniverseObservationArchive,
) -> CollectionReceipt:
    """Collect both fixed resources through one synchronizer and archive authority."""
    if max_pages < 1 or timeout_seconds <= 0:
        raise ValueError("collection bounds must be positive")
    started = clock()
    archive = archive_factory(archive_path)
    synchronizer = UniverseSynchronizer(
        transport,
        MemoryUniverseRepository(),
        archive=archive,
        clock=clock,
        timeout=timeout_seconds,
        max_pages=max_pages,
    )
    market_run = synchronizer.sync("markets")
    event_run = synchronizer.sync("events")
    return CollectionReceipt(
        Path(archive_path),
        archive.authority_id,
        market_run,
        event_run,
        started,
        clock(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect read-only Market/Event evidence")
    parser.add_argument("--archive", required=True, type=Path, help="local SQLite archive path")
    parser.add_argument(
        "--live-public-read",
        action="store_true",
        help="explicitly permit unauthenticated public Kalshi GET requests",
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    return parser


def _reason(receipt: CollectionReceipt) -> str:
    failures = []
    for label, run in (("market", receipt.market_run), ("event", receipt.event_run)):
        if run.completeness is not Completeness.COMPLETE:
            failures.append(f"{label} acquisition did not complete ({run.failure or 'partial'})")
    return "; ".join(failures)


def _print_receipt(receipt: CollectionReceipt) -> None:
    print(f"Evidence collection: {'COMPLETE' if receipt.complete else 'INCOMPLETE'}")
    print(f"Markets: {receipt.market_run.records_received:,} observed")
    print(f"Events: {receipt.event_run.records_received:,} observed")
    print(f"Archive: {receipt.archive_path}")
    print(f"Archive authority: {receipt.archive_authority_id}")
    print(f"Market run: {receipt.market_run.run_id}")
    print(f"Event run: {receipt.event_run.run_id}")
    print(f"Started: {receipt.started_at.isoformat()}")
    print(f"Finished: {receipt.finished_at.isoformat()}")
    print("Production influence: 0")
    if not receipt.complete:
        print(f"Reason: {_reason(receipt)}")
        print("No reviewed evidence-unit authority changed.")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live_public_read:
        print("Evidence collection: NOT STARTED")
        print("Reason: explicit --live-public-read permission is required")
        return 2
    try:
        receipt = collect_evidence(
            args.archive,
            PublicUniverseTransport(),
            max_pages=args.max_pages,
            timeout_seconds=args.timeout_seconds,
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
