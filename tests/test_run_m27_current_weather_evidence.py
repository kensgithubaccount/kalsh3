"""Offline behavioral tests for the parse-verified selector operator integration."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

import scripts.run_m27_current_weather_evidence as cli
import scripts.select_parse_verified_current_weather_source as selector
from scripts.run_m27_current_weather_evidence import compose
from scripts.select_parse_verified_current_weather_source import (
    EVALUATION_BLOCKED,
    MULTIPLE_VALID,
    ZERO_VALID,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_current_cycle_acquisition import aws_index_url
from tests.test_m27c_weather_calibration_grib import _extraction
from tests.test_weather_current_cycle_acquisition import GOOD_NAME, _index_xml

DAY = date(2026, 8, 20)
NAME_B = "YGUZ98_KWBN_2026082102"
RAW_OBJECT_BYTES = b"GRIB" + b"\x00" * 32


def _transport(
    names: tuple[str, ...] = (GOOD_NAME,),
    *,
    body: bytes = RAW_OBJECT_BYTES,
    blocked_names: frozenset[str] = frozenset(),
    calls: list[str] | None = None,
):
    index_url = aws_index_url(DAY)

    def transport(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        if url == index_url:
            return _index_xml(names)
        name = url.rsplit("/", 1)[-1]
        if name in blocked_names:
            raise OSError("blocked fake object")
        return body

    return transport


def _fake_subprocess_run(extraction_text: str, *, calls: list[list[str]] | None = None):
    def run(command: object, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if calls is not None:
            calls.append(command)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        return subprocess.CompletedProcess(command, 0, extraction_text, "")

    return run


def _fake_wgrib2_bin(tmp_path: Path) -> str:
    path = tmp_path / "wgrib2"
    path.write_text("fake")
    path.chmod(0o755)
    return str(path)


def _compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport,
    *,
    subprocess_calls: list[list[str]] | None = None,
):
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_run(_extraction(), calls=subprocess_calls)
    )
    return compose(DAY, transport=transport, wgrib2_bin=_fake_wgrib2_bin(tmp_path))


def test_single_candidate_success_builds_weather_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _compose(tmp_path, monkeypatch, _transport())
    assert result["classification"] == "SUCCESS"
    assert len(result["records"]) == 3
    assert result["acquisition"]["selected_name"] == GOOD_NAME


def test_exact_raw_evidence_object_is_passed_to_downstream_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed: list[object] = []
    built: list[object] = []
    real_parser = selector.parse_wgrib2_max_t_evidence
    real_builder = cli.build_current_weather_forecast_evidence

    def parser(*args: object, **kwargs: object):
        evidence = real_parser(*args, **kwargs)
        parsed.append(evidence)
        return evidence

    def builder(evidence: object, **kwargs: object):
        built.append(evidence)
        return real_builder(evidence, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(selector, "parse_wgrib2_max_t_evidence", parser)
    monkeypatch.setattr(cli, "build_current_weather_forecast_evidence", builder)
    result = _compose(tmp_path, monkeypatch, _transport())

    assert result["classification"] == "SUCCESS"
    assert parsed and built
    assert all(item is parsed[0] for item in built)


def test_no_reacquisition_or_downstream_wgrib2_and_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport_calls: list[str] = []
    subprocess_calls: list[list[str]] = []
    parser_calls: list[object] = []
    real_parser = selector.parse_wgrib2_max_t_evidence

    def parser(*args: object, **kwargs: object):
        result = real_parser(*args, **kwargs)
        parser_calls.append(result)
        return result

    monkeypatch.setattr(selector, "parse_wgrib2_max_t_evidence", parser)
    result = _compose(
        tmp_path,
        monkeypatch,
        _transport(calls=transport_calls),
        subprocess_calls=subprocess_calls,
    )
    assert result["classification"] == "SUCCESS"
    assert len(transport_calls) == 2  # index + selected object, exactly once each
    assert len(subprocess_calls) == 2  # resolver identity + selector extraction
    assert len(parser_calls) == 1


def test_success_provenance_is_exact_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _compose(tmp_path, monkeypatch, _transport())
    provenance = result["acquisition"]
    assert provenance["selected_name"] == GOOD_NAME
    assert provenance["selected_object_url"].endswith("/" + GOOD_NAME)
    assert provenance["raw_grib_byte_length"] == len(RAW_OBJECT_BYTES)
    assert provenance["raw_grib_sha256"] == hashlib.sha256(RAW_OBJECT_BYTES).hexdigest()
    assert provenance["extraction_sha256"] == hashlib.sha256(_extraction().encode()).hexdigest()
    assert len(provenance["wgrib2_executable_sha256"]) == 64
    assert provenance["evidence_family_identity"] == ("POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z")
    assert "raw_bytes" not in json.dumps(result)
    assert "raw_body_b64" not in json.dumps(result)


def test_zero_filename_candidates_stop_without_object_wgrib2_or_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    subprocess_calls: list[list[str]] = []
    monkeypatch.setattr(
        cli, "build_current_weather_forecast_evidence", lambda *a, **k: pytest.fail("builder")
    )
    result = _compose(
        tmp_path,
        monkeypatch,
        _transport((), calls=calls),
        subprocess_calls=subprocess_calls,
    )
    assert result["classification"] == ZERO_VALID
    assert result["acquisition"]["classification"] == ZERO_VALID
    assert len(calls) == 1
    assert subprocess_calls == []


def test_zero_content_valid_candidates_stop_without_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_body = b"not-grib"
    transport_calls: list[str] = []
    runner_calls: list[list[str]] = []

    monkeypatch.setattr(
        cli, "build_current_weather_forecast_evidence", lambda *a, **k: pytest.fail("builder")
    )

    def reject_invalid_body(
        command: object, *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "3.8.0\n", "")
        runner_calls.append(command)
        assert Path(command[1]).read_bytes() == raw_body
        raise ForecastError("simulated wgrib2 rejection")

    monkeypatch.setattr(subprocess, "run", reject_invalid_body)
    result = compose(
        DAY,
        transport=_transport(body=raw_body, calls=transport_calls),
        wgrib2_bin=_fake_wgrib2_bin(tmp_path),
    )
    assert result["classification"] == ZERO_VALID
    assert result["records"] is None
    assert len(transport_calls) == 2
    assert len(runner_calls) == 1
    assert raw_body.decode() not in json.dumps(result)


def test_multiple_valid_candidates_stop_without_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "build_current_weather_forecast_evidence", lambda *a, **k: pytest.fail("builder")
    )
    result = _compose(tmp_path, monkeypatch, _transport((GOOD_NAME, NAME_B)))
    assert result["classification"] == MULTIPLE_VALID
    assert result["acquisition"]["selected_name"] is None
    assert result["records"] is None


def test_blocked_candidate_propagates_without_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "build_current_weather_forecast_evidence", lambda *a, **k: pytest.fail("builder")
    )
    result = _compose(
        tmp_path,
        monkeypatch,
        _transport((GOOD_NAME, NAME_B), blocked_names=frozenset({NAME_B})),
    )
    assert result["classification"] == EVALUATION_BLOCKED
    assert result["acquisition"]["classification"] == EVALUATION_BLOCKED
    assert result["records"] is None


def test_candidate_accounting_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def impossible(*args: object, **kwargs: object):
        raise selector.CandidateAccountingError("impossible test state")

    monkeypatch.setattr(selector, "_partition_outcomes", impossible)
    result = _compose(tmp_path, monkeypatch, _transport())
    assert result["classification"] == EVALUATION_BLOCKED
    assert result["records"] is None


def test_reversed_candidate_order_is_equivalent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _compose(tmp_path, monkeypatch, _transport((GOOD_NAME, NAME_B)))
    second = _compose(tmp_path, monkeypatch, _transport((NAME_B, GOOD_NAME)))
    assert first["classification"] == second["classification"] == MULTIPLE_VALID
    assert first["acquisition"]["candidate_names"] == second["acquisition"]["candidate_names"]


def test_compose_requires_transport_and_only_cli_wires_public_adapter() -> None:
    parameter = inspect.signature(compose).parameters["transport"]
    assert parameter.default is inspect.Parameter.empty
    source = Path("scripts/run_m27_current_weather_evidence.py").read_text()
    assert "compose(day, transport=_public_transport" in source
    assert "_get(url, cache=None)" in source


def test_existing_top_level_output_keys_remain_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _compose(tmp_path, monkeypatch, _transport())
    assert {
        "schema",
        "software_version",
        "classification",
        "reason",
        "acquisition",
        "wgrib2_executable_sha256",
        "extraction_sha256",
        "records",
        "content_hash",
    } <= set(result)


def test_operator_has_no_account_credential_store_signer_or_mutation_authority() -> None:
    source = Path("scripts/run_m27_current_weather_evidence.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = (
        "kalshi",
        "production_execution",
        "kalshi_account_gateway",
        "risk_engine",
        "supervised_canary",
        "store",
        "credentials",
        "signer",
    )
    assert not any(any(token in module.lower() for token in forbidden) for module in imported)
    assert not any(token in source.lower() for token in ("create_envelope", "order", "arm", "burn"))
