"""Bounded RT-A1 collection cycle. No alert or execution surface exists here."""
# The URL is constrained to HTTPS Rotten Tomatoes hosts before urllib is called.
# ruff: noqa: S310

from __future__ import annotations

import base64
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.market_universe.public_read import get_orderbook_with_body

from .collector import RtSnapshot, first_inclusion, parse_rt_html, reconstruct_transition
from .domain import ActiveKxrtMarket, discover_active_kxrt
from .store import RtA1Store

MAX_RT_BYTES = 4_000_000
USER_AGENT = "kalsh3-rt-a1-research/1"
RT_PAGE_CADENCE_SECONDS = 180
KALSHI_ORDERBOOK_CADENCE_SECONDS = 90
MAX_BACKOFF_SECONDS = 900


class AcquisitionBlocked(RuntimeError):
    pass


def fetch_rt_page(url: str, *, timeout_seconds: float = 15.0) -> tuple[bytes, datetime]:
    """GET one RT page after a robots.txt check; redirects and oversized pages are rejected."""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith("rottentomatoes.com")
    ):
        raise AcquisitionBlocked("RT URL is outside the permitted public host")
    robots_url = f"https://{parsed.hostname}/robots.txt"
    parser = RobotFileParser(robots_url)
    try:
        request = urllib.request.Request(
            robots_url, headers={"User-Agent": USER_AGENT}, method="GET"
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            robots = response.read(100_000)
        parser.parse(robots.decode("utf-8", errors="replace").splitlines())
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise AcquisitionBlocked("robots.txt could not be verified") from exc
    if not parser.can_fetch(USER_AGENT, url):
        raise AcquisitionBlocked("robots.txt disallows acquisition")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, method="GET"
    )
    observed = datetime.now(UTC)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            if response.geturl() != url:
                raise AcquisitionBlocked("RT redirect rejected")
            body = response.read(MAX_RT_BYTES + 1)
            if len(body) > MAX_RT_BYTES:
                raise AcquisitionBlocked("RT response exceeded bound")
            return body, observed
    except AcquisitionBlocked:
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise AcquisitionBlocked("RT page acquisition failed") from exc


def _persist_transition(store: RtA1Store, previous: RtSnapshot | None, current: RtSnapshot) -> None:
    if previous is None:
        return
    transition = reconstruct_transition(previous, current)
    store.append_transition(transition)
    for event in first_inclusion(previous, current):
        store.append_first_inclusion(event)


def run_cycle(
    store: RtA1Store,
    *,
    run_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    discover: Callable[[], tuple[ActiveKxrtMarket, ...]] = discover_active_kxrt,
    rt_fetch: Callable[[str], tuple[bytes, datetime]] = fetch_rt_page,
) -> dict[str, int | str]:
    """Register and execute one bounded acquisition cycle, retaining failures as attempts."""
    started = now().astimezone(UTC)
    store.register_run(run_id, started_at=started)
    discovery_attempt = store.begin_attempt(
        run_id, "kalshi:active-kxrt-universe", started_at=started
    )
    try:
        markets = discover()
        store.finish_attempt(discovery_attempt, finished_at=now().astimezone(UTC), status="SUCCESS")
    except Exception as exc:
        store.finish_attempt(
            discovery_attempt,
            finished_at=now().astimezone(UTC),
            status="FAILURE",
            error=type(exc).__name__ + ": " + str(exc),
        )
        return {
            "run_id": run_id,
            "markets": 0,
            "rt_success": 0,
            "orderbook_success": 0,
            "failures": 1,
        }
    rt_success = 0
    book_success = 0
    failures = 0
    for market in markets:
        attempt = store.begin_attempt(run_id, f"rt:{market.rt_url}", started_at=started)
        try:
            body, observed = rt_fetch(market.rt_url)
            snapshot = parse_rt_html(market.rt_url, body, observed_at=observed)
            previous = store.latest_snapshot(market.rt_url)
            store.append_rt_snapshot(snapshot)
            _persist_transition(store, previous, snapshot)
            store.finish_attempt(
                attempt,
                finished_at=now().astimezone(UTC),
                status="SUCCESS",
                raw_body_sha256=snapshot.raw_body_sha256,
            )
            rt_success += 1
        except Exception as exc:  # acquisition failures are durable and cycle continues
            store.finish_attempt(
                attempt,
                finished_at=now().astimezone(UTC),
                status="FAILURE",
                error=type(exc).__name__ + ": " + str(exc),
            )
            failures += 1
        for ticker in [market.market_ticker]:
            attempt = store.begin_attempt(
                run_id, f"kalshi:orderbook:{ticker}", started_at=now().astimezone(UTC)
            )
            try:
                book = acquire_orderbook_snapshot(
                    ticker, transport=get_orderbook_with_body, clock=now
                )
                if not book.succeeded or book.raw_body_b64 is None or book.body_sha256 is None:
                    raise AcquisitionBlocked(book.reason or "orderbook acquisition failed")
                raw = base64.b64decode(book.raw_body_b64, validate=True)
                store.append_orderbook(
                    ticker, book.observed_at, raw, book.body_sha256, book.yes_levels, book.no_levels
                )
                store.finish_attempt(
                    attempt,
                    finished_at=now().astimezone(UTC),
                    status="SUCCESS",
                    raw_body_sha256=book.body_sha256,
                )
                book_success += 1
            except Exception as exc:
                store.finish_attempt(
                    attempt,
                    finished_at=now().astimezone(UTC),
                    status="FAILURE",
                    error=type(exc).__name__ + ": " + str(exc),
                )
                failures += 1
    return {
        "run_id": run_id,
        "markets": len(markets),
        "rt_success": rt_success,
        "orderbook_success": book_success,
        "failures": failures,
    }
