import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts/arm_cpi_e1_p11_systemd.sh"
CLEANUP = ROOT / "scripts/cleanup_cpi_e1_p11_systemd.sh"
PROVISION = ROOT / "scripts/provision_cpi_e1_p11_gce.sh"
SUBMIT = ROOT / "scripts/submit_cpi_e1_p11_reuters_gce.sh"


def test_gce_shell_artifacts_are_syntax_valid() -> None:
    for path in (ARM, CLEANUP, PROVISION, SUBMIT):
        subprocess.run(["/usr/bin/bash", "-n", str(path)], check=True)


def test_systemd_timers_are_exact_utc_and_nonpersistent() -> None:
    arm = ARM.read_text()
    for stamp in (
        "2026-10-14 11:45:00 UTC",
        "2026-10-14 12:05:00 UTC",
        "2026-10-14 12:20:00 UTC",
        "2026-10-14 12:35:00 UTC",
        "2026-10-14 12:45:00 UTC",
        "2026-10-14 13:00:00 UTC",
        "2026-10-14 14:00:00 UTC",
        "2026-10-14 16:00:00 UTC",
    ):
        assert stamp in arm
    assert "Persistent=false" in arm
    assert "Persistent=true" not in arm
    assert "RandomizedDelaySec=0" in arm
    assert "AccuracySec=1s" in arm


def test_vm_runtime_binds_same_frozen_protocol_and_root() -> None:
    arm = ARM.read_text()
    assert "e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d" in arm
    assert 'RUN_ROOT="$HOME/cpi_e1_p11_prospective_20261014"' in arm
    assert 'STATE="$HOME/cpi_p11_vm_scheduler_20261014"' in arm
    assert "$STATE/logs/" in arm
    assert "$RUN_ROOT/logs/" not in arm
    assert "NTPSynchronized" in arm


def test_gce_provisioner_uses_standard_protected_ubuntu_vm() -> None:
    script = PROVISION.read_text()
    assert "--image-family=ubuntu-2404-lts-amd64" in script
    assert "--image-project=ubuntu-os-cloud" in script
    assert "--provisioning-model=STANDARD" in script
    assert "--maintenance-policy=MIGRATE" in script
    assert "--restart-on-failure" in script
    assert "--shielded-secure-boot" in script
    assert "--deletion-protection" in script
    assert "--preemptible" not in script
    assert "--spot" not in script
    assert "local main must exactly equal origin/main" in script


def test_cloud_bootstrap_requires_exact_runtime_head() -> None:
    script = PROVISION.read_text()
    assert 'RUNTIME_HEAD="${1:?runtime head required}"' in script
    assert "git rev-parse HEAD" in script
    assert "cloud main does not equal requested runtime head" in script
    assert '"uv==$UV_VERSION"' in script


def test_reuters_submitter_uses_pinned_remote_runner() -> None:
    script = SUBMIT.read_text()
    assert "record-reuters-$MODE" in script
    assert "~/run_cpi_p11_frozen_vm.sh" in script
    assert "gcloud compute scp" in script
    assert "gcloud compute ssh" in script


def test_cleanup_never_targets_scientific_evidence_root() -> None:
    cleanup = CLEANUP.read_text()
    assert "cpi_e1_p11_prospective_20261014" not in cleanup
    assert "Scientific evidence root was not modified." in cleanup
