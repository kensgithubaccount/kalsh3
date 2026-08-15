"""Content-addressed, research-only current event-market fee metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.historical_replay.archive import stable_hash
from services.market_universe.domain import UniverseValidationError, exact_numeric, parse_time

from .domain import OpportunityError
from .fees import FeeType

SUPPORTED = frozenset({FeeType.QUADRATIC, FeeType.QUADRATIC_WITH_MAKER_FEES})


def _fee_type(value: Any, field: str) -> FeeType:
    if not isinstance(value, str):
        raise OpportunityError(f"invalid {field}")
    try:
        parsed = FeeType(value)
    except ValueError as exc:
        raise OpportunityError(f"unsupported {field}") from exc
    if parsed not in SUPPORTED:
        raise OpportunityError(f"unsupported {field}")
    return parsed


def _multiplier(value: Any, field: str) -> Decimal:
    try:
        result = exact_numeric(value, field)
    except UniverseValidationError as exc:
        raise OpportunityError(str(exc)) from exc
    if result < 0:
        raise OpportunityError(f"negative {field}")
    return result


@dataclass(frozen=True, slots=True)
class EventFeeOverride:
    fee_type: FeeType | None
    fee_multiplier: Decimal | None
    complete: bool
    metadata_hash: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> EventFeeOverride:
        type_value, multiplier_value = (
            raw.get("fee_type_override"),
            raw.get("fee_multiplier_override"),
        )
        present = (type_value is not None, multiplier_value is not None)
        digest = stable_hash((type_value, multiplier_value))
        if present == (False, False):
            return cls(None, None, False, digest)
        if present[0] != present[1]:
            raise OpportunityError("partial event fee override")
        return cls(
            _fee_type(type_value, "event fee type"),
            _multiplier(multiplier_value, "event fee multiplier"),
            True,
            digest,
        )


@dataclass(frozen=True, slots=True)
class CurrentSeriesFeeObservation:
    series_ticker: str
    fee_type: FeeType
    fee_multiplier: Decimal
    source_updated_at: datetime
    observed_at: datetime
    source_hash: str
    observation_id: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def parse(cls, raw: dict[str, Any], *, observed_at: datetime) -> CurrentSeriesFeeObservation:
        ticker = raw.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            raise OpportunityError("series ticker missing")
        updated = parse_time(raw.get("last_updated_ts"))
        if updated is None:
            raise OpportunityError("series source timestamp missing")
        observed = _utc(observed_at)
        if updated > observed:
            raise OpportunityError("series metadata observed before source update")
        source_hash = stable_hash(raw)
        values = (
            ticker,
            str(raw.get("fee_type")),
            str(raw.get("fee_multiplier")),
            updated,
            observed,
            source_hash,
        )
        return cls(
            ticker,
            _fee_type(raw.get("fee_type"), "series fee type"),
            _multiplier(raw.get("fee_multiplier"), "series fee multiplier"),
            updated,
            observed,
            source_hash,
            stable_hash(values),
        )


@dataclass(frozen=True, slots=True)
class FeeChange:
    change_id: str
    series_ticker: str
    fee_type: FeeType
    fee_multiplier: Decimal
    scheduled_at: datetime
    content_hash: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> FeeChange:
        identifier, ticker = raw.get("id"), raw.get("series_ticker")
        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(ticker, str)
            or not ticker
        ):
            raise OpportunityError("fee change identity invalid")
        scheduled = parse_time(raw.get("scheduled_ts"))
        if scheduled is None:
            raise OpportunityError("fee change timestamp missing")
        digest = stable_hash(raw)
        return cls(
            identifier,
            ticker,
            _fee_type(raw.get("fee_type"), "fee change type"),
            _multiplier(raw.get("fee_multiplier"), "fee change multiplier"),
            scheduled,
            digest,
        )

    def is_scheduled(self, at: datetime) -> bool:
        return self.scheduled_at > _utc(at)


@dataclass(frozen=True, slots=True)
class ResolvedFeeRegime:
    regime_id: str
    version: str
    fee_type: FeeType
    fee_multiplier: Decimal
    source: str
    series_observation_id: str
    event_metadata_hash: str
    production_influence: Decimal = Decimal(0)


def resolve_current_fee_regime(
    series: CurrentSeriesFeeObservation, event: EventFeeOverride
) -> ResolvedFeeRegime:
    fee_type = event.fee_type if event.complete else series.fee_type
    multiplier = event.fee_multiplier if event.complete else series.fee_multiplier
    if fee_type is None or multiplier is None:
        raise OpportunityError("fee regime incomplete")
    source = "event_override" if event.complete else "current_series"
    values = (
        "m27a-current-fee-resolution-v1",
        fee_type,
        multiplier,
        source,
        series.observation_id,
        event.metadata_hash,
    )
    return ResolvedFeeRegime(
        stable_hash(values),
        "m27a-current-fee-resolution-v1",
        fee_type,
        multiplier,
        source,
        series.observation_id,
        event.metadata_hash,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
