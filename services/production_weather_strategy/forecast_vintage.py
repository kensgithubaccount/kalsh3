"""Leakage-safe historical forecast-vintage evidence for M28 production research.

This module is pure. It does not acquire forecasts, access credentials, call Kalshi,
mutate production state, authorize risk, approve execution, or send orders.

The purpose of these contracts is to make historical feature timing explicit. A model may
only consume a weather value when the source publication timestamp is at-or-before the
predeclared decision cutoff. Retrieval may happen later; publication may not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from services.historical_replay.archive import stable_hash


class ForecastVintageError(ValueError):
    """Historical forecast evidence violates an M28 timing or identity invariant."""


_SUPPORTED_MEASUREMENTS = frozenset({"DAILY_MAX", "DAILY_MIN"})


@dataclass(frozen=True, slots=True)
class ForecastVintagePoint:
    """One source-vintaged forecast value that genuinely existed before a decision cutoff."""

    evidence_id: str
    source_name: str
    source_family: str
    station_id: str
    measurement: str
    target_local_date: date
    forecast_reference_time: datetime
    source_published_at: datetime
    decision_cutoff: datetime
    retrieved_at: datetime
    horizon_seconds: int
    forecast_deg_f: Decimal
    source_hash: str
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        source_name: str,
        source_family: str,
        station_id: str,
        measurement: str,
        target_local_date: date,
        forecast_reference_time: datetime,
        source_published_at: datetime,
        decision_cutoff: datetime,
        retrieved_at: datetime,
        forecast_deg_f: Decimal,
        source_hash: str,
    ) -> ForecastVintagePoint:
        for value, name in (
            (source_name, "source name"),
            (source_family, "source family"),
            (station_id, "station id"),
            (source_hash, "source hash"),
        ):
            if not value.strip():
                raise ForecastVintageError(f"{name} is required")
        if measurement not in _SUPPORTED_MEASUREMENTS:
            raise ForecastVintageError("forecast measurement is unsupported")
        reference = _utc(forecast_reference_time, "forecast reference time")
        published = _utc(source_published_at, "source publication time")
        cutoff = _utc(decision_cutoff, "decision cutoff")
        retrieved = _utc(retrieved_at, "retrieval time")
        if reference > published:
            raise ForecastVintageError("forecast reference time is after source publication")
        if published > cutoff:
            raise ForecastVintageError("forecast was published after the decision cutoff")
        if retrieved < published:
            raise ForecastVintageError("historical evidence was retrieved before publication")
        if not forecast_deg_f.is_finite():
            raise ForecastVintageError("forecast temperature is non-finite")
        target_start = datetime.combine(target_local_date, datetime.min.time(), tzinfo=UTC)
        horizon_seconds = int((target_start - reference).total_seconds())
        # A same-date forecast can legitimately have a negative UTC-midnight delta for a
        # western local timezone. Preserve the source-derived timing rather than fabricating
        # a local-midnight conversion, but reject obviously unrelated forecast vintages.
        if horizon_seconds < -24 * 3600 or horizon_seconds > 14 * 24 * 3600:
            raise ForecastVintageError("forecast horizon is outside the reviewed M28 bound")
        material = (
            "m28d-forecast-vintage-v1",
            source_name,
            source_family,
            station_id,
            measurement,
            target_local_date.isoformat(),
            reference.isoformat(),
            published.isoformat(),
            cutoff.isoformat(),
            retrieved.isoformat(),
            horizon_seconds,
            str(forecast_deg_f),
            source_hash,
        )
        digest = stable_hash(material)
        return cls(
            evidence_id=digest,
            source_name=source_name,
            source_family=source_family,
            station_id=station_id,
            measurement=measurement,
            target_local_date=target_local_date,
            forecast_reference_time=reference,
            source_published_at=published,
            decision_cutoff=cutoff,
            retrieved_at=retrieved,
            horizon_seconds=horizon_seconds,
            forecast_deg_f=forecast_deg_f,
            source_hash=source_hash,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ForecastRevisionFeatures:
    """Pre-cutoff forecast level/revision features derived from two immutable vintages."""

    feature_id: str
    station_id: str
    measurement: str
    target_local_date: date
    decision_cutoff: datetime
    latest_evidence_id: str
    prior_evidence_id: str
    latest_forecast_deg_f: Decimal
    prior_forecast_deg_f: Decimal
    revision_deg_f: Decimal
    reference_time_delta_seconds: int
    content_hash: str

    @classmethod
    def build(
        cls,
        latest: ForecastVintagePoint,
        prior: ForecastVintagePoint,
    ) -> ForecastRevisionFeatures:
        identity = (
            latest.station_id,
            latest.measurement,
            latest.target_local_date,
            latest.decision_cutoff,
        )
        prior_identity = (
            prior.station_id,
            prior.measurement,
            prior.target_local_date,
            prior.decision_cutoff,
        )
        if identity != prior_identity:
            raise ForecastVintageError("forecast revisions do not describe the same decision")
        if prior.forecast_reference_time >= latest.forecast_reference_time:
            raise ForecastVintageError("prior forecast is not older than latest forecast")
        if prior.source_published_at > latest.source_published_at:
            raise ForecastVintageError("prior forecast publication is after latest publication")
        delta = int((latest.forecast_reference_time - prior.forecast_reference_time).total_seconds())
        revision = latest.forecast_deg_f - prior.forecast_deg_f
        material = (
            "m28d-forecast-revision-v1",
            latest.evidence_id,
            prior.evidence_id,
            str(revision),
            delta,
        )
        digest = stable_hash(material)
        return cls(
            feature_id=digest,
            station_id=latest.station_id,
            measurement=latest.measurement,
            target_local_date=latest.target_local_date,
            decision_cutoff=latest.decision_cutoff,
            latest_evidence_id=latest.evidence_id,
            prior_evidence_id=prior.evidence_id,
            latest_forecast_deg_f=latest.forecast_deg_f,
            prior_forecast_deg_f=prior.forecast_deg_f,
            revision_deg_f=revision,
            reference_time_delta_seconds=delta,
            content_hash=digest,
        )


def choose_latest_pre_cutoff_vintage(
    points: tuple[ForecastVintagePoint, ...],
    *,
    station_id: str,
    measurement: str,
    target_local_date: date,
    decision_cutoff: datetime,
) -> ForecastVintagePoint:
    """Select the newest admissible source vintage without looking beyond the cutoff."""

    cutoff = _utc(decision_cutoff, "decision cutoff")
    matches = [
        point
        for point in points
        if point.station_id == station_id
        and point.measurement == measurement
        and point.target_local_date == target_local_date
        and point.decision_cutoff == cutoff
        and point.source_published_at <= cutoff
    ]
    if not matches:
        raise ForecastVintageError("no admissible pre-cutoff forecast vintage")
    return max(
        matches,
        key=lambda point: (point.forecast_reference_time, point.source_published_at, point.evidence_id),
    )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastVintageError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
