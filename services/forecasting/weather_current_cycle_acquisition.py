"""Current-cycle raw-GRIB ACQUISITION layer for POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z.

Boundary (read before extending): this module acquires and independently validates ONLY the
exact raw GRIB2 bytes for one selected NOAA/AWS source object, plus their full acquisition
provenance. It never invokes wgrib2, never runs a subprocess, and never claims DAILY_MAX
semantics, KMDW correctness, target local date correctness, a supported midpoint, or the frozen
model identity -- every one of those claims belongs exclusively to
:func:`services.forecasting.weather_calibration_grib.parse_wgrib2_max_t_evidence` /
:func:`services.forecasting.weather_calibration_grib.validate_raw_grib_max_t_evidence`, which
this module never modifies, reimplements, or weakens.

What "acquisition" means here, precisely, and what it does NOT cover:

* Source authority and object-selection semantics are mirrored EXACTLY from the existing
  reviewed historical collector, ``scripts/collect_m27c_weather_calibration_coverage.py``
  (``_aws_index_url``, ``_aws_candidates``, ``_datasets``'s ``YGUZ98_KWBN_`` prefix, and
  ``_collect_post2020_raw_grib``'s ``expected_name_hour = "02"`` filename filter for this MAXT
  family) -- this module redeclares those exact same constants/rules (never importing from
  ``scripts/``, since ``services/`` must never depend on ``scripts/`` -- see
  ``services/market_universe/public_read.py``'s own stated dependency-direction rule) rather than
  inventing a new selection policy.
* IMPORTANT, flagged rather than silently resolved: the S3 filename's embedded hour for this MAXT
  family is ``"02"``, NOT ``"03"`` -- ``"03Z"`` refers to the GRIB-INTERNAL reference time the
  frozen parser alone verifies (``reference.hour != 3`` in ``validate_raw_grib_max_t_evidence``),
  a completely different thing from the filename. This module's own candidate SELECTION uses the
  filename rule (all it can observe pre-download); it makes no claim that the selected object's
  internal reference time actually is 03Z -- only the frozen parser, after wgrib2 extraction, can
  make that claim.
* This module deliberately does NOT invoke wgrib2 or any subprocess. The existing collector
  bundles "download GRIB" and "invoke wgrib2" together as one "collector" responsibility, and
  ``weather_calibration_grib.py``'s own docstring states plainly: "The collector owns downloading
  GRIB and invoking wgrib2. This module [the frozen parser] accepts only captured extraction
  text." Wgrib2 invocation depends on an external binary this session's "fake transports only"
  testing constraint cannot safely exercise (a subprocess dependency, not a network transport),
  so it is out of scope here. A caller must still run the exact raw bytes this module returns
  through the EXISTING, UNCHANGED ``_run_wgrib2``/``_resolve_wgrib2`` pattern (or an equivalent
  future thin extraction of it) to get wgrib2 TEXT, before calling
  ``parse_wgrib2_max_t_evidence``. This is the smallest honest acquisition boundary -- flagged
  explicitly rather than silently claiming this module alone completes "raw bytes -> frozen
  parser."
* "Current cycle" freshness/staleness has no existing reviewed policy anywhere in this
  repository -- the only reviewed use of this exact source (the historical collector) is
  operator-supplied date-range backfill, never a "today" policy. This module therefore does NOT
  invent a specific staleness threshold: :func:`validate_acquired_forecast_source` requires the
  caller to supply ``maximum_age`` explicitly rather than defaulting to an unreviewed number.
* FRESHNESS AUTHORITY, stated precisely so it is never confused for canary eligibility:
  ``maximum_age`` here governs ONLY how old THIS ACQUISITION artifact (``fetched_at``, i.e. when
  this module's own network call happened) may be. It is structurally unable to override, widen,
  or substitute for :data:`services.supervised_canary.m27d.MAX_FORECAST_AGE`, which is a
  completely different check on a completely different timestamp
  (``CurrentWeatherForecastEvidence.forecast_reference_time``, the GRIB-internal 03Z reference
  time -- always exactly 03Z for a genuine record, regardless of when it was fetched) applied by
  M27D's own, unmodified, downstream eligibility gate. This module never imports
  ``services.supervised_canary`` at all -- there is no code path here through which an operator
  could widen ``maximum_age`` to make M27D accept forecast evidence it would otherwise reject as
  stale. This module establishes acquisition/source/provenance truth only; it never redefines,
  and cannot redefine, canary eligibility.

No Kalshi imports. No credentials. No market economics. No execution.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import SplitResult, urlencode, urlsplit

from services.forecasting.weather_calibration_grib import POST2020_GRIB_FAMILY

SCHEMA = "kalsh3.forecasting.current-cycle-raw-grib-acquisition.v1"
SOFTWARE_VERSION = "kalsh3.forecasting.weather_current_cycle_acquisition/1"

# Same value as scripts/collect_m27c_weather_calibration_coverage.py:AWS_HOST -- redeclared, not
# imported (services/ must never depend on scripts/, mirroring services/market_universe/
# public_read.py's own stated dependency-direction rule).
AWS_HOST = "noaa-ndfd-pds.s3.amazonaws.com"

# Same reviewed filter as scripts/collect_m27c_weather_calibration_coverage.py:_datasets /
# _aws_candidates for CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z.
_FAMILY = POST2020_GRIB_FAMILY
_FILENAME_PREFIX = "YGUZ98_KWBN_"
# Same reviewed filter as _collect_post2020_raw_grib's `expected_name_hour` for the MAXT (not
# MinT) family -- see module docstring for why this is the filename's embedded hour, not the
# GRIB-internal 03Z reference time.
_FILENAME_HOUR_SUFFIX = "02"
_PRODUCT_PATH = "maxt"

# Defense-in-depth bounds. A bucket-listing XML response is small; a single GRIB2 object for one
# station/day is at most a few MB.
MAX_INDEX_BYTES = 2_000_000
MAX_RAW_GRIB_BYTES = 5_000_000
_GRIB_MAGIC = b"GRIB"


class SourceAcquisitionError(RuntimeError):
    """A structurally-relevant claim in current-cycle acquisition evidence failed validation."""


class MalformedIndexError(SourceAcquisitionError):
    """The AWS discovery index response itself is not well-formed -- distinct from a
    well-formed index yielding zero/multiple candidates (see :class:`SourceAcquisitionError`
    used directly for that case)."""


def aws_index_url(day: date) -> str:
    """Mirrors ``_aws_index_url`` exactly, MAXT-only (this module's sole supported family)."""
    query = urlencode(
        {"list-type": "2", "prefix": f"wmo/{_PRODUCT_PATH}/{day:%Y/%m/%d}/", "max-keys": "1000"}
    )
    return f"https://{AWS_HOST}/?{query}"


def _validate_source_url(url: str) -> SplitResult:
    """Mirrors the collector's ``_validate_public_url``, scoped to AWS_HOST only (the sole
    origin this family's reviewed selection rule ever names)."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceAcquisitionError("source URL has malformed authority") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.hostname != AWS_HOST
    ):
        raise SourceAcquisitionError("source URL is not the allowlisted HTTPS AWS origin")
    return parsed


def select_candidate_object_name(index_body: bytes) -> str:
    """Mirrors ``_aws_candidates`` exactly (prefix filter, DOCTYPE/ENTITY XML-bomb guard), then
    applies the same ``expected_name_hour`` filename filter ``_collect_post2020_raw_grib`` uses
    for this MAXT family, and requires EXACTLY one surviving candidate.

    This is filename-level ambiguity resolution only -- weaker than the existing collector's own
    disambiguation, which additionally requires a candidate to successfully wgrib2-extract and
    parse through the frozen parser before counting it as "valid" (see module docstring: this
    module never invokes wgrib2). If more than one filename-level candidate survives here, a
    caller wanting the collector's stronger parse-verified uniqueness must run each one through
    wgrib2 + the frozen parser itself, exactly as ``_collect_post2020_raw_grib`` does.
    """
    if b"<!DOCTYPE" in index_body.upper() or b"<!ENTITY" in index_body.upper():
        raise MalformedIndexError("unsafe AWS discovery XML")
    try:
        root = ET.fromstring(index_body)  # noqa: S314 -- DTD/ENTITY input is rejected above.
    except ET.ParseError as exc:
        raise MalformedIndexError(f"AWS discovery response is not valid XML: {exc}") from exc
    names = sorted(
        {
            key.text.rsplit("/", 1)[-1]
            for key in root.findall("{*}Contents/{*}Key")
            if key.text and key.text.rsplit("/", 1)[-1].startswith(_FILENAME_PREFIX)
        }
    )
    candidates = tuple(
        name for name in names if name.rsplit("_", 1)[-1][8:10] == _FILENAME_HOUR_SUFFIX
    )
    if len(candidates) != 1:
        raise SourceAcquisitionError(
            f"expected exactly one {_FILENAME_PREFIX}*_{_FILENAME_HOUR_SUFFIX} candidate, "
            f"found {len(candidates)}: {candidates}"
        )
    return candidates[0]


def object_url(day: date, name: str) -> str:
    return f"https://{AWS_HOST}/wmo/{_PRODUCT_PATH}/{day:%Y/%m/%d}/{name}"


def _source_selection_identity(day: date, name: str) -> str:
    material = f"{_FAMILY}|{_FILENAME_PREFIX}|{_FILENAME_HOUR_SUFFIX}|{day.isoformat()}|{name}"
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AcquiredForecastSource:
    schema: str
    software_version: str
    environment: str
    family_identity: str
    requested_day: str
    host: str
    index_url: str | None
    index_body_sha256: str | None
    object_url: str | None
    object_name: str | None
    http_status: int | None
    fetched_at: datetime
    expires_at: datetime
    byte_length: int | None
    raw_sha256: str | None
    raw_body_b64: str | None
    source_selection_identity: str | None
    parser_version: str
    classification: str
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "environment": self.environment,
            "family_identity": self.family_identity,
            "requested_day": self.requested_day,
            "host": self.host,
            "index_url": self.index_url,
            "index_body_sha256": self.index_body_sha256,
            "object_url": self.object_url,
            "object_name": self.object_name,
            "http_status": self.http_status,
            "fetched_at": self.fetched_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "byte_length": self.byte_length,
            "raw_sha256": self.raw_sha256,
            "raw_body_b64": self.raw_body_b64,
            "source_selection_identity": self.source_selection_identity,
            "parser_version": self.parser_version,
            "classification": self.classification,
            "reason": self.reason,
        }

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"


_EVIDENCE_FIELDS = frozenset(AcquiredForecastSource.__dataclass_fields__)
# A raw GRIB2 object is not itself ephemeral like a market quote, but this module still stamps a
# bounded self-consistency window (mirroring market_snapshot's expires_at pattern) so a
# deserialized artifact can be rejected as internally inconsistent -- separate from, and narrower
# than, whatever "current cycle" staleness policy a caller applies via `maximum_age`.
_SELF_CONSISTENCY_WINDOW = timedelta(minutes=5)


def _failed(
    day: date,
    fetched_at: datetime,
    classification: str,
    reason: str,
    *,
    index_url: str | None = None,
    index_body_sha256: str | None = None,
    obj_url: str | None = None,
    obj_name: str | None = None,
    http_status: int | None = None,
) -> AcquiredForecastSource:
    return AcquiredForecastSource(
        SCHEMA,
        SOFTWARE_VERSION,
        "PRODUCTION",
        _FAMILY,
        day.isoformat(),
        AWS_HOST,
        index_url,
        index_body_sha256,
        obj_url,
        obj_name,
        http_status,
        fetched_at,
        fetched_at + _SELF_CONSISTENCY_WINDOW,
        None,
        None,
        None,
        None,
        SOFTWARE_VERSION,
        classification,
        reason,
    )


def acquire_current_cycle_raw_grib(
    day: date,
    *,
    transport: Callable[[str], tuple[dict[str, object], bytes]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AcquiredForecastSource:
    """Acquire the exact raw GRIB2 bytes for the one reviewed-selection-rule candidate object
    for ``day``, via two bounded GETs (index, then object) through the injected ``transport``.

    ``transport`` has NO default: this module constructs the URLs itself
    (:func:`aws_index_url`/:func:`object_url`, both pre-validated by
    :func:`_validate_source_url` before ``transport`` is ever called) and never follows a
    redirect -- it only ever calls ``transport`` with a URL it built, never one taken from a
    prior response. A real production transport (bounded GET, no-redirect, allowlisted host) is
    intentionally not wired here; see this module's integration notes.

    Never trusts a caller-supplied family/date/hour claim: every candidate name is independently
    re-derived from the exact received index bytes via :func:`select_candidate_object_name`, and
    the object bytes are structurally sniffed (GRIB2 magic header) before being accepted --
    though the authoritative DAILY_MAX/KMDW/03Z claims remain exclusively the frozen parser's,
    downstream of this module.
    """
    started = clock()
    index_url = aws_index_url(day)
    try:
        _validate_source_url(index_url)
        index_evidence, index_body = transport(index_url)
    except SourceAcquisitionError as exc:
        return _failed(day, started, "SOURCE_AUTHORITY_MISMATCH", str(exc), index_url=index_url)

    index_status = index_evidence.get("status")
    index_sha = index_evidence.get("body_sha256")
    index_sha_str = index_sha if isinstance(index_sha, str) else None
    if (
        index_evidence.get("classification") != "SUCCESS"
        or index_status != 200
        or index_evidence.get("url") != index_url
    ):
        return _failed(
            day,
            started,
            "HTTP_OR_NETWORK_FAILURE",
            f"index acquisition did not succeed (status={index_status!r})",
            index_url=index_url,
            http_status=index_status if isinstance(index_status, int) else None,
        )
    if len(index_body) == 0:
        return _failed(
            day, started, "MALFORMED_ENVELOPE", "index response is empty", index_url=index_url
        )
    if len(index_body) > MAX_INDEX_BYTES:
        return _failed(
            day,
            started,
            "OVERSIZED_BODY",
            "index response exceeded the bounded size",
            index_url=index_url,
        )
    if index_sha_str is None or hashlib.sha256(index_body).hexdigest() != index_sha_str:
        return _failed(
            day,
            started,
            "MALFORMED_ENVELOPE",
            "index body hash did not match the exact received bytes",
            index_url=index_url,
        )

    try:
        object_name = select_candidate_object_name(index_body)
    except MalformedIndexError as exc:
        return _failed(
            day,
            started,
            "MALFORMED_ENVELOPE",
            str(exc),
            index_url=index_url,
            index_body_sha256=index_sha_str,
        )
    except SourceAcquisitionError as exc:
        return _failed(
            day,
            started,
            "AMBIGUOUS_SOURCE_SELECTION",
            str(exc),
            index_url=index_url,
            index_body_sha256=index_sha_str,
        )

    obj_url = object_url(day, object_name)
    try:
        _validate_source_url(obj_url)
        obj_evidence, obj_body = transport(obj_url)
    except SourceAcquisitionError as exc:
        return _failed(
            day,
            started,
            "SOURCE_AUTHORITY_MISMATCH",
            str(exc),
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
        )

    obj_status = obj_evidence.get("status")
    obj_sha = obj_evidence.get("body_sha256")
    obj_sha_str = obj_sha if isinstance(obj_sha, str) else None
    if (
        obj_evidence.get("classification") != "SUCCESS"
        or obj_status != 200
        or obj_evidence.get("url") != obj_url
    ):
        return _failed(
            day,
            started,
            "HTTP_OR_NETWORK_FAILURE",
            f"object acquisition did not succeed (status={obj_status!r})",
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
            http_status=obj_status if isinstance(obj_status, int) else None,
        )
    if len(obj_body) == 0:
        return _failed(
            day,
            started,
            "MALFORMED_ENVELOPE",
            "object response is empty",
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
        )
    if len(obj_body) > MAX_RAW_GRIB_BYTES:
        return _failed(
            day,
            started,
            "OVERSIZED_BODY",
            "object response exceeded the bounded size",
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
        )
    if obj_sha_str is None or hashlib.sha256(obj_body).hexdigest() != obj_sha_str:
        return _failed(
            day,
            started,
            "MALFORMED_ENVELOPE",
            "object body hash did not match the exact received bytes",
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
        )
    if not obj_body.startswith(_GRIB_MAGIC):
        return _failed(
            day,
            started,
            "MALFORMED_ENVELOPE",
            "object response is not GRIB2-shaped (missing GRIB magic header)",
            index_url=index_url,
            index_body_sha256=index_sha_str,
            obj_url=obj_url,
            obj_name=object_name,
        )

    return AcquiredForecastSource(
        SCHEMA,
        SOFTWARE_VERSION,
        "PRODUCTION",
        _FAMILY,
        day.isoformat(),
        AWS_HOST,
        index_url,
        index_sha_str,
        obj_url,
        object_name,
        200,
        started,
        started + _SELF_CONSISTENCY_WINDOW,
        len(obj_body),
        obj_sha_str,
        base64.b64encode(obj_body).decode("ascii"),
        _source_selection_identity(day, object_name),
        SOFTWARE_VERSION,
        "SUCCESS",
        None,
    )


@dataclass(frozen=True, slots=True)
class AcquisitionValidation:
    classification: str
    reason: str | None = None
    object_name: str | None = None
    raw_sha256: str | None = None
    fetched_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def validate_acquired_forecast_source(
    payload: object, *, expected_day: date, now: datetime, maximum_age: timedelta
) -> AcquisitionValidation:
    """Independently re-validate a serialized :class:`AcquiredForecastSource` payload.

    Never trusts any stamped field in isolation. This module retains only the OBJECT bytes (not
    the index bytes) to keep the artifact bounded, so the candidate-selection step itself cannot
    be re-run here from a serialized artifact alone; what IS independently re-derived is the
    object's own raw hash, GRIB magic-header shape, and ``source_selection_identity`` recomputed
    from the stamped ``requested_day``/``object_name`` (rejecting any payload where the identity
    does not match, i.e. where ``object_name`` was edited without the identity being recomputed
    to match).

    ``maximum_age`` has no default -- see module docstring: no reviewed "current cycle" staleness
    policy exists yet, so this function never invents one; the caller must supply it explicitly.
    """
    if not isinstance(payload, dict) or set(payload) != _EVIDENCE_FIELDS:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition payload has unexpected or missing fields"
        )
    if payload.get("schema") != SCHEMA:
        return AcquisitionValidation("MALFORMED_SNAPSHOT_EVIDENCE", "acquisition schema mismatch")
    if payload.get("classification") != "SUCCESS":
        return AcquisitionValidation("SOURCE_AUTHORITY_MISMATCH", "acquisition did not succeed")
    if payload.get("environment") != "PRODUCTION":
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition environment is not PRODUCTION"
        )
    if payload.get("family_identity") != _FAMILY:
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition family identity is not the frozen family"
        )
    if payload.get("host") != AWS_HOST:
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition host is not the fixed AWS origin"
        )
    if payload.get("requested_day") != expected_day.isoformat():
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition requested_day does not match the expected day"
        )
    if payload.get("http_status") != 200:
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition HTTP status was not 200"
        )

    object_name = payload.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition object_name is missing"
        )
    if payload.get("object_url") != object_url(expected_day, object_name):
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition object_url does not match the expected object"
        )
    if payload.get("index_url") != aws_index_url(expected_day):
        return AcquisitionValidation(
            "SOURCE_AUTHORITY_MISMATCH", "acquisition index_url does not match the expected day"
        )
    expected_identity = _source_selection_identity(expected_day, object_name)
    if payload.get("source_selection_identity") != expected_identity:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "acquisition source_selection_identity does not match an independent re-derivation",
        )

    fetched_at = _parse_timestamp(payload.get("fetched_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if fetched_at is None or expires_at is None:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition timestamps are malformed or naive"
        )
    if expires_at <= fetched_at or expires_at > fetched_at + _SELF_CONSISTENCY_WINDOW:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition expiry does not satisfy the fixed window"
        )
    if fetched_at > now:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition fetched_at is in the future"
        )
    age = now - fetched_at
    if age < timedelta(0) or age > maximum_age:
        return AcquisitionValidation(
            "ACQUISITION_EVIDENCE_STALE", "acquisition exceeds the caller's maximum_age"
        )

    raw_body_b64 = payload.get("raw_body_b64")
    raw_sha256 = payload.get("raw_sha256")
    if (
        not isinstance(raw_body_b64, str)
        or not raw_body_b64
        or not isinstance(raw_sha256, str)
        or not raw_sha256
    ):
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition is missing raw body material"
        )
    try:
        body = base64.b64decode(raw_body_b64, validate=True)
    except (binascii.Error, ValueError):
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition raw body is not valid base64"
        )
    if len(body) > MAX_RAW_GRIB_BYTES:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition raw body exceeds the bounded size"
        )
    if hashlib.sha256(body).hexdigest() != raw_sha256:
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition raw body does not match its recorded sha256"
        )
    if payload.get("byte_length") != len(body):
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "acquisition byte_length does not match the raw body"
        )
    if not body.startswith(_GRIB_MAGIC):
        return AcquisitionValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "acquisition raw body is not GRIB2-shaped (missing GRIB magic header)",
        )

    return AcquisitionValidation(
        "PASS", None, object_name=object_name, raw_sha256=raw_sha256, fetched_at=fetched_at
    )


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)
