from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from services.production_gdp_strategy import schedule_authority as subject

FIXTURES = Path(__file__).parent / "fixtures"
EVENT_PATH = subject.KALSHI_EVENT_PATH
MARKET_PATH = subject.KALSHI_MARKET_PATH
BEA_PATH = subject.BEA_SCHEDULE_PATH
ACQUIRED = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _event() -> bytes:
    return (FIXTURES / "d1_g3_kxgdp_q3_2026_event.json").read_bytes()


def _market(close: str | None = None, **changes: object) -> bytes:
    payload = json.loads(_event())
    value = payload["event"]["markets"][0]
    assert isinstance(value, dict)
    if close is not None:
        value["close_time"] = close
    value.update(changes)
    return json.dumps({"market": value}, separators=(",", ":")).encode()


def _bea(body: str | None = None) -> bytes:
    if body is None:
        return (FIXTURES / "d1_g3_bea_schedule_q3_2026.html").read_bytes()
    return body.encode()


def _issued(path: str, body: bytes, content_type: str) -> subject.SourceEvidence:
    response = subject._RawResponse(
        locator=(subject.BEA_ORIGIN if path == BEA_PATH else subject.KALSHI_ORIGIN) + path,
        host=subject.BEA_HOST if path == BEA_PATH else subject.KALSHI_HOST,
        path=path,
        method="GET",
        status=200,
        content_type=content_type,
        body=body,
        acquired_at=ACQUIRED,
    )
    return subject.SourceEvidence(response=response, _capability=subject._AUTHORITY_ISSUER)


def _result(
    *,
    event: bytes | None = None,
    market: bytes | None = None,
    bea: bytes | None = None,
) -> subject.ScheduleAuthorityResult:
    return subject._issue_authority(
        _issued(EVENT_PATH, event or _event(), "application/json"),
        _issued(MARKET_PATH, market or _market(), "application/json"),
        _issued(BEA_PATH, bea or _bea(), "text/html"),
    )


class _FakeResponse:
    def __init__(self, status: int, content_type: str, body: bytes) -> None:
        self.status = status
        self._content_type = content_type
        self._body = body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._content_type if name.casefold() == "content-type" else default

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


class _FakeConnection:
    responses: ClassVar[dict[str, _FakeResponse]] = {}

    def __init__(self, host: str, *, timeout: float, context: object) -> None:
        self.host = host

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        assert method == "GET"

    def getresponse(self) -> _FakeResponse:
        key = self.host + self._path  # type: ignore[attr-defined]
        return self.responses[key]

    def close(self) -> None:
        return None


def _network_result(
    monkeypatch: pytest.MonkeyPatch,
    *,
    event: bytes | None = None,
    market: bytes | None = None,
    bea: bytes | None = None,
    status: int = 200,
    content_type: str = "text/html",
) -> subject.ScheduleAuthorityResult:
    class Connection(_FakeConnection):
        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            super().request(method, path, headers=headers)
            self._path = path

    Connection.responses = {
        subject.KALSHI_HOST + EVENT_PATH: _FakeResponse(200, "application/json", event or _event()),
        subject.KALSHI_HOST + MARKET_PATH: _FakeResponse(
            200, "application/json", market or _market()
        ),
        subject.BEA_HOST + BEA_PATH: _FakeResponse(status, content_type, bea or _bea()),
    }
    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(subject, "_utc_now", lambda: ACQUIRED)
    return subject.acquire_schedule_authority()


def test_current_q3_fixture_is_incomplete() -> None:
    result = _result()
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    assert result.contradiction == "KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE"


def test_valid_matching_schedule_is_complete() -> None:
    event_payload = json.loads(_event())
    event_payload["event"]["markets"][0]["close_time"] = "2026-10-29T12:29:00Z"
    event_payload["event"]["markets"][0]["expected_expiration_time"] = "2026-10-29T14:00:00Z"
    result = _result(
        event=json.dumps(event_payload).encode(),
        market=_market(
            close="2026-10-29T12:29:00Z",
            expected_expiration_time="2026-10-29T14:00:00Z",
        ),
        bea=_bea(_bea().decode().replace("October 29", "October 29", 1)),
    )
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.bea_release_at == datetime(2026, 10, 29, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "replacement", ["GDP (Second Estimate)", "GDP (Third Estimate)", "Annual GDP"]
)
def test_wrong_edition_or_annual_gdp_is_incomplete(replacement: str) -> None:
    body = _bea().decode().replace("GDP (Advance Estimate)", replacement, 1)
    assert _result(bea=body.encode()).status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


@pytest.mark.parametrize("field,value", [("strike_period", "Q2 2026"), ("title", "Nominal GDP")])
def test_wrong_quarter_or_metric_is_incomplete(field: str, value: str) -> None:
    payload = json.loads(_event())
    if field == "strike_period":
        payload["event"][field] = value
    else:
        payload["event"]["markets"][0]["rules_primary"] = value
    assert (
        _result(event=json.dumps(payload).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_missing_time_duplicate_match_and_layout_drift_are_incomplete() -> None:
    no_time = _bea().decode().replace("8:30 AM", "", 1)
    duplicate_row = (
        "<tr><td>October 29</td><td>8:30 AM</td><td>News</td>"
        "<td>GDP (Advance Estimate), 3rd Quarter 2026</td></tr>"
    )
    duplicate = _bea().decode().replace("</table>", duplicate_row + "</table>")
    drift = _bea().decode().replace("Year 2026", "Calendar", 1)
    for body in (no_time, duplicate, drift):
        assert _result(bea=body.encode()).status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


def test_kalshi_identity_rules_and_timestamps_fail_closed() -> None:
    payload = json.loads(_event())
    payload["event"]["markets"][0]["updated_time"] = "0001-01-01T00:00:00Z"
    assert (
        _result(event=json.dumps(payload).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )
    assert (
        _result(market=_market(event_ticker="OTHER-EVENT")).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )
    assert (
        _result(market=_market(open_time="2026-10-30T12:30:00Z")).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_malformed_kalshi_json_is_incomplete() -> None:
    assert _result(event=b"not-json").status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


def test_close_after_release_is_incomplete() -> None:
    event_payload = json.loads(_event())
    event_payload["event"]["markets"][0]["close_time"] = "2026-10-29T12:31:00Z"
    result = _result(
        event=json.dumps(event_payload).encode(),
        market=_market(close="2026-10-29T12:31:00Z"),
    )
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    assert result.contradiction == "KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE"


def test_dst_ambiguous_and_nonexistent_local_times_fail_closed() -> None:
    for value in ("2026-11-01 01:30 AM", "2026-03-08 02:30 AM"):
        with pytest.raises(subject.ScheduleAuthorityError):
            subject._strict_local(value, "fixture")


def test_network_boundaries_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _network_result(monkeypatch, status=503).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )
    assert (
        _network_result(monkeypatch, content_type="application/json").status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_redirect_and_oversized_response_are_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _network_result(monkeypatch, status=302).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )

    class OversizedConnection(_FakeConnection):
        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            super().request(method, path, headers=headers)
            self._path = path

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse(200, "text/html", b"x" * (subject.MAX_RESPONSE_BYTES + 1))

    monkeypatch.setattr(subject.http.client, "HTTPSConnection", OversizedConnection)
    monkeypatch.setattr(subject, "_utc_now", lambda: ACQUIRED)
    assert (
        subject.acquire_schedule_authority().status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_caller_cannot_issue_source_evidence_or_authority() -> None:
    response = subject._RawResponse(
        locator=subject.BEA_ORIGIN + BEA_PATH,
        host=subject.BEA_HOST,
        path=BEA_PATH,
        method="GET",
        status=200,
        content_type="text/html",
        body=_bea(),
        acquired_at=ACQUIRED,
    )
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.SourceEvidence(response=response, _capability=object())
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.ScheduleAuthority(values={}, _capability=object())


def test_raw_substitution_and_identity_mismatch_are_incomplete() -> None:
    event = _issued(EVENT_PATH, _event(), "application/json")
    market = _issued(
        MARKET_PATH,
        _market(close="2026-10-29T12:29:00Z", expected_expiration_time="2026-10-29T14:00:00Z"),
        "application/json",
    )
    bea = _issued(BEA_PATH, _bea(), "text/html")
    object.__setattr__(event, "raw_body", b"substituted")
    assert (
        subject._issue_authority(event, market, bea).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_reconstructed_evidence_timestamp_is_incomplete() -> None:
    event = _issued(EVENT_PATH, _event(), "application/json")
    market = _issued(
        MARKET_PATH,
        _market(close="2026-10-29T12:29:00Z", expected_expiration_time="2026-10-29T14:00:00Z"),
        "application/json",
    )
    bea = _issued(BEA_PATH, _bea(), "text/html")
    object.__setattr__(event, "acquired_at", datetime(2026, 9, 10, 12, 0))
    assert (
        subject._issue_authority(event, market, bea).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )
    event = _issued(EVENT_PATH, _event(), "application/json")
    object.__setattr__(event, "source_identity", "wrong")
    assert (
        subject._issue_authority(event, market, bea).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_authority_evidence_identity_is_bound() -> None:
    event_payload = json.loads(_event())
    event_payload["event"]["markets"][0]["close_time"] = "2026-10-29T12:29:00Z"
    event_payload["event"]["markets"][0]["expected_expiration_time"] = "2026-10-29T14:00:00Z"
    result = _result(
        event=json.dumps(event_payload).encode(),
        market=_market(
            close="2026-10-29T12:29:00Z", expected_expiration_time="2026-10-29T14:00:00Z"
        ),
    )
    assert result.authority is not None
    assert result.authority.kalshi_event_sha256 == result.event_evidence.raw_sha256  # type: ignore[union-attr]
    assert result.authority.bea_schedule_sha256 == result.bea_evidence.raw_sha256  # type: ignore[union-attr]
