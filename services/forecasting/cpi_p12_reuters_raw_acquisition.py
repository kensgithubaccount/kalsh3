"""Independent, research-only Reuters-source raw HTTPS acquisition for CPI P12.

This records raw first-party HTTPS response bytes from *explicit, preselected* URLs.
It does not search the web, infer a forecast, validate Reuters attribution, select
a CPI value, or authorize scientific evidence. Never call this with target source
URLs before a separately reviewed pre-event source-selection protocol is frozen.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import ssl
import string
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

RECORD_TYPE = "CPI-P12-REUTERS-RAW-HTTPS-OBSERVATIONS-v1"
MAX_SOURCES = 8
MAX_RESPONSE_BYTES = 1_000_000

# Explicit exact hosts, not suffix matching or caller-configurable host groups.
# Presence of a host here does not establish Reuters attribution or independence.
HOST_OPERATORS = {
    "www.reuters.com": "reuters",
    "reuters.com": "reuters",
    "finance.yahoo.com": "yahoo",
    "www.yahoo.com": "yahoo",
    "www.nasdaq.com": "nasdaq",
    "www.tradingview.com": "tradingview",
    "www.kfgo.com": "kfgo",
}


class P12ReutersRawAcquisitionError(ValueError):
    """An invalid source, failed fetch or missed prospective cutoff."""


def _utc(value: datetime, label: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise P12ReutersRawAcquisitionError(f"{label} must be an aware datetime")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _validate_url(url: str) -> tuple[str, str, str]:
    if (
        type(url) is not str
        or len(url) > 2048
        or not url.isascii()
        or any(ord(ch) < 33 or ord(ch) == 127 for ch in url)
    ):
        raise P12ReutersRawAcquisitionError("source URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise P12ReutersRawAcquisitionError("source URL is invalid") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or type(host) is not str
        or host not in HOST_OPERATORS
        or parsed.netloc != host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or ".." in parsed.path.split("/")
    ):
        raise P12ReutersRawAcquisitionError("source URL is not an approved exact HTTPS endpoint")
    return host, parsed.path, HOST_OPERATORS[host]


def _read_complete_chunked_body(response: http.client.HTTPResponse) -> bytes:
    """Strictly validate HTTP/1.1 chunk boundaries before trusting entity bytes.

    HTTPResponse.read() silently tolerates some malformed chunk terminators and
    unfinished trailer sections. Read its already-positioned raw body stream
    directly and require the terminal CRLF. Unsupported chunk extensions fail
    closed rather than granting an operational PASS to incomplete evidence.
    """
    stream = response.fp
    if stream is None:
        raise http.client.HTTPException("chunked response stream is unavailable")
    hexdigits = b"0123456789abcdefABCDEF"
    token_bytes = b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    body = bytearray()
    for _ in range(4096):
        line = stream.readline(67)
        size_text = line[:-2]
        if (
            not line.endswith(b"\r\n")
            or not 1 <= len(size_text) <= 16
            or any(ch not in hexdigits for ch in size_text)
        ):
            raise http.client.HTTPException("invalid chunk size line")
        size = int(size_text, 16)
        if size > MAX_RESPONSE_BYTES - len(body):
            raise http.client.HTTPException("chunk body exceeds byte limit")
        if size == 0:
            trailer_bytes = 0
            for _ in range(32):
                trailer = stream.readline(1025)
                trailer_bytes += len(trailer)
                if not trailer.endswith(b"\r\n") or len(trailer) > 1024 or trailer_bytes > 8192:
                    raise http.client.HTTPException("invalid or oversized chunk trailer")
                if trailer == b"\r\n":
                    return bytes(body)
                name, separator, value = trailer[:-2].partition(b":")
                if (
                    not separator
                    or not name
                    or any(ch not in token_bytes for ch in name)
                    or any((ch < 32 and ch != 9) or ch == 127 for ch in value)
                ):
                    raise http.client.HTTPException("invalid chunk trailer field")
            raise http.client.HTTPException("too many chunk trailers")
        chunk = stream.read(size)
        if len(chunk) != size or stream.read(2) != b"\r\n":
            raise http.client.HTTPException("truncated chunk or invalid chunk separator")
        body.extend(chunk)
    raise http.client.HTTPException("too many chunks")


class _StrictHeaderStream:
    """Reject malformed header bytes before http.client's permissive parser sees them."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._state = "status"
        self._header_bytes = 0
        self.validated = False

    def readline(self, limit: int = -1) -> bytes:
        line: bytes = self._stream.readline(limit)
        if self._state == "body":
            return line
        if not line.endswith(b"\r\n"):
            raise P12ReutersRawAcquisitionError("malformed source HTTP header termination")
        if self._state == "status":
            if any((ch < 32 and ch != 9) or ch == 127 for ch in line[:-2]):
                raise P12ReutersRawAcquisitionError("malformed source HTTP status line")
            # Unexpected interim responses are unsupported rather than allowing
            # parsing of an unverified second header block.
            if line.split()[1:2] == [b"100"]:
                raise P12ReutersRawAcquisitionError("unsupported interim HTTP response")
            self._state = "headers"
            return line
        self._header_bytes += len(line)
        if self._header_bytes > 65536:
            raise P12ReutersRawAcquisitionError("source HTTP headers exceed byte limit")
        if line == b"\r\n":
            self._state = "body"
            self.validated = True
            return line
        name, sep, value = line[:-2].partition(b":")
        allowed = b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if (
            not sep
            or not name
            or any(ch not in allowed for ch in name)
            or any((ch < 32 and ch != 9) or ch == 127 for ch in value)
        ):
            raise P12ReutersRawAcquisitionError("malformed source HTTP header bytes")
        return line

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self._stream.close()


class _StrictHTTPResponse(http.client.HTTPResponse):
    """HTTPResponse with provenance that raw header syntax was inspected."""

    def begin(self) -> None:
        if self.headers is not None:
            return
        if self.fp is None:
            raise P12ReutersRawAcquisitionError("missing source HTTP response stream")
        verified_stream = _StrictHeaderStream(self.fp)
        self.fp = verified_stream  # type: ignore[assignment]
        super().begin()
        if not verified_stream.validated:
            raise P12ReutersRawAcquisitionError("source HTTP headers were not validated")
        self.raw_headers_validated = True


def _https_get(url: str) -> tuple[int, bytes, dict[str, str]]:
    """TLS-verified, exact-origin GET; redirects are NOT followed."""
    host, path, _ = _validate_url(url)
    conn = http.client.HTTPSConnection(host, timeout=8, context=ssl.create_default_context())
    conn.response_class = _StrictHTTPResponse
    try:
        conn.request(
            "GET",
            path,
            headers={"Accept-Encoding": "identity", "User-Agent": "kalsh3-P12-research/1"},
        )
        response = conn.getresponse()
        if type(response) is not _StrictHTTPResponse or getattr(response, "raw_headers_validated", False) is not True:
            raise P12ReutersRawAcquisitionError("source HTTP headers lack raw-byte validation")
        # http.client uses email parsing and can silently discard all headers
        # following an invalid field line. That can hide Content-Length or
        # Transfer-Encoding and turn a truncated body into an operational PASS.
        defects = getattr(response.msg, "defects", None)
        if not isinstance(defects, list) or defects:
            raise P12ReutersRawAcquisitionError("malformed source HTTP headers")
        source_headers = response.getheaders()
        field_name_chars = "!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters
        if any(
            not name
            or not name.isascii()
            or any(ch not in field_name_chars for ch in name)
            or "\r" in value
            or "\n" in value
            for name, value in source_headers
        ):
            raise P12ReutersRawAcquisitionError("invalid source HTTP header field")
        lengths = [value for key, value in source_headers if key.lower() == "content-length"]
        transfer_encodings = [
            value for key, value in source_headers if key.lower() == "transfer-encoding"
        ]
        if (
            len(lengths) > 1
            or len(transfer_encodings) > 1
            or (lengths and transfer_encodings)
            or (transfer_encodings and transfer_encodings[0].lower() != "chunked")
        ):
            raise P12ReutersRawAcquisitionError("ambiguous or unsupported source response framing")
        expected_length = None
        if lengths:
            value = lengths[0]
            if not value.isascii() or not value.isdecimal() or len(value) > 18:
                raise P12ReutersRawAcquisitionError("invalid source content length")
            expected_length = int(value)
            if expected_length > MAX_RESPONSE_BYTES:
                raise P12ReutersRawAcquisitionError("source body exceeds byte limit")
        raw = (
            _read_complete_chunked_body(response)
            if transfer_encodings
            else response.read(MAX_RESPONSE_BYTES + 1)
        )
        if expected_length is not None and len(raw) != expected_length:
            raise P12ReutersRawAcquisitionError("source body is truncated or length inconsistent")
        headers = {
            name.lower(): value
            for name, value in source_headers
            if name.lower() in {"content-type", "last-modified", "etag"}
        }
        return response.status, raw, headers
    finally:
        conn.close()


def capture_reuters_raw_sources(
    *,
    source_urls: tuple[str, ...],
    window_start: datetime,
    deadline: datetime,
    getter: Callable[[str], tuple[int, bytes, Mapping[str, str]]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture exact raw source bodies; any invalid/late response fails this lane.

    No source discovery, no unreviewed backfill, no Reuters value extraction.
    Caller URL selection and cutoff dates confer *no* scientific authority.
    """
    if (
        type(source_urls) is not tuple
        or not 1 <= len(source_urls) <= MAX_SOURCES
        or any(type(url) is not str for url in source_urls)
    ):
        raise P12ReutersRawAcquisitionError("source cohort size or type is invalid")
    if len(set(source_urls)) != len(source_urls):
        raise P12ReutersRawAcquisitionError("duplicate source URLs")
    parsed_sources = [_validate_url(url) for url in source_urls]
    start = _utc(window_start, "window_start")
    end = _utc(deadline, "deadline")
    if start >= end:
        raise P12ReutersRawAcquisitionError("time window is empty")
    now = clock or (lambda: datetime.now(UTC))
    started = _utc(now(), "capture start")
    if not start <= started < end:
        raise P12ReutersRawAcquisitionError("capture began outside window")
    transport = _https_get if getter is None else getter
    if not callable(transport):
        raise P12ReutersRawAcquisitionError("source getter is not callable")

    observations: list[dict[str, Any]] = []
    for url, (host, _, group) in zip(source_urls, parsed_sources, strict=True):
        before = _utc(now(), "request start")
        previous = (
            started
            if not observations
            else datetime.fromisoformat(
                observations[-1]["response_completed_at_utc"].replace("Z", "+00:00")
            )
        )
        if before < previous:
            raise P12ReutersRawAcquisitionError("acquisition clock moved backwards")
        if not start <= before < end:
            raise P12ReutersRawAcquisitionError("window expired before request")
        try:
            response = transport(url)
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise P12ReutersRawAcquisitionError("Reuters-source transport failure") from exc
        after = _utc(now(), "request completed")
        if not before <= after < end:
            raise P12ReutersRawAcquisitionError("response missed frozen prospective deadline")
        if type(response) is not tuple or len(response) != 3:
            raise P12ReutersRawAcquisitionError("malformed transport response")
        status, raw, headers = response
        if type(status) is not int or status != 200:
            raise P12ReutersRawAcquisitionError("source did not return HTTP 200")
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
            raise P12ReutersRawAcquisitionError("source response body size or type invalid")
        if not isinstance(headers, Mapping):
            raise P12ReutersRawAcquisitionError("source response headers invalid")
        safe_headers: dict[str, str] = {}
        for key in ("content-type", "last-modified", "etag"):
            value = headers.get(key)
            if value is not None:
                if type(value) is not str or len(value) > 256 or any(ch in value for ch in "\r\n"):
                    raise P12ReutersRawAcquisitionError("source header invalid")
                safe_headers[key] = value
        observations.append(
            {
                "url": url,
                "host": host,
                "operator_group_unverified": group,
                "http_status": status,
                "request_started_at_utc": _iso(before),
                "response_completed_at_utc": _iso(after),
                "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
                "raw_body_b64": base64.b64encode(raw).decode("ascii"),
                "unverified_http_headers": safe_headers,
                "publication_time_verified": False,
                "reuters_attribution_verified": False,
                "forecast_value_extracted": False,
            }
        )
    completed = _utc(now(), "capture completion")
    last_observed = datetime.fromisoformat(
        observations[-1]["response_completed_at_utc"].replace("Z", "+00:00")
    )
    if completed < last_observed or completed >= end:
        raise P12ReutersRawAcquisitionError("capture finished outside window")
    return {
        "record_type": RECORD_TYPE,
        "stage": "REUTERS_ACQUISITION",
        "source_count": len(observations),
        "observations": observations,
        "window_start_utc": _iso(start),
        "deadline_utc": _iso(end),
        "capture_started_at_utc": _iso(started),
        "capture_completed_at_utc": _iso(completed),
        "transport_mode": "DIRECT_VERIFIED_HTTPS" if getter is None else "INJECTED_TEST_GETTER",
        "selection_policy": "EXPLICIT_URLS_NOT_FROZEN_NO_DISCOVERY",
        "scientific_evidence_authorized": False,
        "source_publication_verified": False,
        "reuters_forecast_authorized": False,
        "phase1_economic_test_authorized": False,
        "p12_frozen": False,
        "promotion_authority": "NONE",
        "production_influence": "0",
    }
