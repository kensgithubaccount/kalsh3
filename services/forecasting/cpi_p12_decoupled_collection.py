"""Pre-freeze CPI P12 collection-isolation runtime.

This module is operational reliability infrastructure only. It deliberately does not
freeze P12, select a Kalshi event, inspect a Reuters target forecast, score an event,
or authorize production/trading.

It repairs the failure mode exposed by CPI-E1-P11: a BLS schedule transport failure
must not prevent independent Kalshi or Reuters pre-release collection.

Design:
- BLS schedule authority is verified from a previously sealed local receipt, never
  fetched live by this runtime.
- Kalshi and Reuters stages are independent callbacks.
- a stage failure writes only that stage's operational receipt and does not terminalize
  the root or suppress later stages.
- the preclose gate is read-only with respect to collectors: it inspects stage receipts
  and never invokes acquisition itself.
- every orchestration receipt is create-once and explicitly non-scientific.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.historical_replay.cpi_price_evidence import strict_json_loads

POLICY_VERSION = "cpi-p12-decoupled-collection-runtime-v1"
STAGE_RECEIPT_SCHEMA = "cpi-p12-decoupled-stage-receipt-v1"
GATE_RECEIPT_SCHEMA = "cpi-p12-decoupled-preclose-gate-v1"
BLS_SCHEDULE_RECEIPT_TYPE = "CPI-P12-MANUAL-BLS-CALENDAR-RECEIPT-v1"
BLS_SCHEDULE_LOCATOR = "https://www.bls.gov/schedule/news_release/cpi.htm"
ZERO_TEXT = "0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class P12IsolationError(RuntimeError):
    """Base class for P12 pre-freeze collection-isolation errors."""


class P12IsolationAuthorityError(P12IsolationError):
    """A caller, artifact, or stage violated the reviewed isolation contract."""


class P12ExpectedStageFailure(P12IsolationError):
    """Expected transport/acquisition failure that should remain stage-local."""


class P12CollectorStage(StrEnum):
    BLS_SCHEDULE_AUTHORITY = "bls_schedule_authority"
    KALSHI_MARKET_CAPTURE = "kalshi_market_capture"
    REUTERS_ACQUISITION = "reuters_acquisition"


class P12StageStatus(StrEnum):
    SUCCEEDED = "PASS"
    FAILURE = "FAILURE"


class P12GateDecision(StrEnum):
    READY = "READY_ALL_REQUIRED_STAGES_PRESENT"
    INCOMPLETE = "INCOMPLETE_REQUIRED_STAGE"


@dataclass(frozen=True, slots=True)
class P12IsolationPaths:
    root: Path

    @property
    def orchestration(self) -> Path:
        return self.root / "orchestration"

    def stage_receipt(self, stage: P12CollectorStage) -> Path:
        if type(stage) is not P12CollectorStage:
            raise P12IsolationAuthorityError("stage must have exact enum identity")
        return self.orchestration / "stages" / f"{stage.value}.json"

    @property
    def gate(self) -> Path:
        return self.orchestration / "preclose_gate.json"


@dataclass(frozen=True, slots=True)
class SealedBLSScheduleExpectation:
    reference_month: str
    release_at_utc: datetime
    source_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if type(self.reference_month) is not str or re.fullmatch(
            r"\d{4}-\d{2}", self.reference_month
        ) is None:
            raise P12IsolationAuthorityError("reference_month must be exact YYYY-MM")
        month = int(self.reference_month[5:])
        if month < 1 or month > 12:
            raise P12IsolationAuthorityError("reference_month must be exact YYYY-MM")
        if (
            type(self.release_at_utc) is not datetime
            or self.release_at_utc.tzinfo is None
            or self.release_at_utc.utcoffset() is None
        ):
            raise P12IsolationAuthorityError("release_at_utc must be timezone-aware")
        object.__setattr__(self, "release_at_utc", self.release_at_utc.astimezone(UTC))
        _require_sha256(self.source_sha256, "source_sha256")
        _require_sha256(self.receipt_sha256, "receipt_sha256")


@dataclass(frozen=True, slots=True)
class StageArtifact:
    path: Path
    sha256: str
    byte_count: int

    def as_dict(self, root: Path) -> dict[str, object]:
        try:
            relative = self.path.resolve().relative_to(root.resolve())
            path_text = relative.as_posix()
        except ValueError:
            path_text = str(self.path.resolve())
        return {
            "path": path_text,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class StageCollection:
    artifacts: tuple[Path, ...]


StageCollector = Callable[[], StageCollection]
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise P12IsolationAuthorityError("timestamp must be an aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: object, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise P12IsolationAuthorityError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise P12IsolationAuthorityError(f"create-once receipt already exists: {path}") from exc
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, indent=2) + "\n").encode()


def _write_json_exclusive(path: Path, value: Mapping[str, object]) -> None:
    _write_bytes_exclusive(path, _json_bytes(value))


def _read_json(path: Path) -> dict[str, Any]:
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise P12IsolationAuthorityError(f"{path} is not a JSON object")
    return value


def _parse_utc(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise P12IsolationAuthorityError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P12IsolationAuthorityError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise P12IsolationAuthorityError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _artifact(path: Path) -> StageArtifact:
    if not path.is_file():
        raise P12IsolationAuthorityError(f"stage artifact is not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise P12IsolationAuthorityError(f"stage artifact is empty: {path}")
    return StageArtifact(
        path=path,
        sha256=_sha256_file(path),
        byte_count=size,
    )


def verify_presealed_bls_schedule_receipt(
    receipt_path: Path,
    expectation: SealedBLSScheduleExpectation,
) -> StageCollection:
    """Verify a pre-event BLS schedule receipt without any network access.

    The expectation remains caller-supplied pre-freeze configuration and therefore grants
    no scientific authority. A later frozen P12 protocol must bind these exact values.
    """
    if type(receipt_path) is not Path:
        raise P12IsolationAuthorityError("receipt_path must be pathlib.Path")
    if type(expectation) is not SealedBLSScheduleExpectation:
        raise P12IsolationAuthorityError("expectation must have exact reviewed type")
    if not receipt_path.is_file():
        raise P12ExpectedStageFailure("sealed BLS schedule receipt is unavailable")

    receipt_raw = receipt_path.read_bytes()
    if _sha256_bytes(receipt_raw) != expectation.receipt_sha256:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt SHA-256 mismatch")

    payload = strict_json_loads(receipt_raw)
    if not isinstance(payload, dict):
        raise P12ExpectedStageFailure("sealed BLS schedule receipt is not a JSON object")
    if payload.get("record_type") != BLS_SCHEDULE_RECEIPT_TYPE:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt type mismatch")
    if payload.get("source_locator") != BLS_SCHEDULE_LOCATOR:
        raise P12ExpectedStageFailure("sealed BLS schedule source locator mismatch")
    if payload.get("reference_month") != expectation.reference_month:
        raise P12ExpectedStageFailure("sealed BLS schedule reference month mismatch")
    if _parse_utc(payload.get("release_at_utc"), "release_at_utc") != expectation.release_at_utc:
        raise P12ExpectedStageFailure("sealed BLS schedule release instant mismatch")
    if payload.get("raw_body_sha256") != expectation.source_sha256:
        raise P12ExpectedStageFailure("sealed BLS schedule source SHA-256 mismatch")
    if payload.get("research_only") is not True:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt lost research-only boundary")
    if payload.get("production_influence") != ZERO_TEXT:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt gained production influence")
    if payload.get("scientific_evidence_authorized") is not False:
        raise P12ExpectedStageFailure(
            "sealed BLS schedule receipt unexpectedly claims scientific authority"
        )
    if payload.get("p12_frozen") is not False:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt unexpectedly claims P12 freeze")

    encoded = payload.get("raw_body_b64")
    if type(encoded) is not str:
        raise P12ExpectedStageFailure("sealed BLS schedule receipt lacks exact source bytes")
    try:
        source_raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise P12ExpectedStageFailure(
            "sealed BLS schedule source bytes are invalid base64"
        ) from exc
    if not source_raw or _sha256_bytes(source_raw) != expectation.source_sha256:
        raise P12ExpectedStageFailure("embedded BLS schedule bytes failed SHA-256 validation")
    byte_count = payload.get("byte_count")
    if type(byte_count) is not int or isinstance(byte_count, bool) or byte_count != len(source_raw):
        raise P12ExpectedStageFailure("embedded BLS schedule byte count mismatch")

    return StageCollection(artifacts=(receipt_path,))


def _normalize_collection(
    root: Path,
    collection: StageCollection,
) -> tuple[StageArtifact, ...]:
    if type(collection) is not StageCollection:
        raise P12IsolationAuthorityError("collector must return exact StageCollection")
    if type(collection.artifacts) is not tuple:
        raise P12IsolationAuthorityError("collector artifacts must be an exact tuple")
    if not collection.artifacts:
        raise P12IsolationAuthorityError("successful collector must preserve at least one artifact")

    output: list[StageArtifact] = []
    seen: set[Path] = set()
    for item in collection.artifacts:
        if type(item) is not Path:
            raise P12IsolationAuthorityError("collector artifact path must be pathlib.Path")
        resolved = item.resolve()
        if resolved in seen:
            raise P12IsolationAuthorityError("collector returned duplicate artifact path")
        seen.add(resolved)
        output.append(_artifact(item))
    return tuple(output)


def _stage_receipt_payload(
    *,
    stage: P12CollectorStage,
    status: P12StageStatus,
    started_at: datetime,
    completed_at: datetime,
    artifacts: Iterable[StageArtifact],
    root: Path,
    failure_type: str | None,
    failure_reason: str | None,
) -> dict[str, object]:
    return {
        "record_type": STAGE_RECEIPT_SCHEMA,
        "policy_version": POLICY_VERSION,
        "stage": stage.value,
        "status": status.value,
        "started_at_utc": _iso(started_at),
        "completed_at_utc": _iso(completed_at),
        "artifacts": [artifact.as_dict(root) for artifact in artifacts],
        "failure_type": failure_type,
        "failure_reason": failure_reason,
        "stage_local_failure_only": status is P12StageStatus.FAILURE,
        "scientific_evidence_authorized": False,
        "p12_frozen": False,
        "production_influence": ZERO_TEXT,
    }


def run_independent_stage(
    paths: P12IsolationPaths,
    stage: P12CollectorStage,
    collector: StageCollector,
    *,
    clock: Clock = _now,
) -> dict[str, Any]:
    """Run exactly one stage without inspecting or requiring any other stage.

    Expected operational/acquisition failures are converted into a create-once, stage-local
    FAILURE receipt. Programming/authority errors are not swallowed.
    """
    if type(paths) is not P12IsolationPaths:
        raise P12IsolationAuthorityError("paths must have exact reviewed type")
    if type(stage) is not P12CollectorStage:
        raise P12IsolationAuthorityError("stage must have exact enum identity")
    if not callable(collector):
        raise P12IsolationAuthorityError("collector must be callable")

    receipt_path = paths.stage_receipt(stage)
    if receipt_path.exists():
        return validate_stage_receipt(paths, stage)

    started = clock()
    try:
        collection = collector()
        artifacts = _normalize_collection(paths.root, collection)
    except P12ExpectedStageFailure as exc:
        completed = clock()
        receipt = _stage_receipt_payload(
            stage=stage,
            status=P12StageStatus.FAILURE,
            started_at=started,
            completed_at=completed,
            artifacts=(),
            root=paths.root,
            failure_type=type(exc).__name__,
            failure_reason=str(exc),
        )
        _write_json_exclusive(receipt_path, receipt)
        return validate_stage_receipt(paths, stage)

    completed = clock()
    receipt = _stage_receipt_payload(
        stage=stage,
        status=P12StageStatus.SUCCEEDED,
        started_at=started,
        completed_at=completed,
        artifacts=artifacts,
        root=paths.root,
        failure_type=None,
        failure_reason=None,
    )
    _write_json_exclusive(receipt_path, receipt)
    return validate_stage_receipt(paths, stage)


def validate_stage_receipt(
    paths: P12IsolationPaths,
    stage: P12CollectorStage,
) -> dict[str, Any]:
    """Revalidate one operational stage receipt without running acquisition."""
    receipt_path = paths.stage_receipt(stage)
    if not receipt_path.is_file():
        raise P12IsolationAuthorityError(f"stage receipt is missing: {stage.value}")
    receipt = _read_json(receipt_path)

    if receipt.get("record_type") != STAGE_RECEIPT_SCHEMA:
        raise P12IsolationAuthorityError("stage receipt record type mismatch")
    if receipt.get("policy_version") != POLICY_VERSION:
        raise P12IsolationAuthorityError("stage receipt policy version mismatch")
    if receipt.get("stage") != stage.value:
        raise P12IsolationAuthorityError("stage receipt identity mismatch")
    status_raw = receipt.get("status")
    try:
        status = P12StageStatus(status_raw)
    except (TypeError, ValueError) as exc:
        raise P12IsolationAuthorityError("stage receipt status is invalid") from exc
    _parse_utc(receipt.get("started_at_utc"), "started_at_utc")
    _parse_utc(receipt.get("completed_at_utc"), "completed_at_utc")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list):
        raise P12IsolationAuthorityError("stage receipt artifacts are malformed")
    if status is P12StageStatus.SUCCEEDED and not artifacts:
        raise P12IsolationAuthorityError("PASS stage receipt must preserve artifacts")
    if status is P12StageStatus.FAILURE and artifacts:
        raise P12IsolationAuthorityError("FAILURE orchestration receipt must not claim artifacts")

    for item in artifacts:
        if not isinstance(item, dict):
            raise P12IsolationAuthorityError("stage artifact reference is malformed")
        _require_sha256(item.get("sha256"), "artifact.sha256")
        size = item.get("byte_count")
        if type(size) is not int or isinstance(size, bool) or size <= 0:
            raise P12IsolationAuthorityError("stage artifact byte count is invalid")

    failure_type = receipt.get("failure_type")
    failure_reason = receipt.get("failure_reason")
    if status is P12StageStatus.SUCCEEDED:
        if failure_type is not None or failure_reason is not None:
            raise P12IsolationAuthorityError("PASS receipt cannot contain failure metadata")
        if receipt.get("stage_local_failure_only") is not False:
            raise P12IsolationAuthorityError("PASS receipt failure scope is malformed")
    else:
        if type(failure_type) is not str or not failure_type:
            raise P12IsolationAuthorityError("FAILURE receipt lacks failure type")
        if type(failure_reason) is not str or not failure_reason:
            raise P12IsolationAuthorityError("FAILURE receipt lacks failure reason")
        if receipt.get("stage_local_failure_only") is not True:
            raise P12IsolationAuthorityError("FAILURE receipt is not stage-local")

    if receipt.get("scientific_evidence_authorized") is not False:
        raise P12IsolationAuthorityError("orchestration receipt claims scientific authority")
    if receipt.get("p12_frozen") is not False:
        raise P12IsolationAuthorityError("orchestration receipt claims P12 freeze")
    if receipt.get("production_influence") != ZERO_TEXT:
        raise P12IsolationAuthorityError("orchestration receipt gained production influence")
    return receipt


def run_all_independent_stages(
    paths: P12IsolationPaths,
    collectors: Mapping[P12CollectorStage, StageCollector],
    *,
    clock: Clock = _now,
) -> dict[str, dict[str, Any]]:
    """Run every required stage even if another stage records an expected failure."""
    if type(paths) is not P12IsolationPaths:
        raise P12IsolationAuthorityError("paths must have exact reviewed type")
    if not isinstance(collectors, Mapping):
        raise P12IsolationAuthorityError("collectors must be a mapping")
    required = tuple(P12CollectorStage)
    if set(collectors) != set(required):
        raise P12IsolationAuthorityError("collector mapping must cover exactly all required stages")

    outcomes: dict[str, dict[str, Any]] = {}
    for stage in required:
        outcomes[stage.value] = run_independent_stage(
            paths,
            stage,
            collectors[stage],
            clock=clock,
        )
    return outcomes


def evaluate_preclose_completeness(
    paths: P12IsolationPaths,
    *,
    clock: Clock = _now,
) -> dict[str, Any]:
    """Create the preclose operational gate from receipts only.

    This function never calls a collector. Missing or failed stages yield INCOMPLETE; existing
    acquisition artifacts remain untouched for postmortem/review.
    """
    if type(paths) is not P12IsolationPaths:
        raise P12IsolationAuthorityError("paths must have exact reviewed type")
    if paths.gate.exists():
        return validate_preclose_gate(paths)

    stage_states: dict[str, str] = {}
    all_pass = True
    for stage in P12CollectorStage:
        receipt_path = paths.stage_receipt(stage)
        if not receipt_path.is_file():
            stage_states[stage.value] = "MISSING"
            all_pass = False
            continue
        receipt = validate_stage_receipt(paths, stage)
        status = receipt["status"]
        stage_states[stage.value] = status
        if status != P12StageStatus.SUCCEEDED.value:
            all_pass = False

    gate = {
        "record_type": GATE_RECEIPT_SCHEMA,
        "policy_version": POLICY_VERSION,
        "decision": (
            P12GateDecision.READY.value if all_pass else P12GateDecision.INCOMPLETE.value
        ),
        "evaluated_at_utc": _iso(clock()),
        "required_stage_states": stage_states,
        "collectors_invoked_by_gate": False,
        "missing_or_failed_stages_preserve_other_stage_artifacts": True,
        "scientific_evidence_authorized": False,
        "p12_frozen": False,
        "production_influence": ZERO_TEXT,
    }
    _write_json_exclusive(paths.gate, gate)
    return validate_preclose_gate(paths)


def validate_preclose_gate(paths: P12IsolationPaths) -> dict[str, Any]:
    if not paths.gate.is_file():
        raise P12IsolationAuthorityError("preclose gate receipt is missing")
    gate = _read_json(paths.gate)
    if gate.get("record_type") != GATE_RECEIPT_SCHEMA:
        raise P12IsolationAuthorityError("preclose gate record type mismatch")
    if gate.get("policy_version") != POLICY_VERSION:
        raise P12IsolationAuthorityError("preclose gate policy version mismatch")
    try:
        decision = P12GateDecision(gate.get("decision"))
    except (TypeError, ValueError) as exc:
        raise P12IsolationAuthorityError("preclose gate decision is invalid") from exc
    _parse_utc(gate.get("evaluated_at_utc"), "evaluated_at_utc")

    states = gate.get("required_stage_states")
    if not isinstance(states, dict) or set(states) != {stage.value for stage in P12CollectorStage}:
        raise P12IsolationAuthorityError("preclose gate stage coverage is invalid")
    allowed = {"MISSING", P12StageStatus.SUCCEEDED.value, P12StageStatus.FAILURE.value}
    if any(type(value) is not str or value not in allowed for value in states.values()):
        raise P12IsolationAuthorityError("preclose gate stage state is invalid")

    all_pass = all(value == P12StageStatus.SUCCEEDED.value for value in states.values())
    if (decision is P12GateDecision.READY) != all_pass:
        raise P12IsolationAuthorityError("preclose gate decision conflicts with stage states")
    if gate.get("collectors_invoked_by_gate") is not False:
        raise P12IsolationAuthorityError("preclose gate may not invoke collectors")
    if gate.get("missing_or_failed_stages_preserve_other_stage_artifacts") is not True:
        raise P12IsolationAuthorityError("preclose gate artifact-preservation boundary changed")
    if gate.get("scientific_evidence_authorized") is not False:
        raise P12IsolationAuthorityError("preclose gate claims scientific authority")
    if gate.get("p12_frozen") is not False:
        raise P12IsolationAuthorityError("preclose gate claims P12 freeze")
    if gate.get("production_influence") != ZERO_TEXT:
        raise P12IsolationAuthorityError("preclose gate gained production influence")
    return gate
