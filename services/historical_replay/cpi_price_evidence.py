"""Research-only, immutable CPI sibling price evidence for CPI-E1-P9A."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from services.historical_replay.archive import stable_hash

SCHEMA_VERSION = "cpi-e1-p9a-historical-price-evidence-v1"
MAX_CANDLE_AGE_SECONDS = 60 * 60
PROVENANCE_MODE = "RECONSTRUCTED_PUBLIC_HISTORICAL"


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
    manifest = json.loads(manifest_path.read_text())
    recorded = manifest.pop("final_manifest_sha256", None)
    if recorded != stable_hash(manifest):
        raise ValueError("frozen manifest hash mismatch")
    rows = manifest.get("markets")
    events = manifest.get("events")
    if not isinstance(rows, list) or len(rows) != 474:
        raise ValueError("frozen cohort must contain exactly 474 markets")
    if not isinstance(events, list) or len(events) != 60:
        raise ValueError("frozen cohort must contain exactly 60 events")
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
        if row.get("underlying_event_id") != f"kalshi:{event}":
            raise ValueError("market is bound to the wrong underlying event")
        raw_path = root / str(row.get("raw_artifact"))
        if not raw_path.is_file():
            raise ValueError(f"missing raw artifact: {raw_path}")
        raw = raw_path.read_bytes()
        if sha256(raw).hexdigest() != row.get("raw_sha256"):
            raise ValueError(f"raw artifact hash mismatch: {raw_path}")
        payload = json.loads(raw)
        candles = payload.get("candlesticks")
        selected_hash = row.get("selected_candle_hash")
        selected_end = row.get("candle_end_period_ts")
        selected = [candle for candle in candles or [] if stable_hash(candle) == selected_hash]
        if selected_end is not None and len(selected) != 1:
            raise ValueError("selected candle identity is absent or duplicated")
        if selected_end is not None and selected_end >= int(
            datetime.fromisoformat(str(row["market_close"]).replace("Z", "+00:00")).timestamp()
        ):
            raise ValueError("selected candle is not strictly before market close")
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
