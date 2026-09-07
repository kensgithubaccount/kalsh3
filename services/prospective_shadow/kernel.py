"""Small, append-only A0.1 prospective observation kernel.

This module is deliberately a research boundary.  It composes the reviewed weather
authority, structural cohort scanner, and sequenced book view; it has no account,
credential, or order-path import.  All persisted values are canonical JSON strings.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.forecasting.daily_temperature import route_daily_temperature
from services.forecasting.domain import ForecastError
from services.forecasting.weather_prospective import (
    PROTOCOL_IDENTITY as WEATHER_PROTOCOL_ID,
)
from services.forecasting.weather_prospective import (
    PROTOCOL_VERSION as WEATHER_PROTOCOL_VERSION,
)
from services.forecasting.weather_prospective import (
    validate_prospective_forecast_evidence,
)
from services.market_universe.domain import Event, Market, stable_hash
from services.market_universe.event_snapshot import PARSER_VERSION as EVENT_PARSER_VERSION
from services.market_universe.event_snapshot import SCHEMA as EVENT_SNAPSHOT_SCHEMA
from services.market_universe.event_snapshot import AuthoritativeEventSnapshot
from services.market_universe.market_snapshot import PARSER_VERSION as MARKET_PARSER_VERSION
from services.market_universe.market_snapshot import SCHEMA as MARKET_SNAPSHOT_SCHEMA
from services.market_universe.market_snapshot import AuthoritativeMarketSnapshot
from services.opportunity_engine.structural import (
    POLICY_VERSION as STRUCTURAL_SCANNER_VERSION,
)
from services.opportunity_engine.structural import (
    StructuralLead,
)
from services.real_time_market_data.orderbook import BookState, BookView
from services.supervised_canary.m27n2_evidence_reconstruction import (
    EvidenceReconstructionError,
    reconstruct_event,
    reconstruct_market,
)

KERNEL_VERSION = "kalshi-a0.1-shadow-kernel-v1"
SCHEMA_ID = "kalshi.a01.prospective-observation.v1"
RECEIPT_SCHEMA_ID = "kalshi.a01.prospective-start.v1"
WEATHER_ADAPTER_VERSION = "kalshi-a0.1-weather-daily-temperature-v1"
STRUCTURAL_ADAPTER_VERSION = "kalshi-a0.1-monotonic-threshold-v1"
ADAPTER_VERSIONS = {
    "daily_weather": WEATHER_ADAPTER_VERSION,
    "structural_threshold": STRUCTURAL_ADAPTER_VERSION,
}
STRUCTURAL_POLICY_ID = stable_hash((STRUCTURAL_SCANNER_VERSION, "compatibility-positive-only-v1"))
ZERO = "0"
DEFAULT_FEE_POLICY_ID = "research-only-fees-v1"
DEFAULT_EXECUTION_CONVENTION = "displayed-price-only"
DEFAULT_SLIPPAGE_MODEL = "none-v1; no executable conclusion"
INDEPENDENCE_RULES = "event/day/location for weather; one cohort/event relationship for structural"
ABSTENTION_BEHAVIOR = "fail-closed; persist explicit abstention; no retroactive backfill"
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_GIT_ID = re.compile(r"\A[0-9a-f]{40}\Z")
_AUTHORITY_KEYS = frozenset(
    {
        "market_body_sha256",
        "market_rules_hash",
        "market_metadata_hash",
        "event_body_sha256",
        "event_metadata_hash",
        "market_parser_version",
        "event_parser_version",
        "market_snapshot_schema",
        "event_snapshot_schema",
    }
)
StartReceipt = dict[str, Any]


class ShadowKernelError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HydratedMarketAuthority:
    """Typed market/event objects reconstructed only from validated exact snapshots."""

    market: Market
    event: Event
    market_snapshot: AuthoritativeMarketSnapshot
    event_snapshot: AuthoritativeEventSnapshot

    @property
    def identity(self) -> dict[str, str]:
        if self.market_snapshot.body_sha256 is None or self.event_snapshot.body_sha256 is None:
            raise ShadowKernelError("successful authority snapshot is missing body hash")
        return {
            "market_body_sha256": self.market_snapshot.body_sha256,
            "market_rules_hash": self.market.rules_hash,
            "market_metadata_hash": self.market.metadata_hash,
            "event_body_sha256": self.event_snapshot.body_sha256,
            "event_metadata_hash": self.event.metadata_hash,
            "market_parser_version": MARKET_PARSER_VERSION,
            "event_parser_version": EVENT_PARSER_VERSION,
            "market_snapshot_schema": MARKET_SNAPSHOT_SCHEMA,
            "event_snapshot_schema": EVENT_SNAPSHOT_SCHEMA,
        }


def hydrate_market_authority(
    summary: Mapping[str, Any],
    *,
    market_snapshot: AuthoritativeMarketSnapshot,
    event_snapshot: AuthoritativeEventSnapshot,
) -> HydratedMarketAuthority:
    """Hydrate discovery-only data with exact, independently validated authority snapshots."""
    ticker = summary.get("ticker")
    event_ticker = summary.get("event_ticker")
    if not isinstance(ticker, str) or not isinstance(event_ticker, str):
        raise ShadowKernelError("summary market identity is missing")
    if market_snapshot.ticker != ticker or event_snapshot.ticker != event_ticker:
        raise ShadowKernelError("summary/detail ticker identity mismatch")
    if not market_snapshot.succeeded or not event_snapshot.succeeded:
        raise ShadowKernelError(
            "market/event authority snapshot unavailable: "
            f"market={market_snapshot.classification} event={event_snapshot.classification}"
        )
    try:
        market = reconstruct_market(
            market_snapshot.to_json(),
            expected_ticker=ticker,
            expected_event_ticker=event_ticker,
        )
        event = reconstruct_event(event_snapshot.to_json(), expected_ticker=event_ticker)
    except EvidenceReconstructionError as exc:
        raise ShadowKernelError(f"canonical authority reconstruction failed: {exc}") from exc
    if market.event_ticker != event.ticker:
        raise ShadowKernelError("market/event authority mismatch")
    return HydratedMarketAuthority(market, event, market_snapshot, event_snapshot)


def _validate_authority_identity(identity: object) -> None:
    if not isinstance(identity, dict) or set(identity) != _AUTHORITY_KEYS:
        raise ShadowKernelError("canonical market authority shape mismatch")
    for field in (
        "market_body_sha256",
        "market_rules_hash",
        "market_metadata_hash",
        "event_body_sha256",
        "event_metadata_hash",
    ):
        value = identity.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ShadowKernelError(f"canonical market authority {field} is malformed")
    expected = {
        "market_parser_version": MARKET_PARSER_VERSION,
        "event_parser_version": EVENT_PARSER_VERSION,
        "market_snapshot_schema": MARKET_SNAPSHOT_SCHEMA,
        "event_snapshot_schema": EVENT_SNAPSHOT_SCHEMA,
    }
    for field, value in expected.items():
        if identity.get(field) != value:
            raise ShadowKernelError(f"canonical market authority {field} mismatch")


def _validate_observation_authority(obs: Mapping[str, Any]) -> None:
    evidence = obs["evidence"]
    tickers = obs.get("market_tickers")
    if (
        not isinstance(tickers, list)
        or not tickers
        or any(not isinstance(ticker, str) or not ticker for ticker in tickers)
        or len(set(tickers)) != len(tickers)
    ):
        raise ShadowKernelError("observation market ticker identity is malformed")
    authority = evidence.get("market_authority")
    if obs["strategy_family"] == "daily_weather":
        if len(tickers) != 1 or not isinstance(authority, dict):
            raise ShadowKernelError("weather authority must be one exact identity")
        _validate_authority_identity(authority)
        return
    if obs["strategy_family"] == "structural_threshold":
        if len(tickers) != 2 or not isinstance(authority, dict) or set(authority) != set(tickers):
            raise ShadowKernelError("structural authority must cover exactly both leg tickers")
        for ticker in tickers:
            _validate_authority_identity(authority[ticker])
        return
    raise ShadowKernelError("unknown observation authority family")


def _iso(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ShadowKernelError(f"{field} must be canonical UTC")
    result = value.isoformat()
    if result != value.isoformat():
        raise ShadowKernelError(f"{field} is not canonical")
    return result


def _dec(value: Any, field: str) -> str:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # Decimal raises multiple subclasses across Python versions.
        raise ShadowKernelError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise ShadowKernelError(f"{field} is not finite")
    return str(result)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _require_text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ShadowKernelError(f"missing {field}")
    return value


def _book(view: BookView, decision_at: datetime, stale_after: timedelta) -> dict[str, Any]:
    if view.state is not BookState.CURRENT:
        raise ShadowKernelError(f"{view.ticker}: book is {view.state.value}")
    observed = _iso(view.observed_at, f"{view.ticker}.observed_at")
    if view.observed_at > decision_at or decision_at - view.observed_at > stale_after:
        raise ShadowKernelError(f"{view.ticker}: stale or future book")
    yes_bids = [[str(price), str(quantity)] for price, quantity in view.yes_bids]
    no_bids = [[str(price), str(quantity)] for price, quantity in view.no_bids]
    return {
        "ticker": view.ticker,
        "snapshot_id": stable_hash((view.ticker, view.sequence, observed, yes_bids, no_bids)),
        "sequence": view.sequence,
        "observed_at": observed,
        "state": view.state.value,
        "yes_bids": yes_bids,
        "no_bids": no_bids,
        "best_yes_bid": None if view.best_yes_bid is None else str(view.best_yes_bid),
        "best_yes_ask": None if view.best_yes_ask is None else str(view.best_yes_ask),
    }


def build_start_receipt(
    *,
    canonical_base_sha: str,
    canonical_base_tree: str,
    start_at: datetime,
    source_identities: Mapping[str, str],
    fee_policy_id: str,
    execution_convention: str = DEFAULT_EXECUTION_CONVENTION,
    slippage_model: str = DEFAULT_SLIPPAGE_MODEL,
) -> dict[str, Any]:
    receipt = {
        "schema": RECEIPT_SCHEMA_ID,
        "kernel_version": KERNEL_VERSION,
        "adapter_versions": ADAPTER_VERSIONS,
        "canonical_base_sha": canonical_base_sha,
        "canonical_base_tree": canonical_base_tree,
        "schema_identity": SCHEMA_ID,
        "weather_protocol_identity": WEATHER_PROTOCOL_ID,
        "weather_protocol_version": WEATHER_PROTOCOL_VERSION,
        "structural_policy_identity": STRUCTURAL_POLICY_ID,
        "structural_scanner_version": STRUCTURAL_SCANNER_VERSION,
        "source_identities": dict(source_identities),
        "fee_policy_identity": fee_policy_id,
        "execution_price_convention": execution_convention,
        "slippage_depth_model": slippage_model,
        "independence_rules": INDEPENDENCE_RULES,
        "abstention_behavior": ABSTENTION_BEHAVIOR,
        "start_at": _iso(start_at, "start_at"),
        "research_only": True,
        "production_influence": ZERO,
    }
    receipt["receipt_digest"] = content_hash(receipt)
    return receipt


def validate_start_receipt(receipt: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "kernel_version",
        "adapter_versions",
        "canonical_base_sha",
        "canonical_base_tree",
        "schema_identity",
        "weather_protocol_identity",
        "weather_protocol_version",
        "structural_policy_identity",
        "structural_scanner_version",
        "source_identities",
        "fee_policy_identity",
        "execution_price_convention",
        "slippage_depth_model",
        "independence_rules",
        "abstention_behavior",
        "start_at",
        "research_only",
        "production_influence",
        "receipt_digest",
    }
    if set(receipt) != required:
        raise ShadowKernelError("prospective-start receipt field set mismatch")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA_ID
        or receipt.get("kernel_version") != KERNEL_VERSION
    ):
        raise ShadowKernelError("invalid prospective-start receipt")
    if receipt.get("adapter_versions") != ADAPTER_VERSIONS:
        raise ShadowKernelError("prospective-start adapter versions drifted")
    if receipt.get("schema_identity") != SCHEMA_ID:
        raise ShadowKernelError("prospective-start schema identity mismatch")
    if (
        receipt.get("weather_protocol_identity") != WEATHER_PROTOCOL_ID
        or receipt.get("weather_protocol_version") != WEATHER_PROTOCOL_VERSION
    ):
        raise ShadowKernelError("prospective-start weather protocol drifted")
    if (
        receipt.get("structural_policy_identity") != STRUCTURAL_POLICY_ID
        or receipt.get("structural_scanner_version") != STRUCTURAL_SCANNER_VERSION
    ):
        raise ShadowKernelError("prospective-start structural policy drifted")
    if receipt.get("research_only") is not True or receipt.get("production_influence") != ZERO:
        raise ShadowKernelError("prospective-start safety invariant failed")
    if not isinstance(receipt.get("canonical_base_sha"), str) or not _GIT_ID.fullmatch(
        receipt["canonical_base_sha"]
    ):
        raise ShadowKernelError("prospective-start canonical base SHA is malformed")
    if not isinstance(receipt.get("canonical_base_tree"), str) or not _GIT_ID.fullmatch(
        receipt["canonical_base_tree"]
    ):
        raise ShadowKernelError("prospective-start canonical base tree is malformed")
    if not isinstance(receipt.get("source_identities"), dict) or not receipt["source_identities"]:
        raise ShadowKernelError("prospective-start source identities missing")
    if any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in receipt["source_identities"].items()
    ):
        raise ShadowKernelError("prospective-start source identity malformed")
    for field in ("fee_policy_identity", "execution_price_convention", "slippage_depth_model"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ShadowKernelError(f"prospective-start {field} missing")
    if (
        receipt.get("independence_rules") != INDEPENDENCE_RULES
        or receipt.get("abstention_behavior") != ABSTENTION_BEHAVIOR
    ):
        raise ShadowKernelError("prospective-start semantics drifted")
    if receipt.get("production_influence") != ZERO:
        raise ShadowKernelError("prospective-start receipt has production influence")
    digest = receipt.get("receipt_digest")
    body = dict(receipt)
    body.pop("receipt_digest", None)
    if digest != content_hash(body):
        raise ShadowKernelError("prospective-start receipt digest mismatch")
    try:
        start = datetime.fromisoformat(_require_text(receipt, "start_at"))
    except ValueError as exc:
        raise ShadowKernelError("prospective-start timestamp is malformed") from exc
    _iso(start, "start_at")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ShadowKernelError("prospective-start receipt digest is malformed")


def _base(
    adapter: str,
    acquired_at: datetime,
    decision_at: datetime,
    event_id: str,
    independent_id: str,
    tickers: Sequence[str],
    lifecycle: Mapping[str, Any],
    rules_hash: str,
    settlement: str,
    source: Mapping[str, Any],
    books: Sequence[dict[str, Any]],
    decision: str,
    reason: str | None,
    values: Mapping[str, Any],
    start_receipt_digest: str = "",
) -> dict[str, Any]:
    if decision_at < acquired_at:
        raise ShadowKernelError("decision precedes acquisition")
    payload: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "strategy_family": adapter,
        "strategy_version": ADAPTER_VERSIONS[adapter],
        "acquisition_timestamp": _iso(acquired_at, "acquisition_timestamp"),
        "decision_timestamp": _iso(decision_at, "decision_timestamp"),
        "event_identity": event_id,
        "independent_event_identity": independent_id,
        "market_tickers": list(tickers),
        "market_lifecycle": _jsonable(lifecycle),
        "contract_rules_hash": rules_hash,
        "settlement_specification_authority": settlement,
        "source": _jsonable(source),
        "books": list(books),
        "realtime": {"fresh": True, "gapped": False, "reconnected": False},
        "fee_policy_identity": DEFAULT_FEE_POLICY_ID,
        "execution_price_convention": DEFAULT_EXECUTION_CONVENTION,
        "slippage_depth_model": DEFAULT_SLIPPAGE_MODEL,
        "decision": decision,
        "reason_code": reason,
        "start_receipt_digest": start_receipt_digest,
        "evidence": _jsonable(values),
        "research_only": True,
        "production_influence": ZERO,
    }
    payload["observation_id"] = content_hash(payload)
    payload["evidence_hash"] = content_hash(payload["evidence"])
    return payload


def capture_weather_observation(
    *,
    market: Market,
    event: Event,
    forecast: Mapping[str, Any],
    book: BookView,
    acquired_at: datetime,
    decision_at: datetime,
    stale_after: timedelta = timedelta(seconds=30),
    authority: HydratedMarketAuthority | None = None,
    start_receipt_digest: str = "",
) -> dict[str, Any]:
    if authority is not None and (
        authority.market.ticker != market.ticker or authority.event.ticker != event.ticker
    ):
        raise ShadowKernelError("weather authority does not match supplied market/event")
    route = route_daily_temperature(market, event)
    if route.contract is None:
        return _base(
            "daily_weather",
            acquired_at,
            decision_at,
            event.ticker,
            stable_hash((event.ticker, "weather-event")),
            (market.ticker,),
            {"market_status": market.status.value},
            route.source_identity,
            "unbound",
            {"authority": route.policy_identity},
            [],
            "ABSTAIN",
            route.reason.value if route.reason else "WEATHER_UNSUPPORTED",
            {},
            start_receipt_digest=start_receipt_digest,
        )
    contract = route.contract
    try:
        validate_prospective_forecast_evidence(forecast)
    except ForecastError as exc:
        return _base(
            "daily_weather",
            acquired_at,
            decision_at,
            event.ticker,
            stable_hash((event.ticker, "weather-event")),
            (market.ticker,),
            {"market_status": market.status.value},
            market.rules_hash,
            contract.settlement_authority,
            {"source_identity": route.source_identity},
            [],
            "ABSTAIN",
            "WEATHER_AUTHORITY_OR_REVISION_GAP",
            {"detail": str(exc)},
            start_receipt_digest=start_receipt_digest,
        )
    forecast_collected = datetime.fromisoformat(str(forecast["collection_timestamp"]))
    if forecast_collected > acquired_at or acquired_at > decision_at:
        raise ShadowKernelError("weather source vintage is after acquisition or decision")
    event_id = stable_hash(
        (contract.station_id, contract.measurement, contract.local_date.isoformat())
    )
    try:
        snapshot = _book(book, decision_at, stale_after)
    except ShadowKernelError as exc:
        return _base(
            "daily_weather",
            acquired_at,
            decision_at,
            event.ticker,
            stable_hash((event.ticker, "weather-event")),
            (market.ticker,),
            {"market_status": market.status.value},
            market.rules_hash,
            contract.settlement_authority,
            {"source_identity": route.source_identity},
            [],
            "ABSTAIN",
            "STALE_OR_GAPPED_BOOK",
            {"detail": str(exc)},
            start_receipt_digest=start_receipt_digest,
        )
    values = {
        "weather_protocol_identity": WEATHER_PROTOCOL_ID,
        "forecast": dict(forecast),
        "contract": {
            "measurement": contract.measurement,
            "date": contract.local_date.isoformat(),
            "station": contract.station_id,
            "rules_hash": market.rules_hash,
        },
    }
    if authority is None:
        return _base(
            "daily_weather",
            acquired_at,
            decision_at,
            event_id,
            event_id,
            (market.ticker,),
            {"market_status": market.status.value},
            market.rules_hash,
            contract.settlement_authority,
            {"source_identity": route.source_identity},
            [snapshot],
            "ABSTAIN",
            "MARKET_AUTHORITY_NOT_HYDRATED",
            values,
            start_receipt_digest=start_receipt_digest,
        )
    values["market_authority"] = authority.identity
    return _base(
        "daily_weather",
        acquired_at,
        decision_at,
        event_id,
        event_id,
        (market.ticker,),
        {"market_status": market.status.value},
        market.rules_hash,
        contract.settlement_authority,
        {"source_identity": route.source_identity, "authority_identity": route.policy_identity},
        [snapshot],
        "OBSERVE",
        None,
        values,
        start_receipt_digest=start_receipt_digest,
    )


def capture_structural_observation(
    *,
    lead: StructuralLead,
    broad_book: BookView,
    narrow_book: BookView,
    acquired_at: datetime,
    decision_at: datetime,
    stale_after: timedelta = timedelta(seconds=30),
    raw_inconsistency: bool | None = None,
    leg_authority: Mapping[str, HydratedMarketAuthority] | None = None,
    start_receipt_digest: str = "",
) -> dict[str, Any]:
    try:
        broad = _book(broad_book, decision_at, stale_after)
        narrow = _book(narrow_book, decision_at, stale_after)
    except ShadowKernelError as exc:
        return _base(
            "structural_threshold",
            acquired_at,
            decision_at,
            lead.event_ticker,
            stable_hash((lead.cohort_identity, lead.event_ticker)),
            (lead.broad_market_ticker, lead.narrow_market_ticker),
            {"state": "active"},
            stable_hash((lead.broad_rules_hash, lead.narrow_rules_hash)),
            lead.source_authority,
            {"source_authority": lead.source_authority},
            [],
            "ABSTAIN",
            "STALE_OR_GAPPED_BOOK",
            {"detail": str(exc), "cohort_identity": lead.cohort_identity},
            start_receipt_digest=start_receipt_digest,
        )
    if (
        lead.broad_market_ticker != broad_book.ticker
        or lead.narrow_market_ticker != narrow_book.ticker
    ):
        raise ShadowKernelError("structural leg ticker mismatch")
    if leg_authority is None or set(leg_authority) != {
        lead.broad_market_ticker,
        lead.narrow_market_ticker,
    }:
        return _base(
            "structural_threshold",
            acquired_at,
            decision_at,
            lead.event_ticker,
            stable_hash((lead.cohort_identity, lead.event_ticker)),
            (lead.broad_market_ticker, lead.narrow_market_ticker),
            {"state": "active"},
            stable_hash((lead.broad_rules_hash, lead.narrow_rules_hash)),
            lead.source_authority,
            {"source_authority": lead.source_authority},
            [broad, narrow],
            "ABSTAIN",
            "MARKET_AUTHORITY_NOT_HYDRATED",
            {"cohort_identity": lead.cohort_identity},
            start_receipt_digest=start_receipt_digest,
        )
    for ticker, authority in leg_authority.items():
        if authority.market.ticker != ticker or authority.event.ticker != lead.event_ticker:
            raise ShadowKernelError("structural leg authority identity mismatch")
    values = {
        "structural_policy_identity": STRUCTURAL_POLICY_ID,
        "relationship_type": lead.relationship_type.value,
        "cohort_identity": lead.cohort_identity,
        "thresholds": [str(lead.broad_threshold), str(lead.narrow_threshold)],
        "raw_structural_inconsistency": raw_inconsistency,
        "executable_price_simulation": "NOT_ATTEMPTED",
        "fee_treatment": "NOT_ATTEMPTED",
        "non_atomic_fill_risk": "NOT_SIMULATED",
        "leg_freshness": {
            broad["ticker"]: broad["snapshot_id"],
            narrow["ticker"]: narrow["snapshot_id"],
        },
        "market_authority": {
            ticker: leg_authority[ticker].identity for ticker in sorted(leg_authority)
        },
    }
    return _base(
        "structural_threshold",
        acquired_at,
        decision_at,
        lead.event_ticker,
        stable_hash((lead.cohort_identity, lead.event_ticker)),
        (lead.broad_market_ticker, lead.narrow_market_ticker),
        {"state": "active"},
        stable_hash((lead.broad_rules_hash, lead.narrow_rules_hash)),
        lead.source_authority,
        {"source_authority": lead.source_authority},
        [broad, narrow],
        "OBSERVE",
        None,
        values,
        start_receipt_digest=start_receipt_digest,
    )


def validate_observation(
    obs: Mapping[str, Any],
    *,
    now: datetime | None = None,
    start_receipt: Mapping[str, Any] | None = None,
) -> None:
    if (
        obs.get("schema") != SCHEMA_ID
        or obs.get("research_only") is not True
        or obs.get("production_influence") != ZERO
    ):
        raise ShadowKernelError("observation safety/schema invariant failed")
    if obs.get("strategy_family") not in ADAPTER_VERSIONS:
        raise ShadowKernelError("unknown strategy adapter")
    if obs.get("strategy_version") != ADAPTER_VERSIONS[obs["strategy_family"]]:
        raise ShadowKernelError("strategy adapter identity mismatch")
    if not _require_text(obs, "independent_event_identity"):
        raise ShadowKernelError("event independence identity missing")
    if obs.get("decision") not in {"OBSERVE", "ABSTAIN"}:
        raise ShadowKernelError("invalid decision")
    if obs["decision"] == "ABSTAIN" and not _require_text(obs, "reason_code"):
        raise ShadowKernelError("abstention reason missing")
    acquired = datetime.fromisoformat(_require_text(obs, "acquisition_timestamp"))
    decision = datetime.fromisoformat(_require_text(obs, "decision_timestamp"))
    _iso(acquired, "acquisition_timestamp")
    _iso(decision, "decision_timestamp")
    if decision < acquired or (now is not None and decision > now):
        raise ShadowKernelError("future or inverted observation time")
    receipt_digest = _require_text(obs, "start_receipt_digest")
    if not _SHA256.fullmatch(receipt_digest):
        raise ShadowKernelError("observation start receipt digest is malformed")
    if start_receipt is not None:
        validate_start_receipt(start_receipt)
        if receipt_digest != start_receipt["receipt_digest"]:
            raise ShadowKernelError("observation start receipt binding mismatch")
        if acquired < datetime.fromisoformat(start_receipt["start_at"]):
            raise ShadowKernelError("observation acquisition predates prospective start")
        if obs.get("strategy_version") != start_receipt["adapter_versions"].get(
            obs.get("strategy_family")
        ):
            raise ShadowKernelError("observation adapter convention drifted")
        for observation_field, receipt_field in (
            ("fee_policy_identity", "fee_policy_identity"),
            ("execution_price_convention", "execution_price_convention"),
            ("slippage_depth_model", "slippage_depth_model"),
        ):
            if obs.get(observation_field) != start_receipt[receipt_field]:
                raise ShadowKernelError(f"observation {observation_field} drifted from receipt")
    if not isinstance(obs.get("evidence"), dict) or obs.get("evidence_hash") != content_hash(
        obs["evidence"]
    ):
        raise ShadowKernelError("evidence hash mismatch")
    if obs["decision"] == "OBSERVE":
        _validate_observation_authority(obs)
        if obs["strategy_family"] == "daily_weather":
            if obs["evidence"].get("weather_protocol_identity") != (
                WEATHER_PROTOCOL_ID
                if start_receipt is None
                else start_receipt["weather_protocol_identity"]
            ):
                raise ShadowKernelError("weather protocol identity drifted")
        else:
            if obs["evidence"].get("structural_policy_identity") != (
                STRUCTURAL_POLICY_ID
                if start_receipt is None
                else start_receipt["structural_policy_identity"]
            ):
                raise ShadowKernelError("structural policy identity drifted")
    if not isinstance(obs.get("books"), list):
        raise ShadowKernelError("book snapshots missing")
    for book in obs["books"]:
        if book.get("state") != BookState.CURRENT.value or not isinstance(
            book.get("sequence"), int
        ):
            raise ShadowKernelError("book is stale/gapped or sequence malformed")
        if datetime.fromisoformat(book["observed_at"]) > decision:
            raise ShadowKernelError("book arrived after decision")
    body = dict(obs)
    body.pop("observation_id", None)
    body.pop("evidence_hash", None)
    expected = content_hash(body)
    if obs.get("observation_id") != expected:
        raise ShadowKernelError("observation identity/hash mismatch")


class ShadowObservationStore:
    """Durable append-only store with a single immutable start authority."""

    def __init__(self, path: str | Path, start_receipt: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS start_receipt (
                id INTEGER PRIMARY KEY CHECK(id=1), canonical_json TEXT NOT NULL,
                digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                sequence INTEGER PRIMARY KEY, observation_id TEXT UNIQUE NOT NULL,
                canonical_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                production_influence TEXT NOT NULL CHECK(production_influence='0')
            );
            CREATE TABLE IF NOT EXISTS abstentions (
                sequence INTEGER PRIMARY KEY, observation_id TEXT UNIQUE NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS observations_no_update
                BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS observations_no_delete
                BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'append only'); END;
            """)
        if start_receipt is not None:
            validate_start_receipt(start_receipt)
            encoded = canonical_json(start_receipt)
            with self._connect() as db:
                row = db.execute("SELECT canonical_json FROM start_receipt WHERE id=1").fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO start_receipt VALUES(1,?,?)",
                        (encoded, start_receipt["receipt_digest"]),
                    )
                elif row[0] != encoded:
                    raise ShadowKernelError("prospective-start authority already differs")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _start_receipt(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT canonical_json FROM start_receipt WHERE id=1").fetchone()
        if row is None:
            raise ShadowKernelError("prospective-start receipt missing")
        receipt = json.loads(row[0])
        if not isinstance(receipt, dict):
            raise ShadowKernelError("persisted prospective-start receipt is malformed")
        validate_start_receipt(receipt)
        return receipt

    def append(self, observation: Mapping[str, Any], *, now: datetime | None = None) -> int:
        start = self._start_receipt()
        validate_observation(observation, now=now, start_receipt=start)
        encoded = canonical_json(observation)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute(
                "SELECT sequence,content_hash FROM observations WHERE observation_id=?",
                (observation["observation_id"],),
            ).fetchone()
            if row is not None:
                if row[1] == digest:
                    return int(row[0])
                raise ShadowKernelError("duplicate observation ID with mutated evidence")
            seq = int(
                db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM observations").fetchone()[0]
            )
            db.execute(
                "INSERT INTO observations VALUES(?,?,?,?,?,?)",
                (
                    seq,
                    observation["observation_id"],
                    encoded,
                    digest,
                    datetime.now(UTC).isoformat(),
                    ZERO,
                ),
            )
            if observation["decision"] == "ABSTAIN":
                db.execute(
                    "INSERT INTO abstentions VALUES(?,?,?)",
                    (seq, observation["observation_id"], observation["reason_code"]),
                )
            return seq

    def validate(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._connect() as db:
            receipt = db.execute("SELECT canonical_json FROM start_receipt WHERE id=1").fetchone()
            rows = db.execute(
                "SELECT sequence,observation_id,canonical_json,content_hash "
                "FROM observations ORDER BY sequence"
            ).fetchall()
        if receipt is None:
            raise ShadowKernelError("prospective-start receipt missing")
        start = json.loads(receipt[0])
        validate_start_receipt(start)
        previous = 0
        ids = set()
        for seq, oid, encoded, digest in rows:
            if (
                seq != previous + 1
                or oid in ids
                or hashlib.sha256(encoded.encode()).hexdigest() != digest
            ):
                raise ShadowKernelError("sequence, duplicate, or content hash failure")
            obs = json.loads(encoded)
            validate_observation(obs, now=now, start_receipt=start)
            if oid != obs["observation_id"]:
                raise ShadowKernelError("observation row identity mismatch")
            ids.add(oid)
            previous = seq
        return {
            "receipt_digest": start["receipt_digest"],
            "observations": previous,
            "abstentions": sum(1 for r in rows if json.loads(r[2])["decision"] == "ABSTAIN"),
        }
