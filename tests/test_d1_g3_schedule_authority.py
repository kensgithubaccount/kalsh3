from __future__ import annotations

import json
from datetime import UTC, datetime
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
    def __init__(
        self, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.status, self.content_type, self.body = status, content_type, body
        self.headers = headers or {}

    def getheader(self, name: str, default: str | None = None) -> str | None:
        if name.casefold() == "content-type":
            return self.content_type
        return self.headers.get(name.casefold(), default)

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
    event_body = event or event_bytes()
    event_payload = json.loads(event_body)
    nested_count = len(event_payload["event"]["markets"])
    # The independent source contract is unchanged when only a body field is
    # rewritten; a genuinely acquired alternate-strike fixture carries a
    # matching contract count.
    market_count = nested_count if event_payload["event"].get("market_count") == nested_count else 1
    event_headers = {
        "x-kalshi-event-market-count": str(market_count),
        "x-kalshi-event-pagination-terminal": "true",
    }
    Connection.responses = {
        subject.KALSHI_HOST + subject.KALSHI_EVENT_PATH: Response(
            200, "application/json", event_body, event_headers
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
    market.update(
        ticker="KXGDP-26OCT30-T2.0",
        floor_strike="2.0",
        rules_primary=market["rules_primary"].replace("more than 1.0", "more than 2.0"),
    )
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
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


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


def test_caller_raw_response_and_direct_helpers_cannot_issue_authority() -> None:
    assert not hasattr(subject, "_RAW_REGISTRY")
    raw = subject._RawResponse(
        subject.BEA_ORIGIN + subject.BEA_SCHEDULE_PATH,
        subject.BEA_HOST,
        subject.BEA_SCHEDULE_PATH,
        "GET",
        200,
        "text/html",
        bea_bytes(),
        ACQUIRED,
    )
    with pytest.raises(subject.ScheduleAuthorityError):
        subject._new_evidence(raw)
    forged_evidence = object.__new__(subject.SourceEvidence)
    result = subject._issue_authority(forged_evidence, None, forged_evidence)
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


@pytest.mark.parametrize("close", ["2026-06-24T14:00:00Z", "2026-06-24T13:59:59Z"])
def test_open_not_strictly_before_close_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, close: str
) -> None:
    p = payload()
    p["event"]["markets"][0]["close_time"] = close  # type: ignore[index]
    assert (
        acquire(monkeypatch, event=json.dumps(p).encode()).status
        is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    )


def test_quarter_forms_normalize_independently() -> None:
    assert subject._quarter("Q3 2026", "quarter").canonical == "2026-Q3"
    assert subject._quarter("2026-Q3", "quarter").canonical == "2026-Q3"
    with pytest.raises(subject.ScheduleAuthorityError):
        subject._quarters(["Q3 2026", "2026-Q4"])


def test_q4_publication_uses_following_source_year(monkeypatch: pytest.MonkeyPatch) -> None:
    p = payload()
    p["event"]["strike_period"] = "2025-Q4"  # type: ignore[index]
    p["event"]["title"] = "US real GDP growth in 2025-Q4?"  # type: ignore[index]
    p["event"]["sub_title"] = "In 2025-Q4"  # type: ignore[index]
    market = p["event"]["markets"][0]  # type: ignore[index]
    market["rules_primary"] = market["rules_primary"].replace("Q3 2026", "2025-Q4")
    market["rules_secondary"] = market["rules_secondary"].replace("Q3 2026", "2025-Q4")
    market["close_time"] = "2026-10-29T12:29:00Z"
    market["expected_expiration_time"] = "2026-10-29T14:00:00Z"
    body = bea_bytes().decode().replace("3rd Quarter 2026", "4th Quarter 2025")
    body = body.replace("third-quarter-2026", "fourth-quarter-2025")
    result = acquire(monkeypatch, event=json.dumps(p).encode(), bea=body.encode())
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.bea_release_at.year == 2026


def test_multiyear_page_binds_year_and_locator_to_selected_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Connection.responses = {
        subject.BEA_HOST + subject.BEA_SCHEDULE_PATH: Response(
            200, "text/html", (FIXTURES / "d1_g3_bea_schedule_multiyear_q4_2025.html").read_bytes()
        )
    }
    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Connection)
    evidence = subject._acquire(
        subject.BEA_HOST, subject.BEA_ORIGIN, subject.BEA_SCHEDULE_PATH, ("text/html",)
    )
    schedule = subject._bea(evidence, subject.Quarter("2025-Q4", "2025-Q4"))
    assert schedule.release_at.year == 2026
    assert schedule.bea_release_locator.endswith("gdp-advance-estimate-fourth-quarter-2025")


def test_nested_market_mutation_invalidates_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.authority.eligible_markets[0].strike, "floor_strike", "9.0")
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)
