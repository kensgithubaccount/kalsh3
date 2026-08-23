from __future__ import annotations

import ast
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.risk_engine.domain import (
    ComplianceState,
    KillCategory,
    KillLevel,
    ReconciliationStatus,
)
from services.supervised_canary.m27o_state_bootstrap import (
    EXACT_CONFIRMATION,
    bootstrap_state,
)
from services.supervised_canary.m27q_state_inspection import (
    M27IImmutableStateView,
    M27QStateInspectionError,
    build_safety_state,
    inspect_first_canary_state,
)

NOW = datetime(2026, 8, 22, 23, 45, tzinfo=UTC)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bootstrap(tmp_path: Path) -> Path:
    path = tmp_path / "production-canary" / "m27o-shared.sqlite3"
    bootstrap_state(
        state_path=path,
        actor="M27Q TEST",
        reason="read-only state inspection fixture",
        confirmation=EXACT_CONFIRMATION,
    )
    return path


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    )


def _checkpoint_and_remove_sidecars(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert result is not None
        assert int(result[0]) == 0
    finally:
        db.close()

    for sidecar in _sidecars(path):
        if sidecar.exists():
            if sidecar.name.endswith("-wal"):
                assert sidecar.stat().st_size == 0
            sidecar.unlink()


def test_pristine_bootstrap_inspection_is_filesystem_read_only(
    tmp_path: Path,
) -> None:
    path = _bootstrap(tmp_path)

    before_sha = _sha(path)
    before_stat = path.stat()
    before_names = sorted(item.name for item in path.parent.iterdir())

    result = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )

    after_sha = _sha(path)
    after_stat = path.stat()
    after_names = sorted(item.name for item in path.parent.iterdir())

    assert result.pristine_first_canary
    assert result.database_sha256 == before_sha == after_sha

    assert before_stat.st_ino == after_stat.st_ino
    assert before_stat.st_size == after_stat.st_size
    assert before_stat.st_mtime_ns == after_stat.st_mtime_ns
    assert before_names == after_names

    assert not any(sidecar.exists() for sidecar in _sidecars(path))


def test_pristine_inspection_maps_to_m27q_durable_and_safety_state(
    tmp_path: Path,
) -> None:
    path = _bootstrap(tmp_path)

    inspection = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )
    durable = inspection.first_canary_durable_state()
    safety = build_safety_state(
        inspection,
        now=NOW,
        reconciliation_status=ReconciliationStatus.RECONCILED,
    )

    assert durable.production_state == "DISARMED"
    assert durable.real_submission_count == 0
    assert durable.real_fill_count == 0
    assert durable.unresolved_canary_present is False

    assert safety.global_halt is False
    assert safety.compliance is ComplianceState.CLEAR
    assert safety.reconciliation is ReconciliationStatus.RECONCILED
    assert {kill.category for kill in safety.kills} == set(KillCategory)
    assert all(kill.level is KillLevel.NORMAL for kill in safety.kills)

    assert safety.losses.daily_loss == 0
    assert safety.losses.weekly_loss == 0
    assert safety.losses.monthly_loss == 0
    assert safety.losses.drawdown == 0
    assert safety.losses.version == inspection.loss_state_version


def test_missing_database_blocks_without_creating_anything(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.sqlite3"

    with pytest.raises(
        M27QStateInspectionError,
        match="does not exist",
    ):
        inspect_first_canary_state(
            state_path=path,
            now=NOW,
        )

    assert not path.exists()
    assert not any(sidecar.exists() for sidecar in _sidecars(path))


def test_symlink_database_path_is_rejected(tmp_path: Path) -> None:
    real_path = _bootstrap(tmp_path / "real")
    link_path = tmp_path / "linked.sqlite3"
    link_path.symlink_to(real_path)

    with pytest.raises(
        M27QStateInspectionError,
        match="regular non-symlink",
    ):
        inspect_first_canary_state(
            state_path=link_path,
            now=NOW,
        )


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_any_existing_sqlite_sidecar_blocks_immutable_read(
    tmp_path: Path,
    suffix: str,
) -> None:
    path = _bootstrap(tmp_path)
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_bytes(b"")

    with pytest.raises(
        M27QStateInspectionError,
        match="SQLite sidecars",
    ):
        inspect_first_canary_state(
            state_path=path,
            now=NOW,
        )


def test_wrong_file_mode_blocks(tmp_path: Path) -> None:
    path = _bootstrap(tmp_path)
    path.chmod(0o644)

    with pytest.raises(
        M27QStateInspectionError,
        match="mode is not 0600",
    ):
        inspect_first_canary_state(
            state_path=path,
            now=NOW,
        )


def test_spent_submission_budget_is_observed_but_not_accepted_as_pristine(
    tmp_path: Path,
) -> None:
    path = _bootstrap(tmp_path)

    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE production_submission_counter "
            "SET real_submission_count=1 WHERE singleton=1"
        )
    _checkpoint_and_remove_sidecars(path)

    inspection = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )

    assert inspection.real_submission_count == 1
    assert inspection.pristine_first_canary is False

    with pytest.raises(
        M27QStateInspectionError,
        match="not pristine",
    ):
        inspection.first_canary_durable_state()


def test_compliance_hold_is_preserved_in_safety_state(
    tmp_path: Path,
) -> None:
    path = _bootstrap(tmp_path)

    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE compliance_state "
            "SET state='HOLD',reason='test hold' "
            "WHERE singleton=1"
        )
    _checkpoint_and_remove_sidecars(path)

    inspection = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )
    safety = build_safety_state(
        inspection,
        now=NOW,
        reconciliation_status=ReconciliationStatus.RECONCILED,
    )

    assert inspection.pristine_first_canary is False
    assert safety.compliance is ComplianceState.HOLD


def test_state_versions_are_deterministic(tmp_path: Path) -> None:
    path = _bootstrap(tmp_path)

    one = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )
    two = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )

    assert one.compliance_state_version == two.compliance_state_version
    assert one.kill_state_version == two.kill_state_version
    assert one.loss_state_version == two.loss_state_version


def test_source_is_structurally_read_only() -> None:
    path = Path(
        "services/supervised_canary/m27q_state_inspection.py"
    )
    source = path.read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    forbidden_imports = {
        "services.supervised_canary.store",
        "services.risk_engine.authorization",
        "services.production_execution",
        "services.kalshi_account_gateway",
        "socket",
        "urllib",
        "requests",
        "httpx",
    }

    assert not any(
        module == forbidden or module.startswith(forbidden + ".")
        for module in imported
        for forbidden in forbidden_imports
    )

    upper = source.upper()
    assert "IMMUTABLE=1" in upper
    assert "MODE=RO" in upper
    assert "INSERT INTO" not in upper
    assert "UPDATE " not in upper
    assert "DELETE FROM" not in upper
    assert "BEGIN IMMEDIATE" not in upper
    assert "JOURNAL_MODE=WAL" not in upper


def test_no_real_default_state_path_is_referenced_by_tests() -> None:
    source = Path(__file__).read_text()
    forbidden_default = ".local/state/" + "kalsh3/production-canary"
    forbidden_user = "/Users/" + "ksyme"
    assert forbidden_default not in source
    assert forbidden_user not in source



def test_immutable_state_view_matches_m27i_read_contract(
    tmp_path: Path,
) -> None:
    path = _bootstrap(tmp_path)

    inspection = inspect_first_canary_state(
        state_path=path,
        now=NOW,
    )
    view = M27IImmutableStateView(inspection)

    summary = view.safety_summary()

    assert summary["global_halt"] is False
    assert summary["compliance_state"] == "CLEAR"
    assert summary["loss_state_version"] == inspection.loss_state_version

    kill_rows = summary["kill_states"]
    assert isinstance(kill_rows, tuple)
    assert len(kill_rows) == 4
    assert all(row[1] == "NORMAL" for row in kill_rows)

    assert view.submission_budget_used() is False
    assert view.unresolved_canary_present() is False

    # Calling the M27I-facing methods does not reopen SQLite or create sidecars.
    assert not any(sidecar.exists() for sidecar in _sidecars(path))
