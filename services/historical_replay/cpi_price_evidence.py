"""Research-only, immutable CPI sibling price evidence for CPI-E1-P9A."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote

from services.contract_intelligence.specification import (
    ContractSpecificationParser,
    SemanticsInputBundle,
)
from services.historical_replay.archive import stable_hash

SCHEMA_VERSION = "cpi-e1-p9a-historical-price-evidence-v1"
MAX_CANDLE_AGE_SECONDS = 60 * 60
PROVENANCE_MODE = "RECONSTRUCTED_PUBLIC_HISTORICAL"
PUBLIC_ORIGIN = "https://external-api.kalshi.com"
KXCPI_INVENTORY_PATH = "/trade-api/v2/historical/markets?limit=1000&series_ticker=KXCPI"
KXCPI_INVENTORY_CURSOR = ""
APPROVED_RUNTIME_MANIFEST_DIGEST = (
    "d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699"
)


@dataclass(frozen=True, slots=True)
class CanonicalCandleRequest:
    path: str
    url: str
    start_ts: int
    end_ts: int
    period_interval_minutes: int
    request_identity: str


def canonical_candle_request(market: dict[str, Any]) -> CanonicalCandleRequest:
    ticker = market.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("market ticker is malformed")
    try:
        opened = int(
            datetime.fromisoformat(str(market["open_time"]).replace("Z", "+00:00")).timestamp()
        )
        close = int(
            datetime.fromisoformat(str(market["close_time"]).replace("Z", "+00:00")).timestamp()
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("market timestamps are malformed") from exc
    if opened >= close:
        raise ValueError("market open must precede close")
    start = max(opened, close - int(timedelta(days=90).total_seconds()))
    path = (
        f"/trade-api/v2/historical/markets/{quote(ticker, safe='')}/candlesticks"
        f"?start_ts={start}&end_ts={close}&period_interval=60"
    )
    return CanonicalCandleRequest(
        path, PUBLIC_ORIGIN + path, start, close, 60, stable_hash((path, start, close, 60))
    )


def strict_json_loads(raw: bytes | str) -> Any:
    """Parse evidence JSON rejecting duplicate keys and non-standard constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ValueError(f"{field} is not decimal") from exc
    if not result.is_finite() or not Decimal(0) <= result <= Decimal(1):
        raise ValueError(f"{field} is outside [0, 1]")
    return result


def _nonnegative_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ValueError(f"{field} is not decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} is negative or non-finite")
    return result


def validate_candle_payload(
    payload: object,
    *,
    market_ticker: str,
    request_start_ts: int,
    request_end_ts: int,
    period_interval_minutes: int,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ticker") != market_ticker:
        raise ValueError("candle payload ticker mismatch")
    candles = payload.get("candlesticks")
    if not isinstance(candles, list):
        raise ValueError("candle payload candlesticks are malformed")
    previous: int | None = None
    output: list[dict[str, Any]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            raise ValueError("candle is malformed")
        period = candle.get("end_period_ts")
        if isinstance(period, bool) or not isinstance(period, int):
            raise ValueError("candle timestamp is malformed")
        if not request_start_ts <= period <= request_end_ts:
            raise ValueError("candle timestamp is outside request bounds")
        if previous is not None and period <= previous:
            raise ValueError("candle timestamps are not strictly ordered")
        if period % (period_interval_minutes * 60) != 0:
            raise ValueError("candle timestamp is off-grid")
        previous = period
        for field in ("yes_bid", "yes_ask"):
            quote_container = candle.get(field)
            if quote_container is not None and not isinstance(quote_container, dict):
                raise ValueError("quote container is malformed")
            if isinstance(quote_container, dict):
                _decimal(quote_container.get("close"), field)
        bid = (
            None
            if not isinstance(candle.get("yes_bid"), dict)
            else _decimal(candle["yes_bid"].get("close"), "yes_bid")
        )
        ask = (
            None
            if not isinstance(candle.get("yes_ask"), dict)
            else _decimal(candle["yes_ask"].get("close"), "yes_ask")
        )
        if bid is not None and ask is not None and bid > ask:
            raise ValueError("YES bid exceeds ask")
        output.append(candle)
    return output


@dataclass(frozen=True, slots=True)
class CPIPriceEvidence:
    event_ticker: str
    market_ticker: str
    underlying_event_id: str
    comparator: str
    threshold: Decimal
    market_open: datetime
    market_close: datetime
    request_path: str
    request_start_ts: int
    request_end_ts: int
    period_interval_minutes: int
    raw_sha256: str
    retrieved_at: datetime
    candle_end_period_ts: int | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    no_quote_provenance: str
    quote_age_seconds: int | None
    candle_volume: Decimal | None
    historical_total_volume: Decimal
    missing_side_reason: str | None
    staleness_state: str
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.underlying_event_id != f"kalshi:{self.event_ticker}":
            raise ValueError("siblings must share the canonical underlying event identity")
        if self.market_open.tzinfo is None or self.market_close.tzinfo is None:
            raise ValueError("market timestamps must be timezone-aware")
        if self.market_open >= self.market_close:
            raise ValueError("market open must precede market close")
        if not self.research_only or self.production_influence != 0:
            raise ValueError("P9A evidence cannot influence production")
        if self.candle_end_period_ts is not None:
            if self.candle_end_period_ts >= int(self.market_close.timestamp()):
                raise ValueError("selected candle is not strictly before market close")
            if self.quote_age_seconds is None or self.quote_age_seconds < 0:
                raise ValueError("selected candle age is required")

    @property
    def evidence_id(self) -> str:
        return stable_hash((SCHEMA_VERSION, self))


def parse_threshold(market: dict[str, Any]) -> Decimal:
    value = market.get("floor_strike")
    if value is None:
        value = market.get("subtitle", "").replace(">", "").replace("%", "")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ValueError("threshold is not decimal") from exc
    if not result.is_finite():
        raise ValueError("threshold is non-finite")
    return result


def build_price_evidence(
    market: dict[str, Any],
    *,
    request_path: str,
    request_start_ts: int,
    request_end_ts: int,
    raw_body: bytes,
    retrieved_at: datetime,
    candles: list[dict[str, Any]],
) -> CPIPriceEvidence:
    close = datetime.fromisoformat(str(market["close_time"]).replace("Z", "+00:00")).astimezone(UTC)
    opened = datetime.fromisoformat(str(market["open_time"]).replace("Z", "+00:00")).astimezone(UTC)
    candidates = [
        candle
        for candle in candles
        if isinstance(candle.get("end_period_ts"), int)
        and candle["end_period_ts"] < int(close.timestamp())
    ]
    selected = max(candidates, key=lambda row: row["end_period_ts"]) if candidates else None
    yes_bid = yes_ask = no_bid = no_ask = None
    candle_volume = None
    age = None
    missing: list[str] = []
    quote_age = None
    if selected is None:
        missing.append("NO_CANDLE_STRICTLY_BEFORE_CLOSE")
    else:
        bid = selected.get("yes_bid", {}).get("close")
        ask = selected.get("yes_ask", {}).get("close")
        yes_bid, yes_ask = _decimal(bid, "yes_bid"), _decimal(ask, "yes_ask")
        candle_volume = _nonnegative_decimal(selected.get("volume", "0"), "candle_volume")
        quote_age = int(close.timestamp()) - int(selected["end_period_ts"])
        age = quote_age
        if yes_bid is None or yes_ask is None:
            missing.append("INCOMPLETE_YES_QUOTE")
        else:
            no_bid, no_ask = Decimal(1) - yes_ask, Decimal(1) - yes_bid
            if yes_ask == 1:
                missing.append("YES_ENTRY_BOUNDARY_ASK_1.00")
            if yes_bid == 0:
                missing.append("NO_ENTRY_BOUNDARY_FROM_YES_BID_0.00")
        if quote_age > MAX_CANDLE_AGE_SECONDS:
            missing.append("STALE_OVER_1H")
    return CPIPriceEvidence(
        event_ticker=str(market["event_ticker"]),
        market_ticker=str(market["ticker"]),
        underlying_event_id=f"kalshi:{market['event_ticker']}",
        comparator="GT",
        threshold=parse_threshold(market),
        market_open=opened,
        market_close=close,
        request_path=request_path,
        request_start_ts=request_start_ts,
        request_end_ts=request_end_ts,
        period_interval_minutes=60,
        raw_sha256=sha256(raw_body).hexdigest(),
        retrieved_at=retrieved_at.astimezone(UTC),
        candle_end_period_ts=None if selected is None else selected["end_period_ts"],
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        no_quote_provenance="DERIVED_COMPLEMENT" if no_bid is not None else "NONE",
        quote_age_seconds=age,
        candle_volume=candle_volume,
        historical_total_volume=_nonnegative_decimal(
            market.get("volume_fp", "0"), "historical_total_volume"
        )
        or Decimal(0),
        missing_side_reason=";".join(missing) or None,
        staleness_state=(
            "NO_CANDLE"
            if selected is None
            else "STALE"
            if quote_age is not None and quote_age > MAX_CANDLE_AGE_SECONDS
            else "FRESH"
        ),
    )


def validate_frozen_cohort(root: Path) -> dict[str, int]:
    """Offline integrity/completeness validation for the tracked P9A cohort."""
    manifest_path = root / "manifest.json"
    manifest = strict_json_loads(manifest_path.read_bytes())
    recorded = manifest.pop("final_manifest_sha256", None)
    if recorded != stable_hash(manifest):
        raise ValueError("frozen manifest hash mismatch")
    inventory_path = root / "market_inventory.json"
    if not inventory_path.is_file():
        raise ValueError("missing frozen market inventory")
    inventory_raw = inventory_path.read_bytes()
    inventory_sha256 = sha256(inventory_raw).hexdigest()
    if inventory_sha256 != manifest.get("market_inventory_sha256"):
        raise ValueError("market inventory hash mismatch")
    if manifest.get("acquisition_manifest_artifact") != "acquisition_manifest.json":
        raise ValueError("acquisition manifest artifact path is invalid")
    acquisition_path = root / "acquisition_manifest.json"
    if not acquisition_path.is_file():
        raise ValueError("missing acquisition manifest")
    acquisition_raw = acquisition_path.read_bytes()
    if sha256(acquisition_raw).hexdigest() != manifest.get("acquisition_manifest_sha256"):
        raise ValueError("acquisition manifest raw hash mismatch")
    acquisition = strict_json_loads(acquisition_raw)
    if not isinstance(acquisition, dict):
        raise ValueError("acquisition manifest is malformed")
    acquisition_digest = acquisition.copy()
    acquisition_recorded = acquisition_digest.pop("manifest_sha256", None)
    if (
        manifest.get("approved_acquisition_manifest_sha256") != APPROVED_RUNTIME_MANIFEST_DIGEST
        or acquisition_recorded != APPROVED_RUNTIME_MANIFEST_DIGEST
        or stable_hash(acquisition_digest) != APPROVED_RUNTIME_MANIFEST_DIGEST
    ):
        raise ValueError("approved acquisition manifest digest mismatch")
    if (
        acquisition.get("series_ticker") != "KXCPI"
        or acquisition.get("market_inventory", {}).get("path") != KXCPI_INVENTORY_PATH
        or acquisition.get("market_inventory", {}).get("sha256") != inventory_sha256
        or manifest.get("market_inventory_sha256") != inventory_sha256
    ):
        raise ValueError("acquisition inventory authority mismatch")
    inventory = strict_json_loads(inventory_raw)
    if (
        manifest.get("series_ticker") != "KXCPI"
        or manifest.get("series_membership_invariant")
        != "INVENTORY_RESPONSE_FILTERED_BY_SERIES_TICKER_KXCPI"
        or manifest.get("market_inventory_request")
        != {
            "path": KXCPI_INVENTORY_PATH,
            "cursor": KXCPI_INVENTORY_CURSOR,
            "cursor_exhausted": True,
            "response_sha256": manifest.get("market_inventory_sha256"),
        }
    ):
        raise ValueError("frozen KXCPI inventory provenance is invalid")
    if not isinstance(inventory, dict) or inventory.get("cursor") not in (None, ""):
        raise ValueError("frozen KXCPI inventory cursor is not exhausted")
    inventory_rows = inventory.get("markets") if isinstance(inventory, dict) else None
    if not isinstance(inventory_rows, list) or len(inventory_rows) != 474:
        raise ValueError("frozen market inventory is incomplete")
    inventory_by_ticker = {row.get("ticker"): row for row in inventory_rows}
    if len(inventory_by_ticker) != 474:
        raise ValueError("frozen market inventory ticker identity is duplicated")
    rows = manifest.get("markets")
    events = manifest.get("events")
    acquisition_rows = acquisition.get("markets")
    acquisition_events = acquisition.get("events")
    if not isinstance(acquisition_rows, list) or len(acquisition_rows) != 474:
        raise ValueError("acquisition market cohort is incomplete")
    if not isinstance(acquisition_events, list) or len(acquisition_events) != 60:
        raise ValueError("acquisition event cohort is incomplete")
    if {row.get("market_ticker") for row in acquisition_rows} != {
        row.get("market_ticker") for row in rows
    }:
        raise ValueError("acquisition market identities do not match frozen cohort")
    if {event.get("event_ticker") for event in acquisition_events} != {
        event.get("event_ticker") for event in events
    }:
        raise ValueError("acquisition event identities do not match frozen cohort")
    acquisition_by_ticker = {row["market_ticker"]: row for row in acquisition_rows}
    if not isinstance(rows, list) or len(rows) != 474:
        raise ValueError("frozen cohort must contain exactly 474 markets")
    if not isinstance(events, list) or len(events) != 60:
        raise ValueError("frozen cohort must contain exactly 60 events")
    if (
        manifest.get("research_only") is not True
        or manifest.get("production_influence") != "0"
        or manifest.get("provenance_mode") != PROVENANCE_MODE
        or manifest.get("actual_bot_ingest_at") is not None
        or manifest.get("prospective_observation") is not False
    ):
        raise ValueError("frozen cohort safety or provenance fields are invalid")
    tickers = [row.get("market_ticker") for row in rows]
    if any(not isinstance(ticker, str) for ticker in tickers) or len(set(tickers)) != 474:
        raise ValueError("market ticker identity is duplicated or malformed")
    event_tickers = [event.get("event_ticker") for event in events]
    if len(set(event_tickers)) != 60:
        raise ValueError("event identity is duplicated or malformed")
    both = fresh = 0
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event = row.get("event_ticker")
        grouped.setdefault(str(event), []).append(row)
        inventory_row = inventory_by_ticker.get(row.get("market_ticker"))
        if inventory_row is None:
            raise ValueError("market is absent from frozen inventory")
        if row.get("market_row_hash") != stable_hash(inventory_row):
            raise ValueError("market inventory row hash mismatch")
        if row.get("event_ticker") != inventory_row.get("event_ticker"):
            raise ValueError("market inventory event binding mismatch")
        acquisition_row = acquisition_by_ticker.get(row.get("market_ticker"))
        if acquisition_row is None:
            raise ValueError("market is absent from acquisition manifest")
        for field in (
            "event_ticker",
            "underlying_event_id",
            "raw_artifact",
            "request_path",
            "request_start_ts",
            "request_end_ts",
            "period_interval_minutes",
        ):
            if acquisition_row.get(field) != row.get(field):
                raise ValueError("acquisition market identity mismatch")
        for field in ("raw_sha256",):
            if acquisition_row.get(field) != row.get(field):
                raise ValueError("acquisition raw identity mismatch")
        if not re.fullmatch(
            r"(?:KXCPI|CPI)-[0-9]{2}[A-Z]{3}(?:-T(?:N?[0-9]+(?:\.[0-9]+)?|-[0-9]+(?:\.[0-9]+)?))?",
            str(row.get("market_ticker")),
        ):
            raise ValueError("market ticker grammar is invalid")
        if not re.fullmatch(r"(?:KXCPI|CPI)-[0-9]{2}[A-Z]{3}", str(row.get("event_ticker"))):
            raise ValueError("event ticker grammar is invalid")
        if (
            not isinstance(inventory_row.get("rules_primary"), str)
            or "CPI" not in inventory_row["rules_primary"]
        ):
            raise ValueError("inventory row is not a CPI series member")
        request = canonical_candle_request(inventory_row)
        if any(
            row.get(field) != value
            for field, value in (
                ("request_path", request.path),
                ("request_url", request.url),
                ("request_start_ts", request.start_ts),
                ("request_end_ts", request.end_ts),
                ("period_interval_minutes", request.period_interval_minutes),
                ("request_identity", request.request_identity),
            )
        ):
            raise ValueError("canonical candle request identity mismatch")
        if row.get("raw_artifact") != f"raw/{row.get('market_ticker')}.json":
            raise ValueError("raw artifact path is detached from market ticker")
        row_open = datetime.fromisoformat(str(row["market_open"]).replace("Z", "+00:00"))
        inventory_open = datetime.fromisoformat(
            str(inventory_row["open_time"]).replace("Z", "+00:00")
        )
        if row_open != inventory_open:
            raise ValueError("market inventory open timestamp mismatch")
        row_close = datetime.fromisoformat(str(row["market_close"]).replace("Z", "+00:00"))
        inventory_close = datetime.fromisoformat(
            str(inventory_row["close_time"]).replace("Z", "+00:00")
        )
        if row_close != inventory_close:
            raise ValueError("market inventory close timestamp mismatch")
        semantics = ContractSpecificationParser().parse(
            SemanticsInputBundle.build(
                inventory_row,
                {"event_ticker": inventory_row["event_ticker"], "series_ticker": "KXCPI"},
                {"ticker": "KXCPI", "category": "Economics"},
            )
        )
        if (
            row.get("comparator") != semantics.comparator.name
            or row.get("comparator_symbol") != semantics.comparator.value
            or row.get("threshold") != str(semantics.threshold_value)
            or row.get("payout_model") != semantics.payout_model.value
            or row.get("semantic_hash") != semantics.semantic_hash
        ):
            raise ValueError("canonical contract semantics mismatch")
        if row.get("underlying_event_id") != f"kalshi:{event}":
            raise ValueError("market is bound to the wrong underlying event")
        if row.get("point_in_time_feature_eligible") is not False:
            raise ValueError("retrospective volume was marked PIT-eligible")
        if row.get("retrospective_full_lifecycle_volume") != str(
            inventory_row.get("volume_fp", "0")
        ):
            raise ValueError("retrospective volume mismatch")
        if row.get("research_only") is not True or row.get("production_influence") != "0":
            raise ValueError("market safety fields are invalid")
        if (
            row.get("actual_bot_ingest_at") is not None
            or row.get("prospective_observation") is not False
        ):
            raise ValueError("market provenance fields are invalid")
        raw_path = root / str(row.get("raw_artifact"))
        if not raw_path.is_file():
            raise ValueError(f"missing raw artifact: {raw_path}")
        raw = raw_path.read_bytes()
        raw_hash = sha256(raw).hexdigest()
        if raw_hash != row.get("raw_sha256") or raw_hash != row.get("raw_artifact_sha256"):
            raise ValueError(f"raw artifact hash mismatch: {raw_path}")
        payload = strict_json_loads(raw)
        candles = validate_candle_payload(
            payload,
            market_ticker=str(row["market_ticker"]),
            request_start_ts=request.start_ts,
            request_end_ts=request.end_ts,
            period_interval_minutes=request.period_interval_minutes,
        )
        selected_end = row.get("candle_end_period_ts")
        selected_hash = row.get("selected_candle_hash")
        cutoff = int(
            datetime.fromisoformat(str(row["market_close"]).replace("Z", "+00:00")).timestamp()
        )
        candidates = [
            c
            for c in candles
            if isinstance(c.get("end_period_ts"), int) and c["end_period_ts"] < cutoff
        ]
        selected = max(candidates, key=lambda c: c["end_period_ts"]) if candidates else None
        if (selected is None) != (selected_end is None):
            raise ValueError("selected candle presence is inconsistent")
        if selected_end is not None and selected_end >= cutoff:
            raise ValueError("selected candle is not strictly before market close")
        if selected is not None and (
            selected["end_period_ts"] != selected_end or stable_hash(selected) != selected_hash
        ):
            raise ValueError("selected candle is not the latest admissible candle")
        actual_bid = (
            None
            if selected is None
            else _decimal(selected.get("yes_bid", {}).get("close"), "yes_bid")
        )
        actual_ask = (
            None
            if selected is None
            else _decimal(selected.get("yes_ask", {}).get("close"), "yes_ask")
        )
        actual_no_bid = None if actual_ask is None else Decimal(1) - actual_ask
        actual_no_ask = None if actual_bid is None else Decimal(1) - actual_bid
        actual_age = None if selected is None else cutoff - int(selected["end_period_ts"])
        actual_volume = (
            None
            if selected is None
            else _nonnegative_decimal(selected.get("volume", "0"), "candle_volume")
        )
        for field, value in (
            ("yes_bid", actual_bid),
            ("yes_ask", actual_ask),
            ("no_bid", actual_no_bid),
            ("no_ask", actual_no_ask),
            ("candle_volume", actual_volume),
        ):
            manifest_value = row.get(field)
            if value is None:
                matches = manifest_value is None
            else:
                try:
                    matches = Decimal(str(manifest_value)) == value
                except Exception:
                    matches = False
            if not matches:
                raise ValueError(f"derived field mismatch: {field}")
        if row.get("quote_age_seconds") != actual_age:
            raise ValueError("derived field mismatch: quote_age_seconds")
        expected_state = (
            "NO_CANDLE"
            if selected is None
            else "STALE"
            if actual_age is not None and actual_age > MAX_CANDLE_AGE_SECONDS
            else "FRESH"
        )
        if row.get("staleness_state") != expected_state:
            raise ValueError("derived field mismatch: staleness_state")
        missing: list[str] = []
        if selected is None:
            missing.append("NO_CANDLE_STRICTLY_BEFORE_CLOSE")
        elif actual_bid is None or actual_ask is None:
            missing.append("INCOMPLETE_YES_QUOTE")
        else:
            if actual_ask == 1:
                missing.append("YES_ENTRY_BOUNDARY_ASK_1.00")
            if actual_bid == 0:
                missing.append("NO_ENTRY_BOUNDARY_FROM_YES_BID_0.00")
        if actual_age is not None and actual_age > MAX_CANDLE_AGE_SECONDS:
            missing.append("STALE_OVER_1H")
        if row.get("missing_side_reason") != (";".join(missing) or None):
            raise ValueError("derived field mismatch: missing_side_reason")
        if row.get("yes_ask") not in (None, "1.0000") and row.get("yes_bid") not in (
            None,
            "0.0000",
        ):
            both += 1
        if row.get("staleness_state") == "FRESH":
            fresh += 1
    for event in events:
        ticker = event.get("event_ticker")
        members = grouped.get(str(ticker), [])
        if len(members) != event.get("intended_siblings") or not event.get("complete"):
            raise ValueError("event sibling completeness mismatch")
    if both != 267 or fresh != 148:
        raise ValueError("frozen cohort summary counts do not match P9A receipt")
    return {"events": len(events), "siblings": len(rows), "both_usable": both, "fresh": fresh}
