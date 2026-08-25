"""Canonical historical-economics evidence boundary for M28D-R1.

R1 contains evidence only. It proves whether an exact canonical M28C selected candle contains
an executable top-of-book quote at the historical decision cutoff. It does not calculate edge,
PnL, fill truth, profitability, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.model_tournament import (
    HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION,
    MARKET_CANDLE_INTERVAL_MINUTES,
    MARKET_CHECKPOINT_SCHEMA_VERSION,
    HistoricalMarketResponseEvidence,
    MarketCheckpoint,
)

EXECUTABLE_QUOTE_SCHEMA_VERSION = "m28d-r1-executable-quote-evidence-v1"
EXECUTABLE_QUOTE_STALENESS_POLICY_VERSION = "m28d-r1-max-one-canonical-candle-v1"
MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS = MARKET_CANDLE_INTERVAL_MINUTES * 60


class HistoricalEconomicsEvidenceError(ValueError):
    """Historical economics evidence violates a canonical identity or timing invariant."""


class NoQuoteProvenance(StrEnum):
    DIRECTLY_OBSERVED = "DIRECTLY_OBSERVED"
    DERIVED_COMPLEMENT = "DERIVED_COMPLEMENT"


@dataclass(frozen=True, slots=True)
class ExecutableQuoteEvidence:
    """One exact M28C-bound historical quote eligible for later reconstructed economics."""

    market_ticker: str
    checkpoint_at: datetime
    response_evidence_id: str
    selected_candle_hash: str
    selected_candle_end_ts: int
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    no_quote_provenance: NoQuoteProvenance
    quote_age_seconds: int
    max_quote_staleness_seconds: int
    staleness_policy_version: str
    schema_version: str
    evidence_id: str
    content_hash: str


def build_executable_quote_evidence(
    *,
    response_evidence: HistoricalMarketResponseEvidence,
    checkpoint: MarketCheckpoint,
    selected_candle: Mapping[str, object],
) -> ExecutableQuoteEvidence:
    """Bind actual bid/ask fields from the exact canonical M28C-selected candle."""

    if not isinstance(response_evidence, HistoricalMarketResponseEvidence):
        raise HistoricalEconomicsEvidenceError("canonical historical market response is required")
    if not isinstance(checkpoint, MarketCheckpoint):
        raise HistoricalEconomicsEvidenceError("canonical market checkpoint is required")
    if not isinstance(selected_candle, Mapping):
        raise HistoricalEconomicsEvidenceError("exact selected candle mapping is required")

    _validate_response_identity(response_evidence)
    _validate_checkpoint_identity(checkpoint)
    if response_evidence.market_ticker != checkpoint.market_ticker:
        raise HistoricalEconomicsEvidenceError("response and checkpoint ticker binding is invalid")
    if response_evidence.evidence_id != checkpoint.response_evidence_id:
        raise HistoricalEconomicsEvidenceError("checkpoint is bound to another response")
    if (
        response_evidence.request_start_ts != checkpoint.request_start_ts
        or response_evidence.request_end_ts != checkpoint.request_end_ts
        or response_evidence.request_path != checkpoint.request_path
    ):
        raise HistoricalEconomicsEvidenceError("response and checkpoint request binding is invalid")
    if checkpoint.selected_candle_hash not in response_evidence.candle_hashes:
        raise HistoricalEconomicsEvidenceError("selected candle is absent from bound response")

    exact_candle_hash = stable_hash(selected_candle)
    if exact_candle_hash != checkpoint.selected_candle_hash:
        raise HistoricalEconomicsEvidenceError("exact selected candle hash binding is invalid")
    period = selected_candle.get("end_period_ts")
    if isinstance(period, bool) or not isinstance(period, int):
        raise HistoricalEconomicsEvidenceError("selected candle end timestamp is malformed")
    if period != checkpoint.selected_candle_end_ts:
        raise HistoricalEconomicsEvidenceError("selected candle end timestamp binding is invalid")

    cutoff = _aware_utc(checkpoint.checkpoint_at, field_name="checkpoint")
    selected_end = datetime.fromtimestamp(period, tz=UTC)
    age_seconds = int((cutoff - selected_end).total_seconds())
    if age_seconds < 0:
        raise HistoricalEconomicsEvidenceError("executable quote is after decision cutoff")
    if age_seconds > MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS:
        raise HistoricalEconomicsEvidenceError("executable quote exceeds fixed staleness bound")

    yes_bid = _required_quote_decimal(selected_candle, "yes_bid")
    yes_ask = _required_quote_decimal(selected_candle, "yes_ask")
    if yes_bid > yes_ask:
        raise HistoricalEconomicsEvidenceError("historical YES bid exceeds YES ask")

    has_no_bid = "no_bid" in selected_candle
    has_no_ask = "no_ask" in selected_candle
    if has_no_bid != has_no_ask:
        raise HistoricalEconomicsEvidenceError("direct NO quote evidence is incomplete")
    if has_no_bid:
        no_bid = _required_quote_decimal(selected_candle, "no_bid")
        no_ask = _required_quote_decimal(selected_candle, "no_ask")
        if no_bid > no_ask:
            raise HistoricalEconomicsEvidenceError("historical NO bid exceeds NO ask")
        no_provenance = NoQuoteProvenance.DIRECTLY_OBSERVED
    else:
        no_bid = Decimal("1") - yes_ask
        no_ask = Decimal("1") - yes_bid
        no_provenance = NoQuoteProvenance.DERIVED_COMPLEMENT

    material = (
        EXECUTABLE_QUOTE_SCHEMA_VERSION,
        checkpoint.market_ticker,
        cutoff.isoformat(),
        response_evidence.evidence_id,
        checkpoint.selected_candle_hash,
        checkpoint.selected_candle_end_ts,
        str(yes_bid),
        str(yes_ask),
        str(no_bid),
        str(no_ask),
        no_provenance.value,
        age_seconds,
        MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS,
        EXECUTABLE_QUOTE_STALENESS_POLICY_VERSION,
    )
    digest = stable_hash(material)
    return ExecutableQuoteEvidence(
        market_ticker=checkpoint.market_ticker,
        checkpoint_at=cutoff,
        response_evidence_id=response_evidence.evidence_id,
        selected_candle_hash=checkpoint.selected_candle_hash,
        selected_candle_end_ts=checkpoint.selected_candle_end_ts,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        no_quote_provenance=no_provenance,
        quote_age_seconds=age_seconds,
        max_quote_staleness_seconds=MAX_EXECUTABLE_QUOTE_STALENESS_SECONDS,
        staleness_policy_version=EXECUTABLE_QUOTE_STALENESS_POLICY_VERSION,
        schema_version=EXECUTABLE_QUOTE_SCHEMA_VERSION,
        evidence_id=digest,
        content_hash=digest,
    )


def _validate_response_identity(response: HistoricalMarketResponseEvidence) -> None:
    expected = stable_hash(
        (
            HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION,
            response.request_path,
            response.market_ticker,
            response.request_start_ts,
            response.request_end_ts,
            response.interval_minutes,
            response.response_sha256,
            response.candle_hashes,
        )
    )
    if response.schema_version != HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION:
        raise HistoricalEconomicsEvidenceError("historical market response schema is not canonical")
    if response.evidence_id != expected or response.content_hash != expected:
        raise HistoricalEconomicsEvidenceError("historical market response identity is invalid")


def _validate_checkpoint_identity(checkpoint: MarketCheckpoint) -> None:
    expected = stable_hash(
        (
            MARKET_CHECKPOINT_SCHEMA_VERSION,
            checkpoint.market_ticker,
            _aware_utc(checkpoint.checkpoint_at, field_name="checkpoint").isoformat(),
            checkpoint.request_start_ts,
            checkpoint.request_end_ts,
            checkpoint.request_path,
            checkpoint.response_evidence_id,
            checkpoint.selected_candle_end_ts,
            checkpoint.selected_candle_hash,
            str(checkpoint.yes_probability),
        )
    )
    if checkpoint.checkpoint_id != expected or checkpoint.content_hash != expected:
        raise HistoricalEconomicsEvidenceError("canonical market checkpoint identity is invalid")


def _required_quote_decimal(candle: Mapping[str, object], field_name: str) -> Decimal:
    nested = candle.get(field_name)
    if not isinstance(nested, Mapping):
        raise HistoricalEconomicsEvidenceError(f"executable {field_name} field is missing")
    value: object | None = None
    for key in ("close_dollars", "close"):
        if nested.get(key) is not None:
            value = nested[key]
            break
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise HistoricalEconomicsEvidenceError(f"executable {field_name} value is malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise HistoricalEconomicsEvidenceError(
            f"executable {field_name} value is nonnumeric"
        ) from exc
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("1"):
        raise HistoricalEconomicsEvidenceError(f"executable {field_name} is outside [0,1]")
    return result


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalEconomicsEvidenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
