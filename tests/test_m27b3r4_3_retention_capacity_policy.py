"""M27B.3R4.3 retention capacity policy tests.

The R4.3 read-only audit established that the reviewed 24 GiB budget is undersized against the
empirically measured first-scan compressed evidence growth (302,743,552 bytes), which projects to
29,063,479,296 bytes (27.067474365234375 GiB) across the reviewed 96-scan/900-second-cadence
contract -- code-exact, independently recomputed from `AuditableRetentionLedger.record_scan`'s own
formula. 28 GiB was reviewed and approved to cover that projection with margin.

This repair binds the new 28 GiB budget explicitly at the one existing reviewed operator
entrypoint (`scripts/run_m27b3_smoke_receipt.py`) rather than changing
`AuditableRetentionLedger`/`RetentionPolicy`'s repository-wide default (still 24), which remains
available to any other, differently-reviewed caller of the same module. These tests prove:

* the wrapper's fixed command and process receipt now bind exactly budget=28 GiB, floor=8 GiB,
  expected_scans=96, with cadence (900s) and every other previously-reviewed shape unchanged;
* the unmodified, already-reviewed retention gate mechanism (`RetentionPolicy`/`check_before_scan`/
  `record_scan`) correctly enforces that new 28 GiB value: a projection that would have been
  rejected under the prior 24 GiB budget now passes under 28 GiB, a projection above 28 GiB still
  fails closed, and the free-space floor still fails closed independently of the budget value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import services.opportunity_engine.auditable_retention as retention_module
from scripts import run_m27b3_smoke_receipt as receipt
from services.opportunity_engine.auditable_retention import (
    AuditableRetentionLedger,
    RetentionGateError,
    RetentionPolicy,
)

GIB = 1024**3
REVIEWED_BUDGET_BYTES = 28 * GIB
REVIEWED_FLOOR_BYTES = 8 * GIB
REVIEWED_EXPECTED_SCANS = 96

# Empirically measured in the R4.3 audit; independently recomputed from record_scan's own
# formula (byte_count + growth_high_water * (expected_scans - sample_count)), not the task's
# supplied simplified figure (which differs by exactly before_scan_bytes -- see the R4.3 review
# doc). Recorded here as a fixed regression anchor.
AUDITED_FIRST_SCAN_GROWTH_BYTES = 302_743_552
AUDITED_96_SCAN_PROJECTION_BYTES = 29_063_479_296


def _write(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


# -- wrapper: reviewed 28 GiB policy bound explicitly at the smoke/pilot entrypoint -----------


class TestWrapperBindsReviewed28GiBPolicy:
    def test_module_constants_are_the_exact_reviewed_values(self) -> None:
        assert receipt.BUDGET_GIB == 28
        assert receipt.FREE_SPACE_FLOOR_GIB == 8
        assert receipt.EXPECTED_SCANS == 96

    def test_build_command_binds_exact_reviewed_policy_and_preserves_prior_shape(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "run"
        run.mkdir()
        command = receipt.build_command(Path("/bin/echo"), run)
        assert command[1:] == [
            "-u",
            "-m",
            receipt.MODULE,
            "--archive",
            str(run / "universe.sqlite"),
            "--evidence-db",
            str(run / "observations.sqlite"),
            "--live-public-read",
            "--cadence-seconds",
            "900",
            "--max-iterations",
            "1",
            "--source-authority",
            receipt.HOST,
            "--storage-budget-gib",
            "28",
            "--free-space-floor-gib",
            "8",
            "--expected-scans",
            "96",
        ]
        # The database-path indices _validate_child_paths relies on are unchanged by the new,
        # appended flags.
        escaped = list(command)
        escaped[5] = str(tmp_path / "escape.sqlite")
        with pytest.raises(receipt.ReceiptValidationError, match="escapes"):
            receipt._validate_child_paths(escaped, run)
        receipt._validate_child_paths(command, run)  # must not raise

    def test_no_authenticated_or_write_flag_was_introduced(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        run.mkdir()
        command = receipt.build_command(Path("/bin/echo"), run)
        for forbidden in ("--api-key", "--credential", "--authenticated", "--write", "--order"):
            assert forbidden not in command
        # The wrapper's own operator-facing parser still accepts only the five reviewed flags --
        # unaffected by the internal child-command change.
        for extra in (["--api-key", "secret"], ["--max-iterations", "2"]):
            with pytest.raises(SystemExit):
                receipt._parser().parse_args(extra)

    def test_receipt_records_explicit_reviewed_policy_and_production_influence_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "runs"
        parent.mkdir()
        monkeypatch.setattr(
            receipt,
            "repository_identity",
            lambda: receipt.RepositoryIdentity(Path("/repo"), "a" * 40, "b" * 40, True),
        )

        class Child:
            pid = 321
            returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        monkeypatch.setattr(receipt.subprocess, "Popen", lambda *a, **k: Child())
        monkeypatch.setattr(receipt, "_hashes", lambda run: {})
        result = receipt.main(
            [
                "--parent-dir",
                str(parent),
                "--run-dir",
                str(parent / "run"),
                "--expected-code-sha",
                "a" * 40,
                "--expected-tree",
                "b" * 40,
                "--python",
                "/bin/echo",
            ]
        )
        assert result == 0
        payload = json.loads((parent / "run" / "process-receipt.json").read_text())
        assert payload["storage_budget_gib"] == 28
        assert payload["free_space_floor_gib"] == 8
        assert payload["expected_scans"] == 96
        assert payload["production_influence"] == 0
        assert type(payload["production_influence"]) is int
        assert payload["source_authority"] == receipt.HOST
        assert "--storage-budget-gib" in payload["command"]
        assert "28" in payload["command"]


# -- retention: the unmodified gate correctly enforces the new reviewed budget -----------------


def _policy(**overrides: int) -> RetentionPolicy:
    values: dict[str, int] = {
        "budget_bytes": REVIEWED_BUDGET_BYTES,
        "free_space_floor_bytes": 1,  # isolate the budget check from the floor check by default
        "expected_scans": REVIEWED_EXPECTED_SCANS,
    }
    values.update(overrides)
    return RetentionPolicy(**values)


class TestRetentionEnforcesReviewed28GiBBudget:
    def test_projection_between_24_and_28_gib_passes_under_28gib_but_would_fail_under_24gib(
        self, tmp_path: Path
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        # 1 MiB real growth; expected_scans chosen so the resulting projection lands at
        # ~26.00 GiB -- strictly between the prior 24 GiB budget and the new 28 GiB one.
        expected_scans = 26_625
        ledger_28 = AuditableRetentionLedger(
            tmp_path / "retention-28", _policy(expected_scans=expected_scans)
        )
        ledger_28.check_before_scan((archive, evidence))
        _write(archive, 1024 * 1024 + 1)
        receipt = ledger_28.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        assert 24 * GIB < receipt["projected_24h_bytes"] <= 28 * GIB
        assert receipt["approved_budget_bytes"] == 28 * GIB
        assert receipt["production_influence"] == 0

        # The identical observed growth, replayed against a fresh ledger under the *prior*
        # 24 GiB budget, must still fail closed -- confirms the repair actually changed the
        # enforced ceiling rather than the underlying gate behavior.
        ledger_24 = AuditableRetentionLedger(
            tmp_path / "retention-24",
            _policy(budget_bytes=24 * GIB, expected_scans=expected_scans),
        )
        _write(archive, 1)
        ledger_24.check_before_scan((archive, evidence))
        _write(archive, 1024 * 1024 + 1)
        with pytest.raises(RetentionGateError, match="exceeds approved storage budget"):
            ledger_24.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )

    def test_projection_above_28gib_still_fails_closed(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        # Same 1 MiB growth, but expected_scans chosen so the projection lands at ~29.00 GiB --
        # above even the new, reviewed 28 GiB budget.
        expected_scans = 29_697
        ledger = AuditableRetentionLedger(
            tmp_path / "retention", _policy(expected_scans=expected_scans)
        )
        ledger.check_before_scan((archive, evidence))
        _write(archive, 1024 * 1024 + 1)
        with pytest.raises(RetentionGateError, match="exceeds approved storage budget"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )
        # Fail-closed leaves no trace, exactly as R4.1 established for any budget value.
        assert ledger._lease_handle is None
        assert not any(ledger.receipts.glob("*.json"))
        state = json.loads(ledger.state_path.read_text())
        assert state["scans"] == []
        assert state["growth_high_water_bytes"] == 0
        assert state["projected_bytes"] == 0

    def test_insufficient_free_space_fails_closed_independent_of_28gib_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        # A comfortably small projected growth relative to the 28 GiB budget -- the budget check
        # alone would pass -- but the reviewed 8 GiB free-space floor must independently reject
        # when the host itself does not have enough free space, regardless of how generous the
        # approved budget is.
        ledger = AuditableRetentionLedger(
            tmp_path / "retention",
            RetentionPolicy(
                budget_bytes=REVIEWED_BUDGET_BYTES,
                free_space_floor_bytes=REVIEWED_FLOOR_BYTES,
                expected_scans=REVIEWED_EXPECTED_SCANS,
            ),
        )
        low_free = type("Usage", (), {"free": 1 * GIB, "total": 0, "used": 0})()
        monkeypatch.setattr(retention_module.shutil, "disk_usage", lambda _path: low_free)
        with pytest.raises(RetentionGateError, match="free-space floor"):
            ledger.check_before_scan((archive, evidence))

    def test_growth_high_water_semantics_unchanged_at_the_new_budget(self, tmp_path: Path) -> None:
        """A large scan followed by a tiny one must still not erase the high-water evidence --
        R4.1's gap-D fix -- with the new 28 GiB budget in place."""
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        ledger = AuditableRetentionLedger(tmp_path / "retention", _policy(expected_scans=10))
        ledger.check_before_scan((archive, evidence))
        _write(archive, 1_000_000)
        r1 = ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        assert r1["approved_budget_bytes"] == REVIEWED_BUDGET_BYTES
        ledger.check_before_scan((archive, evidence))
        _write(archive, 1_000_001)  # tiny growth
        r2 = ledger.record_scan(
            scan_run_id="scan-2", complete=True, paths=(archive, evidence), smoke=False
        )
        assert r2["growth_high_water_bytes"] == r1["growth_high_water_bytes"]

    def test_audited_projection_figures_are_reproducible_from_the_supplied_measurements(
        self,
    ) -> None:
        """Regression anchor: the exact figures cited in the R4.3 review doc are re-derivable
        from record_scan's own formula, not merely asserted."""
        before_scan_bytes = 98_304
        universe_after = 302_792_704
        observations = 49_152
        byte_count = universe_after + observations
        observed_delta = max(0, byte_count - before_scan_bytes)
        assert observed_delta == AUDITED_FIRST_SCAN_GROWTH_BYTES
        growth_high_water = max(0, observed_delta)
        remaining_scans = max(0, REVIEWED_EXPECTED_SCANS - 1)
        projected = byte_count + growth_high_water * remaining_scans
        assert projected == AUDITED_96_SCAN_PROJECTION_BYTES
        assert 24 * GIB < projected <= 28 * GIB
