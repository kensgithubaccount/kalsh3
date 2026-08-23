"""M27Q read-only inspection of first-canary durable SQLite state.

This module never constructs CanaryStore or AuthorizationStore. Those classes
are schema-owning mutable boundaries and therefore inappropriate for a
preflight inspection whose job is only to observe already-created state.

The M27P bootstrap publishes a fully checkpointed SQLite main database with no
WAL/SHM/journal sidecars. For this first-canary-only inspector we preserve that
invariant: any sidecar blocks inspection. The database is then opened through
SQLite's immutable read-only URI so the inspection itself cannot create WAL or
SHM bookkeeping files.

Once the shared state has entered normal mutable operation, this inspector is
no longer the correct access path.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from services.risk_engine.domain import (
    ComplianceState,
    KillCategory,
    KillLevel,
    ReconciliationStatus,
    content_hash,
)
from services.risk_engine.invariants import CANONICAL_POLICY
from services.risk_engine.states import KillState, SafetyState, evaluate_loss_windows

from .m27q_risk_preflight import FirstCanaryDurableState

SOFTWARE_VERSION = "kalsh3.m27q.first-canary-state-inspection/1"

REQUIRED_TABLES = frozenset(
    {
        "canary_runtime",
        "canary_previews",
        "canary_approvals",
        "canary_sessions",
        "production_fill_counter",
        "production_submission_counter",
        "canary_events",
        "risk_authorizations",
        "risk_reservations",
        "global_halt_state",
        "compliance_state",
        "risk_events",
        "durable_kill_states",
        "durable_loss_holds",
    }
)

UNRESOLVED_STATES = frozenset(
    {
        "READY_FOR_APPROVAL",
        "AWAITING_REAUTH",
        "HUMAN_APPROVED",
        "FINAL_REVALIDATION",
        "CANARY_AUTHORIZED",
        "SUBMISSION_PENDING",
        "SUBMITTED_OR_UNKNOWN",
        "RECONCILING",
    }
)

EXPECTED_KILL_CATEGORIES = frozenset(KillCategory)


class M27QStateInspectionError(RuntimeError):
    """Durable state could not be inspected without weakening first-canary safety."""


@dataclass(frozen=True, slots=True)
class DurableKillRow:
    category: KillCategory
    level: KillLevel
    reason: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class FirstCanaryStateInspection:
    software_version: str
    state_path: Path
    inspected_at: datetime
    database_sha256: str

    production_state: str
    real_submission_count: int
    real_fill_count: int

    preview_count: int
    approval_count: int
    session_count: int
    unresolved_canary_count: int

    risk_authorization_count: int
    risk_reservation_count: int
    active_risk_reservation_count: int

    global_halt_active: bool
    global_halt_reason: str | None

    compliance_state: ComplianceState
    compliance_reason: str | None
    compliance_changed_at: datetime
    compliance_actor: str

    kill_states: tuple[DurableKillRow, ...]

    weekly_review_required: bool
    monthly_review_required: bool
    experiment_halt_required: bool
    loss_state_version: str
    loss_state_changed_at: datetime

    @property
    def compliance_state_version(self) -> str:
        return content_hash(
            (
                "m27q-compliance-state",
                self.compliance_state,
                self.compliance_reason,
                self.compliance_changed_at,
                self.compliance_actor,
            )
        )

    @property
    def kill_state_version(self) -> str:
        return content_hash(
            tuple(
                (
                    row.category,
                    row.level,
                    row.reason,
                    row.changed_at,
                )
                for row in self.kill_states
            )
        )

    @property
    def pristine_first_canary(self) -> bool:
        kills_normal = (
            {row.category for row in self.kill_states}
            == EXPECTED_KILL_CATEGORIES
            and all(row.level is KillLevel.NORMAL for row in self.kill_states)
        )

        return (
            self.production_state == "DISARMED"
            and self.real_submission_count == 0
            and self.real_fill_count == 0
            and self.preview_count == 0
            and self.approval_count == 0
            and self.session_count == 0
            and self.unresolved_canary_count == 0
            and self.risk_authorization_count == 0
            and self.risk_reservation_count == 0
            and self.active_risk_reservation_count == 0
            and not self.global_halt_active
            and self.compliance_state is ComplianceState.CLEAR
            and kills_normal
            and not self.weekly_review_required
            and not self.monthly_review_required
            and not self.experiment_halt_required
        )

    def first_canary_durable_state(self) -> FirstCanaryDurableState:
        if not self.pristine_first_canary:
            raise M27QStateInspectionError(
                "durable production state is not pristine for the first canary"
            )

        return FirstCanaryDurableState(
            production_state=self.production_state,
            real_submission_count=self.real_submission_count,
            real_fill_count=self.real_fill_count,
            unresolved_canary_present=False,
        )


@dataclass(frozen=True, slots=True)
class M27IImmutableStateView:
    """Adapter exposing only the read interfaces M27I requires.

    The underlying inspection has already been obtained through immutable
    SQLite access. These methods perform no further filesystem or database
    operation.
    """

    inspection: FirstCanaryStateInspection

    def safety_summary(self) -> dict[str, object]:
        return {
            "global_halt": self.inspection.global_halt_active,
            "compliance_state": self.inspection.compliance_state.value,
            "kill_states": tuple(
                (
                    row.category.value,
                    row.level.value,
                    row.reason,
                    row.changed_at.isoformat(),
                )
                for row in self.inspection.kill_states
            ),
            "loss_state_version": self.inspection.loss_state_version,
        }

    def submission_budget_used(self) -> bool:
        return self.inspection.real_submission_count != 0

    def unresolved_canary_present(self) -> bool:
        return self.inspection.unresolved_canary_count != 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    )


def _require_no_sidecars(path: Path) -> None:
    existing = [item.name for item in _sidecars(path) if os.path.lexists(item)]
    if existing:
        raise M27QStateInspectionError(
            "first-canary state has SQLite sidecars and cannot be read as immutable"
        )


def _parse_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise M27QStateInspectionError(f"{field} is malformed")

    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise M27QStateInspectionError(f"{field} is malformed") from exc

    if result.tzinfo is None or result.utcoffset() is None:
        raise M27QStateInspectionError(f"{field} is not timezone-aware")

    return result


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise M27QStateInspectionError(f"{field} is malformed")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise M27QStateInspectionError(f"{field} is malformed")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise M27QStateInspectionError(f"{field} is malformed")
    return value


def _bool_int(value: object, *, field: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise M27QStateInspectionError(f"{field} is malformed")
    return bool(value)


def _one_row(
    db: sqlite3.Connection,
    query: str,
    *,
    field: str,
) -> tuple[object, ...]:
    rows = db.execute(query).fetchall()
    if len(rows) != 1:
        raise M27QStateInspectionError(f"{field} singleton is unavailable")
    return tuple(rows[0])


def _count(db: sqlite3.Connection, query: str, *, field: str) -> int:
    row = _one_row(db, query, field=field)
    if len(row) != 1:
        raise M27QStateInspectionError(f"{field} count is malformed")
    return _nonnegative_int(row[0], field=field)


def inspect_first_canary_state(
    *,
    state_path: Path,
    now: datetime,
) -> FirstCanaryStateInspection:
    """Read first-canary durable state without changing the filesystem."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise M27QStateInspectionError("inspection time must be timezone-aware")

    state_path = state_path.expanduser().absolute()

    if not os.path.lexists(state_path):
        raise M27QStateInspectionError("production canary state database does not exist")

    metadata = state_path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise M27QStateInspectionError(
            "production canary state path is not a regular non-symlink file"
        )

    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise M27QStateInspectionError("production canary state database mode is not 0600")

    if metadata.st_nlink != 1:
        raise M27QStateInspectionError(
            "production canary state database has unexpected hard-link aliases"
        )

    _require_no_sidecars(state_path)

    before_stat = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    before_sha = _sha256(state_path)

    # M27P guarantees all committed state has been checkpointed into the main
    # database and removes sidecars before publication. immutable=1 is therefore
    # allowed only after the explicit no-sidecar check above.
    uri = state_path.as_uri() + "?mode=ro&immutable=1"

    try:
        with sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")

            quick_check = db.execute("PRAGMA quick_check(1)").fetchone()
            if quick_check != ("ok",):
                raise M27QStateInspectionError(
                    "production canary state failed SQLite quick_check"
                )

            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not REQUIRED_TABLES.issubset(tables):
                raise M27QStateInspectionError(
                    "shared M16/M13 production schema is incomplete"
                )

            runtime = _one_row(
                db,
                "SELECT production_state "
                "FROM canary_runtime WHERE singleton=1",
                field="canary runtime",
            )
            if len(runtime) != 1:
                raise M27QStateInspectionError("canary runtime row is malformed")
            production_state = _required_text(
                runtime[0],
                field="production_state",
            )

            submission = _one_row(
                db,
                "SELECT real_submission_count "
                "FROM production_submission_counter WHERE singleton=1",
                field="submission counter",
            )
            fill = _one_row(
                db,
                "SELECT real_fill_count "
                "FROM production_fill_counter WHERE singleton=1",
                field="fill counter",
            )
            if len(submission) != 1 or len(fill) != 1:
                raise M27QStateInspectionError("production counters are malformed")

            real_submission_count = _nonnegative_int(
                submission[0],
                field="real_submission_count",
            )
            real_fill_count = _nonnegative_int(
                fill[0],
                field="real_fill_count",
            )

            preview_count = _count(
                db,
                "SELECT COUNT(*) FROM canary_previews",
                field="preview_count",
            )
            approval_count = _count(
                db,
                "SELECT COUNT(*) FROM canary_approvals",
                field="approval_count",
            )
            session_count = _count(
                db,
                "SELECT COUNT(*) FROM canary_sessions",
                field="session_count",
            )

            session_states = db.execute(
                "SELECT state FROM canary_sessions"
            ).fetchall()
            unresolved_canary_count = sum(
                isinstance(row[0], str) and row[0] in UNRESOLVED_STATES
                for row in session_states
            )

            risk_authorization_count = _count(
                db,
                "SELECT COUNT(*) FROM risk_authorizations",
                field="risk_authorization_count",
            )
            risk_reservation_count = _count(
                db,
                "SELECT COUNT(*) FROM risk_reservations",
                field="risk_reservation_count",
            )
            active_risk_reservation_count = _count(
                db,
                "SELECT COUNT(*) FROM risk_reservations WHERE active=1",
                field="active_risk_reservation_count",
            )

            halt = _one_row(
                db,
                "SELECT active,reason "
                "FROM global_halt_state WHERE singleton=1",
                field="global halt",
            )
            if len(halt) != 2:
                raise M27QStateInspectionError("global halt row is malformed")
            global_halt_active = _bool_int(
                halt[0],
                field="global_halt_active",
            )
            global_halt_reason = _optional_text(
                halt[1],
                field="global_halt_reason",
            )

            compliance = _one_row(
                db,
                "SELECT state,reason,changed_at,actor "
                "FROM compliance_state WHERE singleton=1",
                field="compliance",
            )
            if len(compliance) != 4:
                raise M27QStateInspectionError("compliance row is malformed")

            try:
                compliance_state = ComplianceState(
                    _required_text(
                        compliance[0],
                        field="compliance_state",
                    )
                )
            except ValueError as exc:
                raise M27QStateInspectionError(
                    "compliance state is unsupported"
                ) from exc

            compliance_reason = _optional_text(
                compliance[1],
                field="compliance_reason",
            )
            compliance_changed_at = _parse_datetime(
                compliance[2],
                field="compliance_changed_at",
            )
            compliance_actor = _required_text(
                compliance[3],
                field="compliance_actor",
            )

            kill_rows: list[DurableKillRow] = []
            for row in db.execute(
                "SELECT category,level,reason,changed_at "
                "FROM durable_kill_states ORDER BY category"
            ).fetchall():
                if len(row) != 4:
                    raise M27QStateInspectionError("kill-state row is malformed")
                try:
                    category = KillCategory(
                        _required_text(row[0], field="kill category")
                    )
                    level = KillLevel(
                        _required_text(row[1], field="kill level")
                    )
                except ValueError as exc:
                    raise M27QStateInspectionError(
                        "kill-state category or level is unsupported"
                    ) from exc

                kill_rows.append(
                    DurableKillRow(
                        category=category,
                        level=level,
                        reason=_required_text(
                            row[2],
                            field="kill reason",
                        ),
                        changed_at=_parse_datetime(
                            row[3],
                            field="kill changed_at",
                        ),
                    )
                )

            kill_states = tuple(kill_rows)
            if (
                len(kill_states) != len(EXPECTED_KILL_CATEGORIES)
                or {row.category for row in kill_states}
                != EXPECTED_KILL_CATEGORIES
            ):
                raise M27QStateInspectionError(
                    "durable kill-state set is incomplete or duplicated"
                )

            loss = _one_row(
                db,
                "SELECT weekly_review_required,"
                "monthly_review_required,"
                "experiment_halt_required,"
                "state_version,"
                "changed_at "
                "FROM durable_loss_holds WHERE singleton=1",
                field="durable loss state",
            )
            if len(loss) != 5:
                raise M27QStateInspectionError("durable loss row is malformed")

            weekly_review_required = _bool_int(
                loss[0],
                field="weekly_review_required",
            )
            monthly_review_required = _bool_int(
                loss[1],
                field="monthly_review_required",
            )
            experiment_halt_required = _bool_int(
                loss[2],
                field="experiment_halt_required",
            )
            loss_state_version = _required_text(
                loss[3],
                field="loss_state_version",
            )
            loss_state_changed_at = _parse_datetime(
                loss[4],
                field="loss_state_changed_at",
            )

    except sqlite3.Error as exc:
        raise M27QStateInspectionError(
            "read-only SQLite inspection failed"
        ) from exc

    _require_no_sidecars(state_path)

    after_metadata = state_path.lstat()
    after_stat = (
        after_metadata.st_dev,
        after_metadata.st_ino,
        after_metadata.st_size,
        after_metadata.st_mtime_ns,
    )
    after_sha = _sha256(state_path)

    if before_stat != after_stat or before_sha != after_sha:
        raise M27QStateInspectionError(
            "production canary database changed during read-only inspection"
        )

    return FirstCanaryStateInspection(
        software_version=SOFTWARE_VERSION,
        state_path=state_path,
        inspected_at=now,
        database_sha256=before_sha,
        production_state=production_state,
        real_submission_count=real_submission_count,
        real_fill_count=real_fill_count,
        preview_count=preview_count,
        approval_count=approval_count,
        session_count=session_count,
        unresolved_canary_count=unresolved_canary_count,
        risk_authorization_count=risk_authorization_count,
        risk_reservation_count=risk_reservation_count,
        active_risk_reservation_count=active_risk_reservation_count,
        global_halt_active=global_halt_active,
        global_halt_reason=global_halt_reason,
        compliance_state=compliance_state,
        compliance_reason=compliance_reason,
        compliance_changed_at=compliance_changed_at,
        compliance_actor=compliance_actor,
        kill_states=kill_states,
        weekly_review_required=weekly_review_required,
        monthly_review_required=monthly_review_required,
        experiment_halt_required=experiment_halt_required,
        loss_state_version=loss_state_version,
        loss_state_changed_at=loss_state_changed_at,
    )


def build_safety_state(
    inspection: FirstCanaryStateInspection,
    *,
    now: datetime,
    reconciliation_status: ReconciliationStatus,
) -> SafetyState:
    """Build M13 SafetyState from durable state plus current account reconciliation."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise M27QStateInspectionError("safety evaluation time must be timezone-aware")
    if not isinstance(reconciliation_status, ReconciliationStatus):
        raise M27QStateInspectionError(
            "reconciliation_status must be a ReconciliationStatus"
        )

    losses = evaluate_loss_windows(
        now=now,
        realized_daily_pnl=Decimal(0),
        realized_weekly_pnl=Decimal(0),
        realized_monthly_pnl=Decimal(0),
        drawdown=Decimal(0),
        policy=CANONICAL_POLICY,
        prior_weekly_review=inspection.weekly_review_required,
        prior_monthly_review=inspection.monthly_review_required,
        prior_experiment_halt=inspection.experiment_halt_required,
    )

    # The durable state version is the authority M27I independently compares
    # against the risk decision at consumption time.
    losses = replace(
        losses,
        version=inspection.loss_state_version,
    )

    return SafetyState(
        global_halt=inspection.global_halt_active,
        global_halt_reason=inspection.global_halt_reason,
        compliance=inspection.compliance_state,
        reconciliation=reconciliation_status,
        kills=tuple(
            KillState(
                category=row.category,
                level=row.level,
                reason=row.reason,
                changed_at=row.changed_at,
            )
            for row in inspection.kill_states
        ),
        losses=losses,
    )
