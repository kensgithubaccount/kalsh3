"""Small, append-only A0.1 prospective observation kernel.

This module is deliberately a research boundary.  It composes the reviewed weather
authority, structural cohort scanner, and sequenced book view; it has no account,
credential, or order-path import.  All persisted values are canonical JSON strings.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
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
from services.opportunity_engine.structural import (
    POLICY_VERSION as STRUCTURAL_SCANNER_VERSION,
)
from services.opportunity_engine.structural import (
    StructuralLead,
)
from services.real_time_market_data.orderbook import BookState, BookView

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
StartReceipt = dict[str, Any]


class ShadowKernelError(ValueError):
    pass


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
    return {
        "ticker": view.ticker,
        "snapshot_id": stable_hash(
            (view.ticker, view.sequence, observed, view.yes_bids, view.no_bids)
        ),
        "sequence": view.sequence,
        "observed_at": observed,
        "state": view.state.value,
        "yes_bids": [[str(p), str(q)] for p, q in view.yes_bids],
        "no_bids": [[str(p), str(q)] for p, q in view.no_bids],
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
    execution_convention: str = "research-only-no-order; displayed-price-only",
    slippage_model: str = "none-v1; executable simulation separately labeled",
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
        "independence_rules": (
            "event/day/location for weather; one cohort/event relationship for structural"
        ),
        "abstention_behavior": "fail-closed; persist explicit abstention; no retroactive backfill",
        "start_at": _iso(start_at, "start_at"),
        "research_only": True,
        "production_influence": ZERO,
    }
    receipt["receipt_digest"] = content_hash(receipt)
    return receipt


def validate_start_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema") != RECEIPT_SCHEMA_ID or receipt.get("research_only") is not True:
        raise ShadowKernelError("invalid prospective-start receipt")
    if receipt.get("production_influence") != ZERO:
        raise ShadowKernelError("prospective-start receipt has production influence")
    digest = receipt.get("receipt_digest")
    body = dict(receipt)
    body.pop("receipt_digest", None)
    if digest != content_hash(body):
        raise ShadowKernelError("prospective-start receipt digest mismatch")
    _iso(datetime.fromisoformat(_require_text(receipt, "start_at")), "start_at")
    if receipt.get("weather_protocol_identity") != WEATHER_PROTOCOL_ID:
        raise ShadowKernelError("weather protocol identity mismatch")
    if receipt.get("structural_policy_identity") != STRUCTURAL_POLICY_ID:
        raise ShadowKernelError("structural policy identity mismatch")


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
        "fee_policy_identity": "research-only-fees-v1",
        "execution_price_convention": "displayed-price-only",
        "slippage_depth_model": "none-v1; no executable conclusion",
        "decision": decision,
        "reason_code": reason,
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
) -> dict[str, Any]:
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
        )
    if (
        lead.broad_market_ticker != broad_book.ticker
        or lead.narrow_market_ticker != narrow_book.ticker
    ):
        raise ShadowKernelError("structural leg ticker mismatch")
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
    )


def validate_observation(obs: Mapping[str, Any], *, now: datetime | None = None) -> None:
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
    if not isinstance(obs.get("evidence"), dict) or obs.get("evidence_hash") != content_hash(
        obs["evidence"]
    ):
        raise ShadowKernelError("evidence hash mismatch")
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

    def append(self, observation: Mapping[str, Any], *, now: datetime | None = None) -> int:
        validate_observation(observation, now=now)
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
            validate_observation(obs, now=now)
            if oid != obs["observation_id"]:
                raise ShadowKernelError("observation row identity mismatch")
            boundary = datetime.fromisoformat(start["start_at"])
            if datetime.fromisoformat(obs["acquisition_timestamp"]) < boundary:
                raise ShadowKernelError("observation acquisition predates prospective start")
            ids.add(oid)
            previous = seq
        return {
            "receipt_digest": start["receipt_digest"],
            "observations": previous,
            "abstentions": sum(1 for r in rows if json.loads(r[2])["decision"] == "ABSTAIN"),
        }
