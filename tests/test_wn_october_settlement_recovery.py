"""Synthetic offline recovery cases; no fixtures can issue real source authority."""

from __future__ import annotations

import ast
import hashlib
import json
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from services.forecasting import wn_october_settlement_recovery as recovery

CUTOFF = {
    "market_settled_ts": "2026-09-03T00:00:00Z",
    "orders_updated_ts": "2026-10-15T00:00:00Z",
    "trades_created_ts": "2026-09-03T00:00:00Z",
}


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("NETWORK_DENIED_OFFLINE_TEST")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def encoded(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def synthetic_market(contract: recovery.Contract, value: str) -> dict[str, object]:
    temp = Decimal(value)
    if contract.kind == "between":
        yes = contract.floor <= temp <= contract.cap  # type: ignore[operator]
    elif contract.kind == "less":
        yes = temp < contract.cap  # type: ignore[operator]
    else:
        yes = temp > contract.floor  # type: ignore[operator]
    return {
        "ticker": contract.ticker,
        "event_ticker": contract.event,
        "market_type": "binary",
        "status": "finalized",
        "rules_primary": contract.primary,
        "rules_secondary": contract.secondary,
        "strike_type": contract.kind,
        "floor_strike": None if contract.floor is None else str(contract.floor),
        "cap_strike": None if contract.cap is None else str(contract.cap),
        "expiration_value": value,
        "settlement_ts": contract.settled_hint.isoformat(),
        "close_time": (contract.settled_hint - timedelta(hours=6)).isoformat(),
        "result": "yes" if yes else "no",
        "settlement_value_dollars": "1.0000" if yes else "0.0000",
    }


def responses(cutoff: dict[str, str] | None = None) -> tuple[recovery.SimulatedResponse, ...]:
    raw = encoded(CUTOFF if cutoff is None else cutoff)
    plan = recovery.plan_after_cutoff(raw)
    result = [recovery.SimulatedResponse(recovery.cutoff_request().path, 200, raw)]
    for family in recovery.frozen_families():
        lower = next(c for c in family.contracts if c.kind == "less")
        value = str(lower.cap + 2)  # type: ignore[operator]
        for contract in family.contracts:
            path = plan[len(result) - 1].path
            result.append(
                recovery.SimulatedResponse(
                    path, 200, encoded({"market": synthetic_market(contract, value)})
                )
            )
    return tuple(result)


def changed(index: int = 1, **kwargs: object) -> tuple[recovery.SimulatedResponse, ...]:
    rows = list(responses())
    rows[index] = replace(rows[index], **kwargs)  # type: ignore[arg-type]
    return tuple(rows)


def mutate(field: str, value: object) -> tuple[recovery.SimulatedResponse, ...]:
    rows = list(responses())
    payload = json.loads(rows[1].body)
    payload["market"][field] = value
    rows[1] = replace(rows[1], body=encoded(payload))
    return tuple(rows)


def test_frozen_roster_full_denominator_and_no_failed_forecast_targets() -> None:
    raw = recovery.ROSTER.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recovery.ROSTER_SHA256
    roster = json.loads(raw)
    assert len(roster["full_city_day_roster"]) == 60
    assert len({x["same_date_cluster"] for x in roster["full_city_day_roster"]}) == 12
    assert len(roster["prior_429_receipts"]) == 19
    assert sum(x["capture_state"] == "FAILED" for x in roster["full_city_day_roster"]) == 5
    assert sum(x["depth_abstentions"] for x in roster["full_city_day_roster"]) == 59
    families = recovery.frozen_families()
    assert len(families) == 14
    assert all(f.target_date != "2026-10-04" for f in families)
    assert len({c.ticker for f in families for c in f.contracts}) == 84


def test_valid_http_200_is_only_simulated_consistency_and_never_authority() -> None:
    result = recovery.replay_offline(responses())
    assert result.request_count == 85
    assert len(result.receipts) == 85
    assert all(f.state == "SIMULATED_METADATA_CONSISTENT" for f in result.families)
    assert not result.live_authorized and not result.point_score_authorized
    assert result.simulation_only and result.paper_trading_authority == "NONE"
    assert all(
        not f.source_custody_attested and not f.point_score_eligible for f in result.families
    )
    starts = [r.simulated_start_ms for r in result.receipts]
    assert all(b - a >= 3000 for a, b in pairwise(starts))
    assert result.logical_elapsed_ms < recovery.TOTAL_DEADLINE_MS
    assert all(
        r.request.origin == recovery.ORIGIN and r.request.method == "GET" for r in result.receipts
    )
    assert all(r.request.headers == recovery.HEADERS for r in result.receipts)
    assert len(result.preserved_city_days) == 60
    assert sum(r.capture_state == "FAILED" for r in result.preserved_city_days) == 5
    assert sum(r.depth_abstentions for r in result.preserved_city_days) == 59
    assert all(not r.point_score_authorized for r in result.preserved_city_days)
    assert all(
        r.recovery_state == "NOT_A_RECOVERY_TARGET"
        for r in result.preserved_city_days
        if r.target_date == "2026-10-04"
    )


def test_simulation_objects_cannot_be_relabelled_as_source_or_live_authority() -> None:
    result = recovery.replay_offline(responses())
    for change in [
        {"live_authorized": True},
        {"point_score_authorized": True},
        {"simulation_only": False},
        {"paper_trading_authority": "PAPER"},
    ]:
        with pytest.raises(recovery.RecoveryError):
            replace(result, **change)  # type: ignore[arg-type]
    with pytest.raises(recovery.RecoveryError):
        replace(result.families[0], source_custody_attested=True)  # type: ignore[arg-type]
    with pytest.raises(recovery.RecoveryError):
        replace(result.preserved_city_days[0], point_score_authorized=True)  # type: ignore[arg-type]
    with pytest.raises(recovery.RecoveryError):
        replace(result.receipts[0], simulation_only=False)  # type: ignore[arg-type]


def test_ci_network_guard_rejects_before_dns_or_socket_connection() -> None:
    with pytest.raises(AssertionError, match="NETWORK_DENIED"):
        socket.getaddrinfo("invalid.example", 443)


def test_rate_limit_at_cutoff_and_historical_route_have_no_hidden_extra_requests() -> None:
    denied = recovery.replay_offline(changed(0, status=429))
    assert denied.request_count == 1
    assert all(f.state == "UNVERIFIED" for f in denied.families)
    archived = recovery.replay_offline(
        responses({**CUTOFF, "market_settled_ts": "2026-11-01T00:00:00Z"})
    )
    assert archived.request_count == 85
    assert all("/historical/markets/" in r.request.path for r in archived.receipts[1:])
    assert all(f.state == "SIMULATED_METADATA_CONSISTENT" for f in archived.families)


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("incomplete", [False, True])
def test_access_denial_and_rate_limit_stop_all_further_attempts(
    status: int, incomplete: bool
) -> None:
    result = recovery.replay_offline(
        changed(status=status, body=b'{"error":"denied"}', complete=not incomplete)
    )
    assert result.request_count == 2
    assert result.receipts[1].state == f"HTTP_{status}"
    assert result.receipts[1].status == status
    assert result.receipts[1].retained_body == b'{"error":"denied"}'
    assert result.logical_elapsed_ms == 3001
    assert all(f.state == "UNVERIFIED" for f in result.families)
    assert all(r.status is None for r in result.receipts[2:])


def test_404_is_unverified_without_retry_or_route_fallback() -> None:
    result = recovery.replay_offline(changed(status=404, body=b"{}"))
    assert result.request_count == 85
    assert result.families[0].state == "UNVERIFIED"
    assert result.families[0].reason == "HTTP_404"
    paths = [r.request.path for r in result.receipts]
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_never_follow_even_same_origin(status: int) -> None:
    result = recovery.replay_offline(
        changed(status=status, headers=(("Location", recovery.ORIGIN),))
    )
    assert result.receipts[1].state == "REDIRECT_REJECTED"
    assert result.request_count == 85
    assert result.families[0].state == "UNVERIFIED"


@pytest.mark.parametrize(
    "body", [b"{", b"not json", b"[]", b'{"market":null}', b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff"]
)
def test_malformed_json_is_unverified(body: bytes) -> None:
    result = recovery.replay_offline(changed(body=body))
    assert result.families[0].state == "UNVERIFIED"


def test_complete_json_but_incomplete_http_is_rejected() -> None:
    result = recovery.replay_offline(changed(complete=False))
    assert result.receipts[1].state == "INCOMPLETE_RESPONSE"


def test_truncated_content_length_and_wrong_content_type() -> None:
    for headers, expected in [
        ((("Content-Type", "application/json"), ("Content-Length", "9999")), "CONTENT_LENGTH"),
        ((("Content-Type", "text/html"),), "CONTENT_TYPE"),
    ]:
        result = recovery.replay_offline(changed(headers=headers))
        assert result.receipts[1].state == expected


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", True, 0, "-Infinity", "x" * 65])
def test_missing_or_invalid_expiration_value_never_uses_binary_payout(value: object) -> None:
    result = recovery.replay_offline(mutate("expiration_value", value))
    assert result.families[0].state == "UNVERIFIED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("ticker", "KXHIGHMIA-26OCT04-T87"),
        ("event_ticker", "KXHIGHMIA-26OCT04"),
        ("status", "settled"),
        ("market_type", "categorical"),
        ("floor_strike", "999"),
        ("strike_type", "greater"),
        ("rules_primary", "other CLI"),
        ("rules_secondary", "new correction rule"),
        ("result", "invalid"),
        ("settlement_value_dollars", "0.50"),
        ("settlement_ts", "2026-10-15T00:00:00Z"),
        ("close_time", "2026-12-01T00:00:00Z"),
    ],
)
def test_changed_target_strikes_rules_and_chronology_fail_closed(field: str, value: object) -> None:
    result = recovery.replay_offline(mutate(field, value))
    assert result.families[0].state == "UNVERIFIED"


def test_conflicting_sibling_values_fail_even_when_each_predicate_is_consistent() -> None:
    rows = list(responses())
    contract = recovery.frozen_families()[0].contracts[0]
    payload = json.loads(rows[1].body)
    value = str(Decimal(payload["market"]["expiration_value"]) + Decimal("0.01"))
    rows[1] = replace(rows[1], body=encoded({"market": synthetic_market(contract, value)}))
    result = recovery.replay_offline(tuple(rows))
    assert result.families[0].reason == "SIBLING_TEMPERATURE_OR_PRECISION_CONFLICT"


def test_original_frozen_ladder_validation_is_not_just_hash_self_consistency() -> None:
    family = recovery.frozen_families()[0]
    broken = replace(
        family, contracts=(replace(family.contracts[0], floor=Decimal(999)), *family.contracts[1:])
    )
    with pytest.raises(recovery.RecoveryError):
        recovery._ladder(broken)


def test_cutoff_routing_uses_exact_settlement_timestamp_and_equality_abstains() -> None:
    early = recovery.plan_after_cutoff(encoded(CUTOFF))
    assert all("/historical/markets/" not in r.path for r in early)
    later = {**CUTOFF, "market_settled_ts": "2026-11-01T00:00:00Z"}
    assert all("/historical/markets/" in r.path for r in recovery.plan_after_cutoff(encoded(later)))
    hint = recovery.frozen_families()[0].contracts[0].settled_hint.isoformat()
    with pytest.raises(recovery.RecoveryError, match="EQUALITY"):
        recovery.plan_after_cutoff(encoded({**CUTOFF, "market_settled_ts": hint}))


@pytest.mark.parametrize("field", list(CUTOFF))
def test_malformed_cutoff_stops_before_any_market_request(field: str) -> None:
    source = list(responses())
    source[0] = replace(source[0], body=encoded({**CUTOFF, field: "2026-10-01"}))
    result = recovery.replay_offline(tuple(source))
    assert result.request_count == 1
    assert all(f.state == "UNVERIFIED" for f in result.families)


@pytest.mark.parametrize("index,expected_requests", [(0, 0), (1, 1), (7, 7), (84, 84)])
def test_cancellation_no_new_requests_and_all_families_retained(
    index: int, expected_requests: int
) -> None:
    result = recovery.replay_offline(responses(), cancel_before_request=index)
    assert result.request_count == expected_requests
    assert len(result.families) == 14
    assert result.full_city_days_preserved == 60 and result.full_date_clusters_preserved == 12
    assert not result.point_score_authorized
    if index == 1:
        assert result.logical_elapsed_ms == 1


def test_mid_response_cancellation_stops_future_attempts() -> None:
    result = recovery.replay_offline(changed(cancelled=True))
    assert result.request_count == 2
    assert result.families[0].reason == "CANCELLED"


def test_timeout_byte_limit_and_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    timed = recovery.replay_offline(changed(elapsed_ms=10_000))
    assert timed.receipts[1].state == "REQUEST_TIMEOUT"
    huge = recovery.replay_offline(changed(body=b"x" * (recovery.MAX_BODY_BYTES + 1)))
    assert huge.receipts[1].state == "BODY_LIMIT"
    assert len(huge.receipts[1].retained_body) == recovery.MAX_BODY_BYTES
    assert not huge.receipts[1].complete_body
    monkeypatch.setattr(recovery, "TOTAL_DEADLINE_MS", 4_000)
    ended = recovery.replay_offline(changed(elapsed_ms=1_000))
    assert ended.logical_elapsed_ms <= 4_000
    assert ended.request_count == 2
    assert all(r.status is None for r in ended.receipts[2:])


def test_hard_budget_untrusted_input_types_and_no_callback_execution() -> None:
    with pytest.raises(recovery.RecoveryError, match="BUDGET"):
        recovery.replay_offline((*responses(), responses()[0]))
    with pytest.raises(recovery.RecoveryError, match="CANCEL"):
        recovery.replay_offline(responses(), cancel_before_request=True)
    with pytest.raises(recovery.RecoveryError, match="BUILTIN"):
        recovery.replay_offline((object(),))  # type: ignore[arg-type]


def test_production_roots_unchanged_and_no_network_or_scoring_capability() -> None:
    tree = ast.parse(Path(recovery.__file__).read_text())
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imports & {"socket", "http.client", "urllib.request", "subprocess", "requests"}
    assert not any("score_point_forecast" in str(n) for n in imports)
    assert not hasattr(recovery, "main") and not hasattr(recovery, "live_get")
