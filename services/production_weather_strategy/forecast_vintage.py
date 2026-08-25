"""Canonical point-in-time forecast-vintage evidence for M28D-R1.

This module is pure/offline and evidence-only. Ordinary caller-created forecast artifacts are
replay-only. Strict historical authority requires an independently bound publication/vintage
proof issued through the private reviewed-evidence capability. No network, credential, account,
promotion, risk, approval, signer, execution, or order authority is created here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType

from services.historical_replay.archive import stable_hash

FORECAST_SOURCE_ARTIFACT_SCHEMA_VERSION = "m28d-r1-forecast-source-artifact-v1"
FORECAST_PUBLICATION_EVIDENCE_SCHEMA_VERSION = "m28d-r1-forecast-publication-evidence-v1"
FORECAST_VINTAGE_EVIDENCE_SCHEMA_VERSION = "m28d-r1-forecast-vintage-evidence-v1"
TARGET_LOCAL_DATE_TIME_SEMANTICS = "LOCAL_DATE_NO_ASSUMED_UTC_MIDNIGHT"
_SUPPORTED_MEASUREMENTS = frozenset({"DAILY_MAX", "DAILY_MIN"})
_FORECAST_VINTAGE_AUTHORITY_CAPABILITY = object()
_FORECAST_VINTAGE_EVIDENCE_CONSTRUCTION_CAPABILITY = object()


class ForecastVintageError(ValueError):
    """Forecast-vintage evidence violates a timing, identity, or authority invariant."""


class ForecastEvidenceClassification(StrEnum):
    REPLAY_ONLY = "REPLAY_ONLY"
    HISTORICAL_POINT_IN_TIME = "HISTORICAL_POINT_IN_TIME"


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastVintageError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_target_date(value: date) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ForecastVintageError("target local date is invalid")
    return value


def _validate_measurement(value: str) -> str:
    if value not in _SUPPORTED_MEASUREMENTS:
        raise ForecastVintageError("forecast measurement is unsupported")
    return value


@dataclass(frozen=True, slots=True)
class ForecastSourceArtifact:
    """Exact retrieved forecast artifact; construction alone never grants historical authority."""

    provider: str
    source_identity: str
    station_id: str
    measurement: str
    target_local_date: date
    forecast_reference_time: datetime
    retrieved_at: datetime
    parser_version: str
    forecast_deg_f: Decimal
    raw_artifact: bytes = field(repr=False, compare=False)
    schema_version: str = field(init=False, default=FORECAST_SOURCE_ARTIFACT_SCHEMA_VERSION)
    raw_artifact_sha256: str = field(init=False)
    artifact_id: str = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        source_identity = self.source_identity.strip()
        station_id = self.station_id.strip()
        parser_version = self.parser_version.strip()
        if not provider or not source_identity or not station_id or not parser_version:
            raise ForecastVintageError("forecast source artifact identity is incomplete")
        measurement = _validate_measurement(self.measurement)
        target = _validate_target_date(self.target_local_date)
        reference = _aware_utc(self.forecast_reference_time, field_name="forecast reference time")
        retrieved = _aware_utc(self.retrieved_at, field_name="forecast retrieval time")
        if not isinstance(self.raw_artifact, bytes) or not self.raw_artifact:
            raise ForecastVintageError("exact raw forecast artifact bytes are required")
        if not isinstance(self.forecast_deg_f, Decimal) or not self.forecast_deg_f.is_finite():
            raise ForecastVintageError("forecast value must be a finite Decimal")
        raw_hash = sha256(self.raw_artifact).hexdigest()
        digest = stable_hash(
            (
                FORECAST_SOURCE_ARTIFACT_SCHEMA_VERSION,
                provider,
                source_identity,
                station_id,
                measurement,
                target.isoformat(),
                reference.isoformat(),
                retrieved.isoformat(),
                parser_version,
                str(self.forecast_deg_f),
                raw_hash,
            )
        )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source_identity", source_identity)
        object.__setattr__(self, "station_id", station_id)
        object.__setattr__(self, "parser_version", parser_version)
        object.__setattr__(self, "forecast_reference_time", reference)
        object.__setattr__(self, "retrieved_at", retrieved)
        object.__setattr__(self, "raw_artifact_sha256", raw_hash)
        object.__setattr__(self, "artifact_id", digest)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True, init=False)
class HistoricalForecastPublicationEvidence:
    """Capability-gated proof that one exact forecast artifact existed at a historical time."""

    provider: str
    source_identity: str
    artifact_id: str
    raw_artifact_sha256: str
    station_id: str
    measurement: str
    target_local_date: date
    forecast_reference_time: datetime
    source_published_at: datetime
    evidence_id: str
    schema_version: str
    content_hash: str

    def __init__(
        self,
        *,
        artifact: ForecastSourceArtifact,
        source_published_at: datetime,
        evidence_id: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _FORECAST_VINTAGE_AUTHORITY_CAPABILITY:
            raise ForecastVintageError(
                "historical forecast publication evidence requires internal reviewed capability"
            )
        if not isinstance(artifact, ForecastSourceArtifact):
            raise ForecastVintageError("exact forecast source artifact is required")
        published = _aware_utc(source_published_at, field_name="source publication time")
        if artifact.forecast_reference_time > published:
            raise ForecastVintageError("forecast reference time is after source publication")
        if artifact.retrieved_at < published:
            raise ForecastVintageError("forecast artifact was retrieved before publication")
        proof_id = evidence_id.strip()
        if not proof_id:
            raise ForecastVintageError("publication evidence identity is required")
        digest = stable_hash(
            (
                FORECAST_PUBLICATION_EVIDENCE_SCHEMA_VERSION,
                artifact.artifact_id,
                artifact.raw_artifact_sha256,
                artifact.provider,
                artifact.source_identity,
                artifact.station_id,
                artifact.measurement,
                artifact.target_local_date.isoformat(),
                artifact.forecast_reference_time.isoformat(),
                published.isoformat(),
                proof_id,
            )
        )
        values = {
            "provider": artifact.provider,
            "source_identity": artifact.source_identity,
            "artifact_id": artifact.artifact_id,
            "raw_artifact_sha256": artifact.raw_artifact_sha256,
            "station_id": artifact.station_id,
            "measurement": artifact.measurement,
            "target_local_date": artifact.target_local_date,
            "forecast_reference_time": artifact.forecast_reference_time,
            "source_published_at": published,
            "evidence_id": proof_id,
            "schema_version": FORECAST_PUBLICATION_EVIDENCE_SCHEMA_VERSION,
            "content_hash": digest,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True, init=False)
class ForecastVintageEvidence:
    """Builder-issued cutoff-relative forecast evidence with content-addressed strictness."""

    classification: ForecastEvidenceClassification
    provider: str
    source_identity: str
    source_artifact_id: str
    raw_artifact_sha256: str
    station_id: str
    measurement: str
    target_local_date: date
    forecast_reference_time: datetime
    source_published_at: datetime | None
    decision_cutoff: datetime
    retrieved_at: datetime
    parser_version: str
    forecast_deg_f: Decimal
    publication_evidence_id: str | None
    target_local_date_time_semantics: str
    schema_version: str
    evidence_id: str
    content_hash: str

    def __init__(
        self,
        *,
        _capability: object | None = None,
        _values: Mapping[str, object] | None = None,
    ) -> None:
        if (
            _capability is not _FORECAST_VINTAGE_EVIDENCE_CONSTRUCTION_CAPABILITY
            or _values is None
        ):
            raise ForecastVintageError("forecast vintage evidence must be issued by reviewed builder")
        for name in (
            "classification",
            "provider",
            "source_identity",
            "source_artifact_id",
            "raw_artifact_sha256",
            "station_id",
            "measurement",
            "target_local_date",
            "forecast_reference_time",
            "source_published_at",
            "decision_cutoff",
            "retrieved_at",
            "parser_version",
            "forecast_deg_f",
            "publication_evidence_id",
            "target_local_date_time_semantics",
            "schema_version",
            "evidence_id",
            "content_hash",
        ):
            object.__setattr__(self, name, _values[name])


def build_forecast_vintage_evidence(
    artifact: ForecastSourceArtifact,
    *,
    decision_cutoff: datetime,
) -> ForecastVintageEvidence:
    """Build replay-only evidence from an ordinary exact source artifact."""

    if not isinstance(artifact, ForecastSourceArtifact):
        raise ForecastVintageError("exact forecast source artifact is required")
    cutoff = _aware_utc(decision_cutoff, field_name="decision cutoff")
    if artifact.forecast_reference_time > cutoff:
        raise ForecastVintageError("forecast reference time is after decision cutoff")
    return _build_vintage(
        artifact,
        cutoff=cutoff,
        classification=ForecastEvidenceClassification.REPLAY_ONLY,
        publication=None,
    )


def build_point_in_time_forecast_vintage_evidence(
    artifact: ForecastSourceArtifact,
    *,
    decision_cutoff: datetime,
    publication_evidence: HistoricalForecastPublicationEvidence,
) -> ForecastVintageEvidence:
    """Build strict evidence only from independently bound publication/source-vintage proof."""

    if not isinstance(artifact, ForecastSourceArtifact):
        raise ForecastVintageError("exact forecast source artifact is required")
    if not isinstance(publication_evidence, HistoricalForecastPublicationEvidence):
        raise ForecastVintageError("strict forecast evidence requires bound publication proof")
    cutoff = _aware_utc(decision_cutoff, field_name="decision cutoff")
    bindings = (
        (publication_evidence.provider, artifact.provider, "provider"),
        (publication_evidence.source_identity, artifact.source_identity, "source identity"),
        (publication_evidence.artifact_id, artifact.artifact_id, "source artifact"),
        (
            publication_evidence.raw_artifact_sha256,
            artifact.raw_artifact_sha256,
            "raw source artifact",
        ),
        (publication_evidence.station_id, artifact.station_id, "station"),
        (publication_evidence.measurement, artifact.measurement, "measurement"),
        (publication_evidence.target_local_date, artifact.target_local_date, "target date"),
        (
            publication_evidence.forecast_reference_time,
            artifact.forecast_reference_time,
            "forecast reference time",
        ),
    )
    for proof_value, artifact_value, name in bindings:
        if proof_value != artifact_value:
            raise ForecastVintageError(f"publication evidence {name} binding is invalid")
    if publication_evidence.source_published_at > cutoff:
        raise ForecastVintageError("forecast was published after decision cutoff")
    if artifact.forecast_reference_time > publication_evidence.source_published_at:
        raise ForecastVintageError("forecast reference time is after source publication")
    return _build_vintage(
        artifact,
        cutoff=cutoff,
        classification=ForecastEvidenceClassification.HISTORICAL_POINT_IN_TIME,
        publication=publication_evidence,
    )


def _build_vintage(
    artifact: ForecastSourceArtifact,
    *,
    cutoff: datetime,
    classification: ForecastEvidenceClassification,
    publication: HistoricalForecastPublicationEvidence | None,
) -> ForecastVintageEvidence:
    published = publication.source_published_at if publication is not None else None
    publication_hash = publication.content_hash if publication is not None else None
    digest = stable_hash(
        (
            FORECAST_VINTAGE_EVIDENCE_SCHEMA_VERSION,
            classification.value,
            artifact.content_hash,
            publication_hash,
            cutoff.isoformat(),
            TARGET_LOCAL_DATE_TIME_SEMANTICS,
        )
    )
    return ForecastVintageEvidence(
        _capability=_FORECAST_VINTAGE_EVIDENCE_CONSTRUCTION_CAPABILITY,
        _values=MappingProxyType(
            {
                "classification": classification,
                "provider": artifact.provider,
                "source_identity": artifact.source_identity,
                "source_artifact_id": artifact.artifact_id,
                "raw_artifact_sha256": artifact.raw_artifact_sha256,
                "station_id": artifact.station_id,
                "measurement": artifact.measurement,
                "target_local_date": artifact.target_local_date,
                "forecast_reference_time": artifact.forecast_reference_time,
                "source_published_at": published,
                "decision_cutoff": cutoff,
                "retrieved_at": artifact.retrieved_at,
                "parser_version": artifact.parser_version,
                "forecast_deg_f": artifact.forecast_deg_f,
                "publication_evidence_id": publication_hash,
                "target_local_date_time_semantics": TARGET_LOCAL_DATE_TIME_SEMANTICS,
                "schema_version": FORECAST_VINTAGE_EVIDENCE_SCHEMA_VERSION,
                "evidence_id": digest,
                "content_hash": digest,
            }
        ),
    )


def _issue_historical_forecast_publication_evidence(
    *,
    artifact: ForecastSourceArtifact,
    source_published_at: datetime,
    evidence_id: str,
    _capability: object | None = None,
) -> HistoricalForecastPublicationEvidence:
    """Private synthetic/review seam; not a public or live evidence issuer."""

    return HistoricalForecastPublicationEvidence(
        artifact=artifact,
        source_published_at=source_published_at,
        evidence_id=evidence_id,
        _capability=_capability,
    )
