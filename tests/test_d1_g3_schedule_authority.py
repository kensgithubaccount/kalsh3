from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from services.production_gdp_strategy import schedule_authority as subject

FIXTURES = Path(__file__).parent / "fixtures"
ACQUIRED = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
KALSHI_HOST = subject.KALSHI_HOST
KALSHI_ORIGIN = subject.KALSHI_ORIGIN
KALSHI_EVENT_PATH = subject.KALSHI_EVENT_PATH
BEA_HOST = subject.BEA_HOST
BEA_ORIGIN = subject.BEA_ORIGIN
BEA_SCHEDULE_PATH = subject.BEA_SCHEDULE_PATH


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
    seen_hosts: ClassVar[list[str]] = []

    def __init__(self, host: str, *, timeout: float, context: object) -> None:
        self.host, self.path = host, ""
        self.seen_hosts.append(host)

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
        KALSHI_HOST + KALSHI_EVENT_PATH: Response(
            200, "application/json", event_body, event_headers
        ),
        BEA_HOST + BEA_SCHEDULE_PATH: Response(200, "text/html", bea or bea_bytes()),
    }
    Connection.seen_hosts = []
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
    assert not hasattr(subject, "_new_evidence")
    assert not hasattr(subject, "_acquire_evidence")
    assert not hasattr(subject, "_acquire_fixed")
    raw = subject._RawResponse(
        BEA_ORIGIN + BEA_SCHEDULE_PATH,
        BEA_HOST,
        BEA_SCHEDULE_PATH,
        "GET",
        200,
        "text/html",
        bea_bytes(),
        ACQUIRED,
    )
    assert raw.body == bea_bytes()
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
        BEA_HOST + BEA_SCHEDULE_PATH: Response(
            200, "text/html", (FIXTURES / "d1_g3_bea_schedule_multiyear_q4_2025.html").read_bytes()
        )
    }
    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Connection)
    evidence = subject._acquire_bea_schedule()
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


def test_endpoint_descriptors_are_not_module_visible_or_mutable() -> None:
    assert not hasattr(subject, "_ReviewedEndpoint")
    assert not hasattr(subject, "_KALSHI_ENDPOINT")
    assert not hasattr(subject, "_BEA_ENDPOINT")


@pytest.mark.parametrize(
    "constant, replacement",
    [
        ("KALSHI_HOST", "evil.example"),
        ("KALSHI_ORIGIN", "https://evil.example"),
        ("KALSHI_EVENT_PATH", "/evil"),
        ("BEA_HOST", "evil.example"),
        ("BEA_ORIGIN", "https://evil.example"),
        ("BEA_SCHEDULE_PATH", "/evil"),
    ],
)
def test_rebinding_endpoint_constants_does_not_redirect_captured_acquisition(
    monkeypatch: pytest.MonkeyPatch, constant: str, replacement: str
) -> None:
    Connection.responses = {
        KALSHI_HOST + KALSHI_EVENT_PATH: Response(
            200,
            "application/json",
            event_bytes(),
            {
                "x-kalshi-event-market-count": "1",
                "x-kalshi-event-pagination-terminal": "true",
            },
        ),
        BEA_HOST + BEA_SCHEDULE_PATH: Response(200, "text/html", bea_bytes()),
    }
    Connection.seen_hosts = []
    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(subject, "_utc_now", lambda: ACQUIRED)
    monkeypatch.setattr(subject, constant, replacement)
    result = subject.acquire_schedule_authority()
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert Connection.seen_hosts == [KALSHI_HOST, BEA_HOST]


def test_wrong_host_transport_cannot_masquerade_as_reviewed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WrongHostConnection(Connection):
        def __init__(self, host: str, *, timeout: float, context: object) -> None:
            super().__init__("evil.example", timeout=timeout, context=context)
            self.requested_host = host

        def getresponse(self) -> Response:
            raise OSError(f"unexpected transport host: {self.host}")

    monkeypatch.setattr(subject.http.client, "HTTPSConnection", WrongHostConnection)
    monkeypatch.setattr(subject, "_utc_now", lambda: ACQUIRED)
    result = subject.acquire_schedule_authority()
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_host", "evil.example"),
        ("source_locator", "https://evil.example/evil"),
        ("source_path", "/evil"),
        ("source_identity", "self-computed"),
    ],
)
def test_evidence_provenance_mutation_invalidates_authority(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    assert result.event_evidence is not None
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.event_evidence, field, value)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)


def _forged_source_evidence(**overrides: object) -> subject.SourceEvidence:
    forged = object.__new__(subject.SourceEvidence)
    values: dict[str, object] = {
        "source_locator": KALSHI_ORIGIN + KALSHI_EVENT_PATH,
        "source_host": KALSHI_HOST,
        "source_path": KALSHI_EVENT_PATH,
        "method": "GET",
        "http_status": 200,
        "content_type": "application/json",
        "content_length": len(event_bytes()),
        "acquired_at": ACQUIRED,
        "parser_version": subject.PARSER_VERSION,
        "raw_body": event_bytes(),
        "raw_sha256": hashlib.sha256(event_bytes()).hexdigest(),
        "headers": (
            ("x-kalshi-event-market-count", "1"),
            ("x-kalshi-event-pagination-terminal", "true"),
        ),
        "source_identity": "forged",
    }
    values.update(overrides)
    for name, value in values.items():
        object.__setattr__(forged, name, value)
    return forged


@pytest.mark.parametrize(
    "kalshi_noop, bea_noop",
    [(True, False), (False, True), (True, True)],
)
def test_validator_rebinding_does_not_weaken_issuance(
    monkeypatch: pytest.MonkeyPatch, kalshi_noop: bool, bea_noop: bool
) -> None:
    if kalshi_noop:
        monkeypatch.setattr(subject, "_validate_kalshi_evidence", lambda _: None)
    if bea_noop:
        monkeypatch.setattr(subject, "_validate_bea_evidence", lambda _: None)
    forged_event = _forged_source_evidence()
    forged_bea = _forged_source_evidence(
        source_locator=BEA_ORIGIN + BEA_SCHEDULE_PATH,
        source_host=BEA_HOST,
        source_path=BEA_SCHEDULE_PATH,
        content_type="text/html",
        raw_body=bea_bytes(),
        content_length=len(bea_bytes()),
        raw_sha256=hashlib.sha256(bea_bytes()).hexdigest(),
        headers=(),
    )
    result = subject._issue_authority(forged_event, None, forged_bea)
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    assert result.authority is None


@pytest.mark.parametrize("both", [True, False])
def test_validator_rebinding_does_not_weaken_forged_authority_validation(
    monkeypatch: pytest.MonkeyPatch, both: bool
) -> None:
    monkeypatch.setattr(subject, "_validate_kalshi_evidence", lambda _: None)
    if both:
        monkeypatch.setattr(subject, "_validate_bea_evidence", lambda _: None)
    forged = object.__new__(subject.ScheduleAuthority)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(forged)


def test_validator_rebinding_does_not_mask_linked_evidence_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    assert result.event_evidence is not None
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.event_evidence, "raw_body", b"forged-after-issuance")
    monkeypatch.setattr(subject, "_validate_kalshi_evidence", lambda _: None)
    monkeypatch.setattr(subject, "_validate_bea_evidence", lambda _: None)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)


def test_caller_created_source_evidence_cannot_produce_complete_authority() -> None:
    forged_event = _forged_source_evidence()
    forged_bea = _forged_source_evidence(
        source_locator=BEA_ORIGIN + BEA_SCHEDULE_PATH,
        source_host=BEA_HOST,
        source_path=BEA_SCHEDULE_PATH,
        content_type="text/html",
        raw_body=bea_bytes(),
    )
    result = subject._issue_authority(forged_event, None, forged_bea)
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


def test_populated_forged_evidence_remains_rejected() -> None:
    forged_event = _forged_source_evidence(
        headers=(
            ("x-kalshi-event-market-count", "1"),
            ("x-kalshi-event-pagination-terminal", "true"),
        )
    )
    forged_bea = _forged_source_evidence(
        source_locator=BEA_ORIGIN + BEA_SCHEDULE_PATH,
        source_host=BEA_HOST,
        source_path=BEA_SCHEDULE_PATH,
        content_type="text/html",
        raw_body=bea_bytes(),
        content_length=len(bea_bytes()),
    )
    result = subject._issue_authority(forged_event, None, forged_bea)
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(object.__new__(subject.ScheduleAuthority))


def test_self_computed_evidence_hash_remains_insufficient() -> None:
    body = event_bytes()
    identity = subject._fingerprint(
        subject.TRANSPORT_POLICY_IDENTITY,
        "kalshi-kxgdp-event-v1",
        KALSHI_ORIGIN + KALSHI_EVENT_PATH,
        KALSHI_HOST,
        KALSHI_EVENT_PATH,
        "GET",
        200,
        "application/json",
        len(body),
        ACQUIRED,
        subject.PARSER_VERSION,
        body,
        hashlib.sha256(body).hexdigest(),
        (),
    )
    forged_event = _forged_source_evidence(source_identity=identity, headers=())
    forged_bea = _forged_source_evidence(
        source_locator=BEA_ORIGIN + BEA_SCHEDULE_PATH,
        source_host=BEA_HOST,
        source_path=BEA_SCHEDULE_PATH,
        content_type="text/html",
        raw_body=bea_bytes(),
        content_length=len(bea_bytes()),
    )
    result = subject._issue_authority(forged_event, None, forged_bea)
    assert result.status is subject.AuthorityStatus.EVIDENCE_INCOMPLETE


def test_rebinding_json_helper_does_not_redirect_legitimate_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "_json",
        lambda evidence: {
            "event": {
                "event_ticker": subject.EVENT_TICKER,
                "series_ticker": subject.SERIES_TICKER,
                "markets": [{"forged": True}],
                "market_count": 1,
            }
        },
    )
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.eligible_markets[0].strike.floor_strike == "1.0"


def test_rebinding_bea_helper_does_not_redirect_legitimate_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "_bea",
        lambda evidence, expected, bea_origin=subject.BEA_ORIGIN: (_ for _ in ()).throw(
            AssertionError("rebound _bea must not be reachable from the frozen issuer")
        ),
    )
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.bea_release_locator.endswith("gdp-advance-estimate-third-quarter-2026")


def test_rebinding_market_helper_does_not_redirect_legitimate_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "_market",
        lambda raw, quarter: (_ for _ in ()).throw(
            AssertionError("rebound _market must not be reachable from the frozen issuer")
        ),
    )
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert len(result.authority.eligible_markets) == 1


def test_rebinding_authority_fingerprint_does_not_weaken_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    monkeypatch.setattr(subject, "_authority_fingerprint", lambda values: "forged-constant")
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.authority, "target_quarter", "2099-Q1")
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)


@pytest.mark.parametrize("constant", ["EVENT_TICKER", "SERIES_TICKER"])
def test_rebinding_event_series_identity_constants_does_not_relabel_authority(
    monkeypatch: pytest.MonkeyPatch, constant: str
) -> None:
    monkeypatch.setattr(subject, constant, "evil-identity")
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert result.authority.event_ticker == "KXGDP-26OCT30"
    assert result.authority.series_ticker == "KXGDP"
    subject.validate_schedule_authority(result.authority)


@pytest.mark.parametrize("constant", ["METRIC_SEMANTICS", "SETTLEMENT_EDITION"])
def test_rebinding_metric_edition_identity_constants_does_not_relabel_authority(
    monkeypatch: pytest.MonkeyPatch, constant: str
) -> None:
    original = getattr(subject, constant)
    monkeypatch.setattr(subject, constant, "evil-semantics")
    result = acquire(monkeypatch)
    assert result.status is subject.AuthorityStatus.COMPLETE_AUTHORITY
    assert result.authority is not None
    assert getattr(result.authority, constant.lower()) == original
    subject.validate_schedule_authority(result.authority)


def test_rebinding_evidence_fingerprint_does_not_mask_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    assert result.event_evidence is not None
    subject.validate_schedule_authority(result.authority)
    stale_identity = result.event_evidence.source_identity
    monkeypatch.setattr(subject, "_evidence_fingerprint", lambda evidence: object())
    object.__setattr__(result.event_evidence, "raw_body", b"forged-after-issuance")
    object.__setattr__(result.event_evidence, "source_identity", stale_identity)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)


def test_forged_authority_from_object_new_is_unissued() -> None:
    forged = object.__new__(subject.ScheduleAuthority)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(forged)


def test_copied_legitimate_authority_values_remain_unissued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    copy = object.__new__(subject.ScheduleAuthority)
    for name in subject.ScheduleAuthority.__dataclass_fields__:
        object.__setattr__(copy, name, getattr(result.authority, name))
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(copy)


def test_legitimate_evidence_attached_to_forged_authority_does_not_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    forged = object.__new__(subject.ScheduleAuthority)
    for name in subject.ScheduleAuthority.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(result.authority, name))
    object.__setattr__(forged, "target_quarter", result.authority.quarter.canonical)
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(forged)


def test_post_issuance_top_level_authority_mutation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    result = acquire(monkeypatch)
    assert result.authority is not None
    subject.validate_schedule_authority(result.authority)
    object.__setattr__(result.authority, "target_quarter", "2099-Q1")
    with pytest.raises(subject.ScheduleAuthorityError):
        subject.validate_schedule_authority(result.authority)


def test_no_module_visible_authority_registry_or_registration_helper() -> None:
    assert not hasattr(subject, "_AUTHORITY_REGISTRY")
    assert not hasattr(subject, "_ISSUED_AUTHORITIES")
    assert not hasattr(subject, "_register_authority")
    assert not hasattr(subject, "_bless_authority")
