"""Offline point-in-time climate evidence contracts for later M28C research.

Observation time and evidence availability are separate facts. A historical observation from a
present-day NOAA/NCEI capture is replay-only unless independent source-vintage evidence shows
that the exact source record was available by the historical decision cutoff. These contracts
bind recorded provenance facts; they do not claim NOAA cryptographically attested availability.

The module performs no network I/O and grants no execution, approval, promotion, or trading
authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from services.historical_replay.archive import stable_hash

CLIMATE_SOURCE_PROVENANCE_SCHEMA_VERSION = "m28c-pre-noaa-source-provenance-v1"
CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION = "m28c-pre-noaa-source-artifact-v1"
CLIMATE_OBSERVATION_SCHEMA_VERSION = "m28c-pre-noaa-observation-v1"
CLIMATE_HISTORY_SCHEMA_VERSION = "m28c-pre-noaa-history-v1"
CLIMATE_FEATURE_EVIDENCE_SCHEMA_VERSION = "m28c-pre-noaa-feature-evidence-v1"
CLIMATE_LOOKBACK_YEARS = 10
CLIMATE_SEASONAL_WINDOW_DAYS = 15
_SUPPORTED_MEASUREMENTS = frozenset({"DAILY_MAX", "DAILY_MIN"})


class ClimateEvidenceError(ValueError):
    """Climate evidence violates a provenance, identity, or point-in-time invariant."""


class ClimateSourceVintageStatus(StrEnum):
    """Whether a source capture carries independently recorded vintage evidence."""

    INDEPENDENTLY_EVIDENCED = "INDEPENDENTLY_EVIDENCED"
    UNPROVEN = "UNPROVEN"


class ClimateEvidenceClassification(StrEnum):
    """Cutoff-relative classification for the exact observations used by one feature."""

    HISTORICAL_POINT_IN_TIME = "HISTORICAL_POINT_IN_TIME"
    REPLAY_ONLY = "REPLAY_ONLY"


class ClimateReplayReason(StrEnum):
    """Why exact used evidence cannot pass the strict historical cutoff gate."""

    UNKNOWN_SOURCE_VINTAGE = "UNKNOWN_SOURCE_VINTAGE"
    SOURCE_VINTAGE_AFTER_CUTOFF = "SOURCE_VINTAGE_AFTER_CUTOFF"


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClimateEvidenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class ClimateSourceArtifact:
    """One immutable NOAA/NCEI source capture plus independently evidenced vintage facts.

    ``artifact_id`` is content-addressed over the complete raw capture. ``provenance_id`` omits
    the full raw-content hash intentionally: an exact used observation binds its own raw record
    hash plus this provenance identity, so unrelated rows in a larger capture cannot perturb a
    later feature-subset identity.
    """

    provider: str
    source_identity: str
    station_id: str
    raw_artifact: InitVar[bytes]
    acquired_at: datetime
    parser_version: str
    source_vintage_at: datetime | None = None
    source_vintage_evidence_id: str | None = None
    source_schema_version: str = field(
        init=False, default=CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION
    )
    raw_artifact_sha256: str = field(init=False)
    vintage_status: ClimateSourceVintageStatus = field(init=False)
    provenance_id: str = field(init=False)
    artifact_id: str = field(init=False)

    def __post_init__(self, raw_artifact: bytes) -> None:
        if not self.provider.strip():
            raise ClimateEvidenceError("climate source provider is required")
        if not self.source_identity.strip():
            raise ClimateEvidenceError("climate source identity is required")
        if not self.station_id.strip():
            raise ClimateEvidenceError("climate source station is required")
        if not self.parser_version.strip():
            raise ClimateEvidenceError("climate source parser version is required")
        if not isinstance(raw_artifact, bytes) or not raw_artifact:
            raise ClimateEvidenceError("climate source raw artifact bytes are required")

        acquired_at = _aware_utc(self.acquired_at, field_name="acquired_at")
        object.__setattr__(self, "acquired_at", acquired_at)

        vintage_at = self.source_vintage_at
        vintage_evidence_id = self.source_vintage_evidence_id
        if vintage_evidence_id is not None and not vintage_evidence_id.strip():
            raise ClimateEvidenceError("source vintage evidence id cannot be blank")
        if (vintage_at is None) != (vintage_evidence_id is None):
            raise ClimateEvidenceError(
                "source vintage timestamp and evidence id must be supplied together"
            )
        if vintage_at is None:
            status = ClimateSourceVintageStatus.UNPROVEN
        else:
            vintage_at = _aware_utc(vintage_at, field_name="source_vintage_at")
            if vintage_at > acquired_at:
                raise ClimateEvidenceError("source vintage cannot be after artifact acquisition")
            object.__setattr__(self, "source_vintage_at", vintage_at)
            status = ClimateSourceVintageStatus.INDEPENDENTLY_EVIDENCED
        object.__setattr__(self, "vintage_status", status)

        raw_hash = sha256(raw_artifact).hexdigest()
        object.__setattr__(self, "raw_artifact_sha256", raw_hash)
        provenance_material = (
            CLIMATE_SOURCE_PROVENANCE_SCHEMA_VERSION,
            self.provider,
            self.source_identity,
            self.station_id,
            acquired_at.isoformat(),
            vintage_at.isoformat() if vintage_at is not None else None,
            vintage_evidence_id,
            self.parser_version,
            status.value,
        )
        provenance_id = stable_hash(provenance_material)
        object.__setattr__(self, "provenance_id", provenance_id)
        object.__setattr__(
            self,
            "artifact_id",
            stable_hash(
                (
                    CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION,
                    provenance_id,
                    raw_hash,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ClimateObservation:
    """One immutable daily climate value with exact record-level source provenance."""

    station_id: str
    measurement: str
    local_date: date
    temperature_deg_f: Decimal
    source_artifact: InitVar[ClimateSourceArtifact]
    source_record: InitVar[bytes]
    schema_version: str = field(init=False, default=CLIMATE_OBSERVATION_SCHEMA_VERSION)
    source_provenance_id: str = field(init=False)
    source_record_sha256: str = field(init=False)
    observation_id: str = field(init=False)

    def __post_init__(
        self,
        source_artifact: ClimateSourceArtifact,
        source_record: bytes,
    ) -> None:
        if not self.station_id.strip():
            raise ClimateEvidenceError("climate observation station is required")
        if self.measurement not in _SUPPORTED_MEASUREMENTS:
            raise ClimateEvidenceError("climate observation measurement is unsupported")
        if isinstance(self.local_date, datetime) or not isinstance(self.local_date, date):
            raise ClimateEvidenceError("climate observation local date is invalid")
        if (
            not isinstance(self.temperature_deg_f, Decimal)
            or not self.temperature_deg_f.is_finite()
        ):
            raise ClimateEvidenceError("climate observation temperature must be finite")
        if source_artifact.station_id != self.station_id:
            raise ClimateEvidenceError("climate observation station conflicts with source artifact")
        if not isinstance(source_record, bytes) or not source_record:
            raise ClimateEvidenceError("climate observation source record bytes are required")

        record_hash = sha256(source_record).hexdigest()
        object.__setattr__(self, "source_provenance_id", source_artifact.provenance_id)
        object.__setattr__(self, "source_record_sha256", record_hash)
        object.__setattr__(
            self,
            "observation_id",
            stable_hash(
                (
                    CLIMATE_OBSERVATION_SCHEMA_VERSION,
                    self.station_id,
                    self.measurement,
                    self.local_date.isoformat(),
                    _decimal_text(self.temperature_deg_f),
                    source_artifact.provenance_id,
                    record_hash,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ClimateHistory:
    """Canonical station history with strict semantic uniqueness and full-capture identity."""

    station_id: str
    observations: tuple[ClimateObservation, ...]
    source_artifacts: tuple[ClimateSourceArtifact, ...]
    schema_version: str = field(init=False, default=CLIMATE_HISTORY_SCHEMA_VERSION)
    history_id: str = field(init=False)

    @classmethod
    def build(
        cls,
        *,
        station_id: str,
        observations: Sequence[ClimateObservation],
        source_artifacts: Sequence[ClimateSourceArtifact],
    ) -> ClimateHistory:
        return cls(
            station_id=station_id,
            observations=tuple(observations),
            source_artifacts=tuple(source_artifacts),
        )

    def __post_init__(self) -> None:
        if not self.station_id.strip():
            raise ClimateEvidenceError("climate history station is required")
        if not self.observations:
            raise ClimateEvidenceError("climate history observations are required")
        if not self.source_artifacts:
            raise ClimateEvidenceError("climate history source artifacts are required")

        artifacts_by_provenance: dict[str, ClimateSourceArtifact] = {}
        for artifact in self.source_artifacts:
            if artifact.station_id != self.station_id:
                raise ClimateEvidenceError("climate history source station binding is invalid")
            previous = artifacts_by_provenance.get(artifact.provenance_id)
            if previous is not None:
                raise ClimateEvidenceError("climate history source provenance is duplicated")
            artifacts_by_provenance[artifact.provenance_id] = artifact

        seen_keys: set[tuple[str, str, date]] = set()
        for observation in self.observations:
            if observation.station_id != self.station_id:
                raise ClimateEvidenceError("climate history observation station binding is invalid")
            if observation.source_provenance_id not in artifacts_by_provenance:
                raise ClimateEvidenceError(
                    "climate observation source provenance is not in history"
                )
            key = (observation.station_id, observation.measurement, observation.local_date)
            if key in seen_keys:
                raise ClimateEvidenceError(
                    "duplicate climate observation key (station, measurement, local_date)"
                )
            seen_keys.add(key)

        ordered_observations = tuple(
            sorted(
                self.observations,
                key=lambda row: (row.local_date, row.measurement, row.observation_id),
            )
        )
        ordered_artifacts = tuple(sorted(self.source_artifacts, key=lambda row: row.artifact_id))
        object.__setattr__(self, "observations", ordered_observations)
        object.__setattr__(self, "source_artifacts", ordered_artifacts)
        object.__setattr__(
            self,
            "history_id",
            stable_hash(
                (
                    CLIMATE_HISTORY_SCHEMA_VERSION,
                    self.station_id,
                    tuple(row.observation_id for row in ordered_observations),
                    tuple(row.artifact_id for row in ordered_artifacts),
                )
            ),
        )

    def artifact_for_provenance(self, provenance_id: str) -> ClimateSourceArtifact:
        for artifact in self.source_artifacts:
            if artifact.provenance_id == provenance_id:
                return artifact
        raise ClimateEvidenceError("climate source provenance is not present in history")


def seasonal_distance_days(observed: date, target: date) -> int:
    """Historical M28C month/day distance on a leap-year anchor with year wrap."""

    observed_anchor = date(2000, observed.month, observed.day)
    target_anchor = date(2000, target.month, target.day)
    direct = abs((observed_anchor - target_anchor).days)
    return min(direct, 366 - direct)


@dataclass(frozen=True, slots=True)
class ClimateFeatureEvidence:
    """Exact climate rows used by one feature and their cutoff-relative availability class."""

    station_id: str
    measurement: str
    target_local_date: date
    decision_cutoff_at: datetime
    history: ClimateHistory = field(repr=False, compare=False)
    lookback_years: int = CLIMATE_LOOKBACK_YEARS
    seasonal_window_days: int = CLIMATE_SEASONAL_WINDOW_DAYS
    schema_version: str = field(init=False, default=CLIMATE_FEATURE_EVIDENCE_SCHEMA_VERSION)
    used_observations: tuple[ClimateObservation, ...] = field(init=False)
    used_source_provenance_ids: tuple[str, ...] = field(init=False)
    classification: ClimateEvidenceClassification = field(init=False)
    replay_reasons: tuple[ClimateReplayReason, ...] = field(init=False)
    feature_evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.station_id.strip():
            raise ClimateEvidenceError("climate feature station is required")
        if self.measurement not in _SUPPORTED_MEASUREMENTS:
            raise ClimateEvidenceError("climate feature measurement is unsupported")
        if isinstance(self.target_local_date, datetime) or not isinstance(
            self.target_local_date, date
        ):
            raise ClimateEvidenceError("climate feature target local date is invalid")
        cutoff = _aware_utc(self.decision_cutoff_at, field_name="decision_cutoff_at")
        object.__setattr__(self, "decision_cutoff_at", cutoff)
        if self.history.station_id != self.station_id:
            raise ClimateEvidenceError("climate feature station does not match history")
        if self.lookback_years < 1:
            raise ClimateEvidenceError("climate lookback years must be positive")
        if self.seasonal_window_days < 0:
            raise ClimateEvidenceError("climate seasonal window cannot be negative")

        start_year = self.target_local_date.year - self.lookback_years
        selected = tuple(
            row
            for row in self.history.observations
            if row.measurement == self.measurement
            and start_year <= row.local_date.year < self.target_local_date.year
            and seasonal_distance_days(row.local_date, self.target_local_date)
            <= self.seasonal_window_days
        )
        if not selected:
            raise ClimateEvidenceError("no climate observations satisfy the feature policy")
        object.__setattr__(self, "used_observations", selected)

        provenance_ids = tuple(sorted({row.source_provenance_id for row in selected}))
        object.__setattr__(self, "used_source_provenance_ids", provenance_ids)

        reasons: set[ClimateReplayReason] = set()
        artifact_material: list[tuple[str, str, str | None, str | None]] = []
        for provenance_id in provenance_ids:
            artifact = self.history.artifact_for_provenance(provenance_id)
            vintage_at = artifact.source_vintage_at
            if artifact.vintage_status is ClimateSourceVintageStatus.UNPROVEN or vintage_at is None:
                reasons.add(ClimateReplayReason.UNKNOWN_SOURCE_VINTAGE)
            elif vintage_at > cutoff:
                reasons.add(ClimateReplayReason.SOURCE_VINTAGE_AFTER_CUTOFF)
            artifact_material.append(
                (
                    artifact.provenance_id,
                    artifact.vintage_status.value,
                    vintage_at.isoformat() if vintage_at is not None else None,
                    artifact.source_vintage_evidence_id,
                )
            )

        ordered_reasons = tuple(sorted(reasons, key=lambda item: item.value))
        classification = (
            ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME
            if not ordered_reasons
            else ClimateEvidenceClassification.REPLAY_ONLY
        )
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "replay_reasons", ordered_reasons)
        object.__setattr__(
            self,
            "feature_evidence_id",
            stable_hash(
                (
                    CLIMATE_FEATURE_EVIDENCE_SCHEMA_VERSION,
                    self.station_id,
                    self.measurement,
                    self.target_local_date.isoformat(),
                    cutoff.isoformat(),
                    self.lookback_years,
                    self.seasonal_window_days,
                    tuple(row.observation_id for row in selected),
                    tuple(artifact_material),
                    classification.value,
                    tuple(reason.value for reason in ordered_reasons),
                )
            ),
        )


def build_climate_feature_evidence(
    *,
    station_id: str,
    measurement: str,
    target_local_date: date,
    decision_cutoff_at: datetime,
    history: ClimateHistory,
    lookback_years: int = CLIMATE_LOOKBACK_YEARS,
    seasonal_window_days: int = CLIMATE_SEASONAL_WINDOW_DAYS,
) -> ClimateFeatureEvidence:
    """Build deterministic used-subset evidence, retaining replay-only classifications."""

    return ClimateFeatureEvidence(
        station_id=station_id,
        measurement=measurement,
        target_local_date=target_local_date,
        decision_cutoff_at=decision_cutoff_at,
        history=history,
        lookback_years=lookback_years,
        seasonal_window_days=seasonal_window_days,
    )


def build_point_in_time_climate_feature_evidence(
    *,
    station_id: str,
    measurement: str,
    target_local_date: date,
    decision_cutoff_at: datetime,
    history: ClimateHistory,
    lookback_years: int = CLIMATE_LOOKBACK_YEARS,
    seasonal_window_days: int = CLIMATE_SEASONAL_WINDOW_DAYS,
) -> ClimateFeatureEvidence:
    """Fail closed unless every exact used source provenance is available by the cutoff."""

    evidence = build_climate_feature_evidence(
        station_id=station_id,
        measurement=measurement,
        target_local_date=target_local_date,
        decision_cutoff_at=decision_cutoff_at,
        history=history,
        lookback_years=lookback_years,
        seasonal_window_days=seasonal_window_days,
    )
    if evidence.classification is not ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME:
        reasons = ",".join(reason.value for reason in evidence.replay_reasons)
        raise ClimateEvidenceError(f"climate evidence is replay-only at decision cutoff: {reasons}")
    return evidence
