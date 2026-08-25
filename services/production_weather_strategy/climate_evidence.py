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

from services.forecasting.weather_calibration import (
    CalibrationMeasurement,
    GhcndDailySnapshotEvidence,
    GhcndObservation,
)
from services.historical_replay.archive import stable_hash

CLIMATE_SOURCE_PROVENANCE_SCHEMA_VERSION = "m28c-pre-noaa-source-provenance-v2"
CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION = "m28c-pre-noaa-source-artifact-v2"
CLIMATE_OBSERVATION_SCHEMA_VERSION = "m28c-pre-noaa-observation-v2"
CLIMATE_HISTORY_SCHEMA_VERSION = "m28c-pre-noaa-history-v2"
CLIMATE_FEATURE_EVIDENCE_SCHEMA_VERSION = "m28c-pre-noaa-feature-evidence-v2"
CLIMATE_VINTAGE_EVIDENCE_SCHEMA_VERSION = "m28c-pre-noaa-vintage-evidence-v1"
CLIMATE_RECORD_SCHEMA_VERSION = "m28c-pre-noaa-record-slot-v1"
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
    UNVALIDATED_SOURCE_RECORD = "UNVALIDATED_SOURCE_RECORD"


_CLIMATE_AUTHORITY_CAPABILITY = object()


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClimateEvidenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True, init=False)
class HistoricalClimateVintageEvidence:
    """Capability-gated vintage proof bound to one exact source capture."""

    provider: str
    source_identity: str
    station_id: str
    capture_id: str
    source_vintage_at: datetime
    evidence_id: str
    schema_version: str
    content_hash: str

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CLIMATE_AUTHORITY_CAPABILITY:
            raise ClimateEvidenceError("historical vintage evidence is not caller-constructible")
        required = ("provider", "source_identity", "station_id", "capture_id", "evidence_id")
        if any(
            not isinstance(values.get(name), str) or not str(values[name]).strip()
            for name in required
        ):
            raise ClimateEvidenceError("historical vintage evidence fields are required")
        vintage = values.get("source_vintage_at")
        if not isinstance(vintage, datetime):
            raise ClimateEvidenceError("historical vintage timestamp is invalid")
        vintage = _aware_utc(vintage, field_name="source_vintage_at")
        provider = str(values["provider"])
        source_identity = str(values["source_identity"])
        station_id = str(values["station_id"])
        capture_id = str(values["capture_id"])
        evidence_id = str(values["evidence_id"])
        content_hash = stable_hash(
            (
                CLIMATE_VINTAGE_EVIDENCE_SCHEMA_VERSION,
                provider,
                source_identity,
                station_id,
                capture_id,
                vintage.isoformat(),
                evidence_id,
            )
        )
        for name, value in (
            ("provider", provider),
            ("source_identity", source_identity),
            ("station_id", station_id),
            ("capture_id", capture_id),
            ("source_vintage_at", vintage),
            ("evidence_id", evidence_id),
            ("schema_version", CLIMATE_VINTAGE_EVIDENCE_SCHEMA_VERSION),
            ("content_hash", content_hash),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True, init=False)
class ClimateSourceArtifact:
    """Immutable capture; ordinary construction is replay-only."""

    provider: str
    source_identity: str
    station_id: str
    acquired_at: datetime
    parser_version: str
    source_schema_version: str = field(init=False, default=CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION)
    source_vintage_at: datetime | None = field(init=False, default=None)
    source_vintage_evidence_id: str | None = field(init=False, default=None)
    raw_artifact_sha256: str = field(init=False)
    vintage_status: ClimateSourceVintageStatus = field(init=False)
    provenance_id: str = field(init=False)
    artifact_id: str = field(init=False)
    _raw_artifact: bytes = field(init=False, repr=False, compare=False)
    _capture_id: str = field(init=False, repr=False)

    def __init__(
        self,
        *,
        provider: str,
        source_identity: str,
        station_id: str,
        raw_artifact: bytes,
        acquired_at: datetime,
        parser_version: str,
    ) -> None:
        self._initialize(
            provider=provider,
            source_identity=source_identity,
            station_id=station_id,
            raw_artifact=raw_artifact,
            acquired_at=acquired_at,
            parser_version=parser_version,
            vintage_evidence=None,
        )

    @classmethod
    def _from_reviewed_ghcnd(
        cls,
        *,
        provider: str,
        source_identity: str,
        station_id: str,
        raw_artifact: bytes,
        acquired_at: datetime,
        parser_version: str,
        snapshot: GhcndDailySnapshotEvidence,
        vintage_evidence: HistoricalClimateVintageEvidence | None = None,
        _capability: object | None = None,
    ) -> ClimateSourceArtifact:
        if _capability is not _CLIMATE_AUTHORITY_CAPABILITY:
            raise ClimateEvidenceError("reviewed GHCN source construction is not caller-authorized")
        if not isinstance(snapshot, GhcndDailySnapshotEvidence):
            raise ClimateEvidenceError("reviewed GHCN snapshot evidence is required")
        try:
            decoded = raw_artifact.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClimateEvidenceError("GHCN raw artifact is not UTF-8") from exc
        if snapshot.source_hash != stable_hash(decoded) or snapshot.station_id != station_id:
            raise ClimateEvidenceError("GHCN snapshot does not bind the raw source artifact")
        if _aware_utc(snapshot.acquired_at, field_name="snapshot.acquired_at") != _aware_utc(
            acquired_at, field_name="acquired_at"
        ):
            raise ClimateEvidenceError(
                "GHCN snapshot acquisition does not bind the source artifact"
            )
        instance = object.__new__(cls)
        instance._initialize(
            provider=provider,
            source_identity=source_identity,
            station_id=station_id,
            raw_artifact=raw_artifact,
            acquired_at=acquired_at,
            parser_version=parser_version,
            vintage_evidence=vintage_evidence,
        )
        return instance

    def _initialize(
        self,
        *,
        provider: str,
        source_identity: str,
        station_id: str,
        raw_artifact: bytes,
        acquired_at: datetime,
        parser_version: str,
        vintage_evidence: HistoricalClimateVintageEvidence | None,
    ) -> None:
        if not provider.strip():
            raise ClimateEvidenceError("climate source provider is required")
        if not source_identity.strip():
            raise ClimateEvidenceError("climate source identity is required")
        if not station_id.strip():
            raise ClimateEvidenceError("climate source station is required")
        if not parser_version.strip():
            raise ClimateEvidenceError("climate source parser version is required")
        if not isinstance(raw_artifact, bytes) or not raw_artifact:
            raise ClimateEvidenceError("climate source raw artifact bytes are required")
        acquired_at = _aware_utc(acquired_at, field_name="acquired_at")
        raw_hash = sha256(raw_artifact).hexdigest()
        capture_id = stable_hash(
            (
                CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION,
                provider,
                source_identity,
                station_id,
                acquired_at.isoformat(),
                parser_version,
                raw_hash,
            )
        )
        if vintage_evidence is None:
            status = ClimateSourceVintageStatus.UNPROVEN
            vintage_at = None
            vintage_id = None
        else:
            if (
                vintage_evidence.provider != provider
                or vintage_evidence.source_identity != source_identity
                or vintage_evidence.station_id != station_id
                or vintage_evidence.capture_id != capture_id
            ):
                raise ClimateEvidenceError(
                    "historical vintage evidence does not bind this artifact"
                )
            vintage_at = vintage_evidence.source_vintage_at
            if vintage_at > acquired_at:
                raise ClimateEvidenceError("source vintage cannot be after artifact acquisition")
            status = ClimateSourceVintageStatus.INDEPENDENTLY_EVIDENCED
            vintage_id = vintage_evidence.content_hash
        for name, value in (
            ("provider", provider),
            ("source_identity", source_identity),
            ("station_id", station_id),
            ("acquired_at", acquired_at),
            ("parser_version", parser_version),
            ("_raw_artifact", raw_artifact),
            ("_capture_id", capture_id),
            ("source_vintage_at", vintage_at),
            ("source_vintage_evidence_id", vintage_id),
            ("raw_artifact_sha256", raw_hash),
            ("vintage_status", status),
            ("source_schema_version", CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION),
        ):
            object.__setattr__(self, name, value)
        provenance_id = stable_hash(
            (
                CLIMATE_SOURCE_PROVENANCE_SCHEMA_VERSION,
                provider,
                source_identity,
                station_id,
                acquired_at.isoformat(),
                vintage_at.isoformat() if vintage_at is not None else None,
                vintage_id,
                parser_version,
                status.value,
            )
        )
        object.__setattr__(self, "provenance_id", provenance_id)
        object.__setattr__(
            self,
            "artifact_id",
            stable_hash((CLIMATE_SOURCE_ARTIFACT_SCHEMA_VERSION, provenance_id, raw_hash)),
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
    semantic_authority: bool = field(init=False, default=False)
    record_slot_id: str | None = field(init=False, default=None)
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

    @classmethod
    def _from_ghcnd_observation(
        cls,
        *,
        source_artifact: ClimateSourceArtifact,
        parsed: GhcndObservation,
        _capability: object | None = None,
    ) -> ClimateObservation:
        if _capability is not _CLIMATE_AUTHORITY_CAPABILITY:
            raise ClimateEvidenceError(
                "reviewed GHCN observation construction is not caller-authorized"
            )
        if not parsed.usable or parsed.observed_deg_f is None:
            raise ClimateEvidenceError(
                "only usable GHCN observations with values may be authoritative"
            )
        if parsed.measurement is CalibrationMeasurement.DAILY_MAX:
            measurement = "DAILY_MAX"
        elif parsed.measurement is CalibrationMeasurement.DAILY_MIN:
            measurement = "DAILY_MIN"
        else:
            raise ClimateEvidenceError("unsupported GHCN measurement")
        record, slot_id = _locate_ghcnd_record(source_artifact, parsed)
        observation = cls(
            station_id=parsed.station_id,
            measurement=measurement,
            local_date=parsed.local_date,
            temperature_deg_f=parsed.observed_deg_f,
            source_artifact=source_artifact,
            source_record=record,
        )
        object.__setattr__(observation, "semantic_authority", True)
        object.__setattr__(observation, "record_slot_id", slot_id)
        return observation


def _locate_ghcnd_record(
    source_artifact: ClimateSourceArtifact,
    parsed: GhcndObservation,
) -> tuple[bytes, str]:
    """Bind one parser result to its exact fixed-width monthly record and day slot."""

    candidates: list[bytes] = []
    for line in source_artifact._raw_artifact.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        if len(content) < 21 + 31 * 8:
            continue
        try:
            station = content[0:11].decode("ascii")
            year = int(content[11:15].decode("ascii"))
            month = int(content[15:17].decode("ascii"))
            element = content[17:21].decode("ascii")
        except (UnicodeDecodeError, ValueError):
            continue
        if (
            station != parsed.station_id
            or year != parsed.local_date.year
            or month != parsed.local_date.month
        ):
            continue
        expected_element = (
            "TMAX" if parsed.measurement is CalibrationMeasurement.DAILY_MAX else "TMIN"
        )
        if element != expected_element:
            continue
        candidates.append(line)
    if len(candidates) != 1:
        raise ClimateEvidenceError("GHCN observation does not bind one exact monthly record")

    line = candidates[0]
    content = line.rstrip(b"\r\n")
    offset = 21 + (parsed.local_date.day - 1) * 8
    slot = content[offset : offset + 8]
    if len(slot) != 8:
        raise ClimateEvidenceError("GHCN observation day slot is missing")
    try:
        raw_value = int(slot[0:5].decode("ascii"))
        flags = "".join(character.strip() for character in slot[5:8].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClimateEvidenceError("GHCN observation day slot is malformed") from exc
    if raw_value != parsed.raw_tenths_c or flags != parsed.mflag + parsed.qflag + parsed.sflag:
        raise ClimateEvidenceError("GHCN observation does not match its exact day slot")
    slot_id = stable_hash(
        (
            CLIMATE_RECORD_SCHEMA_VERSION,
            parsed.station_id,
            parsed.local_date.year,
            parsed.local_date.month,
            parsed.measurement.value,
            parsed.local_date.day,
            sha256(line).hexdigest(),
            slot.decode("ascii"),
        )
    )
    return line, slot_id


def build_ghcnd_climate_observations(
    *,
    source_artifact: ClimateSourceArtifact,
    snapshot: GhcndDailySnapshotEvidence,
) -> tuple[ClimateObservation, ...]:
    """Convert reviewed parser output into exact-record-bound observations."""

    if snapshot.station_id != source_artifact.station_id:
        raise ClimateEvidenceError("GHCN snapshot station conflicts with source artifact")
    return tuple(
        ClimateObservation._from_ghcnd_observation(
            source_artifact=source_artifact,
            parsed=parsed,
            _capability=_CLIMATE_AUTHORITY_CAPABILITY,
        )
        for parsed in snapshot.observations
        if parsed.usable and parsed.observed_deg_f is not None
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
        if any(not row.semantic_authority or row.record_slot_id is None for row in selected):
            reasons.add(ClimateReplayReason.UNVALIDATED_SOURCE_RECORD)
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
                    tuple((row.source_record_sha256, row.record_slot_id) for row in selected),
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
