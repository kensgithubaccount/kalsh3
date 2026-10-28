"""No-network OS-process rehearsal; invented P12 data only, no external sources."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.cpi_p12_process_isolation_synthetic_v1.child import (
    SYNTHETIC_RECORD_TYPE,
    SYNTHETIC_ROOT_NAME,
)
from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12GateDecision,
    P12IsolationAuthorityError,
    P12IsolationPaths,
    evaluate_preclose_completeness,
    validate_stage_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _command(root: Path, stage: P12CollectorStage, mode: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "experiments.cpi_p12_process_isolation_synthetic_v1.child",
        "--synthetic-only",
        "--root",
        str(root),
        "--stage",
        stage.value,
        "--mode",
        mode,
    ]


def _run(root: Path, stage: P12CollectorStage, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(root, stage, mode),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _sentinel(root: Path, stage: P12CollectorStage) -> dict[str, object]:
    path = root / "invented-stage-artifacts" / f"{stage.value}.json"
    result: dict[str, object] = json.loads(path.read_text())
    assert result["record_type"] == SYNTHETIC_RECORD_TYPE
    assert result["synthetic_only"] is True
    assert result["contains_target_data"] is False
    assert result["scientific_evidence_authorized"] is False
    assert result["phase1_economic_test_authorized"] is False
    assert result["p12_frozen"] is False
    assert result["production_influence"] == "0"
    assert result["promotion_authority"] == "NONE"
    assert result["trading_authority"] == "NONE"
    return result


def _assert_non_authorizing_receipt(root: Path, stage: P12CollectorStage) -> str:
    receipt = validate_stage_receipt(P12IsolationPaths(root), stage)
    assert receipt["scientific_evidence_authorized"] is False
    assert receipt["p12_frozen"] is False
    assert receipt["production_influence"] == "0"
    return str(receipt["status"])


def test_fatal_child_process_does_not_suppress_independent_sibling_processes(
    tmp_path: Path,
) -> None:
    root = tmp_path / SYNTHETIC_ROOT_NAME
    modes = {
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY: "success",
        P12CollectorStage.KALSHI_MARKET_CAPTURE: "fatal-exit",
        P12CollectorStage.REUTERS_ACQUISITION: "success",
    }
    children = {
        stage: subprocess.Popen(
            _command(root, stage, mode),
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for stage, mode in modes.items()
    }
    results: dict[P12CollectorStage, tuple[int, str, str]] = {}
    for stage, process in children.items():
        try:
            stdout, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        assert process.returncode is not None
        results[stage] = (process.returncode, stdout, stderr)

    assert results[P12CollectorStage.BLS_SCHEDULE_AUTHORITY][0] == 0
    assert results[P12CollectorStage.KALSHI_MARKET_CAPTURE][0] != 0
    assert results[P12CollectorStage.REUTERS_ACQUISITION][0] == 0
    assert (
        "invented fatal child-process failure"
        in (results[P12CollectorStage.KALSHI_MARKET_CAPTURE][2])
    )
    for stage in (
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY,
        P12CollectorStage.REUTERS_ACQUISITION,
    ):
        assert json.loads(results[stage][1])["status"] == "PASS"
        assert _assert_non_authorizing_receipt(root, stage) == "PASS"

    assert (
        not P12IsolationPaths(root).stage_receipt(P12CollectorStage.KALSHI_MARKET_CAPTURE).exists()
    )
    assert not (root / "invented-stage-artifacts" / "kalshi_market_capture.json").exists()
    pids = {
        _sentinel(root, P12CollectorStage.BLS_SCHEDULE_AUTHORITY)["child_pid"],
        _sentinel(root, P12CollectorStage.REUTERS_ACQUISITION)["child_pid"],
    }
    assert len(pids) == 2
    gate = evaluate_preclose_completeness(P12IsolationPaths(root))
    assert gate["decision"] == P12GateDecision.INCOMPLETE.value
    assert gate["required_stage_states"]["kalshi_market_capture"] == "MISSING"
    assert gate["scientific_evidence_authorized"] is False
    assert gate["p12_frozen"] is False


def test_expected_failure_receipt_is_local_to_its_child_process(tmp_path: Path) -> None:
    root = tmp_path / SYNTHETIC_ROOT_NAME
    for stage, mode, expected_exit, expected_status in (
        (P12CollectorStage.BLS_SCHEDULE_AUTHORITY, "expected-failure", 4, "FAILURE"),
        (P12CollectorStage.KALSHI_MARKET_CAPTURE, "success", 0, "PASS"),
        (P12CollectorStage.REUTERS_ACQUISITION, "success", 0, "PASS"),
    ):
        result = _run(root, stage, mode)
        assert result.returncode == expected_exit, result.stderr
        assert json.loads(result.stdout)["status"] == expected_status
        assert _assert_non_authorizing_receipt(root, stage) == expected_status

    gate = evaluate_preclose_completeness(P12IsolationPaths(root))
    assert gate["decision"] == P12GateDecision.INCOMPLETE.value
    assert gate["required_stage_states"] == {
        "bls_schedule_authority": "FAILURE",
        "kalshi_market_capture": "PASS",
        "reuters_acquisition": "PASS",
    }
    _sentinel(root, P12CollectorStage.KALSHI_MARKET_CAPTURE)
    _sentinel(root, P12CollectorStage.REUTERS_ACQUISITION)


def test_all_invented_success_is_only_operational_and_never_scientific(tmp_path: Path) -> None:
    root = tmp_path / SYNTHETIC_ROOT_NAME
    pids: set[object] = set()
    for stage in P12CollectorStage:
        result = _run(root, stage, "success")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["mode"] == "INVENTED_PROCESS_REHEARSAL_ONLY"
        assert output["scientific_evidence_authorized"] is False
        assert output["phase1_economic_test_authorized"] is False
        assert output["p12_frozen"] is False
        assert output["production_influence"] == "0"
        assert output["promotion_authority"] == "NONE"
        assert output["trading_authority"] == "NONE"
        assert _assert_non_authorizing_receipt(root, stage) == "PASS"
        pids.add(_sentinel(root, stage)["child_pid"])
    assert len(pids) == 3
    gate = evaluate_preclose_completeness(P12IsolationPaths(root))
    assert gate["decision"] == P12GateDecision.READY.value
    assert gate["operational_stage_presence_only"] is True
    assert gate["scientific_evidence_authorized"] is False
    assert gate["p12_frozen"] is False
    assert gate["production_influence"] == "0"


def test_restart_reuses_existing_receipt_without_rerunning_collector(tmp_path: Path) -> None:
    root = tmp_path / SYNTHETIC_ROOT_NAME
    stage = P12CollectorStage.REUTERS_ACQUISITION
    first = _run(root, stage, "success")
    assert first.returncode == 0, first.stderr
    artifact = root / "invented-stage-artifacts" / f"{stage.value}.json"
    receipt = P12IsolationPaths(root).stage_receipt(stage)
    before_artifact, before_receipt = artifact.read_bytes(), receipt.read_bytes()
    second = _run(root, stage, "fatal-exit")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "PASS"
    assert (artifact.read_bytes(), receipt.read_bytes()) == (before_artifact, before_receipt)


@pytest.mark.parametrize("missing_ack", [False, True])
def test_child_rejects_non_synthetic_root_or_missing_ack(tmp_path: Path, missing_ack: bool) -> None:
    root = tmp_path / (SYNTHETIC_ROOT_NAME if missing_ack else "actual-p12-run")
    args = _command(root, P12CollectorStage.KALSHI_MARKET_CAPTURE, "success")
    if missing_ack:
        args.remove("--synthetic-only")
    result = subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 2
    assert not root.exists()


def test_mutating_synthetic_artifact_blocks_receipt_validation(tmp_path: Path) -> None:
    root = tmp_path / SYNTHETIC_ROOT_NAME
    stage = P12CollectorStage.REUTERS_ACQUISITION
    assert _run(root, stage, "success").returncode == 0
    artifact = root / "invented-stage-artifacts" / f"{stage.value}.json"
    original = artifact.read_bytes()
    artifact.write_bytes(original[:-1] + b"!")  # Preserve byte count; invalidate SHA-256.
    with pytest.raises(P12IsolationAuthorityError, match="SHA-256 changed"):
        validate_stage_receipt(P12IsolationPaths(root), stage)
