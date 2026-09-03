"""M27B.3R4 adversarial repair tests for the auditable retention ledger.

Each test class in this file targets one confirmed M27B.3R3 vulnerability (gaps A-G from the
M27B.3R4 repair spec) and proves the repaired ``AuditableRetentionLedger`` in
``services/opportunity_engine/auditable_retention.py`` now fails closed against it.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
from pathlib import Path

import pytest

import services.opportunity_engine.auditable_retention as retention_module
from services.market_universe.archive import _hash_bytes, _pack_canonical, _unpack_canonical
from services.opportunity_engine.auditable_retention import (
    AuditableRetentionLedger,
    RetentionGateError,
    RetentionPolicy,
)


def _write(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


def _new_ledger(tmp_path: Path, **policy_kwargs: int) -> AuditableRetentionLedger:
    policy_kwargs.setdefault("budget_bytes", 10_000_000)
    policy_kwargs.setdefault("free_space_floor_bytes", 1)
    policy_kwargs.setdefault("expected_scans", 96)
    return AuditableRetentionLedger(tmp_path / "retention", RetentionPolicy(**policy_kwargs))


def _run_one_scan(
    tmp_path: Path, *, archive_bytes: int = 10, evidence_bytes: int = 10, **policy_kwargs: int
) -> tuple[AuditableRetentionLedger, Path, Path, dict]:
    archive = tmp_path / "universe.sqlite"
    evidence = tmp_path / "observations.sqlite"
    _write(archive, archive_bytes)
    _write(evidence, evidence_bytes)
    ledger = _new_ledger(tmp_path, **policy_kwargs)
    ledger.check_before_scan((archive, evidence))
    receipt = ledger.record_scan(
        scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
    )
    return ledger, archive, evidence, receipt


def _state_path(ledger: AuditableRetentionLedger) -> Path:
    return ledger.state_path


def _load_state(ledger: AuditableRetentionLedger) -> dict:
    return json.loads(ledger.state_path.read_text(encoding="utf-8"))


def _save_state(ledger: AuditableRetentionLedger, state: dict) -> None:
    ledger.state_path.write_text(json.dumps(state), encoding="utf-8")


def _receipt_path(ledger: AuditableRetentionLedger, receipt: dict) -> Path:
    return ledger.receipts / f"{receipt['receipt_id']}.json"


# -- A: required evidence files -----------------------------------------------------------


class TestRequiredEvidenceFiles:
    def test_missing_primary_at_preflight_fails_closed(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        # evidence db is never written -- must not be silently skipped/zero-bytes.
        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger.check_before_scan((archive, evidence))
        assert ledger._lease_handle is None  # lease released, no leak on fail-closed reject

    def test_primary_deleted_between_preflight_and_record_fails_closed(
        self, tmp_path: Path
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))
        evidence.unlink()
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )
        assert ledger._lease_handle is None

    def test_missing_sidecar_is_optional(self, tmp_path: Path) -> None:
        # -wal/-shm sidecars are never written in this test; only the primaries exist.
        _, _, _, receipt = _run_one_scan(tmp_path)
        assert all(f["role"] == "primary" for f in receipt["files"])

    def test_present_sidecar_is_hashed_and_recorded(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        _write(Path(str(evidence) + "-wal"), 3)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))
        receipt = ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        sidecar_entries = [f for f in receipt["files"] if f["role"] == "sidecar"]
        assert len(sidecar_entries) == 1
        assert sidecar_entries[0]["bytes"] == 3


# -- B: receipt and state chain -----------------------------------------------------------


class TestReceiptAndStateChain:
    def test_deleted_referenced_receipt_fails_closed(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        _receipt_path(ledger, receipt).unlink()
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="missing"):
            ledger2.check_before_scan((archive, evidence))

    def test_tampered_receipt_fails_identity_verification(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["cumulative_bytes"] = tampered["cumulative_bytes"] + 999
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="identity verification"):
            ledger2.check_before_scan((archive, evidence))

    def test_forged_state_linkage_fails_closed(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        state = _load_state(ledger)
        state["scans"][0]["cumulative_bytes"] = state["scans"][0]["cumulative_bytes"] + 1
        _save_state(ledger, state)
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="does not match its receipt"):
            ledger2.check_before_scan((archive, evidence))

    def test_forged_receipt_id_in_state_fails_closed(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        state = _load_state(ledger)
        # A forged receipt_id that still looks like a valid hex digest but references nothing.
        state["scans"][0]["receipt_id"] = "0" * 64
        _save_state(ledger, state)
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="missing"):
            ledger2.check_before_scan((archive, evidence))

    def test_orphan_receipt_from_crash_fails_closed(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        # Simulate a crash between receipt publication and state publication: an extra, valid,
        # content-addressed receipt exists that no state entry references.
        _write(archive, 40)
        _write(evidence, 40)
        orphan = dict(receipt)
        del orphan["receipt_id"]
        orphan["cumulative_bytes"] = 80
        orphan["scan_run_id"] = "scan-orphan"
        orphan_id = retention_module._canonical_hash(orphan)
        orphan["receipt_id"] = orphan_id
        (ledger.receipts / f"{orphan_id}.json").write_text(json.dumps(orphan))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="orphan retention receipt"):
            ledger2.check_before_scan((archive, evidence))

    def test_unknown_field_in_state_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        state = _load_state(ledger)
        state["unexpected_field"] = "should not be trusted"
        _save_state(ledger, state)
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="unexpected or incomplete field"):
            ledger2.check_before_scan((archive, evidence))

    def test_unknown_field_in_receipt_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["injected_authority_field"] = "production"
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="unexpected or incomplete field"):
            ledger2.check_before_scan((archive, evidence))

    def test_valid_chain_reopens_and_reloads_successfully(self, tmp_path: Path) -> None:
        _ledger, archive, evidence, _receipt = _run_one_scan(tmp_path)
        ledger2 = _new_ledger(tmp_path)
        ledger2.check_before_scan((archive, evidence))  # must not raise
        ledger2.abort_scan()


# -- C: path and symlink safety -----------------------------------------------------------


class TestPathAndSymlinkSafety:
    def test_final_component_symlink_rejected(self, tmp_path: Path) -> None:
        real = tmp_path / "real-evidence.sqlite"
        _write(real, 5)
        archive = tmp_path / "universe.sqlite"
        archive.symlink_to(real)
        evidence = tmp_path / "observations.sqlite"
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger.check_before_scan((archive, evidence))

    def test_parent_directory_symlink_rejected(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        archive = real_dir / "universe.sqlite"
        _write(archive, 5)
        linked_dir = tmp_path / "linked-dir"
        linked_dir.symlink_to(real_dir)
        redirected_archive = linked_dir / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="symlink"):
            ledger.check_before_scan((redirected_archive, evidence))

    def test_path_swap_during_hashing_detected(self, tmp_path: Path, monkeypatch) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 4 * 1024 * 1024)  # multiple chunks so the swap lands mid-read
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))

        swap_target = tmp_path / "swapped-in.sqlite"
        _write(swap_target, 4 * 1024 * 1024)

        def swap(path: Path) -> None:
            if path == archive:
                os.replace(swap_target, archive)

        monkeypatch.setattr(retention_module, "_after_first_chunk_hook", swap)
        with pytest.raises(RetentionGateError, match="identity changed during hashing"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )

    def test_evidence_path_redirection_rejected(self, tmp_path: Path) -> None:
        _ledger, archive, _evidence, _ = _run_one_scan(tmp_path)
        decoy = tmp_path / "decoy.sqlite"
        _write(decoy, 5)
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="redirection is not permitted"):
            ledger2.check_before_scan((archive, decoy))


# -- D: conservative (high-water) projection ------------------------------------------------


class TestConservativeProjection:
    def test_large_then_small_scan_preserves_growth_high_water(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        ledger = _new_ledger(tmp_path, expected_scans=10, budget_bytes=10_000_000_000)
        ledger.check_before_scan((archive, evidence))
        # Scan 1: large growth.
        _write(archive, 1_000_000)
        _write(evidence, 1_000_000)
        r1 = ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        assert r1["growth_high_water_bytes"] == 1_999_998
        projection_after_large = r1["projected_24h_bytes"]

        ledger.check_before_scan((archive, evidence))
        # Scan 2: tiny growth -- must not erase the scan-1 high-water evidence.
        _write(archive, 1_000_001)
        r2 = ledger.record_scan(
            scan_run_id="scan-2", complete=True, paths=(archive, evidence), smoke=False
        )
        assert r2["growth_high_water_bytes"] == 1_999_998
        # The projection must still reflect the earlier large-growth trend, not collapse to the
        # tiny scan-2 delta alone.
        assert r2["projected_24h_bytes"] >= projection_after_large - 3_000_000

    @pytest.mark.parametrize("expected_scans,sample_count", [(1, 1), (3, 1), (3, 3), (3, 5)])
    def test_projection_boundary_arithmetic_never_negative(
        self, tmp_path: Path, expected_scans: int, sample_count: int
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        ledger = _new_ledger(tmp_path, expected_scans=expected_scans, budget_bytes=10_000_000_000)
        receipt = None
        for i in range(sample_count):
            ledger.check_before_scan((archive, evidence))
            _write(archive, 10 + i)
            receipt = ledger.record_scan(
                scan_run_id=f"scan-{i}", complete=True, paths=(archive, evidence), smoke=(i == 0)
            )
        assert receipt is not None
        assert receipt["projected_24h_bytes"] >= receipt["cumulative_bytes"]


# -- E: free-space reservation --------------------------------------------------------------


class TestFreeSpaceReservation:
    def test_static_floor_check_blocks_before_acquisition(self, tmp_path: Path) -> None:
        ledger = _new_ledger(tmp_path, free_space_floor_bytes=10**18)
        with pytest.raises(RetentionGateError, match="free-space floor"):
            ledger.check_before_scan((tmp_path / "missing-a", tmp_path / "missing-b"))

    def test_known_growth_trend_crossing_floor_blocks_before_acquisition(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        ledger = _new_ledger(
            tmp_path, expected_scans=10, budget_bytes=10_000_000_000, free_space_floor_bytes=100
        )
        ledger.check_before_scan((archive, evidence))
        _write(archive, 1000)
        ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        # Free space is currently comfortably above the floor (150), but the known per-scan
        # growth trend (~999 bytes) would cross it. The static "free <= floor" check alone
        # would wrongly pass this.
        fake_usage = shutil.disk_usage(tmp_path)._replace(free=150)
        monkeypatch.setattr(retention_module.shutil, "disk_usage", lambda _path: fake_usage)
        with pytest.raises(RetentionGateError, match="free-space floor"):
            ledger.check_before_scan((archive, evidence))


# -- F: concurrency and retries --------------------------------------------------------------


def _acquire_and_exit_hard(root: str) -> None:  # pragma: no cover - executed in a child process
    ledger = AuditableRetentionLedger(root, RetentionPolicy())
    archive = Path(root) / "universe.sqlite"
    evidence = Path(root) / "observations.sqlite"
    ledger.check_before_scan((archive, evidence))
    os._exit(0)  # hard exit: no lock cleanup, no atexit -- simulates a real crash


class TestConcurrencyAndRetries:
    def test_second_writer_fails_closed_while_lease_held(self, tmp_path: Path) -> None:
        ledger1, archive, evidence, _ = _run_one_scan(tmp_path)
        ledger1.check_before_scan((archive, evidence))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="another process holds the retention lease"):
            ledger2.check_before_scan((archive, evidence))
        ledger1.abort_scan()

    def test_reservation_active_blocks_reentrant_check_before_scan(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        ledger.check_before_scan((archive, evidence))
        with pytest.raises(RetentionGateError, match="already active"):
            ledger.check_before_scan((archive, evidence))
        ledger.abort_scan()

    def test_abort_scan_releases_lease_for_retry(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        ledger.check_before_scan((archive, evidence))
        ledger.abort_scan()
        ledger.check_before_scan((archive, evidence))  # must succeed after abort
        ledger.abort_scan()

    def test_os_level_crash_releases_lease_for_the_next_process(self, tmp_path: Path) -> None:
        root = tmp_path / "retention"
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        ctx = multiprocessing.get_context("fork")
        proc = ctx.Process(target=_acquire_and_exit_hard, args=(str(tmp_path),))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0
        # The child never released the lock explicitly -- os._exit skipped all cleanup -- but the
        # OS releases flock locks when the holding process's file descriptors close on exit.
        ledger = AuditableRetentionLedger(root, RetentionPolicy())
        ledger.check_before_scan((archive, evidence))
        ledger.abort_scan()

    def test_duplicate_scan_run_id_is_idempotent(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        ledger.check_before_scan((archive, evidence))
        replay = ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        assert replay == receipt
        state = _load_state(ledger)
        assert len(state["scans"]) == 1  # never appended/double-counted

    def test_duplicate_scan_run_id_with_mismatched_evidence_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        ledger.check_before_scan((archive, evidence))
        _write(archive, 999)  # evidence grew since the original scan-1 was recorded
        with pytest.raises(RetentionGateError, match="already recorded with different evidence"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )

    def test_crash_before_receipt_publication_leaves_no_trace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))

        def boom(path: Path) -> None:
            raise OSError("simulated crash mid-hash")

        monkeypatch.setattr(retention_module, "_hash_primary", boom)
        with pytest.raises(OSError, match="simulated crash mid-hash"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )
        assert ledger._lease_handle is None
        assert not any(ledger.receipts.glob("*.json"))
        assert not ledger.state_path.exists() or _load_state(ledger)["scans"] == []

    def test_crash_after_receipt_before_state_publication_recovers_fail_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))

        original_atomic_json = retention_module._atomic_json
        calls = {"n": 0}

        def flaky(path: Path, payload: dict) -> None:
            calls["n"] += 1
            if calls["n"] == 2:  # first call publishes the receipt; second publishes state
                raise OSError("simulated crash before state publication")
            original_atomic_json(path, payload)

        monkeypatch.setattr(retention_module, "_atomic_json", flaky)
        with pytest.raises(OSError, match="simulated crash before state publication"):
            ledger.record_scan(
                scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
            )
        monkeypatch.undo()
        assert any(ledger.receipts.glob("*.json"))  # orphan receipt was published
        # state_path already existed from check_before_scan's own pinning write; what matters is
        # that the new scan was never appended to it -- state publication for this scan never
        # happened.
        assert _load_state(ledger)["scans"] == []

        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="orphan retention receipt"):
            ledger2.check_before_scan((archive, evidence))

    def test_crash_after_state_publication_reopens_cleanly(self, tmp_path: Path) -> None:
        _ledger, archive, evidence, _ = _run_one_scan(tmp_path)
        ledger2 = _new_ledger(tmp_path)
        ledger2.check_before_scan((archive, evidence))
        ledger2.abort_scan()


# -- G: strict numeric and policy validation -------------------------------------------------


class TestStrictNumericAndPolicyValidation:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("cumulative_bytes", -1),
            ("bytes_this_scan", -5),
            ("projected_24h_bytes", -10),
        ],
    )
    def test_negative_numeric_receipt_fields_rejected(
        self, tmp_path: Path, field: str, value: int
    ) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered[field] = value
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError):
            ledger2.check_before_scan((archive, evidence))

    def test_boolean_where_int_expected_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["cumulative_bytes"] = True
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="nonnegative integer"):
            ledger2.check_before_scan((archive, evidence))

    def test_float_where_int_expected_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["cumulative_bytes"] = float(tampered["cumulative_bytes"])
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="nonnegative integer"):
            ledger2.check_before_scan((archive, evidence))

    def test_string_where_int_expected_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["cumulative_bytes"] = str(tampered["cumulative_bytes"])
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="nonnegative integer"):
            ledger2.check_before_scan((archive, evidence))

    def test_production_influence_must_be_exactly_zero(self, tmp_path: Path) -> None:
        ledger, archive, evidence, receipt = _run_one_scan(tmp_path)
        path = _receipt_path(ledger, receipt)
        tampered = json.loads(path.read_text())
        tampered["production_influence"] = 1
        path.write_text(json.dumps(tampered))
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="production_influence must be exactly 0"):
            ledger2.check_before_scan((archive, evidence))

    def test_decreasing_cumulative_accounting_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 1)
        _write(evidence, 1)
        ledger = _new_ledger(tmp_path, expected_scans=10)
        ledger.check_before_scan((archive, evidence))
        _write(archive, 500)
        ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive, evidence), smoke=True
        )
        ledger.check_before_scan((archive, evidence))
        _write(archive, 600)
        ledger.record_scan(
            scan_run_id="scan-2", complete=True, paths=(archive, evidence), smoke=False
        )
        state = _load_state(ledger)
        # Forge scan-2's cumulative_bytes down below scan-1's -- and update the linked receipt to
        # match so the state<->receipt linkage check alone would not catch it, isolating the
        # monotonic-accounting check.
        state["scans"][1]["cumulative_bytes"] = state["scans"][0]["cumulative_bytes"] - 1
        receipt_path = ledger.receipts / f"{state['scans'][1]['receipt_id']}.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["cumulative_bytes"] = state["scans"][1]["cumulative_bytes"]
        receipt_id = retention_module._canonical_hash(
            {k: v for k, v in receipt.items() if k != "receipt_id"}
        )
        receipt["receipt_id"] = receipt_id
        receipt_path.unlink()
        (ledger.receipts / f"{receipt_id}.json").write_text(json.dumps(receipt))
        state["scans"][1]["receipt_id"] = receipt_id
        _save_state(ledger, state)
        ledger2 = _new_ledger(tmp_path, expected_scans=10)
        with pytest.raises(RetentionGateError, match="decreased between scans"):
            ledger2.check_before_scan((archive, evidence))

    def test_policy_change_without_migration_rejected(self, tmp_path: Path) -> None:
        _ledger, archive, evidence, _ = _run_one_scan(tmp_path, budget_bytes=10_000_000)
        changed = AuditableRetentionLedger(
            tmp_path / "retention", RetentionPolicy(budget_bytes=999, free_space_floor_bytes=1)
        )
        with pytest.raises(RetentionGateError, match="explicit reviewed migration"):
            changed.check_before_scan((archive, evidence))

    def test_duplicate_receipt_id_in_state_rejected(self, tmp_path: Path) -> None:
        ledger, archive, evidence, _receipt = _run_one_scan(tmp_path)
        state = _load_state(ledger)
        forged_entry = dict(state["scans"][0])
        forged_entry["scan_run_id"] = "scan-2"
        forged_entry["sample_count"] = 2
        state["scans"].append(forged_entry)
        _save_state(ledger, state)
        ledger2 = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="duplicate receipt_id"):
            ledger2.check_before_scan((archive, evidence))


# -- compression round-trip / independent canonical-hash reconstruction ----------------------


class TestCompressionRoundTrip:
    def test_compressed_payload_reconstructs_exact_canonical_bytes_and_hash(self) -> None:
        canonical = '{"a":1,"b":[true,false,null],"ticker":"KXHIGHNY-25-T70"}'
        packed = _pack_canonical(canonical)
        assert packed != canonical.encode("utf-8")  # actually compressed, not a passthrough
        restored = _unpack_canonical(packed, "test-field")
        assert restored == canonical
        # The identity hash is computed over the canonical bytes independent of compression --
        # reconstructing it from the decompressed payload must match a hash taken before
        # compression ever happened.
        assert _hash_bytes(restored.encode("utf-8")) == _hash_bytes(canonical.encode("utf-8"))

    def test_legacy_uncompressed_string_payload_still_accepted(self) -> None:
        canonical = '{"legacy":true}'
        assert _unpack_canonical(canonical, "test-field") == canonical

    def test_corrupt_compressed_payload_rejected(self) -> None:
        from services.market_universe.archive import ArchiveError

        with pytest.raises(ArchiveError):
            _unpack_canonical(b"m27b3-zlib-v1\x00not-real-zlib-data", "test-field")


# -- incomplete refresh -----------------------------------------------------------------------


class TestIncompleteRefreshRetentionAccounting:
    def test_incomplete_scan_still_requires_and_records_evidence(self, tmp_path: Path) -> None:
        archive = tmp_path / "universe.sqlite"
        evidence = tmp_path / "observations.sqlite"
        _write(archive, 5)
        _write(evidence, 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan((archive, evidence))
        receipt = ledger.record_scan(
            scan_run_id="scan-1", complete=False, paths=(archive, evidence), smoke=True
        )
        assert receipt["complete"] is False
        assert receipt["production_influence"] == 0
