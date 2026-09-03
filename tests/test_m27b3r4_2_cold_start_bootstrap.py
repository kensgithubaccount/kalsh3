"""M27B.3R4.2 cold-start bootstrap tests.

R4.1 correctly requires every primary evidence file to exist at retention preflight -- but on a
genuinely fresh run, ``run_forever`` calls ``retention.check_before_scan`` before
``refresh_universe`` ever creates ``universe.sqlite``, so the very first live scan was
unconditionally rejected. These tests prove the R4.2 cold-start bootstrap in
``services/opportunity_engine/auditable_retention.py`` (``check_before_scan``'s
``bootstrap_primaries`` parameter, ``_is_pristine_ledger``, ``_primary_is_absent``,
``_bootstrap_primary``) fixes exactly that gap without weakening any R4.1 protection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import services.opportunity_engine.auditable_retention as retention_module
from services.market_universe.archive import ArchiveError, UniverseObservationArchive
from services.opportunity_engine.auditable_retention import (
    AuditableRetentionLedger,
    RetentionGateError,
    RetentionPolicy,
)
from services.opportunity_engine.structural_measurement_runner import (
    _bootstrap_universe_archive,
    run_forever,
)
from services.opportunity_engine.structural_measurement_store import StructuralMeasurementStore
from tests.test_structural_measurement_runner import (
    NOW,
    FakeUniverseTransport,
    _orderbook_acquirer,
    _series_transport,
    quote_fields,
    raw_event,
    raw_series,
    semantic_market_fields,
)


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _new_ledger(tmp_path: Path, **policy_kwargs: int) -> AuditableRetentionLedger:
    policy_kwargs.setdefault("budget_bytes", 10_000_000)
    policy_kwargs.setdefault("free_space_floor_bytes", 1)
    policy_kwargs.setdefault("expected_scans", 96)
    return AuditableRetentionLedger(tmp_path / "retention", RetentionPolicy(**policy_kwargs))


def _counting_initializer(calls: list[Path]) -> retention_module.Callable[[Path], None]:
    def initializer(path: Path) -> None:
        calls.append(path)

    return initializer


# -- true cold-start integration -------------------------------------------------------------


class TestTrueColdStartIntegration:
    def test_neither_database_pre_created_completes_one_scan_with_valid_receipt(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        assert not archive_path.exists()
        assert not evidence_path.exists()

        # Constructing the store is the same eager creation the real CLI performs in main()
        # before retention is ever touched -- it is not part of the defect under test.
        store = StructuralMeasurementStore(evidence_path)
        assert evidence_path.exists()
        assert not archive_path.exists()  # the archive specifically must still be absent

        low_raw = semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45"))
        high_raw = semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60"))
        transport = FakeUniverseTransport(markets=[low_raw, high_raw], events=[raw_event()])
        ledger = _new_ledger(tmp_path, expected_scans=1)

        results = list(
            run_forever(
                archive_path=str(archive_path),
                store=store,
                source_authority="test",
                max_iterations=1,
                retention=ledger,
                universe_transport=transport,
                series_read=_series_transport(raw_series()),
                orderbook_acquirer=_orderbook_acquirer({}),
                clock=lambda: NOW,
            )
        )
        assert len(results) == 1
        assert results[0].refresh_complete

        # The archive now exists with the exact canonical schema -- not a placeholder.
        assert archive_path.exists()
        archive = UniverseObservationArchive(archive_path)
        with archive._connect(read_only=True) as db:
            check = db.execute("PRAGMA quick_check").fetchone()
            assert check[0] == "ok"
            row = db.execute("SELECT * FROM archive_metadata WHERE singleton=1").fetchone()
            assert row is not None

        # A valid, chain-verifiable retention receipt was produced.
        receipts = list(ledger.receipts.glob("*.json"))
        assert len(receipts) == 1
        state = retention_module.json.loads(ledger.state_path.read_text())
        assert len(state["scans"]) == 1
        assert state["scans"][0]["sample_count"] == 1

        reopened = _new_ledger(tmp_path, expected_scans=1)
        reopened.check_before_scan((archive_path, evidence_path))  # must not raise
        reopened.abort_scan()

    def test_reopened_ledger_reload_validates_the_bootstrapped_receipt_chain(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        store = StructuralMeasurementStore(tmp_path / "observations.sqlite")
        transport = FakeUniverseTransport(markets=[], events=[])
        ledger = _new_ledger(tmp_path, expected_scans=1)
        list(
            run_forever(
                archive_path=str(archive_path),
                store=store,
                source_authority="test",
                max_iterations=1,
                retention=ledger,
                universe_transport=transport,
                clock=lambda: NOW,
            )
        )
        receipt_path = next(ledger.receipts.glob("*.json"))
        receipt = retention_module.json.loads(receipt_path.read_text())
        assert receipt["production_influence"] == 0
        assert receipt["smoke_sample"] is True
        assert any(f["role"] == "primary" for f in receipt["files"])


# -- missing archive after baseline remains fatal ---------------------------------------------


class TestMissingArchiveAfterBaselineRemainsFatal:
    def test_missing_primary_after_a_completed_scan_is_still_a_hard_failure(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(evidence_path, b"x" * 5)  # stand-in for StructuralMeasurementStore's eager file
        calls: list[Path] = []
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _counting_initializer(calls)},
        )
        ledger.record_scan(
            scan_run_id="scan-1", complete=True, paths=(archive_path, evidence_path), smoke=True
        )
        assert len(calls) == 1
        archive_path.unlink()

        ledger2 = _new_ledger(tmp_path)
        calls2: list[Path] = []
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger2.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls2)},
            )
        assert calls2 == []  # bootstrap must never be offered once a baseline exists

    def test_missing_primary_after_pinned_baseline_with_zero_scans_is_still_fatal(
        self, tmp_path: Path
    ) -> None:
        """A state file can exist with evidence pinned and a baseline captured even before any
        scan is ever recorded (a crash between ``check_before_scan`` succeeding and
        ``record_scan`` running). Bootstrap must remain unreachable in that case too."""
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(evidence_path, b"x" * 5)
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _bootstrap_universe_archive},
        )
        ledger.abort_scan()  # crash/abort before record_scan; state file was already published
        assert ledger.state_path.exists()
        state = retention_module.json.loads(ledger.state_path.read_text())
        assert state["scans"] == []
        assert state["evidence_paths"] is not None

        archive_path.unlink()
        ledger2 = _new_ledger(tmp_path)
        calls: list[Path] = []
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger2.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls)},
            )
        assert calls == []

    def test_orphan_receipt_without_state_file_blocks_bootstrap(self, tmp_path: Path) -> None:
        """An orphan receipt with no state file is a crash signature that
        ``_load_and_validate_state``'s early return for a missing state file cannot see on its
        own; ``_is_pristine_ledger`` must still refuse to treat this ledger as pristine."""
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        ledger = _new_ledger(tmp_path)
        ledger.receipts.mkdir(parents=True, exist_ok=True)
        (ledger.receipts / "deadbeef.json").write_text("{}")
        assert not ledger.state_path.exists()

        calls: list[Path] = []
        with pytest.raises(RetentionGateError, match="missing or unstable"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls)},
            )
        assert calls == []
        assert not archive_path.exists()


# -- malformed pre-existing archive -------------------------------------------------------------


class TestMalformedPreexistingArchiveRejected:
    def test_garbage_bytes_at_archive_path_pass_retention_but_fail_the_real_scan(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(archive_path, b"not a sqlite database at all")
        store = StructuralMeasurementStore(evidence_path)
        ledger = _new_ledger(tmp_path)

        with pytest.raises(ArchiveError):
            list(
                run_forever(
                    archive_path=str(archive_path),
                    store=store,
                    source_authority="test",
                    max_iterations=1,
                    retention=ledger,
                    universe_transport=FakeUniverseTransport(markets=[], events=[]),
                    clock=lambda: NOW,
                )
            )
        # The retention lease was released and no scan was ever recorded -- fail closed, not a
        # silent adoption of the malformed file as evidence.
        assert ledger._lease_handle is None
        assert not any(ledger.receipts.glob("*.json"))

    def test_bootstrap_never_touches_an_already_present_file(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(archive_path, b"garbage")
        _write(evidence_path, b"garbage")
        calls: list[Path] = []
        ledger = _new_ledger(tmp_path)
        ledger.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _counting_initializer(calls)},
        )
        ledger.abort_scan()
        assert calls == []  # bootstrap is only for a genuinely absent primary
        assert archive_path.read_bytes() == b"garbage"  # never overwritten


# -- archive symlink rejected --------------------------------------------------------------


class TestArchiveSymlinkRejected:
    def test_dangling_symlink_is_rejected_even_with_bootstrap_offered(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        target = tmp_path / "nowhere.sqlite"
        archive_path.symlink_to(target)  # dangling: target never created
        calls: list[Path] = []
        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="symlink"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls)},
            )
        assert calls == []
        assert not target.exists()  # bootstrap never wrote through the dangling symlink
        assert ledger._lease_handle is None

    def test_symlink_to_a_real_file_is_rejected_not_adopted(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        real = tmp_path / "elsewhere.sqlite"
        _write(real, b"x" * 10)
        archive_path.symlink_to(real)
        calls: list[Path] = []
        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="symlink"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls)},
            )
        assert calls == []

    def test_bootstrap_primary_helper_rejects_a_symlink_directly(self, tmp_path: Path) -> None:
        target = tmp_path / "real.sqlite"
        link = tmp_path / "link.sqlite"
        link.symlink_to(target)
        with pytest.raises(RetentionGateError, match="must not be a symlink"):
            retention_module._bootstrap_primary(link, lambda _p: None)


# -- free space before bootstrap -----------------------------------------------------------


class TestFreeSpaceCheckedBeforeBootstrap:
    def test_bootstrap_never_runs_when_free_space_already_below_floor(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        ledger = _new_ledger(tmp_path, free_space_floor_bytes=10**18)
        calls: list[Path] = []
        with pytest.raises(RetentionGateError, match="free-space floor"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: _counting_initializer(calls)},
            )
        assert calls == []
        assert not archive_path.exists()


# -- partial/crashed bootstrap ---------------------------------------------------------------


class TestPartialBootstrapFailsClosedOrRecovers:
    def test_initializer_failure_before_any_write_fails_closed_then_recovers_on_retry(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(evidence_path, b"x" * 5)

        def crash_immediately(_path: Path) -> None:
            raise RuntimeError("simulated crash before any schema write")

        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="bootstrap initializer failed"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: crash_immediately},
            )
        assert not ledger.state_path.exists()
        # O_CREAT|O_EXCL already created the (empty) leaf before the initializer ran.
        assert archive_path.exists()
        assert archive_path.stat().st_size == 0

        # Retry with the real canonical initializer: the file already exists (no longer
        # "absent"), so bootstrap is skipped and the unchanged preflight validates it; the real
        # initializer's own existed-but-empty branch then completes initialization properly.
        ledger2 = _new_ledger(tmp_path)
        ledger2.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _bootstrap_universe_archive},
        )
        ledger2.abort_scan()
        assert archive_path.stat().st_size == 0  # still untouched by retention itself

        # A caller that actually runs the initializer against the pre-existing empty file (as
        # refresh_universe does during the real scan) completes initialization cleanly.
        UniverseObservationArchive(archive_path)
        assert archive_path.stat().st_size > 0
        with UniverseObservationArchive(archive_path)._connect(read_only=True) as db:
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def test_partial_schema_write_then_crash_fails_closed_on_the_real_initializer_retry(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(evidence_path, b"x" * 5)

        def crash_after_partial_write(path: Path) -> None:
            path.write_bytes(b"not-a-real-sqlite-schema" * 4)  # non-empty, not a valid database
            raise RuntimeError("simulated crash mid schema write")

        ledger = _new_ledger(tmp_path)
        with pytest.raises(RetentionGateError, match="bootstrap initializer failed"):
            ledger.check_before_scan(
                (archive_path, evidence_path),
                bootstrap_primaries={archive_path: crash_after_partial_write},
            )
        assert archive_path.exists()
        assert archive_path.stat().st_size > 0

        # Retry: the file is no longer absent, so bootstrap is skipped; retention's own
        # preflight (stability/type only) passes, but the canonical initializer -- run the same
        # way the real scan would run it -- must refuse to silently adopt or repair it.
        ledger2 = _new_ledger(tmp_path)
        ledger2.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _bootstrap_universe_archive},
        )
        ledger2.abort_scan()
        with pytest.raises(ArchiveError):
            UniverseObservationArchive(archive_path)


# -- concurrent bootstrap cannot both proceed -------------------------------------------------


class TestConcurrentBootstrapCannotBothProceed:
    def test_second_instance_bootstrap_attempt_fails_closed_while_first_holds_the_lease(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / "universe.sqlite"
        evidence_path = tmp_path / "observations.sqlite"
        _write(evidence_path, b"x" * 5)
        ledger1 = _new_ledger(tmp_path)
        ledger1._acquire_lease()
        try:
            ledger2 = _new_ledger(tmp_path)
            calls: list[Path] = []
            with pytest.raises(
                RetentionGateError, match="another process holds the retention lease"
            ):
                ledger2.check_before_scan(
                    (archive_path, evidence_path),
                    bootstrap_primaries={archive_path: _counting_initializer(calls)},
                )
            assert calls == []  # never even attempted while the lease was held elsewhere
            assert not archive_path.exists()
        finally:
            ledger1._release_lease()

        # Once released, bootstrap proceeds normally for the next holder.
        calls2: list[Path] = []
        ledger3 = _new_ledger(tmp_path)
        ledger3.check_before_scan(
            (archive_path, evidence_path),
            bootstrap_primaries={archive_path: _counting_initializer(calls2)},
        )
        ledger3.abort_scan()
        assert len(calls2) == 1


# -- no incomplete refresh produces structural observations -----------------------------------


class TestNoIncompleteRefreshProducesObservationsDuringColdStart:
    def test_incomplete_refresh_on_the_bootstrapped_first_scan_records_no_observations(
        self, tmp_path: Path
    ) -> None:
        class RepeatingCursorTransport(FakeUniverseTransport):
            def get(self, path: str, *, timeout_seconds: float) -> dict[str, object]:
                del timeout_seconds
                if path.startswith("/trade-api/v2/markets"):
                    return {"markets": [], "cursor": "same-cursor"}
                return {"events": [], "cursor": ""}

        archive_path = tmp_path / "universe.sqlite"
        store = StructuralMeasurementStore(tmp_path / "observations.sqlite")
        ledger = _new_ledger(tmp_path)
        assert not archive_path.exists()

        results = list(
            run_forever(
                archive_path=str(archive_path),
                store=store,
                source_authority="test",
                max_iterations=1,
                retention=ledger,
                universe_transport=RepeatingCursorTransport([], []),
                clock=lambda: NOW,
            )
        )
        assert len(results) == 1
        assert not results[0].refresh_complete
        assert results[0].observations == ()
        assert store.all_observations() == []

        # Retention still requires and records evidence for an incomplete scan -- accounting is
        # independent of whether structural observations were produced.
        state = retention_module.json.loads(ledger.state_path.read_text())
        assert len(state["scans"]) == 1
        receipt_path = next(ledger.receipts.glob("*.json"))
        receipt = retention_module.json.loads(receipt_path.read_text())
        assert receipt["complete"] is False
        assert receipt["production_influence"] == 0
        assert archive_path.exists()  # the archive was still bootstrapped despite incompleteness
