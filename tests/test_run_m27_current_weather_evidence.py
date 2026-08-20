"""scripts/run_m27_current_weather_evidence.py -- fake-transport/fake-wgrib2-only tests.

No network, no real wgrib2 binary, no Kalshi/credentials/economics/risk/execution. Proves:
exact-03Z success composes all three records with correct semantics; filename "02" selection
never itself claims 03Z (a parser-internal non-03Z reference still fails closed); ambiguous
source objects never reach wgrib2; raw-byte mutation is caught by the script's own redundant
hash re-check; wrong wgrib2 version fails closed; no cache reuse; and the script has zero
Kalshi/credential/signer/write capability and no path to widen M27D's freshness authority.
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import scripts.run_m27_current_weather_evidence as cli
from scripts.run_m27_current_weather_evidence import compose
from services.forecasting.domain import ForecastError
from services.forecasting.weather_current_cycle_acquisition import (
    AcquiredForecastSource,
    aws_index_url,
)
from tests.test_m27c_weather_calibration_grib import _extraction
from tests.test_weather_current_cycle_acquisition import GOOD_NAME, _index_xml

DAY = date(2026, 8, 20)
FIXED_NOW = datetime(2026, 8, 20, 4, 0, tzinfo=UTC)
RAW_OBJECT_BYTES = b"GRIB" + b"\x00" * 32


def _fake_get(
    *, index_names: tuple[str, ...] = (GOOD_NAME,), object_body: bytes = RAW_OBJECT_BYTES
):
    idx_url = aws_index_url(DAY)

    def fake(url: str, *, cache: Path | None = None) -> bytes:
        assert cache is None, "a fresh live run must never reuse a cache"
        if url == idx_url:
            return _index_xml(index_names)
        return object_body

    return fake


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


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_now", lambda: FIXED_NOW)


# ---------------------------------------------------------------------------
# Exact 03Z success
# ---------------------------------------------------------------------------


def test_compose_exact_03z_success_produces_three_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    result = compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))

    assert result["classification"] == "SUCCESS"
    assert result["records"] is not None
    assert len(result["records"]) == 3
    record_numbers = sorted(record["record_number"] for record in result["records"])
    assert record_numbers == [1, 2, 3]
    for record in result["records"]:
        assert record["forecast_reference_time"] == "2024-06-15T03:00:00+00:00"
        assert record["wgrib2_version"] == "3.8.0"
    # content-addressed: the stamped content_hash must equal an independent recomputation.
    material = {k: v for k, v in result.items() if k != "content_hash"}
    import json

    expected = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert result["content_hash"] == expected


def test_compose_current_evidence_record_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    result = compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))
    by_number = {record["record_number"]: record for record in result["records"]}
    assert by_number[1]["exact_midpoint_seconds"] == 54_000
    assert by_number[2]["exact_midpoint_seconds"] == 140_400
    assert by_number[3]["exact_midpoint_seconds"] == 226_800
    for record in by_number.values():
        assert record["family_identity"] == "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z"
        assert record["research_only"] is True
        assert record["production_influence"] == "0"


# ---------------------------------------------------------------------------
# Filename "02" is discovery only -- never claims 03Z
# ---------------------------------------------------------------------------


def test_filename_02_selection_with_parser_internal_non_03z_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index-level filename filter selects a candidate purely by its "02" hour suffix -- it
    makes no claim about the GRIB-internal reference time. Only the frozen parser can, and here
    it rejects a reference hour of 02 (not 03) even though filename selection succeeded."""
    non_03z_text = _extraction().replace("reference = 20240615030000", "reference = 20240615020000")
    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(non_03z_text))
    with pytest.raises(ForecastError):
        compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))


# ---------------------------------------------------------------------------
# Ambiguous source objects never reach wgrib2
# ---------------------------------------------------------------------------


def test_ambiguous_candidates_never_invoke_wgrib2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_get", _fake_get(index_names=(GOOD_NAME, GOOD_NAME + "b")))

    def must_not_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("wgrib2 must never run for an ambiguous/unresolved acquisition")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    result = compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))
    assert result["classification"] == "AMBIGUOUS_SOURCE_SELECTION"
    assert result["records"] is None


# ---------------------------------------------------------------------------
# Raw-byte mutation detected by the script's own redundant re-verification
# ---------------------------------------------------------------------------


def test_raw_byte_mutation_between_acquisition_and_compose_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_acquire = cli.acquire_current_cycle_raw_grib

    def tampered_acquire(day: date, *, transport: object, clock: object) -> AcquiredForecastSource:
        result = real_acquire(day, transport=transport, clock=clock)
        assert result.succeeded
        # Simulate a mutated raw_body_b64 whose bytes no longer match the stamped raw_sha256 --
        # compose() must independently catch this, not merely trust the acquisition's own field.
        import base64

        tampered_b64 = base64.b64encode(b"GRIB" + b"\xff" * 32).decode("ascii")
        from dataclasses import replace

        return replace(result, raw_body_b64=tampered_b64)

    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(cli, "acquire_current_cycle_raw_grib", tampered_acquire)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    with pytest.raises(ForecastError, match="does not match"):
        compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))


# ---------------------------------------------------------------------------
# Extraction hash is always derived from the exact extraction text used
# ---------------------------------------------------------------------------


def test_extraction_sha256_is_derived_from_the_exact_extraction_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _extraction()
    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(text))
    result = compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))
    assert result["extraction_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    for record in result["records"]:
        assert record["extraction_sha256"] == result["extraction_sha256"]


# ---------------------------------------------------------------------------
# Wrong wgrib2 version fails closed
# ---------------------------------------------------------------------------


def test_wrong_wgrib2_version_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_get", _fake_get())
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run(_extraction(), wgrib2_version="3.7.0")
    )
    with pytest.raises(ForecastError, match="version"):
        compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))


# ---------------------------------------------------------------------------
# No cache reuse for a fresh live run
# ---------------------------------------------------------------------------


def test_no_cache_reuse_for_a_fresh_live_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path | None] = []

    def tracking_get(url: str, *, cache: Path | None = None) -> bytes:
        calls.append(cache)
        idx_url = aws_index_url(DAY)
        return _index_xml((GOOD_NAME,)) if url == idx_url else RAW_OBJECT_BYTES

    monkeypatch.setattr(cli, "_get", tracking_get)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(_extraction()))
    compose(DAY, wgrib2_bin=_fake_wgrib2_bin(tmp_path))
    assert calls == [None, None]


# ---------------------------------------------------------------------------
# Freshness authority: operator cannot widen freshness to make stale evidence eligible
# ---------------------------------------------------------------------------


def test_script_has_no_path_to_m27d_freshness_authority() -> None:
    """This script must never import services.supervised_canary at all -- there is no code
    path here through which an operator could widen or bypass M27D's own MAX_FORECAST_AGE."""
    source = Path("scripts/run_m27_current_weather_evidence.py").read_text()
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not any("supervised_canary" in module for module in imported_modules), imported_modules


# ---------------------------------------------------------------------------
# Zero Kalshi/credential/signer/write capability
# ---------------------------------------------------------------------------

_FORBIDDEN_NAMES = {
    "KalshiAccountClient",
    "RequestSigner",
    "AuthorizationStore",
    "CanaryStore",
    "ProtectedWriteCredentialStore",
}


def test_script_has_no_kalshi_credential_signer_or_write_capability() -> None:
    source = Path("scripts/run_m27_current_weather_evidence.py").read_text()
    tree = ast.parse(source)
    names: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not (names & _FORBIDDEN_NAMES), names & _FORBIDDEN_NAMES
    assert not any("kalshi" in module.lower() for module in imported_modules), imported_modules
    forbidden_modules = {
        "services.opportunity_engine",
        "services.risk_engine",
        "services.production_execution",
        "services.kalshi_account_gateway",
        "services.supervised_canary",
    }
    hits = {m for m in imported_modules if any(m.startswith(f) for f in forbidden_modules)}
    assert not hits, hits
