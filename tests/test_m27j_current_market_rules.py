from __future__ import annotations

import ast
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import m27e_public_read_acceptance as public_reads
from services.market_universe import market_snapshot, public_read
from services.market_universe.domain import Market, material_hashes
from services.opportunity_engine.live_economics import (
    MarketEconomicsEvidence,
    normalize_live_orderbook,
)
from services.supervised_canary import m27j

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


def _raw_market(
    ticker: str = "M", event_ticker: str = "E", **overrides: object
) -> dict[str, object]:
    raw: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": "binary",
        "status": "active",
        "rules_primary": "YES if the measured value is at least 80 units.",
        "rules_secondary": "Use the final published report.",
        "settlement_sources": [{"name": "NWS"}],
        "strike_type": "greater",
        "floor_strike": "80",
        "cap_strike": None,
        "functional_strike": None,
        "custom_strike": None,
        "early_close_condition": "none",
        "price_level_structure": "linear_cent",
        "title": "Will the measured value be at least 80 units?",
        "is_provisional": False,
        "volume_fp": "0",
        "open_interest_fp": "0",
    }
    raw.update(overrides)
    return raw


def _body(raw_market: dict[str, object]) -> bytes:
    return json.dumps({"market": raw_market}, sort_keys=True).encode()


def _fake_transport(
    raw_market: dict[str, object],
    *,
    ticker: str | None = None,
    observed_at: datetime = NOW,
    status: int = 200,
    classification: str = "SUCCESS",
    body_override: bytes | None = None,
):
    def transport(requested_ticker: str) -> tuple[dict[str, object], bytes]:
        body = body_override if body_override is not None else _body(raw_market)
        evidence: dict[str, object] = {
            "path": f"{public_reads.BASE}/markets/{requested_ticker}",
            "observed_at": observed_at.isoformat(),
            "status": status,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "classification": classification,
        }
        if classification == "SUCCESS":
            evidence["payload"] = json.loads(body) if body else {}
        else:
            evidence["body_preview"] = ""
        return evidence, body

    return transport


def _evidence_payload(
    raw_market: dict[str, object], *, ticker: str = "M", observed_at: datetime = NOW
) -> tuple[dict[str, Any], Market]:
    evidence = m27j.acquire_current_market_rules(
        ticker,
        clock=lambda: observed_at,
        transport=_fake_transport(raw_market, observed_at=observed_at),
    )
    assert evidence.succeeded, evidence.reason
    market = Market.parse(raw_market)
    return evidence.to_json(), market


# ---------------------------------------------------------------------------
# 1. Hash lineage: M27J must not invent a second rules-hash algorithm
# ---------------------------------------------------------------------------


def test_market_parse_rules_hash_equals_canonical_material_hash() -> None:
    raw = _raw_market()
    market = Market.parse(raw)
    expected_rules_hash, expected_metadata_hash = material_hashes(raw)
    assert market.rules_hash == expected_rules_hash
    assert market.metadata_hash == expected_metadata_hash


def test_m27a_market_rules_hash_is_exactly_market_parse_rules_hash_when_caller_passes_it() -> None:
    """The lineage MarketEconomicsEvidence.market_rules_hash <- normalize_live_orderbook(...)

    is caller-supplied at that call site (see live_economics.py); this proves the value is
    algorithmically *compatible* -- feeding it the real canonical ``Market.parse(...).rules_hash``
    round-trips exactly, so M27J may reuse that same value as its own comparison target without
    inventing a second hash algorithm.
    """
    from services.market_universe.pricing import PriceLadder
    from services.opportunity_engine.books import OutcomeSide
    from services.opportunity_engine.fees import current_event_formula_policy
    from services.opportunity_engine.live_economics import (
        MarketEconomicsReplayInput,
        taker_cost,
    )
    from services.opportunity_engine.live_fees import (
        CurrentSeriesFeeObservation,
        EventFeeOverride,
        resolve_current_fee_regime,
    )

    raw_market = _raw_market()
    market = Market.parse(raw_market)
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    book_raw = {
        "ticker": "M",
        "orderbook_fp": {"yes_dollars": [[".300", "5"]], "no_dollars": [[".650", "5"]]},
    }
    observed = normalize_live_orderbook(
        book_raw,
        ticker="M",
        ladder=ladder,
        source_id="snapshot-M",
        observed_at=NOW,
        market_rules_hash=market.rules_hash,
    )
    series_observation = CurrentSeriesFeeObservation.parse(
        {
            "ticker": "S",
            "title": "T",
            "category": "Weather",
            "frequency": "daily",
            "tags": [],
            "settlement_sources": [],
            "fee_type": "quadratic_with_maker_fees",
            "fee_multiplier": "1",
            "last_updated_ts": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        },
        observed_at=NOW,
    )
    event = EventFeeOverride.parse({})
    regime = resolve_current_fee_regime(series_observation, event)
    policy = current_event_formula_policy(
        fee_type=regime.fee_type, fee_multiplier=regime.fee_multiplier
    )
    replay_input = MarketEconomicsReplayInput(observed, ladder, regime, policy)
    from decimal import Decimal

    quantity = Decimal("1.00")
    yes = taker_cost(observed.book, OutcomeSide.YES, quantity, policy)
    no = taker_cost(observed.book, OutcomeSide.NO, quantity, policy)
    values = dict(
        market_ticker="M",
        event_ticker="E",
        series_ticker="CLIMDW",
        market_source_id="src",
        market_rules_hash=market.rules_hash,
        market_metadata_hash=market.metadata_hash,
        price_range_hash=observed.price_range_hash,
        event_fee_hash=regime.event_metadata_hash,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        fee_policy_id=policy.policy_id,
        orderbook_source_id=observed.source_id,
        orderbook_source_hash=observed.source_hash,
        market_observed_at=NOW,
        orderbook_observed_at=NOW,
        economics_observed_at=NOW,
        requested_quantity=quantity,
        yes=yes,
        no=no,
        replay_input=replay_input,
    )
    economics = MarketEconomicsEvidence.create(**values)
    assert economics.market_rules_hash == market.rules_hash == material_hashes(raw_market)[0]


# ---------------------------------------------------------------------------
# 2. Acquisition boundary
# ---------------------------------------------------------------------------


def test_acquisition_success_derives_hash_from_canonical_parser() -> None:
    raw_market = _raw_market()
    evidence = m27j.acquire_current_market_rules(
        "M", clock=lambda: NOW, transport=_fake_transport(raw_market)
    )
    assert evidence.classification == "SUCCESS"
    assert evidence.rules_hash == Market.parse(raw_market).rules_hash
    assert evidence.parsed_event_ticker == "E"
    assert evidence.expires_at == evidence.observed_at + m27j.FRESHNESS
    assert evidence.path == f"{public_reads.BASE}/markets/M"
    assert evidence.host == m27j.HOST


def test_acquisition_no_credentials_or_auth_header_required() -> None:
    """``get_market_with_body`` never accepts or sends any credential material."""
    import inspect

    signature = inspect.signature(public_reads.get_market_with_body)
    assert list(signature.parameters) == ["ticker"]


def test_acquisition_non_200_status_fails_closed() -> None:
    raw_market = _raw_market()
    evidence = m27j.acquire_current_market_rules(
        "M",
        clock=lambda: NOW,
        transport=_fake_transport(raw_market, status=503, classification="HTTP_OR_NETWORK_FAILURE"),
    )
    assert evidence.classification == "HTTP_OR_NETWORK_FAILURE"
    assert evidence.rules_hash is None


def test_acquisition_redirect_status_fails_closed_as_non_200() -> None:
    """``http.client`` never auto-follows redirects; a 3xx simply surfaces as its own status."""
    raw_market = _raw_market()
    evidence = m27j.acquire_current_market_rules(
        "M",
        clock=lambda: NOW,
        transport=_fake_transport(raw_market, status=301, classification="HTTP_OR_NETWORK_FAILURE"),
    )
    assert evidence.classification == "HTTP_OR_NETWORK_FAILURE"


def test_acquisition_oversized_body_fails_closed() -> None:
    raw_market = _raw_market(rules_primary="x" * (market_snapshot.MAX_MARKET_BODY_BYTES + 1))
    evidence = m27j.acquire_current_market_rules(
        "M", clock=lambda: NOW, transport=_fake_transport(raw_market)
    )
    assert evidence.classification == "OVERSIZED_BODY"


def test_acquisition_malformed_json_fails_closed() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        raise public_reads.PublicReadFailure("schema failure: invalid JSON: boom")

    evidence = m27j.acquire_current_market_rules("M", clock=lambda: NOW, transport=transport)
    assert evidence.classification == "ACQUISITION_FAILURE"


def test_acquisition_wrong_ticker_in_body_fails_closed() -> None:
    raw_market = _raw_market(ticker="OTHER")
    evidence = m27j.acquire_current_market_rules(
        "M", clock=lambda: NOW, transport=_fake_transport(raw_market)
    )
    assert evidence.classification == "MARKET_IDENTITY_MISMATCH"


def test_acquisition_wrong_envelope_fails_closed() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        body = json.dumps({"markets": [_raw_market()]}).encode()
        evidence: dict[str, object] = {
            "path": f"{public_reads.BASE}/markets/{ticker}",
            "observed_at": NOW.isoformat(),
            "status": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "classification": "SUCCESS",
            "payload": json.loads(body),
        }
        return evidence, body

    evidence = m27j.acquire_current_market_rules("M", clock=lambda: NOW, transport=transport)
    assert evidence.classification == "MALFORMED_ENVELOPE"


def test_get_market_rejects_malformed_ticker() -> None:
    for bad in ("", "m", "AA/BB", "aa-lower", "-leading-dash"):
        with pytest.raises(public_reads.PublicReadFailure):
            public_reads.get_market_with_body(bad)


def test_get_market_with_body_uses_exact_markets_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_raw(path: str):
        captured["path"] = path
        body = json.dumps({"market": _raw_market()}).encode()
        return body, 200, NOW

    monkeypatch.setattr(public_read, "_get_raw", fake_get_raw)
    public_read.get_market_with_body("M")
    assert captured["path"] == f"{public_read.BASE}/markets/M"


def test_source_scan_no_mutating_verbs_or_authenticated_write_material() -> None:
    """Precise, code-shaped tokens that could never legitimately appear in this module's own
    safety-declaration prose (unlike e.g. "credentials"/"cancel", which its docstrings
    deliberately use to *disclaim* those capabilities)."""
    forbidden = (
        '"POST"',
        '"DELETE"',
        '"PATCH"',
        "Authorization",
        "production_execute",
        "SignAndSendBoundary",
        "private_key",
        "api_key",
        "RequestSigner",
    )
    for relative in (
        "services/supervised_canary/m27j.py",
        "scripts/m27e_public_read_acceptance.py",
    ):
        source = Path(relative).read_text()
        for token in forbidden:
            assert token not in source, f"forbidden token {token!r} found in {relative}"


def test_source_scan_only_get_http_method_used() -> None:
    for relative in (
        "services/market_universe/public_read.py",
        "scripts/m27e_public_read_acceptance.py",
    ):
        source = Path(relative).read_text()
        calls = [line for line in source.splitlines() if ".request(" in line]
        if not calls:
            continue
        assert all('"GET"' in line for line in calls), (relative, calls)


# ---------------------------------------------------------------------------
# Shared transport extraction: scripts -> services only, never services -> scripts
# ---------------------------------------------------------------------------


def test_m27j_imports_only_services_never_scripts() -> None:
    source = Path("services/supervised_canary/m27j.py").read_text()
    assert "import scripts" not in source
    assert "from scripts" not in source


def test_no_services_module_imports_scripts() -> None:
    """Dependency direction is always scripts -> services, never the reverse. Regression guard
    for this exact authority path (public_read / market_snapshot / m27j / authoritative_economics)
    and, defensively, every other module under services/."""
    root = Path("services")
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "scripts" or alias.name.startswith("scripts.") for alias in node.names
            ):
                offenders.append(str(path))
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "scripts" or node.module.startswith("scripts."))
            ):
                offenders.append(str(path))
    assert not offenders, f"services modules importing scripts/: {offenders}"


def test_scripts_module_imports_from_services() -> None:
    source = Path("scripts/m27e_public_read_acceptance.py").read_text()
    assert "from services.market_universe.public_read import" in source


def test_m27e_script_behavior_unchanged_via_shared_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The M27E script's own public behavior (paged_markets) is unchanged by the extraction --
    it still works purely by calling the re-exported ``get`` regardless of which module actually
    defines it."""
    calls: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        calls.append(path)
        return {
            "classification": "SUCCESS",
            "payload": {"markets": [{"ticker": "M"}], "cursor": ""},
        }

    monkeypatch.setattr(public_reads, "get", fake_get)
    result = public_reads.paged_markets()
    assert result["classification"] == "SUCCESS"
    assert result["market_count"] == 1
    assert calls and calls[0].startswith(public_reads.BASE + "/markets?")


def test_get_raw_rejects_crlf_injection() -> None:
    with pytest.raises(public_read.PublicReadFailure):
        public_read._get_raw(f"{public_read.BASE}/markets/M\r\nX-Injected: 1")


def test_get_raw_rejects_path_traversal() -> None:
    with pytest.raises(public_read.PublicReadFailure):
        public_read._get_raw(f"{public_read.BASE}/../secret")


def test_get_raw_bounded_timeout_and_host_are_fixed() -> None:
    source = Path("services/market_universe/public_read.py").read_text()
    assert "timeout=10" in source
    assert 'HOST = "external-api.kalshi.com"' in source


# ---------------------------------------------------------------------------
# 3. Rules validator -- match / change detection
# ---------------------------------------------------------------------------


def test_validate_exact_match_passes() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "PASS"


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("rules_primary", "YES if the measured value is at least 999 units."),
        ("rules_secondary", "Use a different report."),
        ("settlement_sources", [{"name": "OTHER"}]),
        ("strike_type", "less"),
        ("floor_strike", "81"),
        ("cap_strike", "100"),
        ("functional_strike", "some-function"),
        ("custom_strike", {"a": 1}),
        ("early_close_condition", "some condition"),
    ],
)
def test_each_rule_field_change_blocks_rules_current(field: str, new_value: object) -> None:
    base_raw = _raw_market()
    base_market = Market.parse(base_raw)
    changed_raw = _raw_market(**{field: new_value})
    changed_market = Market.parse(changed_raw)
    assert changed_market.rules_hash != base_market.rules_hash

    payload, _ = _evidence_payload(changed_raw)
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=base_market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "RULES_IDENTITY_CHANGED"


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("volume_fp", "500"),
        ("open_interest_fp", "500"),
        ("status", "closed"),
        ("title", "A completely different title"),
    ],
)
def test_non_rule_metadata_change_does_not_alter_rules_hash(field: str, new_value: object) -> None:
    base_raw = _raw_market()
    changed_raw = _raw_market(**{field: new_value})
    assert Market.parse(changed_raw).rules_hash == Market.parse(base_raw).rules_hash


def test_updated_time_change_does_not_alter_rules_hash() -> None:
    base_raw = _raw_market()
    changed_raw = _raw_market(updated_time="2026-08-18T00:00:00Z")
    assert Market.parse(changed_raw).rules_hash == Market.parse(base_raw).rules_hash


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_freshness_exact_boundary_passes() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    boundary_now = NOW + m27j.FRESHNESS
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=boundary_now,
    )
    assert result.classification == "PASS"


def test_freshness_epsilon_stale_fails() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    stale_now = NOW + m27j.FRESHNESS + timedelta(microseconds=1)
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=stale_now,
    )
    assert result.classification == "RULES_EVIDENCE_STALE"


def test_future_observed_at_fails_closed() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market, observed_at=NOW + timedelta(seconds=10))
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW,
    )
    assert result.classification == "MALFORMED_CURRENT_RULES_EVIDENCE"


@pytest.mark.parametrize("bad_ts", [None, "not-a-timestamp", "2026-08-18T12:00:00"])
def test_malformed_or_naive_timestamps_fail_closed(bad_ts: object) -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["observed_at"] = bad_ts
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


# ---------------------------------------------------------------------------
# Forgery resistance
# ---------------------------------------------------------------------------


def test_forged_stamped_rules_hash_does_not_pass() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["rules_hash"] = "f" * 64
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_forged_body_sha256_does_not_pass() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["body_sha256"] = "e" * 64
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_raw_body_changed_without_hash_update_does_not_pass() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    tampered_body = _body(_raw_market(rules_primary="TAMPERED"))
    payload["raw_body_b64"] = base64.b64encode(tampered_body).decode()
    # body_sha256 / rules_hash left as originally stamped.
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_body_hash_updated_but_stamped_rules_hash_stale_does_not_pass() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    tampered_raw = _raw_market(rules_primary="TAMPERED")
    tampered_body = _body(tampered_raw)
    payload["raw_body_b64"] = base64.b64encode(tampered_body).decode()
    payload["body_sha256"] = hashlib.sha256(tampered_body).hexdigest()
    # rules_hash left as the ORIGINAL (now-stale) stamped value.
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_caller_injected_rules_current_field_is_inert() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["rules_current"] = True
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_oversized_stored_raw_body_fails_closed() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    huge_body = b'{"market": {}}' + b" " * (market_snapshot.MAX_MARKET_BODY_BYTES + 1)
    payload["raw_body_b64"] = base64.b64encode(huge_body).decode()
    payload["body_sha256"] = hashlib.sha256(huge_body).hexdigest()
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification != "PASS"


def test_wrong_schema_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["schema"] = "kalsh3.market_universe.authoritative-market-snapshot.v2"
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_wrong_environment_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["environment"] = "SANDBOX"
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_wrong_origin_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["host"] = "https://evil.example.com"
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_wrong_path_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["path"] = f"{public_reads.BASE}/markets/OTHER"
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_wrong_ticker_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="OTHER",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "MARKET_IDENTITY_MISMATCH"


def test_wrong_event_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="SOME_OTHER_EVENT",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "MARKET_IDENTITY_MISMATCH"


def test_ambiguous_shape_not_a_dict_rejected() -> None:
    result = m27j.validate_current_rules_for_candidate(
        ["not", "a", "dict"],
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash="anything",
        now=NOW,
    )
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_http_status_not_200_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["http_status"] = 201
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_classification_not_success_rejected() -> None:
    raw_market = _raw_market()
    payload, market = _evidence_payload(raw_market)
    payload["classification"] = "HTTP_OR_NETWORK_FAILURE"
    result = m27j.validate_current_rules_for_candidate(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=market.rules_hash,
        now=NOW + timedelta(seconds=1),
    )
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"
