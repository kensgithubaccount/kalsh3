"""Parse-verified current-cycle multi-candidate source selection for
POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z.

Reviewed parse-verified current-weather selector. Complete bounded WMO-wrapped source objects are
passed unchanged to reviewed wgrib2 validation. It exists to close a gap identified
by a live-preflight postmortem: the reviewed current-cycle acquisition layer
(:mod:`services.forecasting.weather_current_cycle_acquisition`) resolves multiple
filename-eligible AWS candidates by FILENAME ALONE and fails closed on any ambiguity
(``select_candidate_object_name``), while the reviewed HISTORICAL collector
(``scripts/collect_m27c_weather_calibration_coverage.py``'s ``_collect_post2020_raw_grib``)
resolves the identical ambiguity more precisely: it downloads and wgrib2-parses EVERY
filename-eligible candidate and accepts a day only if EXACTLY ONE candidate is content-valid
(passes wgrib2 extraction, the frozen semantic parser, and the KMDW proximity check). This module
gives the CURRENT-cycle path that same stronger policy, mirroring
``_collect_post2020_raw_grib`` exactly -- it invents no new logic and, in particular, invents NO
latest/earliest/lastmodified/lexicographic tiebreaker for the case where more than one candidate
is genuinely content-valid: that case remains a hard abstention, exactly as
``_collect_post2020_raw_grib`` already treats it (see e.g. its retained 2024-06-25 rejection,
"multiple valid 03Z candidates remained").

Why this cannot live inside services.forecasting.weather_current_cycle_acquisition (frozen):
that module's own docstring states plainly that it "deliberately does NOT invoke wgrib2 or any
subprocess" -- a documented architecture boundary, not an oversight. Wgrib2-invoking
orchestration lives in ``scripts/`` throughout this repository -- this module follows that same
convention rather than modifying the frozen acquisition module. No frozen file is modified by
this module.

Candidate enumeration (AWS index bytes -> filename-eligible candidate names) is MIRRORED, not
imported, from ``services.forecasting.weather_current_cycle_acquisition.
select_candidate_object_name``'s own candidate-listing step, minus its exactly-one enforcement.
Source-URL validation, ``MAX_INDEX_BYTES``, and ``MAX_RAW_GRIB_BYTES`` are imported directly
(private names can be imported without modifying the frozen file at all, so direct reuse is
preferred over mirroring wherever it is possible).

Operational-failure semantics: every per-candidate evaluation step is classified into exactly one
of three outcomes -- ``CONTENT_VALID`` (fully, conclusively evaluated and accepted by the frozen
semantic authority), ``CONTENT_INVALID`` (fully, conclusively evaluated and rejected for a
deterministic, reproducible reason: the frozen semantic authority, the size bound, an
unsafe candidate-controlled name failing the strict filename grammar
-- see :func:`_safe_object_url` -- or the reviewed ``_run_wgrib2`` non-zero-exit
``ForecastError("wgrib2 extraction failed")``, mirroring the frozen historical collector's own
``_collect_post2020_raw_grib``, which treats that exact same exception as an ordinary
per-candidate content rejection rather than a whole-day operational abort), or
``EVALUATION_BLOCKED`` (could NOT be conclusively evaluated: a malformed/non-``bytes`` transport
response, a transport failure, a wgrib2 timeout or any other non-``ForecastError`` failure from
``_run_wgrib2``, a filesystem/temp-file error, an extraction-text hashing/encoding failure, an
unexpected exception from the frozen parser/validator, an inability to resolve wgrib2, or any
other non-semantic execution failure). Every one of these is classified by WHICH OPERATION
BOUNDARY raised, never by exception message text -- see :func:`_evaluate_candidate`'s dedicated
inner ``except ForecastError`` scoped to exactly the ``_run_wgrib2`` call, structurally distinct
from ``_resolve_wgrib2``'s own separate, EVALUATION_BLOCKED-only call site. A single
``EVALUATION_BLOCKED`` outcome anywhere forces the WHOLE selection to ``EVALUATION_BLOCKED``,
regardless of how many other candidates are content-valid; a ``CONTENT_INVALID`` outcome never
does -- it participates in ordinary candidate accounting exactly like any other deterministic
rejection.

Structural exhaustiveness: ``CandidateOutcome.__post_init__`` rejects a non-``CandidateStatus``
status, and enforces that ``provenance`` is present if and only if ``status`` is
``CONTENT_VALID`` -- a ``CONTENT_INVALID``/``EVALUATION_BLOCKED`` outcome can never carry the
evidence that would make it indistinguishable from a genuinely valid one, and a ``CONTENT_VALID``
outcome can never be missing it. :func:`_partition_outcomes` -- the ONLY place the aggregator
reads outcome status -- independently re-verifies, BY IDENTITY (a multiset/``Counter`` comparison
of candidate names, not merely a count), that the outcome set corresponds exactly to the
enumerated candidate set: no candidate missing, none duplicated, none substituted/unexpected.
Only after that identity check passes does it partition by status, with its own count-based
completeness check kept as a second, independent guarantee. Any failure at either layer raises
:class:`CandidateAccountingError` (fail closed) -- and the public
:func:`select_parse_verified_current_source` wraps its entire body in a final top-level exception
boundary so that even an accounting-layer raise can never escape as an uncaught crash: it always
returns a definitive, non-``SUCCESS`` :class:`ParseVerifiedSelectionResult`.

Exact-object provenance: a ``SUCCESS`` result never returns bare, disconnected fields a caller
would have to trust independently -- it returns one frozen :class:`SelectedSourceProvenance`
carrying the exact validated object name, exact validated URL, the exact immutable ``bytes``
object used for every downstream step (hashing, wgrib2, parsing), its length, its SHA-256, the
exact wgrib2 extraction text, its SHA-256, and the parsed/validated evidence -- with its own
``__post_init__`` re-deriving the length and both hashes from the retained bytes/text rather than
trusting caller-supplied values. No re-acquisition, no reconstruction from hashes, no second
independently-authoritative copy of the evidence. ``ParseVerifiedSelectionResult.selected_name``/
``.evidence`` are properties DERIVED from this one provenance object, never independently
supplied fields that could drift from it.

Safe object-name/URL semantics: :func:`_safe_object_url` validates a candidate name against a
strict allowlisted filename grammar (bound to the frozen ``_FILENAME_PREFIX``, digits only after
it -- never a blacklist of individual attack characters) BEFORE constructing any URL, then
constructs the URL through the frozen, unmodified :func:`object_url`, then re-validates the
result through the frozen ``_validate_source_url`` AND independently confirms no query, no
fragment, and an exact expected path family with the exact candidate name as the terminal path
segment. An unsafe name never reaches :func:`object_url`'s output being handed to ``transport``;
it is a deterministic, conclusive ``CONTENT_INVALID`` rejection, exactly like any other source/
content rejection already documented in this module.

Transport: a single caller-supplied ``transport: Callable[[str], bytes]`` is used for both the
index fetch and every object fetch, with no default -- this module must never silently perform
live network I/O. Every transport call is preceded by :func:`_safe_object_url` (candidate objects)
or ``_validate_source_url`` (the index) and followed by a structural check that the response is
actually ``bytes`` (never ``bytearray``/``memoryview``/``str``/anything else) before it is used
for anything.

No credentials, no Kalshi, no market economics, no risk, no execution, no order, no signer, no
SQLite store anywhere in this file. This file never imports or calls ``subprocess`` directly --
the narrow ``except Exception`` boundaries around each operational stage (transport, temp-file
write, wgrib2 invocation, extraction hashing) classify any failure there (including a
``subprocess.TimeoutExpired`` raised by the frozen ``_run_wgrib2``, without this file needing to
name that type) as ``EVALUATION_BLOCKED`` rather than letting it escape uncaught; the only two
functions in this file that can ever cause a process to be spawned remain the imported, frozen,
unmodified ``_resolve_wgrib2``/``_run_wgrib2``. See the AST-based capability test in the sibling
test file.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scripts.collect_m27c_weather_calibration_coverage import (
    EXTRACTION_POLICY_VERSION,
    WGRIB2_VERSION,
    _get,
    _resolve_wgrib2,
    _run_wgrib2,
    _sha256,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    POST2020_GRIB_FAMILY,
    RawGribEvidence,
    parse_wgrib2_max_t_evidence,
    validate_kmdw_points,
)
from services.forecasting.weather_current_cycle_acquisition import (
    _PRODUCT_PATH,  # frozen, private -- imported directly, never modified; see docstring.
    MAX_INDEX_BYTES,
    MAX_RAW_GRIB_BYTES,
    SourceAcquisitionError,
    _validate_source_url,  # frozen, private -- imported directly, never modified; see docstring.
    aws_index_url,
    object_url,
)
from services.forecasting.weather_prospective_capture import KMDW_LATITUDE, KMDW_LONGITUDE

SOFTWARE_VERSION = "kalsh3.scripts.select_parse_verified_current_weather_source/4"

# Mirrors services.forecasting.weather_current_cycle_acquisition's own
# _FILENAME_PREFIX/_FILENAME_HOUR_SUFFIX exactly -- redeclared, not imported (both are private
# module-level names there); see module docstring.
_FILENAME_PREFIX = "YGUZ98_KWBN_"
_FILENAME_HOUR_SUFFIX = "02"

# Strict allowlisted filename grammar for corrective-pass-3 IMPORTANT 4: bound to the reviewed
# prefix, digits only after it. Deliberately an allowlist, not a blacklist of individual attack
# characters (`?`, `#`, `%`, `/`, `\`, `..`, whitespace, control/Unicode ambiguity, embedded
# authority, absolute-URL shape are all excluded simply by not being in the allowed character
# class at all).
_SAFE_CANDIDATE_NAME_RE = re.compile(rf"\A{re.escape(_FILENAME_PREFIX)}[0-9]+\Z")

ZERO_VALID = "ZERO_VALID_CANDIDATES"
MULTIPLE_VALID = "MULTIPLE_VALID_CANDIDATES"
SUCCESS = "SUCCESS"
EVALUATION_BLOCKED = "EVALUATION_BLOCKED"

_SCHEMA = "kalsh3.scripts.parse-verified-source-selection.v3"


class CandidateStatus(StrEnum):
    """Exactly three outcomes for one filename-eligible candidate's evaluation. See module
    docstring's "Operational-failure semantics" section for the precise meaning of each."""

    CONTENT_VALID = "CONTENT_VALID"
    CONTENT_INVALID = "CONTENT_INVALID"
    EVALUATION_BLOCKED = "EVALUATION_BLOCKED"


class CandidateAccountingError(RuntimeError):
    """A :class:`CandidateOutcome`/:class:`SelectedSourceProvenance` was constructed in a
    structurally impossible shape, or :func:`_partition_outcomes` could not account for every
    supplied outcome, by identity, in exactly one of its three status-based partitions. Always
    fail closed on this -- never silently drop or ignore the offending outcome; see module
    docstring."""


class UnsafeCandidateNameError(SourceAcquisitionError):
    """A candidate-controlled name failed the strict allowlisted filename grammar, or the URL
    constructed from it failed structural re-validation. A deterministic, conclusive
    source/content rejection (``CONTENT_INVALID``) -- the name never reaches ``transport``."""


@dataclass(frozen=True, slots=True)
class _KmdwPoint:
    latitude: Decimal
    longitude: Decimal


def candidate_names_from_index(index_body: bytes) -> tuple[str, ...]:
    """Every filename-eligible ``YGUZ98_KWBN_*_02`` candidate in one AWS index response.

    Mirrors ``select_candidate_object_name``'s own candidate-listing step exactly (same
    DOCTYPE/ENTITY guard, same prefix filter, same filename-hour filter), minus its exactly-one
    enforcement -- see module docstring. Raises :class:`SourceAcquisitionError` (fail closed) on
    unsafe or malformed XML, identically to the frozen function. Does not itself bound
    ``index_body``'s size -- callers must apply :data:`MAX_INDEX_BYTES` before calling this, so
    that an oversized index is rejected before any XML parsing is attempted at all.
    """
    if b"<!DOCTYPE" in index_body.upper() or b"<!ENTITY" in index_body.upper():
        raise SourceAcquisitionError("unsafe AWS discovery XML")
    try:
        root = ET.fromstring(index_body)  # noqa: S314 -- DTD/ENTITY input is rejected above.
    except ET.ParseError as exc:
        raise SourceAcquisitionError(f"AWS discovery response is not valid XML: {exc}") from exc
    names = sorted(
        {
            key.text.rsplit("/", 1)[-1]
            for key in root.findall("{*}Contents/{*}Key")
            if key.text and key.text.rsplit("/", 1)[-1].startswith(_FILENAME_PREFIX)
        }
    )
    return tuple(name for name in names if name.rsplit("_", 1)[-1][8:10] == _FILENAME_HOUR_SUFFIX)


def _safe_object_url(day: date, name: str) -> str:
    """Validate ``name`` against the strict allowlisted filename grammar, construct its object
    URL through the frozen, unmodified :func:`object_url`, and structurally re-validate the
    result before any caller may use it for a transport call.

    Raises :class:`UnsafeCandidateNameError` (a :class:`SourceAcquisitionError` subclass, fail
    closed) if ``name`` does not match ``_SAFE_CANDIDATE_NAME_RE``, if the frozen
    ``_validate_source_url`` rejects the constructed URL (scheme/host/userinfo/port), or if the
    constructed URL carries a query/fragment or does not resolve to the exact expected
    ``/wmo/<product>/<day>/<name>`` path with ``name`` as its exact terminal segment. This module
    never modifies :func:`object_url` itself -- see module docstring.
    """
    if not _SAFE_CANDIDATE_NAME_RE.fullmatch(name):
        raise UnsafeCandidateNameError(
            f"candidate name failed the strict allowlisted filename grammar: {name!r}"
        )
    url = object_url(day, name)
    _validate_source_url(url)
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment:
        raise UnsafeCandidateNameError(
            f"constructed candidate URL unexpectedly carries a query/fragment: {url!r}"
        )
    expected_path = f"/wmo/{_PRODUCT_PATH}/{day:%Y/%m/%d}/{name}"
    if parsed.path != expected_path or not parsed.path.endswith(f"/{name}"):
        raise UnsafeCandidateNameError(
            f"constructed candidate URL path does not match the expected family: {url!r}"
        )
    return url


@dataclass(frozen=True, slots=True)
class SelectedSourceProvenance:
    """The exact validated object and bytes behind one ``CONTENT_VALID`` candidate outcome --
    corrective-pass-3 IMPORTANT 3. Every field is re-derived and cross-checked at construction
    time rather than trusted from the caller, so a ``SUCCESS`` result can never carry a
    provenance object whose stamped hashes/length disagree with its own retained bytes/text.

    ``raw_bytes`` is always the exact ``bytes`` object returned by ``transport`` for this
    candidate's object URL (never re-acquired, never reconstructed, never a caller-mutable
    ``bytearray``/``memoryview`` -- see the structural type check in :func:`_evaluate_candidate`
    upstream of this constructor, which only ever admits genuine ``bytes``, an inherently
    immutable type in Python).
    """

    name: str
    url: str
    raw_bytes: bytes
    raw_byte_length: int
    raw_sha256: str
    extraction_text: str
    extraction_sha256: str
    evidence: RawGribEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes):
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} raw_bytes is not bytes: "
                f"{type(self.raw_bytes).__name__}"
            )
        if self.raw_byte_length != len(self.raw_bytes):
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} raw_byte_length "
                f"({self.raw_byte_length}) does not match len(raw_bytes) ({len(self.raw_bytes)})"
            )
        if _sha256(self.raw_bytes) != self.raw_sha256:
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} raw_sha256 does not match a fresh "
                "hash of its own retained raw_bytes"
            )
        if _sha256(self.extraction_text.encode()) != self.extraction_sha256:
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} extraction_sha256 does not match a "
                "fresh hash of its own retained extraction_text"
            )
        if self.evidence.raw_grib_sha256 != self.raw_sha256:
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} evidence.raw_grib_sha256 does not "
                "match this provenance's own raw_sha256"
            )
        if self.evidence.extraction_sha256 != self.extraction_sha256:
            raise CandidateAccountingError(
                f"SelectedSourceProvenance for {self.name!r} evidence.extraction_sha256 does not "
                "match this provenance's own extraction_sha256"
            )


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """One filename-eligible candidate's independent evaluation outcome. ``status`` is always an
    explicit :class:`CandidateStatus` member, structurally validated at construction time.
    ``provenance`` is present if and only if ``status`` is ``CONTENT_VALID`` -- a
    ``CONTENT_INVALID``/``EVALUATION_BLOCKED`` outcome can never carry the evidence that would
    make it indistinguishable from a genuinely valid one."""

    name: str
    status: CandidateStatus
    provenance: SelectedSourceProvenance | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CandidateStatus):
            raise CandidateAccountingError(
                f"CandidateOutcome for {self.name!r} constructed with a non-CandidateStatus "
                f"status: {self.status!r}"
            )
        if self.status is CandidateStatus.CONTENT_VALID:
            if self.provenance is None:
                raise CandidateAccountingError(
                    f"CandidateOutcome for {self.name!r} is CONTENT_VALID but missing provenance"
                )
            if self.provenance.name != self.name:
                raise CandidateAccountingError(
                    f"CandidateOutcome for {self.name!r} carries provenance for a different "
                    f"candidate: {self.provenance.name!r}"
                )
        elif self.provenance is not None:
            raise CandidateAccountingError(
                f"CandidateOutcome for {self.name!r} has status {self.status.value} but carries "
                "provenance, which only CONTENT_VALID may carry"
            )

    @property
    def evidence(self) -> RawGribEvidence | None:
        return self.provenance.evidence if self.provenance is not None else None

    @property
    def raw_sha256(self) -> str | None:
        return self.provenance.raw_sha256 if self.provenance is not None else None


def _invalid(name: str, message: str) -> CandidateOutcome:
    return CandidateOutcome(name, CandidateStatus.CONTENT_INVALID, None, message)


def _blocked(name: str, message: str) -> CandidateOutcome:
    return CandidateOutcome(name, CandidateStatus.EVALUATION_BLOCKED, None, message)


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    """Internal result of fetching one candidate's raw object bytes. Exactly one of
    ``(raw, url)`` together or ``outcome`` is populated."""

    raw: bytes | None
    url: str | None
    outcome: CandidateOutcome | None


def _fetch_candidate_object(
    name: str, day: date, *, transport: Callable[[str], bytes]
) -> _FetchOutcome:
    """Validate this candidate's name/URL and fetch its bytes, structurally checking the
    transport's response shape before handing it back to the caller.

    An unsafe name (``CONTENT_INVALID`` -- deterministic, reproducible; see
    :func:`_safe_object_url`), any transport-call failure (``EVALUATION_BLOCKED`` -- inconclusive:
    this candidate might have been valid, we simply could not fetch it), or a structurally
    malformed transport response -- not exactly ``bytes``, e.g. ``None``, a ``str``, a
    ``bytearray``/``memoryview``, a tuple/other shape a caller mistakenly wired in --
    (``EVALUATION_BLOCKED`` -- the selector cannot evaluate what it cannot conclusively interpret)
    all short-circuit here.
    """
    try:
        url = _safe_object_url(day, name)
    except SourceAcquisitionError as exc:
        return _FetchOutcome(None, None, _invalid(name, f"{name}: {exc}"))

    try:
        raw = transport(url)
    except Exception as exc:
        # Intentional broad catch, scoped to exactly this one call: ANY transport failure for
        # this one candidate (network hiccup, DNS failure, HTTP error, timeout, or any other
        # transport-layer fault) is inconclusive about its content validity and must never be
        # treated as "this candidate is invalid" -- see module docstring. Excludes
        # BaseException/SystemExit/KeyboardInterrupt (none are Exception subclasses).
        return _FetchOutcome(None, None, _blocked(name, f"{name}: object transport failed: {exc}"))

    if not isinstance(raw, bytes):
        return _FetchOutcome(
            None,
            None,
            _blocked(
                name,
                f"{name}: transport returned a malformed response type "
                f"({type(raw).__name__}, expected bytes) -- cannot be conclusively evaluated",
            ),
        )
    return _FetchOutcome(raw, url, None)


def _evaluate_candidate(
    name: str,
    day: date,
    *,
    transport: Callable[[str], bytes],
    wgrib2_executable: str,
) -> CandidateOutcome:
    """Fetch, wgrib2-extract, and frozen-parse ONE candidate; always returns a
    :class:`CandidateOutcome` with an explicit status -- never raises for a reason attributable to
    this one candidate. Distinguishes deterministic content rejection (``CONTENT_INVALID``) from
    inconclusive operational failure (``EVALUATION_BLOCKED``) at every stage; see module
    docstring."""
    fetched = _fetch_candidate_object(name, day, transport=transport)
    if fetched.outcome is not None:
        return fetched.outcome
    raw = fetched.raw
    url = fetched.url
    assert raw is not None and url is not None  # noqa: S101 -- narrows for type checkers

    # Filename, URL, and size authority still reuse current-cycle acquisition. Complete-object
    # content handling intentionally mirrors the historical collector: WMO objects may contain a
    # text envelope before embedded GRIB messages, so the exact downloaded bytes are written
    # unchanged and wgrib2—not an offset-zero magic check—is the reviewed content-format
    # authority. The legacy current-cycle acquisition gate remains unchanged and is not used by
    # this selector. Never a tiebreaker input -- see module docstring.
    if len(raw) > MAX_RAW_GRIB_BYTES:
        return _invalid(
            name,
            f"{name}: object response exceeded the bounded size "
            f"({len(raw)} > {MAX_RAW_GRIB_BYTES})",
        )
    try:
        raw_sha256 = _sha256(raw)
    except Exception as exc:
        return _blocked(name, f"{name}: raw-byte hashing/normalization failed: {exc}")

    try:
        with tempfile.TemporaryDirectory(
            prefix="m27-parse-verified-source-selection-"
        ) as snapshot_dir:
            snapshot_path = Path(snapshot_dir) / "candidate.grib2"
            snapshot_path.write_bytes(raw)
            try:
                extraction_text = _run_wgrib2(
                    wgrib2_executable, snapshot_path, _KmdwPoint(KMDW_LATITUDE, KMDW_LONGITUDE)
                )
            except ForecastError as exc:
                # Structurally scoped to exactly this one call -- never a broad ForecastError
                # catch spanning the resolver too (the resolver has its own, separate,
                # EVALUATION_BLOCKED-only call site well before this per-candidate loop even
                # starts; see select_parse_verified_current_source). Inspecting the frozen
                # _run_wgrib2 (scripts/collect_m27c_weather_calibration_coverage.py) shows
                # ForecastError is the ONLY exception it ever deliberately raises itself --
                # exclusively for `completed.returncode != 0` (subprocess.run's own
                # `check=False`, so it never raises CalledProcessError; a genuine timeout raises
                # subprocess.TimeoutExpired instead, which this except clause does NOT match and
                # which therefore still falls through to the broader operational catch below).
                # The frozen historical collector (`_collect_post2020_raw_grib`) treats this
                # exact same exception as an ordinary per-candidate content rejection, not a
                # whole-day operational abort -- mirrored here for behavioral fidelity: fully
                # conclusive, deterministic, reproducible (the same bytes always produce the
                # same non-zero exit): CONTENT_INVALID, never EVALUATION_BLOCKED. This is a
                # classification by OPERATION BOUNDARY (which call raised, not what its message
                # says) -- no message/substring matching anywhere in this branch.
                return _invalid(name, f"{name}: {exc}")
        # Hashing wgrib2's own output text is itself non-semantic local processing, not a claim
        # about GRIB content -- it belongs inside this SAME operational boundary. A
        # UnicodeEncodeError here (e.g. a lone-surrogate codepoint in wgrib2's stdout that decoded
        # but cannot round-trip encode) is exactly as inconclusive as a wgrib2 timeout or a disk
        # error, and must be classified identically.
        extraction_sha256 = _sha256(extraction_text.encode())
    except Exception as exc:
        # Intentional broad catch, scoped to exactly this one block: ANY OTHER failure
        # snapshotting to disk, running wgrib2 (subprocess.TimeoutExpired, an unexpected
        # non-ForecastError runtime failure -- the ForecastError non-zero-exit case is already
        # handled and returned above, before this point is ever reached for that case), or
        # hashing wgrib2's own output for this one candidate is inconclusive about content
        # validity. Excludes BaseException/SystemExit/KeyboardInterrupt.
        return _blocked(name, f"{name}: candidate evaluation could not be completed: {exc}")

    try:
        evidence = parse_wgrib2_max_t_evidence(
            extraction_text,
            family_identity=POST2020_GRIB_FAMILY,
            extraction_policy_version=EXTRACTION_POLICY_VERSION,
            wgrib2_version=WGRIB2_VERSION,
            raw_grib_sha256=raw_sha256,
            extraction_sha256=extraction_sha256,
        )
        validate_kmdw_points(evidence, KMDW_LATITUDE, KMDW_LONGITUDE)
    except (ForecastError, ET.ParseError, UnicodeError) as exc:
        # The frozen semantic authority -- and only the frozen semantic authority -- rejected
        # this candidate's content (e.g. reference time is not exactly 03Z, wrong grid, wrong
        # location). Fully conclusive, deterministic, reproducible: CONTENT_INVALID.
        return _invalid(name, f"{name}: {exc}")
    except Exception as exc:
        # An UNEXPECTED exception from the frozen parser/validator (anything not in the reviewed
        # rejection-type set above) means we do not actually know whether this candidate is
        # valid -- it must never be silently treated as a semantic rejection.
        return _blocked(name, f"{name}: unexpected parser/validation failure: {exc}")

    try:
        provenance = SelectedSourceProvenance(
            name=name,
            url=url,
            raw_bytes=raw,
            raw_byte_length=len(raw),
            raw_sha256=raw_sha256,
            extraction_text=extraction_text,
            extraction_sha256=extraction_sha256,
            evidence=evidence,
        )
    except CandidateAccountingError as exc:
        return _blocked(name, f"{name}: provenance construction failed: {exc}")
    return CandidateOutcome(name, CandidateStatus.CONTENT_VALID, provenance, None)


def _partition_outcomes(
    candidates: tuple[str, ...], outcomes: tuple[CandidateOutcome, ...]
) -> tuple[
    tuple[CandidateOutcome, ...], tuple[CandidateOutcome, ...], tuple[CandidateOutcome, ...]
]:
    """Independently re-verify, BY IDENTITY, that ``outcomes`` corresponds exactly to
    ``candidates`` before partitioning by status -- corrective-pass-3 IMPORTANT 2.

    A multiset (``Counter``) comparison of candidate names catches every failure mode a bare
    count comparison would miss: a duplicated outcome for one candidate while another is omitted
    still sums to the right total length, but does NOT produce equal ``Counter``s. Raises
    :class:`CandidateAccountingError` (fail closed) on any mismatch -- missing, duplicated, or
    unexpected/substituted candidate identities alike -- naming exactly which candidates are
    missing or extra.

    Only once identity accounting passes does this partition by ``status`` (never by whether
    ``error`` happens to be populated), with its own independent count-based completeness check
    kept as a second, redundant guarantee.
    """
    outcome_counts = Counter(outcome.name for outcome in outcomes)
    candidate_counts = Counter(candidates)
    if outcome_counts != candidate_counts:
        missing = tuple(sorted((candidate_counts - outcome_counts).elements()))
        extra = tuple(sorted((outcome_counts - candidate_counts).elements()))
        raise CandidateAccountingError(
            f"candidate identity accounting is incomplete: {len(candidates)} candidates "
            f"enumerated, {len(outcomes)} outcomes produced; missing={missing} "
            f"extra_or_duplicated={extra}"
        )

    blocked = tuple(o for o in outcomes if o.status is CandidateStatus.EVALUATION_BLOCKED)
    valid = tuple(o for o in outcomes if o.status is CandidateStatus.CONTENT_VALID)
    invalid = tuple(o for o in outcomes if o.status is CandidateStatus.CONTENT_INVALID)
    accounted = len(blocked) + len(valid) + len(invalid)
    if accounted != len(outcomes):
        raise CandidateAccountingError(
            f"candidate status accounting is incomplete: {len(outcomes)} evaluated, only "
            f"{accounted} fell into a recognized status bucket (CONTENT_VALID/CONTENT_INVALID/"
            "EVALUATION_BLOCKED)"
        )
    return blocked, valid, invalid


@dataclass(frozen=True, slots=True)
class ParseVerifiedSelectionResult:
    schema: str
    day: date
    classification: str
    reason: str | None
    candidate_names: tuple[str, ...]
    candidate_errors: tuple[str, ...]
    blocked_candidate_errors: tuple[str, ...]
    selected: SelectedSourceProvenance | None
    wgrib2_executable_sha256: str | None

    @property
    def succeeded(self) -> bool:
        return self.classification == SUCCESS

    @property
    def selected_name(self) -> str | None:
        """Derived from ``selected`` -- never an independently supplied field that could drift
        from the provenance object it names; see module docstring."""
        return self.selected.name if self.selected is not None else None

    @property
    def evidence(self) -> RawGribEvidence | None:
        """Derived from ``selected`` -- see :attr:`selected_name`."""
        return self.selected.evidence if self.selected is not None else None


def _result(
    day: date,
    classification: str,
    reason: str | None,
    *,
    candidate_names: tuple[str, ...] = (),
    candidate_errors: tuple[str, ...] = (),
    blocked_candidate_errors: tuple[str, ...] = (),
    selected: SelectedSourceProvenance | None = None,
    wgrib2_executable_sha256: str | None = None,
) -> ParseVerifiedSelectionResult:
    return ParseVerifiedSelectionResult(
        _SCHEMA,
        day,
        classification,
        reason,
        candidate_names,
        candidate_errors,
        blocked_candidate_errors,
        selected,
        wgrib2_executable_sha256,
    )


def _select_parse_verified_current_source_inner(
    day: date,
    *,
    transport: Callable[[str], bytes],
    wgrib2_bin: str | None,
) -> ParseVerifiedSelectionResult:
    index_url = aws_index_url(day)
    try:
        _validate_source_url(index_url)
        index_body = transport(index_url)
    except SourceAcquisitionError as exc:
        return _result(day, EVALUATION_BLOCKED, f"index URL failed source validation: {exc}")
    except Exception as exc:
        # Intentional broad catch, scoped to exactly this one call: any index transport failure
        # is inconclusive for the whole selection (we don't even know the candidate set).
        # Excludes BaseException/SystemExit/KeyboardInterrupt.
        return _result(day, EVALUATION_BLOCKED, f"index transport failed: {exc}")

    if not isinstance(index_body, bytes):
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"index transport returned a malformed response type "
            f"({type(index_body).__name__}, expected bytes) -- cannot be conclusively evaluated",
        )

    if len(index_body) > MAX_INDEX_BYTES:
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"index response exceeded the bounded size ({len(index_body)} > {MAX_INDEX_BYTES}); "
            "no candidate enumeration was attempted",
        )

    try:
        candidates = candidate_names_from_index(index_body)
    except Exception as exc:
        # Broadened beyond the SourceAcquisitionError candidate_names_from_index itself
        # documents raising: an UNEXPECTED exception during index parsing/enumeration must still
        # never escape this function uncaught.
        return _result(day, EVALUATION_BLOCKED, f"index parsing/enumeration failed: {exc}")

    if not candidates:
        return _result(
            day,
            ZERO_VALID,
            f"no filename-eligible {_FILENAME_PREFIX}*_{_FILENAME_HOUR_SUFFIX} candidates for "
            f"{day.isoformat()}",
        )

    try:
        executable, executable_sha256 = _resolve_wgrib2(wgrib2_bin)
    except Exception as exc:
        # Broadened beyond ForecastError: an unexpected resolver exception must still block
        # rather than escape.
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"wgrib2 could not be resolved: {exc}",
            candidate_names=candidates,
        )

    outcomes = tuple(
        _evaluate_candidate(name, day, transport=transport, wgrib2_executable=executable)
        for name in candidates
    )

    try:
        blocked, valid, invalid = _partition_outcomes(candidates, outcomes)
    except CandidateAccountingError as exc:
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"candidate accounting could not be verified -- refusing to declare a winner: {exc}",
            candidate_names=candidates,
            wgrib2_executable_sha256=executable_sha256,
        )

    if blocked:
        # Detection is `if blocked:` on the STATUS partition alone -- an outcome's optional
        # `error` string is used only to compose this diagnostic message, never to decide
        # whether the candidate counts as blocked. A blocked outcome with error=None still
        # blocks here.
        blocked_errors = tuple(outcome.error for outcome in blocked if outcome.error is not None)
        diagnostic = (
            " | ".join(blocked_errors) if blocked_errors else "(no diagnostic detail available)"
        )
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"{len(blocked)} of {len(candidates)} filename-eligible candidates for "
            f"{day.isoformat()} could not be conclusively evaluated -- refusing to declare a "
            f"winner from a possibly-incomplete candidate set: {diagnostic}",
            candidate_names=candidates,
            blocked_candidate_errors=blocked_errors,
            wgrib2_executable_sha256=executable_sha256,
        )

    invalid_errors = tuple(outcome.error for outcome in invalid if outcome.error is not None)
    if len(valid) != 1:
        classification = ZERO_VALID if not valid else MULTIPLE_VALID
        reason = (
            f"expected exactly one content-valid {_FILENAME_PREFIX}*_{_FILENAME_HOUR_SUFFIX} "
            f"candidate for {day.isoformat()}, found {len(valid)} of {len(candidates)} "
            f"filename-eligible (all conclusively evaluated): "
            f"{tuple(outcome.name for outcome in valid)}"
        )
        if invalid_errors:
            reason += "; " + " | ".join(invalid_errors)
        return _result(
            day,
            classification,
            reason,
            candidate_names=candidates,
            candidate_errors=invalid_errors,
            wgrib2_executable_sha256=executable_sha256,
        )

    chosen = valid[0]
    assert chosen.provenance is not None  # noqa: S101 -- guaranteed by CandidateOutcome invariant
    return _result(
        day,
        SUCCESS,
        None,
        candidate_names=candidates,
        candidate_errors=invalid_errors,
        selected=chosen.provenance,
        wgrib2_executable_sha256=executable_sha256,
    )


def select_parse_verified_current_source(
    day: date,
    *,
    transport: Callable[[str], bytes],
    wgrib2_bin: str | None = None,
) -> ParseVerifiedSelectionResult:
    """Enumerate every filename-eligible candidate for ``day``, content-validate EACH one through
    the existing reviewed wgrib2 resolver/runner and the frozen parser/KMDW-proximity check, and
    accept only if exactly one is content-valid AND every candidate was conclusively evaluated AND
    identity accounting for the full candidate set independently verifies.

    Classification is exactly one of:

    * ``SUCCESS`` -- every candidate was conclusively evaluated, identity-accounted for, and
      exactly one is content-valid. ``selected`` carries the exact validated
      :class:`SelectedSourceProvenance` -- name, URL, immutable raw bytes, length, hashes,
      extraction text, and parsed evidence -- with no re-acquisition or reconstruction possible.
    * ``ZERO_VALID_CANDIDATES`` -- every candidate was conclusively evaluated and none is
      content-valid (this also covers the trivial case of zero filename-eligible candidates).
    * ``MULTIPLE_VALID_CANDIDATES`` -- every candidate was conclusively evaluated and more than
      one is content-valid. No latest/earliest/lastmodified/lexicographic tiebreaker is ever
      applied; ``selected`` is always ``None`` here.
    * ``EVALUATION_BLOCKED`` -- at least one candidate could NOT be conclusively evaluated, OR
      identity/status accounting for the full candidate set could not be independently verified
      (an impossible/corrupted state). Returned regardless of how many OTHER candidates were
      content-valid: an operational fault or accounting anomaly must never silently shrink the
      competing set and manufacture a false unique winner. ``selected`` is always ``None`` here.

    This function's entire body runs behind one final top-level exception boundary: even a raise
    from the internal accounting layer (:class:`CandidateAccountingError`) or any other
    unexpected failure can never escape as an uncaught crash -- it always returns a definitive,
    non-``SUCCESS`` :class:`ParseVerifiedSelectionResult` instead.

    Every candidate object URL is constructed and validated through :func:`_safe_object_url`
    (strict allowlisted filename grammar, then the frozen ``_validate_source_url``, then an
    independent query/fragment/path-family re-check) before ``transport`` is ever called for it;
    the index URL is validated through the frozen ``_validate_source_url`` directly. Every
    transport response (index and each candidate object) is structurally checked to actually be
    ``bytes`` before being used for anything. The index response is rejected if it exceeds
    :data:`MAX_INDEX_BYTES` (before any XML parsing); each candidate's raw object is rejected if
    it exceeds :data:`MAX_RAW_GRIB_BYTES` (before any temp-file write or wgrib2 invocation) --
    both the same reviewed bounds :func:`services.forecasting.weather_current_cycle_acquisition.
    acquire_current_cycle_raw_grib` enforces.
    """
    try:
        return _select_parse_verified_current_source_inner(
            day, transport=transport, wgrib2_bin=wgrib2_bin
        )
    except Exception as exc:
        # Final safety net, scoped around the entire selection: nothing -- not a bug in this
        # module's own accounting logic, not an exception type nobody anticipated -- may ever
        # escape select_parse_verified_current_source uncaught. Excludes
        # BaseException/SystemExit/KeyboardInterrupt.
        return _result(
            day,
            EVALUATION_BLOCKED,
            f"selection could not be conclusively completed: {type(exc).__name__}: {exc}",
        )


def _result_to_json(result: ParseVerifiedSelectionResult) -> dict[str, Any]:
    selected = result.selected
    return {
        "schema": result.schema,
        "software_version": SOFTWARE_VERSION,
        "day": result.day.isoformat(),
        "classification": result.classification,
        "reason": result.reason,
        "candidate_names": list(result.candidate_names),
        "candidate_errors": list(result.candidate_errors),
        "blocked_candidate_errors": list(result.blocked_candidate_errors),
        "selected_name": result.selected_name,
        "selected_object_url": selected.url if selected else None,
        "wgrib2_executable_sha256": result.wgrib2_executable_sha256,
        "raw_grib_byte_length": selected.raw_byte_length if selected else None,
        "raw_grib_sha256": selected.raw_sha256 if selected else None,
        "extraction_sha256": selected.extraction_sha256 if selected else None,
        "record_count": len(selected.evidence.records) if selected else None,
    }


def _live_transport(url: str) -> bytes:  # pragma: no cover - exercised only by real network use
    return _get(url, cache=None)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(
        description=(
            "Parse-verified multi-candidate current-cycle source selector for "
            "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z. No credentials, no Kalshi, "
            "no economics, no execution. Live network only through the real transport wired "
            "here -- never run this against live network without separate explicit "
            "authorization."
        )
    )
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--wgrib2-bin", type=str, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    day = args.day or datetime.now(UTC).date()
    result = select_parse_verified_current_source(
        day, transport=_live_transport, wgrib2_bin=args.wgrib2_bin
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(_result_to_json(result), sort_keys=True, indent=2) + "\n")
    print(f"classification={result.classification} selected={result.selected_name}")
    print("CREDENTIAL_ACCESS: NO  MUTATION: NO  REQUEST_TYPE: PUBLIC_GET_ONLY")
    return 0 if result.succeeded else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
