"""scripts/select_parse_verified_current_weather_source.py -- fake-transport/fake-wgrib2-only
tests.

No network, no real wgrib2 binary, no Kalshi/credentials/economics/risk/execution. Proves, on top
of everything prior passes covered:

* a structurally malformed transport response (wrong type, non-bytes, a mutable
  bytearray/memoryview) is EVALUATION_BLOCKED, never an uncaught crash and never CONTENT_INVALID;
* an unexpected exception at ANY operational stage (transport, index parsing/enumeration, wgrib2
  resolution, raw-byte hashing, the frozen parser/validator) is EVALUATION_BLOCKED and can never
  escape ``select_parse_verified_current_source`` uncaught -- the function has a final top-level
  exception boundary in addition to every per-stage one;
* candidate identity accounting is verified BY IDENTITY (a multiset/Counter comparison), not
  merely by count -- a duplicated outcome for one candidate while another is silently omitted is
  caught even though the bucket counts still sum correctly;
* status-specific CandidateOutcome field invariants are structurally enforced: CONTENT_VALID
  requires a complete SelectedSourceProvenance, and no other status may carry one;
* SUCCESS returns one frozen SelectedSourceProvenance binding the exact validated name, URL,
  immutable raw bytes, length, both hashes, extraction text, and evidence -- with its own
  constructor re-deriving the length/hashes from the retained bytes/text rather than trusting
  supplied values, so a SUCCESS result can never be silently detached from what was actually
  validated;
* a candidate-controlled object name cannot alter the requested URL's scheme, host, path, query,
  or fragment -- a strict allowlisted filename grammar rejects every attempted attack shape before
  any URL is constructed, let alone handed to transport.
"""

from __future__ import annotations

import ast
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

import scripts.select_parse_verified_current_weather_source as selector
from scripts.select_parse_verified_current_weather_source import (
    EVALUATION_BLOCKED,
    MULTIPLE_VALID,
    SUCCESS,
    ZERO_VALID,
    CandidateAccountingError,
    CandidateOutcome,
    CandidateStatus,
    SelectedSourceProvenance,
    UnsafeCandidateNameError,
    _safe_object_url,
    candidate_names_from_index,
    select_parse_verified_current_source,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_current_cycle_acquisition import (
    MAX_INDEX_BYTES,
    MAX_RAW_GRIB_BYTES,
    SourceAcquisitionError,
    aws_index_url,
    object_url,
    select_candidate_object_name,
)
from tests.test_m27c_weather_calibration_grib import _extraction
from tests.test_weather_current_cycle_acquisition import _index_xml

DAY = date(2026, 8, 21)
NAME_A = "YGUZ98_KWBN_202608210216"
NAME_B = "YGUZ98_KWBN_202608210246"
NAME_C = "YGUZ98_KWBN_202608210230"

GOOD_EXTRACTION = _extraction()
NON_03Z_EXTRACTION = _extraction().replace(
    "reference = 20240615030000", "reference = 20240615020000"
)


def test_software_version_is_final_frozen_value() -> None:
    assert selector.SOFTWARE_VERSION == (
        "kalsh3.scripts.select_parse_verified_current_weather_source/3"
    )


def test_serialized_json_emits_exact_software_version() -> None:
    result = selector._result(DAY, ZERO_VALID, "no candidates")
    serialized = json.dumps(selector._result_to_json(result))
    assert json.loads(serialized)["software_version"] == (
        "kalsh3.scripts.select_parse_verified_current_weather_source/3"
    )


def test_production_module_has_no_stale_freeze_markers() -> None:
    source = Path("scripts/select_parse_verified_current_weather_source.py").read_text()
    for marker in ("UNREVIEWED", "NOT FROZEN", "DEVELOPMENT ONLY"):
        assert marker not in source


def _object_bytes(name: str, *, padding: int = 16) -> bytes:
    # GRIB2-magic-prefixed, per-candidate-distinguishable stub bytes -- never real GRIB content;
    # the fake wgrib2 below reads this marker back out of the snapshot file to pick which
    # extraction text to return for which candidate, since the real subprocess.run is faked.
    return b"GRIB" + name.encode() + b"\x00" * padding


def _fake_transport(
    index_names: tuple[str, ...],
    *,
    object_bytes: dict[str, bytes] | None = None,
    object_overrides: dict[str, object] | None = None,
    call_log: list[str] | None = None,
):
    idx_url = aws_index_url(DAY)
    bodies = object_bytes or {name: _object_bytes(name) for name in index_names}
    overrides = object_overrides or {}

    def transport(url: str):
        if call_log is not None:
            call_log.append(url)
        if url == idx_url:
            return _index_xml(index_names)
        for name in index_names:
            if url == object_url(DAY, name):
                if name in overrides:
                    value = overrides[name]
                    if isinstance(value, BaseException):
                        raise value
                    return value
                return bodies[name]
        raise AssertionError(f"unexpected transport URL: {url}")

    return transport


def _fake_subprocess_run(
    extraction_by_marker: dict[bytes, str],
    *,
    timeout_markers: set[bytes] = frozenset(),
    nonzero_exit_markers: set[bytes] = frozenset(),
):
    """Reads the raw snapshot bytes wgrib2 would have been given and returns the extraction text
    keyed by which candidate's marker those bytes carry, raises subprocess.TimeoutExpired for a
    marker registered in ``timeout_markers``, or returns a non-zero-returncode
    CompletedProcess for a marker in ``nonzero_exit_markers`` -- letting the REAL, unmodified,
    reviewed ``_run_wgrib2`` (scripts/collect_m27c_weather_calibration_coverage.py) itself raise
    its own ``ForecastError("wgrib2 extraction failed")`` from that returncode check, exactly as
    it would live, rather than this test fabricating that exception directly."""

    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        snapshot_path = Path(command[1])
        raw = snapshot_path.read_bytes()
        for marker in timeout_markers:
            if marker in raw:
                raise subprocess.TimeoutExpired(cmd=command, timeout=20)
        for marker in nonzero_exit_markers:
            if marker in raw:
                return subprocess.CompletedProcess(command, 1, "", "wgrib2: simulated failure")
        for marker, text in extraction_by_marker.items():
            if marker in raw:
                return subprocess.CompletedProcess(command, 0, text, "")
        raise AssertionError(f"no extraction fixture registered for snapshot bytes: {raw!r}")

    return run


def _fake_wgrib2_bin(tmp_path: Path) -> str:
    path = tmp_path / "wgrib2"
    path.write_text("fake")
    path.chmod(0o755)
    return str(path)


def _version_only_run(reason: str):
    """A fake subprocess.run that answers the reviewed resolver's own unconditional "-version"
    identity check (which always runs once, before any candidate is touched) but refuses to run
    wgrib2 for actual extraction -- used by tests proving wgrib2 is never invoked to PROCESS a
    candidate, without also (wrongly) blocking resolution itself."""

    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        raise AssertionError(reason)

    return run


def _select(
    index_names: tuple[str, ...],
    tmp_path: Path,
    *,
    object_bytes: dict[str, bytes] | None = None,
    object_overrides: dict[str, object] | None = None,
    call_log: list[str] | None = None,
):
    return select_parse_verified_current_source(
        DAY,
        transport=_fake_transport(
            index_names,
            object_bytes=object_bytes,
            object_overrides=object_overrides,
            call_log=call_log,
        ),
        wgrib2_bin=_fake_wgrib2_bin(tmp_path),
    )


def _valid_provenance(name: str = NAME_A) -> SelectedSourceProvenance:
    """Build one genuinely internally-consistent SelectedSourceProvenance for direct
    CandidateOutcome/aggregator-level tests, without going through the full transport/wgrib2
    flow."""
    raw = _object_bytes(name)
    extraction_text = GOOD_EXTRACTION
    from scripts.collect_m27c_weather_calibration_coverage import (
        EXTRACTION_POLICY_VERSION,
        WGRIB2_VERSION,
        _sha256,
    )
    from services.forecasting.weather_calibration_grib import (
        POST2020_GRIB_FAMILY,
        parse_wgrib2_max_t_evidence,
    )

    raw_sha256 = _sha256(raw)
    extraction_sha256 = _sha256(extraction_text.encode())
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        family_identity=POST2020_GRIB_FAMILY,
        extraction_policy_version=EXTRACTION_POLICY_VERSION,
        wgrib2_version=WGRIB2_VERSION,
        raw_grib_sha256=raw_sha256,
        extraction_sha256=extraction_sha256,
    )
    return SelectedSourceProvenance(
        name=name,
        url=object_url(DAY, name),
        raw_bytes=raw,
        raw_byte_length=len(raw),
        raw_sha256=raw_sha256,
        extraction_text=extraction_text,
        extraction_sha256=extraction_sha256,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Candidate enumeration mirrors the frozen function exactly (unchanged from prior passes)
# ---------------------------------------------------------------------------


def test_candidate_enumeration_matches_frozen_selector_on_unambiguous_index() -> None:
    index = _index_xml((NAME_A,))
    assert candidate_names_from_index(index) == (NAME_A,)
    assert select_candidate_object_name(index) == NAME_A


def test_candidate_enumeration_lists_both_where_frozen_selector_raises() -> None:
    index = _index_xml((NAME_A, NAME_B))
    assert candidate_names_from_index(index) == (NAME_A, NAME_B)
    with pytest.raises(SourceAcquisitionError):
        select_candidate_object_name(index)


def test_candidate_enumeration_rejects_unsafe_xml_identically() -> None:
    unsafe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY y "z">]><ListBucketResult/>'
    with pytest.raises(SourceAcquisitionError):
        candidate_names_from_index(unsafe)
    with pytest.raises(SourceAcquisitionError):
        select_candidate_object_name(unsafe)


# ---------------------------------------------------------------------------
# Ordinary content-validation outcomes (unchanged semantics from prior passes)
# ---------------------------------------------------------------------------


def test_zero_filename_eligible_candidates_is_zero_valid_without_invoking_wgrib2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("wgrib2 must never run when there are zero filename candidates")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    result = _select((), tmp_path)
    assert result.classification == ZERO_VALID
    assert result.selected is None
    assert result.selected_name is None
    assert result.evidence is None


def test_single_content_valid_candidate_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == SUCCESS
    assert result.selected_name == NAME_A
    assert result.evidence is not None
    assert len(result.evidence.records) == 3
    assert result.candidate_errors == ()
    assert result.blocked_candidate_errors == ()
    assert result.wgrib2_executable_sha256 is not None


def test_one_parser_invalid_one_valid_is_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression items 20: one invalid plus one valid still produces SUCCESS."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): NON_03Z_EXTRACTION, _object_bytes(NAME_B): GOOD_EXTRACTION}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == SUCCESS
    assert result.selected_name == NAME_B
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 1
    assert NAME_A in result.candidate_errors[0]


def test_two_content_valid_candidates_is_multiple_valid_no_tiebreak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression item 21: two valid candidates still produce MULTIPLE_VALID_CANDIDATES."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION, _object_bytes(NAME_B): GOOD_EXTRACTION}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == MULTIPLE_VALID
    assert result.selected is None
    assert result.selected_name is None
    assert result.evidence is None
    assert result.blocked_candidate_errors == ()
    assert result.candidate_names == (NAME_A, NAME_B)


def test_two_candidates_neither_content_valid_is_zero_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): NON_03Z_EXTRACTION, _object_bytes(NAME_B): NON_03Z_EXTRACTION}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == ZERO_VALID
    assert result.selected is None
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 2


# ---------------------------------------------------------------------------
# OPERATIONAL FAIL-CLOSED (regression items 1-10)
# ---------------------------------------------------------------------------


def test_malformed_transport_response_type_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1: malformed transport response type -> EVALUATION_BLOCKED."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run on a malformed transport response"),
    )
    # Transport returns a plain int instead of bytes for the candidate object.
    result = _select((NAME_A,), tmp_path, object_overrides={NAME_A: 12345})
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS
    assert len(result.blocked_candidate_errors) == 1
    assert "malformed" in result.blocked_candidate_errors[0].lower()


def test_malformed_transport_tuple_shape_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 2: a caller mistakenly wiring the OTHER (evidence, body)-tuple transport shape used
    elsewhere in this repository must be blocked, not crash or be treated as content-invalid."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run on a malformed transport response"),
    )
    result = _select(
        (NAME_A,), tmp_path, object_overrides={NAME_A: ({"status": 200}, _object_bytes(NAME_A))}
    )
    assert result.classification == EVALUATION_BLOCKED


def test_transport_returns_non_bytes_payload_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 3: transport returns a str (not bytes) -> EVALUATION_BLOCKED."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run on a non-bytes transport payload"),
    )
    result = _select((NAME_A,), tmp_path, object_overrides={NAME_A: "GRIB not really"})
    assert result.classification == EVALUATION_BLOCKED


def test_transport_returns_mutable_bytearray_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 29 (rejection branch): a mutable bytearray/memoryview payload is rejected outright
    rather than silently accepted as though it were immutable bytes."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run on a bytearray transport payload"),
    )
    result = _select(
        (NAME_A,), tmp_path, object_overrides={NAME_A: bytearray(_object_bytes(NAME_A))}
    )
    assert result.classification == EVALUATION_BLOCKED


def test_index_transport_malformed_response_is_blocked(tmp_path: Path) -> None:
    def transport(url: str):
        return None  # malformed for both index and object -- index is fetched first

    result = select_parse_verified_current_source(
        DAY, transport=transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert result.classification == EVALUATION_BLOCKED
    assert "malformed" in (result.reason or "").lower()


def test_unexpected_transport_exception_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4: unexpected transport exception -> EVALUATION_BLOCKED, including a valid competitor
    (item 10: valid competitor plus failure never produces SUCCESS)."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select(
        (NAME_A, NAME_B), tmp_path, object_overrides={NAME_B: RuntimeError("unexpected boom")}
    )
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS
    assert result.selected is None


def test_unexpected_resolver_exception_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 5: unexpected resolver exception -> EVALUATION_BLOCKED (not just ForecastError)."""

    def raising_resolve(*args: object, **kwargs: object) -> object:
        raise KeyError("unexpected resolver failure")

    monkeypatch.setattr(selector, "_resolve_wgrib2", raising_resolve)
    result = _select((NAME_A,), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.selected is None


def test_index_parsing_enumeration_exception_fails_closed(tmp_path: Path) -> None:
    """Item 6: index parsing/enumeration exception -> fail-closed overall result, never an
    uncaught crash out of select_parse_verified_current_source."""

    def transport(url: str) -> bytes:
        if url == aws_index_url(DAY):
            return b"not xml at all <<<>>>"
        raise AssertionError("must not reach object fetch")

    result = select_parse_verified_current_source(
        DAY, transport=transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_unexpected_raw_byte_hashing_exception_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 7: unexpected raw-byte hashing/normalization exception -> EVALUATION_BLOCKED."""

    def raising_sha256(payload: object) -> str:
        raise ValueError("unexpected hashing failure")

    monkeypatch.setattr(selector, "_sha256", raising_sha256)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unreachable"))
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == EVALUATION_BLOCKED


def test_unexpected_frozen_parser_exception_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 8: unexpected frozen-parser exception (not one of the reviewed rejection types) ->
    EVALUATION_BLOCKED, never silently treated as CONTENT_INVALID."""

    def raising_parse(*args: object, **kwargs: object) -> object:
        raise KeyError("unexpected parser failure")

    monkeypatch.setattr(selector, "parse_wgrib2_max_t_evidence", raising_parse)
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == EVALUATION_BLOCKED


def test_unexpected_semantic_validation_exception_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 9: unexpected semantic-validation (KMDW) exception -> EVALUATION_BLOCKED."""

    def raising_validate(*args: object, **kwargs: object) -> None:
        raise KeyError("unexpected validation failure")

    monkeypatch.setattr(selector, "validate_kmdw_points", raising_validate)
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == EVALUATION_BLOCKED


def test_wgrib2_timeout_on_competing_candidate_blocks_never_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 10 (timeout variant): valid competitor + wgrib2 timeout never produces SUCCESS."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION}, timeout_markers={_object_bytes(NAME_B)}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS
    assert result.selected is None


def test_extraction_encoding_failure_blocks_never_succeeds_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unencodable_extraction = "\ud800" + GOOD_EXTRACTION
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION, _object_bytes(NAME_B): unencodable_extraction}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)  # must not raise
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_filesystem_error_on_competing_candidate_blocks_never_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )

    call_count = {"n": 0}
    real_tempdir = selector.tempfile.TemporaryDirectory

    class _FailingTempDir:
        def __enter__(self) -> str:
            raise OSError("disk full (simulated)")

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def flaky_tempdir(*args: object, **kwargs: object):
        call_count["n"] += 1
        if call_count["n"] == 2:  # NAME_B evaluates second (sorted after NAME_A)
            return _FailingTempDir()
        return real_tempdir(*args, **kwargs)

    monkeypatch.setattr(selector.tempfile, "TemporaryDirectory", flaky_tempdir)
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_top_level_safety_net_converts_any_unexpected_exception_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves select_parse_verified_current_source's final top-level exception boundary: even an
    exception from deep inside the accounting layer can never escape as an uncaught crash."""

    def corrupt_partition(*args: object, **kwargs: object) -> object:
        raise CandidateAccountingError("simulated deep corruption")

    monkeypatch.setattr(selector, "_partition_outcomes", corrupt_partition)
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)  # must not raise
    assert result.classification == EVALUATION_BLOCKED


# ---------------------------------------------------------------------------
# WGRIB2 NON-ZERO-EXIT CLASSIFICATION FIDELITY (Corrective Pass 3A)
#
# The reviewed _run_wgrib2 (scripts/collect_m27c_weather_calibration_coverage.py) raises
# ForecastError("wgrib2 extraction failed") ONLY when the wgrib2 subprocess itself completes
# with a non-zero exit code. The frozen historical collector (_collect_post2020_raw_grib) treats
# that exact exception as an ordinary per-candidate content rejection, not a whole-day
# operational abort. These tests prove this module now mirrors that fidelity: the non-zero-exit
# case is CONTENT_INVALID (deterministic, reproducible, participates in ordinary candidate
# accounting), while every OTHER failure at the same call boundary (timeout, resolver failure,
# an unexpected non-ForecastError exception, a filesystem error) remains EVALUATION_BLOCKED.
# ---------------------------------------------------------------------------


def test_wgrib2_nonzero_exit_plus_valid_candidate_is_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1: one _run_wgrib2 non-zero-exit ForecastError plus one valid candidate -> SUCCESS
    selecting the valid candidate, never EVALUATION_BLOCKED."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_B): GOOD_EXTRACTION}, nonzero_exit_markers={_object_bytes(NAME_A)}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == SUCCESS
    assert result.selected_name == NAME_B
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 1
    assert NAME_A in result.candidate_errors[0]


def test_wgrib2_nonzero_exit_plus_valid_candidate_is_order_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 2: reversing the candidate order produces the same SUCCESS result and same selected
    candidate."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_B): GOOD_EXTRACTION}, nonzero_exit_markers={_object_bytes(NAME_A)}
        ),
    )

    def transport_for_order(order: tuple[str, ...]):
        def transport(url: str) -> bytes:
            if url == aws_index_url(DAY):
                return _index_xml(order)
            if url == object_url(DAY, NAME_A):
                return _object_bytes(NAME_A)
            if url == object_url(DAY, NAME_B):
                return _object_bytes(NAME_B)
            raise AssertionError(url)

        return transport

    forward = select_parse_verified_current_source(
        DAY, transport=transport_for_order((NAME_A, NAME_B)), wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    reversed_ = select_parse_verified_current_source(
        DAY, transport=transport_for_order((NAME_B, NAME_A)), wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert forward.classification == reversed_.classification == SUCCESS
    assert forward.selected_name == reversed_.selected_name == NAME_B


def test_wgrib2_nonzero_exit_with_no_valid_candidates_is_zero_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 3: one non-zero-exit ForecastError, no valid candidates -> ZERO_VALID_CANDIDATES."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({}, nonzero_exit_markers={_object_bytes(NAME_A)})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == ZERO_VALID
    assert result.selected is None
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 1


def test_two_wgrib2_nonzero_exits_is_zero_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4: two candidates that both receive the reviewed non-zero-exit ForecastError ->
    ZERO_VALID_CANDIDATES, never EVALUATION_BLOCKED and never MULTIPLE_VALID_CANDIDATES."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {}, nonzero_exit_markers={_object_bytes(NAME_A), _object_bytes(NAME_B)}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == ZERO_VALID
    assert result.selected is None
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 2


def test_wgrib2_timeout_plus_valid_candidate_is_still_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 5: valid candidate + _run_wgrib2 timeout -> EVALUATION_BLOCKED (unaffected by the
    non-zero-exit fidelity fix -- a timeout is a distinct exception type, never ForecastError)."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION}, timeout_markers={_object_bytes(NAME_B)}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_wgrib2_resolver_failure_plus_valid_candidate_is_still_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 6: valid candidate + _resolve_wgrib2 failure -> EVALUATION_BLOCKED. The resolver call
    site is structurally separate from (and runs entirely before) the per-candidate _run_wgrib2
    call site -- its own failures can never be reclassified as CONTENT_INVALID."""

    def raising_resolve(*args: object, **kwargs: object) -> object:
        raise ForecastError("wgrib2 3.8.0 is required for post-2020 raw GRIB collection")

    monkeypatch.setattr(selector, "_resolve_wgrib2", raising_resolve)
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.selected is None


def test_wgrib2_unexpected_runtime_exception_plus_valid_candidate_is_still_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 7: valid candidate + an unexpected (non-ForecastError) exception from _run_wgrib2 ->
    EVALUATION_BLOCKED, never silently reclassified as CONTENT_INVALID."""

    def flaky_run(command: object, *args: object, **kwargs: object):
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        snapshot_path = Path(command[1])
        raw = snapshot_path.read_bytes()
        if _object_bytes(NAME_B) in raw:
            raise RuntimeError("simulated unexpected runner failure")
        return subprocess.CompletedProcess(command, 0, GOOD_EXTRACTION, "")

    monkeypatch.setattr(subprocess, "run", flaky_run)
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_wgrib2_filesystem_failure_plus_valid_candidate_is_still_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 8: valid candidate + filesystem/temp-file failure -> EVALUATION_BLOCKED."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    call_count = {"n": 0}
    real_tempdir = selector.tempfile.TemporaryDirectory

    class _FailingTempDir:
        def __enter__(self) -> str:
            raise OSError("disk full (simulated)")

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def flaky_tempdir(*args: object, **kwargs: object):
        call_count["n"] += 1
        if call_count["n"] == 2:  # NAME_B evaluates second (sorted after NAME_A)
            return _FailingTempDir()
        return real_tempdir(*args, **kwargs)

    monkeypatch.setattr(selector.tempfile, "TemporaryDirectory", flaky_tempdir)
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_wgrib2_nonzero_exit_outcome_satisfies_status_specific_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 9: the CONTENT_INVALID outcome produced for a non-zero wgrib2 exit satisfies all the
    same status-specific CandidateOutcome invariants as any other CONTENT_INVALID rejection --
    no provenance, a non-None diagnostic, constructible without raising."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({}, nonzero_exit_markers={_object_bytes(NAME_A)})
    )
    outcome = selector._evaluate_candidate(
        NAME_A,
        DAY,
        transport=_fake_transport((NAME_A,)),
        wgrib2_executable=_fake_wgrib2_bin(tmp_path),
    )
    assert outcome.status is CandidateStatus.CONTENT_INVALID
    assert outcome.provenance is None
    assert outcome.error is not None
    assert outcome.evidence is None
    assert outcome.raw_sha256 is None


def test_wgrib2_nonzero_exit_classification_does_not_depend_on_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 10: classification is determined by WHICH OPERATION raised (the _run_wgrib2 call
    boundary), never by the ForecastError's message text -- proven by changing the diagnostic
    text entirely and confirming the outcome is still CONTENT_INVALID."""

    def raising_run_wgrib2(*args: object, **kwargs: object) -> str:
        raise ForecastError("a completely different diagnostic string, not the usual one")

    monkeypatch.setattr(selector, "_run_wgrib2", raising_run_wgrib2)
    outcome = selector._evaluate_candidate(
        NAME_A,
        DAY,
        transport=_fake_transport((NAME_A,)),
        wgrib2_executable=_fake_wgrib2_bin(tmp_path),
    )
    assert outcome.status is CandidateStatus.CONTENT_INVALID
    assert outcome.provenance is None
    assert "a completely different diagnostic string" in (outcome.error or "")


# ---------------------------------------------------------------------------
# SIZE BOUNDS (preserved from prior passes -- reconfirmed unchanged by this pass)
# ---------------------------------------------------------------------------


def test_oversized_index_fails_before_any_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run when the index itself is oversized"),
    )
    oversized_index = _index_xml((NAME_A,)) + b" " * (MAX_INDEX_BYTES + 1)
    assert len(oversized_index) > MAX_INDEX_BYTES

    def transport(url: str) -> bytes:
        if url == aws_index_url(DAY):
            return oversized_index
        raise AssertionError(
            "candidate enumeration must never be attempted from an oversized index"
        )

    result = select_parse_verified_current_source(
        DAY, transport=transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert result.classification == EVALUATION_BLOCKED
    assert result.candidate_names == ()
    assert "bounded size" in (result.reason or "")


def test_index_exactly_at_bound_behaves_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    padded = _index_xml((NAME_A,))
    padded = padded + b" " * (MAX_INDEX_BYTES - len(padded))
    assert len(padded) == MAX_INDEX_BYTES
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )

    def transport(url: str) -> bytes:
        if url == aws_index_url(DAY):
            return padded
        if url == object_url(DAY, NAME_A):
            return _object_bytes(NAME_A)
        raise AssertionError(url)

    result = select_parse_verified_current_source(
        DAY, transport=transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert result.classification == SUCCESS
    assert result.selected_name == NAME_A


def test_oversized_raw_grib_never_reaches_wgrib2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the frozen sibling's own OVERSIZED_BODY classification exactly: a deterministic,
    reproducible CONTENT_INVALID rejection of this one object, never an operational fault."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _version_only_run("wgrib2 must never run on an oversized raw GRIB object"),
    )
    oversized_object = b"GRIB" + b"\x00" * MAX_RAW_GRIB_BYTES
    assert len(oversized_object) > MAX_RAW_GRIB_BYTES
    result = _select((NAME_A,), tmp_path, object_bytes={NAME_A: oversized_object})
    assert result.classification == ZERO_VALID
    assert len(result.candidate_errors) == 1
    assert "bounded size" in result.candidate_errors[0]
    assert result.blocked_candidate_errors == ()


def test_size_bound_cannot_manufacture_a_false_unique_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three candidates -- one genuinely content-valid, one oversized (deterministic
    CONTENT_INVALID), one parser-rejected non-03Z (also deterministic CONTENT_INVALID). The size
    rejection participates in the same deterministic accounting as any other content rejection."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION, _object_bytes(NAME_C): NON_03Z_EXTRACTION}
        ),
    )
    oversized = b"GRIB" + b"\x00" * MAX_RAW_GRIB_BYTES
    result = _select(
        (NAME_A, NAME_B, NAME_C),
        tmp_path,
        object_bytes={
            NAME_A: _object_bytes(NAME_A),
            NAME_B: oversized,
            NAME_C: _object_bytes(NAME_C),
        },
    )
    assert result.classification == SUCCESS
    assert result.selected_name == NAME_A
    assert result.blocked_candidate_errors == ()
    assert len(result.candidate_errors) == 2


# ---------------------------------------------------------------------------
# IDENTITY ACCOUNTING (regression items 11-21)
# ---------------------------------------------------------------------------


def test_duplicate_outcome_with_omitted_candidate_fails_closed() -> None:
    """Item 11: outcome for candidate A twice while candidate B is omitted fails closed, even
    though the bucket counts still sum to the expected total."""
    a1 = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad-1")
    a2 = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad-2")
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A, NAME_B), (a1, a2))


def test_outcome_with_unexpected_candidate_name_fails_closed() -> None:
    """Item 12: an outcome naming a candidate that was never enumerated fails closed."""
    real = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad")
    substituted = CandidateOutcome(
        "NOT_AN_ENUMERATED_CANDIDATE", CandidateStatus.CONTENT_INVALID, None, "bad"
    )
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A,), (substituted,))
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A, NAME_B), (real, substituted))


def test_missing_candidate_outcome_fails_closed() -> None:
    """Item 13: fewer outcomes than enumerated candidates fails closed."""
    a = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_VALID, _valid_provenance(NAME_A), None)
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A, NAME_B), (a,))


def test_extra_candidate_outcome_fails_closed() -> None:
    """Item 14: more outcomes than enumerated candidates fails closed."""
    a = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad")
    b = CandidateOutcome(NAME_B, CandidateStatus.CONTENT_INVALID, None, "bad")
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A,), (a, b))


def test_every_enumerated_candidate_represented_exactly_once_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 15, end to end."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): GOOD_EXTRACTION, _object_bytes(NAME_B): NON_03Z_EXTRACTION}
        ),
    )
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == SUCCESS
    assert set(result.candidate_names) == {NAME_A, NAME_B}


def test_malformed_content_valid_outcome_without_evidence_is_rejected() -> None:
    """Item 16: a CONTENT_VALID outcome missing its required provenance/evidence is rejected at
    construction, before it could ever be mistaken for a genuinely valid candidate."""
    with pytest.raises(CandidateAccountingError):
        CandidateOutcome(NAME_A, CandidateStatus.CONTENT_VALID, None, None)


def test_content_invalid_outcome_cannot_carry_provenance() -> None:
    """CONTENT_INVALID must not be semantically indistinguishable from CONTENT_VALID -- it can
    never carry a provenance object."""
    with pytest.raises(CandidateAccountingError):
        CandidateOutcome(
            NAME_A, CandidateStatus.CONTENT_INVALID, _valid_provenance(NAME_A), "should be invalid"
        )


def test_evaluation_blocked_outcome_cannot_carry_provenance() -> None:
    with pytest.raises(CandidateAccountingError):
        CandidateOutcome(
            NAME_A,
            CandidateStatus.EVALUATION_BLOCKED,
            _valid_provenance(NAME_A),
            "should be blocked",
        )


def test_provenance_for_wrong_candidate_name_is_rejected() -> None:
    """A CONTENT_VALID outcome whose provenance names a DIFFERENT candidate is rejected --
    guards against a substitution bug even within a single, otherwise well-formed outcome."""
    with pytest.raises(CandidateAccountingError):
        CandidateOutcome(NAME_A, CandidateStatus.CONTENT_VALID, _valid_provenance(NAME_B), None)


def test_blocked_outcome_with_no_error_detail_is_still_counted_as_blocked() -> None:
    """Item 17: blocked outcome remains blocking with optional diagnostic absent."""
    silent_block = CandidateOutcome(NAME_A, CandidateStatus.EVALUATION_BLOCKED, None, None)
    blocked, valid, invalid = selector._partition_outcomes((NAME_A,), (silent_block,))
    assert blocked == (silent_block,)
    assert valid == ()
    assert invalid == ()


def test_blocked_outcome_with_no_error_detail_still_blocks_the_whole_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    real_evaluate = selector._evaluate_candidate

    def patched(
        name: str, day: date, *, transport: object, wgrib2_executable: str
    ) -> CandidateOutcome:
        if name == NAME_B:
            return CandidateOutcome(name, CandidateStatus.EVALUATION_BLOCKED, None, None)
        return real_evaluate(name, day, transport=transport, wgrib2_executable=wgrib2_executable)

    monkeypatch.setattr(selector, "_evaluate_candidate", patched)
    result = _select((NAME_A, NAME_B), tmp_path)
    assert result.classification == EVALUATION_BLOCKED
    assert result.classification != SUCCESS


def test_unknown_status_fails_closed_at_the_aggregator_even_if_construction_were_bypassed() -> None:
    """Item 18 (status corruption): object.__setattr__ post-construction corruption of status
    cannot create SUCCESS -- the aggregator's own independent check still catches it."""
    outcome = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad")
    object.__setattr__(outcome, "status", "not-a-real-status")
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A,), (outcome,))


def test_post_construction_name_corruption_cannot_create_success() -> None:
    """Item 18 (identity corruption variant): object.__setattr__ corrupting an outcome's name
    after construction breaks identity accounting rather than silently succeeding."""
    outcome = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad")
    object.__setattr__(outcome, "name", "SOMETHING_ELSE_ENTIRELY")
    with pytest.raises(CandidateAccountingError):
        selector._partition_outcomes((NAME_A,), (outcome,))


def test_post_construction_provenance_corruption_cannot_create_success() -> None:
    """Corrupting a CONTENT_INVALID outcome to carry a provenance object after construction
    (bypassing __post_init__ entirely) is exactly the "malformed CONTENT_VALID-shaped state"
    concern -- prove downstream code cannot be fooled by it. Since the aggregator partitions
    strictly by `status` (still CONTENT_INVALID here, untouched), this outcome is correctly
    routed to `invalid`, never `valid` -- the corrupted provenance is inert precisely because
    status, not provenance-presence, drives classification."""
    outcome = CandidateOutcome(NAME_A, CandidateStatus.CONTENT_INVALID, None, "bad")
    object.__setattr__(outcome, "provenance", _valid_provenance(NAME_A))
    _blocked, valid, invalid = selector._partition_outcomes((NAME_A,), (outcome,))
    assert valid == ()
    assert invalid == (outcome,)


def test_reversed_candidate_ordering_produces_identical_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 19."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_subprocess_run(
            {_object_bytes(NAME_A): NON_03Z_EXTRACTION, _object_bytes(NAME_B): GOOD_EXTRACTION}
        ),
    )

    def transport_for_order(order: tuple[str, ...]):
        def transport(url: str) -> bytes:
            if url == aws_index_url(DAY):
                return _index_xml(order)
            if url == object_url(DAY, NAME_A):
                return _object_bytes(NAME_A)
            if url == object_url(DAY, NAME_B):
                return _object_bytes(NAME_B)
            raise AssertionError(url)

        return transport

    forward = select_parse_verified_current_source(
        DAY, transport=transport_for_order((NAME_A, NAME_B)), wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    reversed_ = select_parse_verified_current_source(
        DAY, transport=transport_for_order((NAME_B, NAME_A)), wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert forward.classification == reversed_.classification == SUCCESS
    assert forward.selected_name == reversed_.selected_name == NAME_B
    assert forward.candidate_names == reversed_.candidate_names == (NAME_A, NAME_B)


def test_duplicate_index_keys_are_deduped_before_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A, NAME_A), tmp_path)
    assert result.classification == SUCCESS
    assert result.candidate_names == (NAME_A,)


# ---------------------------------------------------------------------------
# EXACT-OBJECT PROVENANCE (regression items 22-30)
# ---------------------------------------------------------------------------


def test_success_returns_exact_object_name_and_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 22."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == SUCCESS
    assert result.selected is not None
    assert result.selected.name == NAME_A
    assert result.selected.url == object_url(DAY, NAME_A)


def test_success_returns_exact_immutable_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 23 & 24 & 25: exact raw bytes, matching length, matching hash."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    assert result.classification == SUCCESS
    selected = result.selected
    assert selected is not None
    assert isinstance(selected.raw_bytes, bytes)
    assert selected.raw_bytes == _object_bytes(NAME_A)
    assert selected.raw_byte_length == len(selected.raw_bytes)
    from scripts.collect_m27c_weather_calibration_coverage import _sha256

    assert selected.raw_sha256 == _sha256(selected.raw_bytes)


def test_success_returns_matching_extraction_hash_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 26 & 27: extraction hash matches extraction text; evidence matches that extraction."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    result = _select((NAME_A,), tmp_path)
    selected = result.selected
    assert selected is not None
    assert selected.extraction_text == GOOD_EXTRACTION
    from scripts.collect_m27c_weather_calibration_coverage import _sha256

    assert selected.extraction_sha256 == _sha256(selected.extraction_text.encode())
    assert selected.evidence.raw_grib_sha256 == selected.raw_sha256
    assert selected.evidence.extraction_sha256 == selected.extraction_sha256
    assert result.evidence is selected.evidence


def test_no_second_transport_call_occurs_after_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 28: no second transport/acquisition occurs after selection -- exactly one transport
    call for the index and exactly one per candidate object."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    call_log: list[str] = []
    result = _select((NAME_A,), tmp_path, call_log=call_log)
    assert result.classification == SUCCESS
    assert call_log.count(aws_index_url(DAY)) == 1
    assert call_log.count(object_url(DAY, NAME_A)) == 1
    assert len(call_log) == 2


def test_source_changing_after_first_acquisition_cannot_affect_returned_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 30: a simulated source that would return DIFFERENT bytes on a hypothetical second
    call cannot affect the already-returned provenance, because the selector calls transport
    exactly once per candidate and never re-acquires after selection."""
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )
    call_count = {"n": 0}
    first_bytes = _object_bytes(NAME_A)
    changed_bytes = _object_bytes(NAME_A, padding=64)  # a "drifted" source

    def drifting_transport(url: str) -> bytes:
        if url == aws_index_url(DAY):
            return _index_xml((NAME_A,))
        call_count["n"] += 1
        return first_bytes if call_count["n"] == 1 else changed_bytes

    result = select_parse_verified_current_source(
        DAY, transport=drifting_transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path)
    )
    assert result.classification == SUCCESS
    assert result.selected is not None
    assert result.selected.raw_bytes == first_bytes
    assert call_count["n"] == 1  # never re-acquired


def test_selected_source_provenance_rejects_inconsistent_length() -> None:
    from scripts.collect_m27c_weather_calibration_coverage import (
        EXTRACTION_POLICY_VERSION,
        WGRIB2_VERSION,
        _sha256,
    )
    from services.forecasting.weather_calibration_grib import (
        POST2020_GRIB_FAMILY,
        parse_wgrib2_max_t_evidence,
    )

    raw = _object_bytes(NAME_A)
    extraction_text = GOOD_EXTRACTION
    raw_sha256 = _sha256(raw)
    extraction_sha256 = _sha256(extraction_text.encode())
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        family_identity=POST2020_GRIB_FAMILY,
        extraction_policy_version=EXTRACTION_POLICY_VERSION,
        wgrib2_version=WGRIB2_VERSION,
        raw_grib_sha256=raw_sha256,
        extraction_sha256=extraction_sha256,
    )
    with pytest.raises(CandidateAccountingError):
        SelectedSourceProvenance(
            name=NAME_A,
            url=object_url(DAY, NAME_A),
            raw_bytes=raw,
            raw_byte_length=len(raw) + 1,  # inconsistent on purpose
            raw_sha256=raw_sha256,
            extraction_text=extraction_text,
            extraction_sha256=extraction_sha256,
            evidence=evidence,
        )


def test_selected_source_provenance_rejects_mismatched_hash() -> None:
    from scripts.collect_m27c_weather_calibration_coverage import (
        EXTRACTION_POLICY_VERSION,
        WGRIB2_VERSION,
        _sha256,
    )
    from services.forecasting.weather_calibration_grib import (
        POST2020_GRIB_FAMILY,
        parse_wgrib2_max_t_evidence,
    )

    raw = _object_bytes(NAME_A)
    extraction_text = GOOD_EXTRACTION
    raw_sha256 = _sha256(raw)
    extraction_sha256 = _sha256(extraction_text.encode())
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        family_identity=POST2020_GRIB_FAMILY,
        extraction_policy_version=EXTRACTION_POLICY_VERSION,
        wgrib2_version=WGRIB2_VERSION,
        raw_grib_sha256=raw_sha256,
        extraction_sha256=extraction_sha256,
    )
    with pytest.raises(CandidateAccountingError):
        SelectedSourceProvenance(
            name=NAME_A,
            url=object_url(DAY, NAME_A),
            raw_bytes=raw,
            raw_byte_length=len(raw),
            raw_sha256="0" * 64,  # wrong on purpose
            extraction_text=extraction_text,
            extraction_sha256=extraction_sha256,
            evidence=evidence,
        )


def test_selected_source_provenance_rejects_non_bytes_raw() -> None:
    from scripts.collect_m27c_weather_calibration_coverage import (
        EXTRACTION_POLICY_VERSION,
        WGRIB2_VERSION,
        _sha256,
    )
    from services.forecasting.weather_calibration_grib import (
        POST2020_GRIB_FAMILY,
        parse_wgrib2_max_t_evidence,
    )

    extraction_text = GOOD_EXTRACTION
    raw = _object_bytes(NAME_A)
    raw_sha256 = _sha256(raw)
    extraction_sha256 = _sha256(extraction_text.encode())
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        family_identity=POST2020_GRIB_FAMILY,
        extraction_policy_version=EXTRACTION_POLICY_VERSION,
        wgrib2_version=WGRIB2_VERSION,
        raw_grib_sha256=raw_sha256,
        extraction_sha256=extraction_sha256,
    )
    with pytest.raises(CandidateAccountingError):
        SelectedSourceProvenance(
            name=NAME_A,
            url=object_url(DAY, NAME_A),
            raw_bytes=bytearray(raw),  # mutable, must be rejected
            raw_byte_length=len(raw),
            raw_sha256=raw_sha256,
            extraction_text=extraction_text,
            extraction_sha256=extraction_sha256,
            evidence=evidence,
        )


# ---------------------------------------------------------------------------
# SAFE OBJECT URL (regression items 31-40)
# ---------------------------------------------------------------------------


def test_ordinary_approved_candidate_name_constructs_expected_url() -> None:
    """Item 31."""
    assert _safe_object_url(DAY, NAME_A) == object_url(DAY, NAME_A)


@pytest.mark.parametrize(
    ("label", "unsafe_name"),
    [
        ("query", NAME_A + "?x=1"),  # item 32
        ("fragment", NAME_A + "#frag"),  # item 33
        ("percent_traversal", "YGUZ98_KWBN_2026082102%2e%2e"),  # item 34
        ("percent_literal", NAME_A + "%00"),  # item 34
        ("slash", "YGUZ98_KWBN_2026/08210216"),  # item 35
        ("backslash", "YGUZ98_KWBN_2026\\08210216"),  # item 36
        ("dot_segment", "../../../etc/passwd"),  # item 37
        ("absolute_url", "https://evil.example.com/" + NAME_A),  # item 38
        ("whitespace", "YGUZ98_KWBN_2026 08210216"),  # item 39
        ("control_char", NAME_A + "\x00"),  # item 39
        ("unicode_lookalike", "YGUZ98​_KWBN_202608210216"),  # item 39 (zero-width space)
        ("wrong_prefix", "EVIL98_KWBN_202608210216"),
        ("userinfo_like", "user:pass@" + NAME_A),
        ("empty", ""),
        ("dot_only", "."),
    ],
)
def test_unsafe_candidate_name_rejected_before_transport(label: str, unsafe_name: str) -> None:
    with pytest.raises(UnsafeCandidateNameError):
        _safe_object_url(DAY, unsafe_name)


def test_unsafe_candidate_name_never_reaches_transport_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsafe = NAME_A + "?x=1"
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run({_object_bytes(NAME_A): GOOD_EXTRACTION})
    )

    def transport(url: str) -> bytes:
        if url == aws_index_url(DAY):
            return _index_xml((NAME_A, unsafe))
        if "?" in url or unsafe in url:
            raise AssertionError("unsafe candidate name must never reach transport")
        if url == object_url(DAY, NAME_A):
            return _object_bytes(NAME_A)
        raise AssertionError(url)

    # candidate_names_from_index itself would never surface a name containing "?" (it only
    # matches the frozen prefix/hour-suffix shape from real <Key> text), so to exercise the
    # selector's OWN defense-in-depth boundary directly, call _evaluate_candidate for the unsafe
    # name explicitly, exactly as select_parse_verified_current_source would for any enumerated
    # name.
    outcome = selector._evaluate_candidate(
        unsafe, DAY, transport=transport, wgrib2_executable=_fake_wgrib2_bin(tmp_path)
    )
    assert outcome.status is CandidateStatus.CONTENT_INVALID
    assert outcome.provenance is None


def test_final_validated_url_has_no_query_fragment_userinfo_or_unexpected_port() -> None:
    """Item 40."""
    from urllib.parse import urlsplit

    url = _safe_object_url(DAY, NAME_A)
    parsed = urlsplit(url)
    assert parsed.query == ""
    assert parsed.fragment == ""
    assert parsed.username is None
    assert parsed.password is None
    assert parsed.port is None
    assert parsed.scheme == "https"


# ---------------------------------------------------------------------------
# wgrib2 executable resolved once, reused across every candidate (unchanged from prior passes)
# ---------------------------------------------------------------------------


def test_wgrib2_resolved_once_and_reused_across_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_calls = 0
    real_fake = _fake_subprocess_run(
        {_object_bytes(NAME_A): GOOD_EXTRACTION, _object_bytes(NAME_B): NON_03Z_EXTRACTION}
    )

    def counting_run(command: object, *a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        nonlocal version_calls
        if isinstance(command, list) and "-version" in command:
            version_calls += 1
        return real_fake(command, *a, **kw)

    monkeypatch.setattr(subprocess, "run", counting_run)
    _select((NAME_A, NAME_B), tmp_path)
    assert version_calls == 1


# ---------------------------------------------------------------------------
# Zero credentials/Kalshi/signer/store capability; no generic subprocess-spawn capability
# ---------------------------------------------------------------------------

_FORBIDDEN_NAMES = {
    "KalshiAccountClient",
    "RequestSigner",
    "AuthorizationStore",
    "CanaryStore",
    "ProtectedWriteCredentialStore",
}

_PROCESS_SPAWNING_CALLS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
    "system",
    "popen",
    "spawn",
    "spawnv",
    "spawnve",
    "execv",
    "execve",
}


def test_module_has_no_kalshi_credential_or_direct_transport_capability() -> None:
    source = Path("scripts/select_parse_verified_current_weather_source.py").read_text()
    tree = ast.parse(source)
    names: set[str] = set()
    imported_modules: set[str] = set()
    call_targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_targets.add(node.func.attr)
    assert not (names & _FORBIDDEN_NAMES), names & _FORBIDDEN_NAMES
    assert not any("kalshi" in module.lower() for module in imported_modules), imported_modules
    forbidden_modules = {"subprocess", "http.client", "urllib.request", "requests", "os"}
    assert not (imported_modules & forbidden_modules), imported_modules & forbidden_modules
    assert not (call_targets & _PROCESS_SPAWNING_CALLS), call_targets & _PROCESS_SPAWNING_CALLS
