from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_d1_g2_p1_one_decision import FixtureSource, _clock, _gdpnow

import services.production_gdp_strategy.settlement_authority as authority
from services.production_gdp_strategy.one_decision import (
    DecisionReceipt,
    _evaluate_fixture_decision,
    _TransportResponse,
    replay_decision,
)

TICKER = "KXGDP-26SEP03-T4.5"
EVENT = "KXGDP-26SEP03"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _market(
    *,
    status: str = "finalized",
    result: str | None = "yes",
    ticker: str = TICKER,
    event: str = EVENT,
    threshold: str = "4.5",
    **extra: object,
) -> authority._Evidence:
    market: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": event,
        "series_ticker": "KXGDP",
        "market_type": "binary",
        "title": "US real GDP Advance Estimate",
        "subtitle": "Q3 2026",
        "rules_primary": (
            "US real GDP seasonally adjusted annualized BEA Advance Estimate more than 4.5"
        ),
        "rules_secondary": "Settlement is based on the reported percent value.",
        "status": status,
        "result": result,
        "settlement_ts": NOW.isoformat(),
        "settlement_value_dollars": "1.00" if result == "yes" else "0.00",
        "expiration_value": "4.7" if result == "yes" else "4.2",
        "updated_time": NOW.isoformat(),
        "threshold": threshold,
    }
    market.update(extra)
    body = json.dumps({"market": market}, sort_keys=True).encode()
    return authority._Evidence(
        source=authority.KALSHI_ORIGIN,
        path=f"{authority.KALSHI_PATH_PREFIX}{ticker}",
        body=body,
        acquired_at=NOW,
        capability=authority._MARKET_CAPABILITY,
    )


def _bea(
    *, value: str = "4.7", quarter: str = "2026-Q3", text_extra: str = ""
) -> authority._Evidence:
    ordinal = ("first", "second", "third", "fourth")[int(quarter[-1]) - 1]
    body = (
        f"<h1>Gross Domestic Product, {ordinal.title()} Quarter {quarter[:4]} "
        f"(Advance Estimate)</h1>\n"
        f"EMBARGOED UNTIL RELEASE AT 8:30 a.m., September 3, 2026\n"
        f"<p>Real gross domestic product (GDP) increased at an annual rate of {value} percent "
        f"in the {ordinal} quarter of {quarter[:4]}.</p>\n{text_extra}"
    ).encode()
    return authority._Evidence(
        source=authority.BEA_ORIGIN,
        path=f"{authority.BEA_ORIGIN}/news/2027/reviewed-release-{quarter}",
        body=body,
        acquired_at=NOW,
        capability=authority._BEA_CAPABILITY,
    )


def _decision() -> DecisionReceipt:
    evidence, vintage = _gdpnow()

    class BoundedFixtureSource(FixtureSource):
        def schedule(self) -> _TransportResponse:
            response = super().schedule()
            payload = json.loads(response.body)
            payload["schedule"]["bea_release_locator"] = (
                "https://www.bea.gov/news/2027/gross-domestic-product-third-quarter-2026-advance-estimate"
            )
            return _TransportResponse(response.path, response.status, json.dumps(payload).encode())

    decision = _evaluate_fixture_decision(
        source=BoundedFixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    replay_decision(decision)
    return decision


def _run(
    monkeypatch: pytest.MonkeyPatch, market: authority._Evidence, bea: authority._Evidence
) -> authority.SettlementAuthority:
    decision = _decision()
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: market)
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: bea)
    return authority.acquire_settlement_authority(decision)


@pytest.mark.parametrize("result,value", [("yes", "4.7"), ("no", "4.2")])
def test_finalized_yes_and_no_reconcile(
    monkeypatch: pytest.MonkeyPatch, result: str, value: str
) -> None:
    market = _market(result=result, threshold="4.5")
    if result == "no":
        market = _market(result=result, threshold="4.5")
    result_obj = _run(monkeypatch, market, _bea(value=value))
    assert result_obj.authority_state is authority.AuthorityState.COMPLETE_SETTLEMENT_AUTHORITY
    assert result_obj.winning_selected_contract_payout == Decimal("1.00")
    assert result_obj.losing_selected_contract_payout == Decimal("0.00")
    assert result_obj.research_only and result_obj.production_influence == Decimal("0")


@pytest.mark.parametrize("status", ["closed", "determined", "disputed", "amended", "", "unknown"])
def test_non_final_lifecycle_states_fail_closed(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market(status=status))
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, _market(status=status), _bea())


@pytest.mark.parametrize(
    "changes",
    [
        {"result": None},
        {"result": "maybe"},
        {"settlement_ts": None},
        {"ticker": "KXGDP-26SEP03-T5.0"},
        {"event_ticker": "KXGDP-26SEP10"},
        {"rules_primary": "nominal GDP, Second Estimate more than threshold"},
    ],
)
def test_malformed_or_wrong_market_evidence_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    monkeypatch.setattr(
        authority,
        "_acquire_market",
        lambda _ticker: _market(**changes),  # type: ignore[arg-type]
    )
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, _market(**changes), _bea())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "extra", ["Prior Second Estimate", "Prior Third Estimate", "Prior revised value"]
)
def test_prior_revision_discussion_does_not_reject_current_advance(
    monkeypatch: pytest.MonkeyPatch, extra: str
) -> None:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market())
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: _bea(text_extra=extra))
    result = _run(monkeypatch, _market(), _bea(text_extra=extra))
    assert result.authority_state is authority.AuthorityState.COMPLETE_SETTLEMENT_AUTHORITY


def test_bea_implied_contradiction_is_preserved_as_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, _market(result="yes"), _bea(value="4.2"))
    assert result.authority_state is authority.AuthorityState.OUTCOME_EVIDENCE_INCOMPLETE
    assert (
        not result.reconciled
        and result.kalshi_result == "yes"
        and result.bea_implied_result == "no"
    )


def test_caller_authored_or_reconstructed_evidence_has_no_authority() -> None:
    evidence = _market()
    forged = authority._Evidence.__new__(authority._Evidence)
    object.__setattr__(forged, "source", evidence.source)
    object.__setattr__(forged, "path", evidence.path)
    object.__setattr__(forged, "raw_body", evidence.raw_body)
    object.__setattr__(forged, "raw_sha256", evidence.raw_sha256)
    object.__setattr__(forged, "acquired_at", evidence.acquired_at)
    object.__setattr__(forged, "evidence_id", evidence.evidence_id)
    with pytest.raises(authority.SettlementAuthorityError):
        authority._parse_market(forged, _decision())


def test_bea_wrong_quarter_and_missing_value_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market())
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: _bea(quarter="2026-Q2"))
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, _market(), _bea(quarter="2026-Q2"))


def test_decreased_gdp_is_negative_and_release_is_date_only() -> None:
    evidence = _bea(value="0.5")
    body = evidence.raw_body.replace(b"increased", b"decreased")
    evidence = authority._Evidence(
        source=authority.BEA_ORIGIN,
        path=evidence.path,
        body=body,
        acquired_at=NOW,
        capability=authority._BEA_CAPABILITY,
    )
    edition, value, release_date, _ = authority._parse_bea(evidence, "2026-Q3")
    assert edition == "BEA Advance Estimate"
    assert value == Decimal("-0.5")
    assert release_date.isoformat() == "2026-09-03"
    assert not isinstance(release_date, datetime)


def test_contradictory_gdp_direction_is_rejected() -> None:
    evidence = _bea()
    evidence = authority._Evidence(
        source=authority.BEA_ORIGIN,
        path=evidence.path,
        body=evidence.raw_body.replace(
            b"increased at an annual rate", b"increased and decreased at an annual rate"
        ),
        acquired_at=NOW,
        capability=authority._BEA_CAPABILITY,
    )
    with pytest.raises(authority.SettlementAuthorityError):
        authority._parse_bea(evidence, "2026-Q3")


def test_q4_release_locator_can_be_in_following_calendar_year() -> None:
    evidence = _bea(value="0.5", quarter="2025-Q4")
    edition, value, release_date, _ = authority._parse_bea(evidence, "2025-Q4")
    assert edition == "BEA Advance Estimate" and value == Decimal("0.5")
    assert release_date == datetime(2026, 9, 3, tzinfo=UTC).date()


def test_missing_or_ambiguous_release_time_does_not_create_an_instant() -> None:
    evidence = _bea()
    assert isinstance(authority._parse_bea(evidence, "2026-Q3")[2], type(datetime.now().date()))
    missing = authority._Evidence(
        source=authority.BEA_ORIGIN,
        path=evidence.path,
        body=evidence.raw_body.replace(
            b"EMBARGOED UNTIL RELEASE AT 8:30 a.m., September 3, 2026", b"Release time unavailable"
        ),
        acquired_at=NOW,
        capability=authority._BEA_CAPABILITY,
    )
    with pytest.raises(authority.SettlementAuthorityError):
        authority._parse_bea(missing, "2026-Q3")


@pytest.mark.parametrize(
    "changes",
    [
        {"expiration_value": "4.2"},
        {"settlement_value_dollars": "0.00"},
        {
            "rules_primary": (
                "US real GDP seasonally adjusted annualized BEA Advance Estimate "
                "more than 4.5 and less than 5.0"
            )
        },
        {
            "rules_primary": (
                "US real GDP seasonally adjusted annualized BEA Advance Estimate more than 5.0"
            )
        },
        {"market_type": "multivariate"},
    ],
)
def test_conflicting_rule_settlement_and_payout_fields_fail_closed(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, _market(**changes), _bea())  # type: ignore[arg-type]


def test_settlement_before_release_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    before_release = (NOW - timedelta(days=1)).isoformat()
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, _market(settlement_ts=before_release), _bea())


def test_market_acquisition_before_settlement_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    market = _market()
    early = authority._Evidence(
        source=authority.KALSHI_ORIGIN,
        path=market.path,
        body=market.raw_body,
        acquired_at=NOW - timedelta(minutes=1),
        capability=authority._MARKET_CAPABILITY,
    )
    with pytest.raises(authority.SettlementAuthorityError):
        _run(monkeypatch, early, _bea())


def test_api_cannot_accept_a_caller_selected_quarter() -> None:
    assert tuple(inspect.signature(authority.acquire_settlement_authority).parameters) == (
        "decision",
    )


def test_reconstructed_evidence_cannot_be_used_with_immutable_decision() -> None:
    evidence = _market()
    forged = authority._Evidence.__new__(authority._Evidence)
    for field in ("source", "path", "raw_body", "raw_sha256", "acquired_at", "evidence_id"):
        object.__setattr__(forged, field, getattr(evidence, field))
    with pytest.raises(authority.SettlementAuthorityError):
        authority._parse_market(forged, _decision())
