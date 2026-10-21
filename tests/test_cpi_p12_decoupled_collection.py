from __future__ import annotations

import base64
import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import services.forecasting.cpi_p12_decoupled_collection as runtime

NOW = datetime(2026, 10, 21, 16, 30, tzinfo=UTC)
RELEASE = datetime(2026, 11, 10, 13, 30, tzinfo=UTC)


def _write_artifact(path: Path, payload: bytes = b"artifact\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _collector(path: Path, payload: bytes = b"artifact\n"):
    def collect() -> runtime.StageCollection:
        return runtime.StageCollection(artifacts=(_write_artifact(path, payload),))

    return collect


def _expected_failure(reason: str):
    def collect() -> runtime.StageCollection:
        raise runtime.P12ExpectedStageFailure(reason)

    return collect


def _sealed_schedule_receipt(tmp_path: Path) -> tuple[Path, runtime.SealedBLSScheduleExpectation]:
    source = (
        b"<html><title>Schedule of Releases for the Consumer Price Index</title>"
        b"<table><tr><td>October 2026</td><td>Nov. 10, 2026</td>"
        b"<td>08:30 AM</td></tr></table></html>"
    )
    source_sha = hashlib.sha256(source).hexdigest()
    payload = {
        "record_type": runtime.BLS_SCHEDULE_RECEIPT_TYPE,
        "source_locator": runtime.BLS_SCHEDULE_LOCATOR,
        "reference_month": "2026-10",
        "release_at_utc": "2026-11-10T13:30:00Z",
        "raw_body_sha256": source_sha,
        "raw_body_b64": base64.b64encode(source).decode("ascii"),
        "byte_count": len(source),
        "research_only": True,
        "production_influence": "0",
        "scientific_evidence_authorized": False,
        "p12_frozen": False,
    }
    receipt_bytes = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path = tmp_path / "sealed_schedule_receipt.json"
    path.write_bytes(receipt_bytes)
    expectation = runtime.SealedBLSScheduleExpectation(
        reference_month="2026-10",
        release_at_utc=RELEASE,
        source_sha256=source_sha,
        receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
    )
    return path, expectation


def test_presealed_schedule_verification_uses_exact_receipt_and_embedded_bytes(
    tmp_path: Path,
) -> None:
    receipt, expectation = _sealed_schedule_receipt(tmp_path)
    collection = runtime.verify_presealed_bls_schedule_receipt(receipt, expectation)
    assert collection.artifacts == (receipt,)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_month", "2026-09"),
        ("release_at_utc", "2026-11-11T13:30:00Z"),
        ("raw_body_sha256", "0" * 64),
        ("research_only", False),
        ("production_influence", "1"),
        ("scientific_evidence_authorized", True),
        ("p12_frozen", True),
    ],
)
def test_presealed_schedule_tampering_fails_only_schedule_stage(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    receipt, original = _sealed_schedule_receipt(tmp_path)
    payload = json.loads(receipt.read_text())
    payload[field] = value
    tampered = tmp_path / "tampered.json"
    tampered_bytes = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    tampered.write_bytes(tampered_bytes)
    expectation = runtime.SealedBLSScheduleExpectation(
        reference_month=original.reference_month,
        release_at_utc=original.release_at_utc,
        source_sha256=original.source_sha256,
        receipt_sha256=hashlib.sha256(tampered_bytes).hexdigest(),
    )
    with pytest.raises(runtime.P12ExpectedStageFailure):
        runtime.verify_presealed_bls_schedule_receipt(tampered, expectation)


def test_receipt_hash_mismatch_fails_schedule_stage(tmp_path: Path) -> None:
    receipt, expectation = _sealed_schedule_receipt(tmp_path)
    wrong = runtime.SealedBLSScheduleExpectation(
        reference_month=expectation.reference_month,
        release_at_utc=expectation.release_at_utc,
        source_sha256=expectation.source_sha256,
        receipt_sha256="f" * 64,
    )
    with pytest.raises(runtime.P12ExpectedStageFailure, match="receipt SHA-256"):
        runtime.verify_presealed_bls_schedule_receipt(receipt, wrong)


def test_bls_failure_cannot_suppress_kalshi_or_reuters(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    calls: list[str] = []

    def bls() -> runtime.StageCollection:
        calls.append("bls")
        raise runtime.P12ExpectedStageFailure("BLS unavailable")

    def kalshi() -> runtime.StageCollection:
        calls.append("kalshi")
        return runtime.StageCollection(
            artifacts=(_write_artifact(paths.root / "kalshi" / "market.json", b"market"),)
        )

    def reuters() -> runtime.StageCollection:
        calls.append("reuters")
        return runtime.StageCollection(
            artifacts=(_write_artifact(paths.root / "reuters" / "coverage.json", b"reuters"),)
        )

    outcomes = runtime.run_all_independent_stages(
        paths,
        {
            runtime.P12CollectorStage.BLS_SCHEDULE_AUTHORITY: bls,
            runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE: kalshi,
            runtime.P12CollectorStage.REUTERS_ACQUISITION: reuters,
        },
        clock=lambda: NOW,
    )

    assert calls == ["bls", "kalshi", "reuters"]
    assert outcomes["bls_schedule_authority"]["status"] == "FAILURE"
    assert outcomes["kalshi_market_capture"]["status"] == "PASS"
    assert outcomes["reuters_acquisition"]["status"] == "PASS"
    assert (paths.root / "kalshi" / "market.json").read_bytes() == b"market"
    assert (paths.root / "reuters" / "coverage.json").read_bytes() == b"reuters"

    gate = runtime.evaluate_preclose_completeness(paths, clock=lambda: NOW)
    assert gate["decision"] == runtime.P12GateDecision.INCOMPLETE.value
    assert gate["required_stage_states"] == {
        "bls_schedule_authority": "FAILURE",
        "kalshi_market_capture": "PASS",
        "reuters_acquisition": "PASS",
    }


def test_kalshi_failure_cannot_suppress_reuters(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    receipt, expectation = _sealed_schedule_receipt(tmp_path)
    calls: list[str] = []

    def bls() -> runtime.StageCollection:
        calls.append("bls")
        return runtime.verify_presealed_bls_schedule_receipt(receipt, expectation)

    def kalshi() -> runtime.StageCollection:
        calls.append("kalshi")
        raise runtime.P12ExpectedStageFailure("Kalshi public read failed")

    def reuters() -> runtime.StageCollection:
        calls.append("reuters")
        return runtime.StageCollection(
            artifacts=(_write_artifact(paths.root / "reuters" / "coverage.json", b"reuters"),)
        )

    outcomes = runtime.run_all_independent_stages(
        paths,
        {
            runtime.P12CollectorStage.BLS_SCHEDULE_AUTHORITY: bls,
            runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE: kalshi,
            runtime.P12CollectorStage.REUTERS_ACQUISITION: reuters,
        },
        clock=lambda: NOW,
    )

    assert calls == ["bls", "kalshi", "reuters"]
    assert outcomes["bls_schedule_authority"]["status"] == "PASS"
    assert outcomes["kalshi_market_capture"]["status"] == "FAILURE"
    assert outcomes["reuters_acquisition"]["status"] == "PASS"


def test_reuters_failure_preserves_schedule_and_kalshi(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    receipt, expectation = _sealed_schedule_receipt(tmp_path)

    outcomes = runtime.run_all_independent_stages(
        paths,
        {
            runtime.P12CollectorStage.BLS_SCHEDULE_AUTHORITY: lambda: (
                runtime.verify_presealed_bls_schedule_receipt(receipt, expectation)
            ),
            runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE: _collector(
                paths.root / "kalshi" / "market.json", b"market"
            ),
            runtime.P12CollectorStage.REUTERS_ACQUISITION: _expected_failure(
                "Reuters acquisition unavailable"
            ),
        },
        clock=lambda: NOW,
    )

    assert outcomes["bls_schedule_authority"]["status"] == "PASS"
    assert outcomes["kalshi_market_capture"]["status"] == "PASS"
    assert outcomes["reuters_acquisition"]["status"] == "FAILURE"
    assert (paths.root / "kalshi" / "market.json").is_file()


def test_all_pass_is_ready_but_still_non_scientific_and_unfrozen(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    receipt, expectation = _sealed_schedule_receipt(tmp_path)
    runtime.run_all_independent_stages(
        paths,
        {
            runtime.P12CollectorStage.BLS_SCHEDULE_AUTHORITY: lambda: (
                runtime.verify_presealed_bls_schedule_receipt(receipt, expectation)
            ),
            runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE: _collector(
                paths.root / "kalshi" / "market.json", b"market"
            ),
            runtime.P12CollectorStage.REUTERS_ACQUISITION: _collector(
                paths.root / "reuters" / "coverage.json", b"reuters"
            ),
        },
        clock=lambda: NOW,
    )
    gate = runtime.evaluate_preclose_completeness(paths, clock=lambda: NOW)
    assert gate["decision"] == runtime.P12GateDecision.READY.value
    assert set(gate["required_stage_states"].values()) == {"PASS"}
    assert gate["collectors_invoked_by_gate"] is False
    assert gate["scientific_evidence_authorized"] is False
    assert gate["p12_frozen"] is False
    assert gate["production_influence"] == "0"


def test_preclose_gate_with_missing_stages_does_not_run_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")

    def forbidden(*args, **kwargs):
        raise AssertionError("preclose gate invoked a collector")

    monkeypatch.setattr(runtime, "run_independent_stage", forbidden)
    gate = runtime.evaluate_preclose_completeness(paths, clock=lambda: NOW)
    assert gate["decision"] == runtime.P12GateDecision.INCOMPLETE.value
    assert set(gate["required_stage_states"].values()) == {"MISSING"}


def test_existing_stage_receipt_is_idempotent_and_does_not_rerun_collector(
    tmp_path: Path,
) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    stage = runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE
    first = runtime.run_independent_stage(
        paths,
        stage,
        _collector(paths.root / "kalshi" / "market.json"),
        clock=lambda: NOW,
    )

    def forbidden() -> runtime.StageCollection:
        raise AssertionError("existing stage receipt caused collector rerun")

    second = runtime.run_independent_stage(paths, stage, forbidden, clock=lambda: NOW)
    assert second == first


def test_only_expected_operational_failure_is_swallowed(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    stage = runtime.P12CollectorStage.REUTERS_ACQUISITION

    def programmer_error() -> runtime.StageCollection:
        raise runtime.P12IsolationAuthorityError("bad collector contract")

    with pytest.raises(runtime.P12IsolationAuthorityError, match="bad collector contract"):
        runtime.run_independent_stage(paths, stage, programmer_error, clock=lambda: NOW)
    assert not paths.stage_receipt(stage).exists()


def test_stage_receipt_mutation_fails_validation(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    stage = runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE
    runtime.run_independent_stage(
        paths,
        stage,
        _collector(paths.root / "kalshi" / "market.json"),
        clock=lambda: NOW,
    )
    payload = json.loads(paths.stage_receipt(stage).read_text())
    payload["scientific_evidence_authorized"] = True
    paths.stage_receipt(stage).write_text(json.dumps(payload))
    with pytest.raises(runtime.P12IsolationAuthorityError, match="scientific authority"):
        runtime.validate_stage_receipt(paths, stage)


def test_stage_artifact_mutation_blocks_gate(tmp_path: Path) -> None:
    paths = runtime.P12IsolationPaths(tmp_path / "run")
    stage = runtime.P12CollectorStage.KALSHI_MARKET_CAPTURE
    artifact = paths.root / "kalshi" / "market.json"
    runtime.run_independent_stage(
        paths,
        stage,
        _collector(artifact, b"original"),
        clock=lambda: NOW,
    )
    artifact.write_bytes(b"mutated")
    with pytest.raises(runtime.P12IsolationAuthorityError, match="SHA-256 changed"):
        runtime.validate_stage_receipt(paths, stage)


def test_runtime_contains_no_direct_transport_or_p11_dependency() -> None:
    source = inspect.getsource(runtime)
    forbidden = (
        "http.client",
        "requests",
        "urllib.request",
        "public_read",
        "cpi_p11_runtime",
        "record_reuters_pass",
        "capture_market_evidence",
    )
    assert all(item not in source for item in forbidden)
