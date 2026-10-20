"""Research-only BLS CPI schedule transport redundancy probe.

This module is deliberately *not* scientific evidence authority. It exists to
exercise multiple first-party BLS schedule pages before a future prospective CPI
protocol is frozen, so network/path failures are discovered before release morning.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

BLS_HOST = "www.bls.gov"
MAX_BLS_BYTES = 4_000_000
DEFAULT_TIMEOUT_SECONDS = 10.0
POLICY_VERSION = "cpi-p12-bls-schedule-redundancy-probe-v1"
EASTERN = ZoneInfo("America/New_York")


class BLSScheduleProbeError(RuntimeError):
    """The diagnostic probe configuration or bounded transport is invalid."""


@dataclass(frozen=True, slots=True)
class BLSScheduleTarget:
    """One CPI reference month and its expected BLS release instant."""

    reference_year: int
    reference_month: int
    release_at_utc: datetime

    def __post_init__(self) -> None:
        if self.reference_year < 1900 or self.reference_year > 2200:
            raise ValueError("reference_year is outside supported bounds")
        if self.reference_month < 1 or self.reference_month > 12:
            raise ValueError("reference_month must be in 1..12")
        if self.release_at_utc.tzinfo is None:
            raise ValueError("release_at_utc must be timezone-aware")
        normalized = self.release_at_utc.astimezone(UTC)
        object.__setattr__(self, "release_at_utc", normalized)

    @property
    def reference_label(self) -> str:
        return datetime(self.reference_year, self.reference_month, 1).strftime("%B %Y")

    @property
    def release_local(self) -> datetime:
        return self.release_at_utc.astimezone(EASTERN)


@dataclass(frozen=True, slots=True)
class BLSProbeEndpoint:
    """One approved first-party BLS path used only for readiness diagnostics."""

    name: str
    path: str
    authority_mode: str


@dataclass(frozen=True, slots=True)
class BLSHTTPObservation:
    """Exact bounded HTTP result returned by the probe transport."""

    status: int
    observed_at_utc: datetime
    body: bytes
    location: str | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class BLSProbeResult:
    endpoint_name: str
    path: str
    transport_ok: bool
    http_status: int | None
    observed_at_utc: str | None
    bytes: int
    body_sha256: str | None
    authority_valid: bool
    diagnostic: str

    def as_dict(self) -> dict[str, object]:
        return {
            "endpoint_name": self.endpoint_name,
            "path": self.path,
            "transport_ok": self.transport_ok,
            "http_status": self.http_status,
            "observed_at_utc": self.observed_at_utc,
            "bytes": self.bytes,
            "body_sha256": self.body_sha256,
            "authority_valid": self.authority_valid,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class BLSRedundancyReport:
    target: BLSScheduleTarget
    collector_label: str
    required_valid_paths: int
    results: tuple[BLSProbeResult, ...]

    @property
    def transport_success_count(self) -> int:
        return sum(result.transport_ok for result in self.results)

    @property
    def authority_valid_count(self) -> int:
        return sum(result.authority_valid for result in self.results)

    @property
    def path_redundancy_ready(self) -> bool:
        return self.authority_valid_count >= self.required_valid_paths

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_version": POLICY_VERSION,
            "collector_label": self.collector_label,
            "reference_month": (
                f"{self.target.reference_year:04d}-{self.target.reference_month:02d}"
            ),
            "release_at_utc": _iso(self.target.release_at_utc),
            "required_valid_paths": self.required_valid_paths,
            "transport_success_count": self.transport_success_count,
            "authority_valid_count": self.authority_valid_count,
            "path_redundancy_ready": self.path_redundancy_ready,
            "scientific_evidence_authorized": False,
            "results": [result.as_dict() for result in self.results],
        }


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_bls_probe_endpoints(target: BLSScheduleTarget) -> tuple[BLSProbeEndpoint, ...]:
    """Return fixed first-party page families for the target's local release month."""
    local = target.release_local
    return (
        BLSProbeEndpoint(
            name="cpi_release_schedule",
            path="/schedule/news_release/cpi.htm",
            authority_mode="CPI_SCHEDULE",
        ),
        BLSProbeEndpoint(
            name="monthly_release_list",
            path=f"/schedule/{local.year:04d}/{local.month:02d}_sched_list.htm",
            authority_mode="MONTHLY_LIST",
        ),
        BLSProbeEndpoint(
            name="cpi_home",
            path="/cpi/",
            authority_mode="CPI_CONTEXT",
        ),
        BLSProbeEndpoint(
            name="current_cpi_release",
            path="/news.release/cpi.nr0.htm",
            authority_mode="CPI_CONTEXT",
        ),
    )


def _fetch_endpoint(endpoint: BLSProbeEndpoint, timeout_seconds: float) -> BLSHTTPObservation:
    if timeout_seconds <= 0:
        raise BLSScheduleProbeError("timeout_seconds must be positive")
    if not endpoint.path.startswith("/") or ".." in endpoint.path.split("/"):
        raise BLSScheduleProbeError("BLS endpoint path is outside fixed authority")
    if "\r" in endpoint.path or "\n" in endpoint.path:
        raise BLSScheduleProbeError("BLS endpoint path contains a control character")

    connection = http.client.HTTPSConnection(
        BLS_HOST,
        timeout=timeout_seconds,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            endpoint.path,
            headers={
                "Accept": "text/html",
                "User-Agent": "kalsh3-cpi-p12-reliability-probe/1.0",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_BLS_BYTES + 1)
        return BLSHTTPObservation(
            status=response.status,
            observed_at_utc=datetime.now(UTC),
            body=body,
            location=response.getheader("Location"),
            content_type=response.getheader("Content-Type"),
        )
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise BLSScheduleProbeError(
            f"bounded BLS request failed: {type(exc).__name__}"
        ) from exc
    finally:
        connection.close()


def _visible_text(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BLSScheduleProbeError("BLS schedule response is not valid UTF-8") from exc
    parser = _VisibleText()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise BLSScheduleProbeError("BLS HTML could not be parsed") from exc
    return " ".join(" ".join(parser.parts).split())


def _release_date_pattern(target: BLSScheduleTarget) -> re.Pattern[str]:
    local = target.release_local
    full = re.escape(local.strftime("%B"))
    short = re.escape(local.strftime("%b"))
    return re.compile(
        rf"\b(?:{full}|{short}\.?)\s+0?{local.day},?\s+{local.year}\b",
        flags=re.IGNORECASE,
    )


def _release_time_pattern(target: BLSScheduleTarget) -> re.Pattern[str]:
    local = target.release_local
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    dotted = "A\\.M\\." if ampm == "AM" else "P\\.M\\."
    return re.compile(
        rf"\b0?{hour12}:{local.minute:02d}\s*(?:{ampm}|{dotted})(?!\w)",
        flags=re.IGNORECASE,
    )


def _validate_authority_text(
    endpoint: BLSProbeEndpoint,
    body: bytes,
    target: BLSScheduleTarget,
) -> None:
    normalized = _visible_text(body)
    reference_matches = list(
        re.finditer(re.escape(target.reference_label), normalized, flags=re.IGNORECASE)
    )
    if not reference_matches:
        raise BLSScheduleProbeError("target reference month is absent")

    if endpoint.authority_mode == "CPI_SCHEDULE":
        if "consumer price index" not in normalized.casefold():
            raise BLSScheduleProbeError("page is not positively identified as CPI schedule")
        require_cpi_in_window = False
    elif endpoint.authority_mode in {"MONTHLY_LIST", "CPI_CONTEXT"}:
        require_cpi_in_window = True
    else:
        raise BLSScheduleProbeError("unknown authority mode")

    date_pattern = _release_date_pattern(target)
    time_pattern = _release_time_pattern(target)
    for match in reference_matches:
        start = max(0, match.start() - 700)
        end = min(len(normalized), match.end() + 900)
        window = normalized[start:end]
        if require_cpi_in_window and "consumer price index" not in window.casefold():
            continue
        if date_pattern.search(window) is None:
            continue
        if time_pattern.search(window) is None:
            continue
        return
    raise BLSScheduleProbeError("target CPI release date/time is not bound to reference month")


FetchFunction = Callable[[BLSProbeEndpoint, float], BLSHTTPObservation]


def probe_bls_schedule_redundancy(
    target: BLSScheduleTarget,
    *,
    collector_label: str,
    required_valid_paths: int = 2,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    fetcher: FetchFunction = _fetch_endpoint,
) -> BLSRedundancyReport:
    """Probe every path independently and preserve failures without short-circuiting.

    The returned report is operational diagnostics only. It cannot be promoted into
    prospective scientific evidence without a separately reviewed authority boundary.
    """
    if not collector_label.strip():
        raise ValueError("collector_label must be non-empty")
    endpoints = build_bls_probe_endpoints(target)
    if required_valid_paths < 1 or required_valid_paths > len(endpoints):
        raise ValueError("required_valid_paths is outside the available endpoint count")

    results: list[BLSProbeResult] = []
    for endpoint in endpoints:
        try:
            observation = fetcher(endpoint, timeout_seconds)
        except BLSScheduleProbeError as exc:
            results.append(
                BLSProbeResult(
                    endpoint_name=endpoint.name,
                    path=endpoint.path,
                    transport_ok=False,
                    http_status=None,
                    observed_at_utc=None,
                    bytes=0,
                    body_sha256=None,
                    authority_valid=False,
                    diagnostic=str(exc),
                )
            )
            continue

        body_hash = hashlib.sha256(observation.body).hexdigest()
        observed_at = _iso(observation.observed_at_utc)
        if observation.location is not None or 300 <= observation.status < 400:
            results.append(
                BLSProbeResult(
                    endpoint_name=endpoint.name,
                    path=endpoint.path,
                    transport_ok=False,
                    http_status=observation.status,
                    observed_at_utc=observed_at,
                    bytes=len(observation.body),
                    body_sha256=body_hash,
                    authority_valid=False,
                    diagnostic="redirect rejected",
                )
            )
            continue
        if observation.status != 200:
            results.append(
                BLSProbeResult(
                    endpoint_name=endpoint.name,
                    path=endpoint.path,
                    transport_ok=False,
                    http_status=observation.status,
                    observed_at_utc=observed_at,
                    bytes=len(observation.body),
                    body_sha256=body_hash,
                    authority_valid=False,
                    diagnostic=f"HTTP {observation.status}",
                )
            )
            continue
        if not observation.body or len(observation.body) > MAX_BLS_BYTES:
            results.append(
                BLSProbeResult(
                    endpoint_name=endpoint.name,
                    path=endpoint.path,
                    transport_ok=False,
                    http_status=observation.status,
                    observed_at_utc=observed_at,
                    bytes=len(observation.body),
                    body_sha256=body_hash,
                    authority_valid=False,
                    diagnostic="response body violates bounded-size policy",
                )
            )
            continue

        try:
            _validate_authority_text(endpoint, observation.body, target)
        except BLSScheduleProbeError as exc:
            results.append(
                BLSProbeResult(
                    endpoint_name=endpoint.name,
                    path=endpoint.path,
                    transport_ok=True,
                    http_status=observation.status,
                    observed_at_utc=observed_at,
                    bytes=len(observation.body),
                    body_sha256=body_hash,
                    authority_valid=False,
                    diagnostic=f"authority mismatch: {exc}",
                )
            )
            continue

        results.append(
            BLSProbeResult(
                endpoint_name=endpoint.name,
                path=endpoint.path,
                transport_ok=True,
                http_status=observation.status,
                observed_at_utc=observed_at,
                bytes=len(observation.body),
                body_sha256=body_hash,
                authority_valid=True,
                diagnostic="PASS",
            )
        )

    return BLSRedundancyReport(
        target=target,
        collector_label=collector_label.strip(),
        required_valid_paths=required_valid_paths,
        results=tuple(results),
    )
