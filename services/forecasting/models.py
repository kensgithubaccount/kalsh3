"""Immutable forecasts, feature snapshots, market references, and abstentions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash

from .domain import ForecastError, ModelFamily


class ForecastKind(StrEnum):
    MARKET_REFERENCE = "MARKET_REFERENCE"
    INDEPENDENT_FUNDAMENTAL = "INDEPENDENT_FUNDAMENTAL"
    MARKET_ANCHORED_ENSEMBLE = "MARKET_ANCHORED_ENSEMBLE"
    CROSS_MARKET_REFERENCE = "CROSS_MARKET_REFERENCE"


class FeatureProvenance(StrEnum):
    FUNDAMENTAL_STRUCTURED = "FUNDAMENTAL_STRUCTURED"
    FUNDAMENTAL_SEMANTIC = "FUNDAMENTAL_SEMANTIC"
    KALSHI_MARKET_DERIVED = "KALSHI_MARKET_DERIVED"
    EXTERNAL_MARKET_DERIVED = "EXTERNAL_MARKET_DERIVED"
    HISTORICAL_OUTCOME = "HISTORICAL_OUTCOME"
    CALENDAR_TIME = "CALENDAR_TIME"
    MODEL_DERIVED = "MODEL_DERIVED"


class AbstentionReason(StrEnum):
    UNSUPPORTED_FAMILY = "UNSUPPORTED_FAMILY"
    AMBIGUOUS_CONTRACT = "AMBIGUOUS_CONTRACT"
    INVALID_RULES = "INVALID_RULES"
    INSUFFICIENT_TRAINING_DATA = "INSUFFICIENT_TRAINING_DATA"
    STALE_MARKET = "STALE_MARKET"
    STALE_SOURCE = "STALE_SOURCE"
    CRITICAL_FEATURE_MISSING = "CRITICAL_FEATURE_MISSING"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    CALIBRATION_UNAVAILABLE = "CALIBRATION_UNAVAILABLE"
    REPLAY_FIDELITY_INSUFFICIENT = "REPLAY_FIDELITY_INSUFFICIENT"
    DISTRIBUTION_INVALID = "DISTRIBUTION_INVALID"
    MODEL_HEALTH_FAILURE = "MODEL_HEALTH_FAILURE"


@dataclass(frozen=True, slots=True)
class FeatureValue:
    name: str
    value: Decimal | str | bool | None
    raw_lineage: tuple[str, ...]
    available_at: datetime | None
    provenance: FeatureProvenance
    missing: bool
    transformation: str | None


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    snapshot_id: str
    forecast_at: datetime
    schema_version: str
    features: tuple[FeatureValue, ...]
    content_hash: str

    @classmethod
    def build(
        cls, forecast_at: datetime, schema_version: str, features: tuple[FeatureValue, ...]
    ) -> FeatureSnapshot:
        for feature in features:
            if feature.available_at is not None and feature.available_at > forecast_at:
                raise ForecastError("future feature cannot enter snapshot")
            if feature.missing and feature.value is not None:
                raise ForecastError("missing feature cannot carry an imputed value")
        digest = stable_hash((forecast_at, schema_version, features))
        return cls(digest, forecast_at, schema_version, features, digest)

    def validate_for(self, kind: ForecastKind) -> None:
        prohibited = {
            FeatureProvenance.KALSHI_MARKET_DERIVED,
            FeatureProvenance.EXTERNAL_MARKET_DERIVED,
        }
        if kind == ForecastKind.INDEPENDENT_FUNDAMENTAL and any(
            feature.provenance in prohibited for feature in self.features
        ):
            raise ForecastError("independent forecast contains market-derived feature")


@dataclass(frozen=True, slots=True)
class MarketReference:
    snapshot_time: datetime
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    reference_probability: Decimal
    spread: Decimal
    construction_method: str
    age_ms: int

    @classmethod
    def midpoint(
        cls,
        *,
        forecast_at: datetime,
        snapshot_time: datetime,
        yes_bid: Decimal,
        yes_ask: Decimal,
        no_bid: Decimal,
        no_ask: Decimal,
        max_age_ms: int,
        max_spread: Decimal,
    ) -> MarketReference:
        values = yes_bid, yes_ask, no_bid, no_ask
        if any(not value.is_finite() or value < 0 or value > 1 for value in values):
            raise ForecastError("invalid market price")
        spread = yes_ask - yes_bid
        age = int((forecast_at - snapshot_time).total_seconds() * 1000)
        if yes_bid > yes_ask or no_bid > no_ask or age < 0 or age > max_age_ms:
            raise ForecastError("crossed, future, or stale market reference")
        if spread > max_spread:
            raise ForecastError("market spread too wide for reference")
        return cls(
            snapshot_time,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            (yes_bid + yes_ask) / Decimal(2),
            spread,
            "YES_BID_ASK_MIDPOINT",
            age,
        )


@dataclass(frozen=True, slots=True)
class Forecast:
    forecast_id: str
    market_ticker: str
    event_id: str
    series_id: str
    market_family: ModelFamily
    rules_version: str
    rules_hash: str
    forecast_kind: ForecastKind
    issued_at: datetime
    replay_time: datetime | None
    target_resolution_time: datetime
    horizon_seconds: int
    model_id: str
    model_version: str
    feature_schema_version: str
    model_artifact_hash: str
    calibration_id: str
    calibration_version: str
    feature_snapshot_id: str
    evidence_bundle_id: str | None
    source_snapshot_id: str
    raw_probability: Decimal | None
    calibrated_probability: Decimal | None
    lower_probability: Decimal | None
    upper_probability: Decimal | None
    interval_level: Decimal | None
    uncertainty_method: str | None
    uncertainty_quality: str
    market_reference: MarketReference | None
    abstention_reason: AbstentionReason | None
    research_status: str
    production_influence: Decimal
    code_git_sha: str
    created_at: datetime
    content_hash: str

    @classmethod
    def freeze(cls, **values: object) -> Forecast:
        probability = values.get("calibrated_probability")
        abstention = values.get("abstention_reason")
        if abstention is None:
            if not isinstance(probability, Decimal) or not probability.is_finite():
                raise ForecastError("non-abstaining forecast requires finite probability")
            lower, upper = values.get("lower_probability"), values.get("upper_probability")
            if (
                not isinstance(lower, Decimal)
                or not isinstance(upper, Decimal)
                or not (Decimal(0) <= lower <= probability <= upper <= Decimal(1))
            ):
                raise ForecastError("invalid probability interval")
        elif probability is not None:
            raise ForecastError("abstention is not a 50% forecast")
        if values.get("production_influence") != Decimal(0):
            raise ForecastError("M8 production influence must be zero")
        material = tuple(
            sorted(
                (key, value)
                for key, value in values.items()
                if key not in {"forecast_id", "content_hash"}
            )
        )
        digest = stable_hash(material)
        return cls(forecast_id=digest, content_hash=digest, **values)  # type: ignore[arg-type]
