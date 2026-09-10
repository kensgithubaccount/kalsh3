from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import services.production_gdp_strategy.settlement_authority as authority

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
            "US real GDP seasonally adjusted annualized BEA Advance Estimate more than threshold"
        ),
        "rules_secondary": "Settlement is based on the reported percent value.",
        "status": status,
        "result": result,
        "settlement_ts": NOW.isoformat(),
        "settlement_value_dollars": "1.00",
        "expiration_value": "4.5",
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
        f"Release: 2026-09-03\n{quarter}\n{ordinal.title()} Quarter {quarter[:4]}\n"
        "Gross Domestic Product (GDP)\n"
        f"Advance Estimate\nReal GDP (seasonally adjusted annual rate): {value} percent\n"
        f"{text_extra}"
    ).encode()
    return authority._Evidence(
        source=authority.BEA_ORIGIN,
        path=authority._bea_path(quarter),
        body=body,
        acquired_at=NOW,
        capability=authority._BEA_CAPABILITY,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch, market: authority._Evidence, bea: authority._Evidence
) -> authority.SettlementAuthority:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: market)
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: bea)
    return authority.acquire_settlement_authority(TICKER, "2026-Q3")


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
        authority.acquire_settlement_authority(TICKER, "2026-Q3")


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
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market(**changes))
    with pytest.raises(authority.SettlementAuthorityError):
        authority.acquire_settlement_authority(TICKER, "2026-Q3")


@pytest.mark.parametrize("extra", ["Second Estimate", "Third Estimate", "revised"])
def test_later_bea_vintages_are_rejected(monkeypatch: pytest.MonkeyPatch, extra: str) -> None:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market())
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: _bea(text_extra=extra))
    with pytest.raises(authority.SettlementAuthorityError):
        authority.acquire_settlement_authority(TICKER, "2026-Q3")


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
        authority._parse_market(forged, TICKER)


def test_bea_wrong_quarter_and_missing_value_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authority, "_acquire_market", lambda _ticker: _market())
    monkeypatch.setattr(authority, "_acquire_bea", lambda _quarter: _bea(quarter="2026-Q2"))
    with pytest.raises(authority.SettlementAuthorityError):
        authority.acquire_settlement_authority(TICKER, "2026-Q3")
