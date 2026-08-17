from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.collect_m27c_weather_calibration_coverage import (
    _resolve_wgrib2,
    _validate_public_url,
)
from services.forecasting.domain import ForecastError


def _fake_executable(tmp_path: Path) -> Path:
    path = tmp_path / "wgrib2"
    path.write_text("fake")
    path.chmod(0o755)
    return path


def test_explicit_wgrib2_path_requires_reported_reviewed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_executable(tmp_path)

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 8, "3.8.0\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    resolved, digest = _resolve_wgrib2(str(executable))
    assert resolved == str(executable)
    assert digest is not None


def test_explicit_wgrib2_path_rejects_missing_nonexecutable_and_wrong_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ForecastError, match="executable regular"):
        _resolve_wgrib2(str(tmp_path / "missing"))
    non_executable = tmp_path / "not-executable"
    non_executable.write_text("fake")
    with pytest.raises(ForecastError, match="executable regular"):
        _resolve_wgrib2(str(non_executable))
    executable = _fake_executable(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "3.7.0\n", ""),
    )
    with pytest.raises(ForecastError, match=r"reviewed 3\.8\.0"):
        _resolve_wgrib2(str(executable))


def test_wgrib2_resolution_falls_back_to_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.collect_m27c_weather_calibration_coverage.shutil.which",
        lambda name: "/approved/wgrib2",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 8, "3.8.0\n", ""),
    )
    resolved, _ = _resolve_wgrib2(None)
    assert resolved == "/approved/wgrib2"


@pytest.mark.parametrize(
    "url",
    (
        "https://user@www.ncei.noaa.gov/thredds/catalog.xml",
        "https://user:pass@www.ncei.noaa.gov/thredds/catalog.xml",
        "https://:pass@www.ncei.noaa.gov/thredds/catalog.xml",
        "https://www.ncei.noaa.gov:8443/thredds/catalog.xml",
        "https://www.ncei.noaa.gov:bad/thredds/catalog.xml",
        "https://www.ncei.noaa.gov.evil.example/thredds/catalog.xml",
    ),
)
def test_public_url_rejects_forbidden_authority_material(url: str) -> None:
    with pytest.raises(ForecastError):
        _validate_public_url(url)


def test_public_url_accepts_exact_allowlisted_https_authority() -> None:
    parsed = _validate_public_url("https://www.ncei.noaa.gov/thredds/catalog.xml")
    assert parsed.hostname == "www.ncei.noaa.gov"
    assert parsed.port is None
