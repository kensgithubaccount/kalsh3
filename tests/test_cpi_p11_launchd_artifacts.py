from pathlib import Path

from services.forecasting.cpi_p11_runtime import (
    PRE_RELEASE_DEADLINE,
    PROTOCOL_SHA256,
    RUN_ROOT_NAME,
)

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts/arm_cpi_e1_p11_launchd.sh"
CLEANUP = ROOT / "scripts/cleanup_cpi_e1_p11_launchd.sh"


def test_launchd_artifacts_bind_frozen_protocol_and_root() -> None:
    arm = ARM.read_text()
    assert PROTOCOL_SHA256 in arm
    assert RUN_ROOT_NAME in arm
    assert "local main must exactly equal origin/main" in arm
    assert "git worktree add --detach" in arm
    assert "run_cpi_p11_frozen.sh" in arm


def test_launchd_schedule_matches_frozen_windows() -> None:
    arm = ARM.read_text()
    assert PRE_RELEASE_DEADLINE.isoformat() == "2026-10-14T12:20:00+00:00"
    assert 'write_job "com.kalsh3.cpi.p11.preflight" "preflight" 7 45' in arm
    assert 'write_job "com.kalsh3.cpi.p11.market" "market" 8 5' in arm
    assert 'write_job "com.kalsh3.cpi.p11.preclose" "preclose" 8 20' in arm
    assert "<key>Hour</key><integer>8</integer><key>Minute</key><integer>35</integer>" in arm
    assert "<key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer>" in arm


def test_scheduler_logs_are_outside_scientific_root() -> None:
    arm = ARM.read_text()
    assert 'STATE="$HOME/cpi_p11_scheduler_20261014"' in arm
    assert 'RUN_ROOT="$HOME/cpi_e1_p11_prospective_20261014"' in arm
    assert "$STATE/logs/" in arm
    assert "$RUN_ROOT/logs/" not in arm


def test_reuters_ingress_uses_same_pinned_runner() -> None:
    arm = ARM.read_text()
    assert "record-reuters-pass|record-reuters-nonpass" in arm
    assert '--root "\$ROOT" "\$COMMAND" --input "\$INPUT"' in arm


def test_cleanup_removes_all_annual_agents_without_touching_evidence() -> None:
    cleanup = CLEANUP.read_text()
    for label in (
        "com.kalsh3.cpi.p11.caffeinate",
        "com.kalsh3.cpi.p11.preflight",
        "com.kalsh3.cpi.p11.market",
        "com.kalsh3.cpi.p11.preclose",
        "com.kalsh3.cpi.p11.truth",
    ):
        assert label in cleanup
    assert "Evidence root was not modified." in cleanup
    assert "cpi_e1_p11_prospective_20261014" not in cleanup
