from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from services.production_gdp_strategy import schedule_authority as subject

FIXTURES = Path(__file__).parent / "fixtures"
ACQUIRED = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def event_bytes() -> bytes:
    return (FIXTURES / "d1_g3_kxgdp_q3_2026_event.json").read_bytes()


def bea_bytes() -> bytes:
    return (FIXTURES / "d1_g3_bea_schedule_q3_2026.html").read_bytes()


class Response:
    def __init__(self, status: int, content_type: str, body: bytes) -> None:
        self.status, self.content_type, self.body = status, content_type, body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.content_type if name.casefold() == "content-type" else default

    def read(self, limit: int = -1) -> bytes:
        return self.body[:limit] if limit >= 0 else self.body


class Connection:
    responses: ClassVar[dict[str, Response]] = {}

    def __init__(self, host: str, *, timeout: float, context: object) -> None:
        self.host, self.path = host, ""

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.path = path

    def getresponse(self) -> Response:
        return self.responses[self.host + self.path]

    def close(self) -> None:
        pass


def acquire(
    monkeypatch: pytest.MonkeyPatch, *, event: bytes | None = None, bea: bytes | None = None
) -> subject.ScheduleAuthorityResult:
    Connection.responses = {
        subject.KALSHI_HOST + subject.KALSHI_EVENT_PATH: Response(
            200, "application/json", event or event_bytes()
        ),
        subject.BEA_HOST + subject.BEA_SCHEDULE_PATH: Response(
            200, "text/html", bea or bea_bytes()
        ),
    }
    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(subject, "_utc_now", lambda: ACQUIRED)
    return subject.acquire_schedule_authority()


def payload() -> dict[str, object]:
    return json.loads(event_bytes())


def test_positive_authority_exposes_complete_set_without_strategy_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.target_quarter == "2026-Q3"
    assert (
        result.authority.bea_release_locator
        == "https://www.bea.gov/news/2026/gdp-advance-estimate-third-quarter-2026"
    )
    assert len(result.authority.eligible_markets) == 1
    assert result.authority.eligible_markets[0].strike.floor_strike == "1.0"
    assert not hasattr(result.authority, "market_ticker")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["event"].update(market_count=2),
        lambda p: p["event"].update(pagination={"cursor": "next"}),
    ],
)
def test_incomplete_market_result_set(monkeypatch: pytest.MonkeyPatch, mutation: object) -> None:
    p = payload()
    mutation(p)  # type: ignore[operator]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "closed"},
        {"event_ticker": "OTHER"},
        {"strike_type": "unknown"},
        {"floor_strike": "2.0"},
        {"cap_strike": "2.0"},
    ],
)
def test_market_semantics_fail_closed(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    p = payload()
    p["event"]["markets"][0].update(change)  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_duplicate_ticker_and_equivalent_strike_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = payload()
    market = dict(p["event"]["markets"][0])  # type: ignore[index]
    p["event"]["markets"].append(market)  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_alternate_legitimate_strike_is_representable(monkeypatch: pytest.MonkeyPatch) -> None:
    p = payload()
    market = dict(p["event"]["markets"][0])  # type: ignore[index]
    market.update(ticker="KXGDP-26OCT30-T2.0", floor_strike="2.0")
    p["event"]["markets"].append(market)  # type: ignore[index]
    p["event"]["market_count"] = 2  # type: ignore[index]
    result = acquire(monkeypatch, event=json.dumps(p).encode())
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert [m.ticker for m in result.authority.eligible_markets] == [
        "KXGDP-26OCT30-T1.0",
        "KXGDP-26OCT30-T2.0",
    ]  # type: ignore[union-attr]


@pytest.mark.parametrize("replacement", ["Q2 2026", "Q3 2025", "Q2 2026 and Q3 2026"])
def test_quarter_normalization_and_contradiction(
    monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    p = payload()
    p["event"]["strike_period"] = replacement  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


@pytest.mark.parametrize(
    "replacement", ["Nominal GDP", "GDP (Second Estimate)", "GDP (Third Estimate)", "revised GDP"]
)
def test_material_rule_changes_fail_closed(
    monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    p = payload()
    p["event"]["markets"][0]["rules_primary"] = replacement  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_missing_or_generated_locator_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        bea_bytes()
        .decode()
        .replace('href="/news/2026/gdp-advance-estimate-third-quarter-2026"', "", 1)
    )
    assert (
        acquire(monkeypatch, bea=body.encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_date_only_release_is_not_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    body = bea_bytes().decode().replace("8:30 AM", "", 1)
    result = acquire(monkeypatch, bea=body.encode())
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert isinstance(result.authority.bea_release_at, date)  # type: ignore[union-attr]


def test_conflicting_timing_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    p = payload()
    p["event"]["markets"][0]["close_time"] = "2026-10-29T12:31:00Z"  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_forged_and_mutated_authority_paths_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.event_evidence, "raw_body", b"forged")  # type: ignore[arg-type]
    assert acquire(monkeypatch).status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.ScheduleAuthority()
    forged = object.__new__(subject.ScheduleAuthority)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(forged)


def test_public_contract_is_research_only() -> None:
    assert "T1.0" not in subject.acquire_schedule_authority.__doc__
    assert "order" not in subject.__doc__.casefold()
