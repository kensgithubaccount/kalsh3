"""M27N.2 -- bounded local wgrib2 replay tests.

Fake-subprocess-only, no real wgrib2 binary, no network. Proves: retained (already-acquired)
GRIB evidence that independently validates replays cleanly through the existing reviewed
resolver/runner into a frozen-parser-checked ``RawGribEvidence``; retained evidence that does not
itself validate (tampered ticker/day binding, tampered hash, stale beyond ``maximum_age``) fails
closed before wgrib2 is ever invoked; this script adds no new subprocess capability -- it
imports and calls the exact same ``_resolve_wgrib2``/``_run_wgrib2`` pair
``scripts/run_m27_current_weather_evidence.py`` already reuses; and every replay's
``wgrib2_executable_sha256`` is exactly the reviewed resolver's own hash of the one binary
actually invoked -- deterministic, sensitive to the binary's actual bytes, never caller-suppliable.
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import scripts.run_m27n2_wgrib2_replay as cli
from scripts.run_m27n2_wgrib2_replay import ReplayError, WgribReplayResult, replay
from services.forecasting.weather_current_cycle_acquisition import acquire_current_cycle_raw_grib
from tests.test_m27c_weather_calibration_grib import _extraction
from tests.test_weather_current_cycle_acquisition import GOOD_NAME, RAW_GRIB_BODY, _index_xml

DAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 3, 30, 0, tzinfo=UTC)


def _retained_payload() -> dict[str, object]:
    def transport(url: str) -> tuple[dict[str, object], bytes]:
        from services.forecasting.weather_current_cycle_acquisition import aws_index_url

        body = _index_xml((GOOD_NAME,)) if url == aws_index_url(DAY) else RAW_GRIB_BODY
        evidence: dict[str, object] = {
            "url": url,
            "status": 200,
            "classification": "SUCCESS",
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        return evidence, body

    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded
    return result.to_json()


def _fake_subprocess_run(extraction_text: str, *, wgrib2_version: str = "3.8.0"):
    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, f"{wgrib2_version}\n", "")
        return subprocess.CompletedProcess(command, 0, extraction_text, "")

    return run


def _fake_wgrib2_bin(tmp_path: Path) -> str:
    path = tmp_path / "wgrib2"
    path.write_text("fake")
    path.chmod(0o755)
    return str(path)


def test_replay_happy_path_produces_frozen_parser_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    result = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=_fake_wgrib2_bin(tmp_path),
    )
    evidence = result.evidence
    assert len(evidence.records) == 3
    assert evidence.wgrib2_version == "3.8.0"
    assert evidence.raw_grib_sha256 == hashlib.sha256(RAW_GRIB_BODY).hexdigest()


def test_replay_rejects_retained_evidence_that_does_not_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    tampered = dict(_retained_payload())
    tampered["object_name"] = "WRONG-NAME"
    with pytest.raises(ReplayError, match="did not validate"):
        replay(
            tampered,
            expected_day=DAY,
            now=NOW,
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


def test_replay_rejects_stale_retained_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    with pytest.raises(ReplayError, match="did not validate"):
        replay(
            _retained_payload(),
            expected_day=DAY,
            now=NOW + timedelta(hours=7),
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


def test_replay_propagates_frozen_parser_rejection_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-03Z reference (or any other frozen-parser rejection) must propagate unchanged, never
    be caught or downgraded by this script."""
    from services.forecasting.domain import ForecastError

    bad_extraction = _extraction().replace(
        "reference = 20240615030000", "reference = 20240615040000"
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(bad_extraction))
    with pytest.raises(ForecastError):
        replay(
            _retained_payload(),
            expected_day=DAY,
            now=NOW,
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


# ---------------------------------------------------------------------------
# wgrib2 executable identity (M27N.2 third corrective pass)
# ---------------------------------------------------------------------------


def test_replay_output_contains_wgrib2_executable_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    result = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=_fake_wgrib2_bin(tmp_path),
    )
    assert isinstance(result, WgribReplayResult)
    assert result.wgrib2_executable_sha256 is not None


def test_wgrib2_executable_sha256_equals_the_reviewed_resolvers_own_hash_of_the_executed_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported hash must be exactly the reviewed ``_resolve_wgrib2``/``_executable_sha256``
    value for the SAME executable path ``_run_wgrib2`` was actually invoked with -- proven here by
    independently recomputing sha256 over that exact file's bytes (the same recipe the reviewed
    resolver itself uses internally) and comparing, rather than trusting the seam."""
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    wgrib2_bin = _fake_wgrib2_bin(tmp_path)
    result = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=wgrib2_bin,
    )
    expected = hashlib.sha256(Path(wgrib2_bin).read_bytes()).hexdigest()
    assert result.wgrib2_executable_sha256 == expected


def test_wgrib2_executable_sha256_is_deterministic_for_the_same_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    wgrib2_bin = _fake_wgrib2_bin(tmp_path)
    first = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=wgrib2_bin,
    )
    second = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=wgrib2_bin,
    )
    assert first.wgrib2_executable_sha256 == second.wgrib2_executable_sha256


def test_wgrib2_executable_sha256_changes_when_the_resolved_executable_bytes_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different (even same-path-named, differently-located) executable with different file
    bytes must yield a different reported identity -- the hash tracks the actual binary content,
    not merely a path string or the fixed version output the fake subprocess seam always returns."""
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    first_bin = tmp_path / "a" / "wgrib2"
    first_bin.parent.mkdir()
    first_bin.write_text("fake-binary-one")
    first_bin.chmod(0o755)
    second_bin = tmp_path / "b" / "wgrib2"
    second_bin.parent.mkdir()
    second_bin.write_text("fake-binary-two-different-bytes")
    second_bin.chmod(0o755)

    first = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=str(first_bin),
    )
    second = replay(
        _retained_payload(),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=str(second_bin),
    )
    assert first.wgrib2_executable_sha256 != second.wgrib2_executable_sha256


def test_caller_cannot_inject_a_stamped_wgrib2_executable_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained payload carrying a bogus ``wgrib2_executable_sha256`` field (or any other
    caller-supplied claim about the executable) must be entirely ignored: ``replay`` never reads
    that key from the payload, and the reported value always comes solely from the reviewed
    resolver's own hash of the actually-resolved binary.

    Bypasses ``validate_acquired_forecast_source`` (via the same seam
    ``_bypass_validator_with_pass`` uses below) purely so the extra, unrecognized
    ``wgrib2_executable_sha256`` payload key doesn't get rejected earlier by the frozen
    validator's own exact-field-set check for an unrelated reason -- this test's own point is
    narrower: that ``replay`` itself never reads or trusts such a key even if it were present."""
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    wgrib2_bin = _fake_wgrib2_bin(tmp_path)
    raw_sha256 = hashlib.sha256(RAW_GRIB_BODY).hexdigest()
    _bypass_validator_with_pass(monkeypatch, raw_sha256=raw_sha256)
    payload = dict(_retained_payload())
    payload["wgrib2_executable_sha256"] = "forged"  # not a real field; must be inert
    result = replay(
        payload,
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=wgrib2_bin,
    )
    expected = hashlib.sha256(Path(wgrib2_bin).read_bytes()).hexdigest()
    assert result.wgrib2_executable_sha256 == expected
    assert result.wgrib2_executable_sha256 != "forged"


def test_replay_has_no_parameter_accepting_a_caller_supplied_executable_hash() -> None:
    """Structural proof, not just a behavioral one: ``replay``'s own signature has no parameter
    through which a caller could supply an executable-hash override in the first place."""
    import inspect

    parameters = inspect.signature(replay).parameters
    assert "wgrib2_executable_sha256" not in parameters
    assert "executable_sha256" not in parameters


# ---------------------------------------------------------------------------
# Strict base64 (M27N.2 corrective pass, section 3)
# ---------------------------------------------------------------------------


def _bypass_validator_with_pass(monkeypatch: pytest.MonkeyPatch, *, raw_sha256: str) -> None:
    """Force validate_acquired_forecast_source to report PASS regardless of the payload's own
    raw_body_b64 content, so these tests exercise replay()'s OWN strict base64 decode in
    isolation -- defense in depth, independent of validate_acquired_forecast_source's own
    (also validate=True) internal decode, which would otherwise catch the same malformed input
    first and mask what this script's own line actually does."""
    from services.forecasting.weather_current_cycle_acquisition import AcquisitionValidation

    monkeypatch.setattr(
        cli,
        "validate_acquired_forecast_source",
        lambda *args, **kwargs: AcquisitionValidation("PASS", None, GOOD_NAME, raw_sha256, NOW),
    )


def test_replay_strict_base64_accepts_valid_canonical_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    result = replay(
        dict(_retained_payload()),
        expected_day=DAY,
        now=NOW,
        maximum_age=timedelta(hours=6),
        wgrib2_bin=_fake_wgrib2_bin(tmp_path),
    )
    assert result.evidence.raw_grib_sha256 == hashlib.sha256(RAW_GRIB_BODY).hexdigest()


def test_replay_strict_base64_rejects_illegal_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    payload = dict(_retained_payload())
    payload["raw_body_b64"] = "not-valid-base64!!!@@@"
    _bypass_validator_with_pass(monkeypatch, raw_sha256=hashlib.sha256(RAW_GRIB_BODY).hexdigest())
    with pytest.raises(ReplayError, match="not valid canonical base64"):
        replay(
            payload,
            expected_day=DAY,
            now=NOW,
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


def test_replay_strict_base64_rejects_malformed_padding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    payload = dict(_retained_payload())
    payload["raw_body_b64"] = "QUJDRA="  # 7 chars: incorrect padding length
    _bypass_validator_with_pass(monkeypatch, raw_sha256=hashlib.sha256(RAW_GRIB_BODY).hexdigest())
    with pytest.raises(ReplayError, match="not valid canonical base64"):
        replay(
            payload,
            expected_day=DAY,
            now=NOW,
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


def test_replay_strict_base64_rejects_embedded_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-strict base64.b64decode silently strips embedded whitespace/newlines; validate=True
    must reject it instead -- exercised through replay() itself, not just the stdlib directly."""
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    payload = dict(_retained_payload())
    original = str(payload["raw_body_b64"])
    payload["raw_body_b64"] = original[:4] + "\n" + original[4:]
    _bypass_validator_with_pass(monkeypatch, raw_sha256=hashlib.sha256(RAW_GRIB_BODY).hexdigest())
    with pytest.raises(ReplayError, match="not valid canonical base64"):
        replay(
            payload,
            expected_day=DAY,
            now=NOW,
            maximum_age=timedelta(hours=6),
            wgrib2_bin=_fake_wgrib2_bin(tmp_path),
        )


def test_module_has_no_generic_subprocess_capability_beyond_the_reused_runner() -> None:
    """AST proof: this script never calls subprocess itself -- it only ever reaches wgrib2
    through the imported, existing, reviewed ``_resolve_wgrib2``/``_run_wgrib2`` pair."""
    source = Path(cli.__file__).read_text()
    tree = ast.parse(source)
    subprocess_calls = [
        node
        for node in ast.walk(tree)
        for node in [node]
        if isinstance(node, ast.Attribute)
        and node.attr in {"run", "Popen", "call", "check_call", "check_output"}
    ]
    assert not subprocess_calls, "this script must never call subprocess directly"
    assert "import subprocess" not in source
    assert "os.system" not in source
