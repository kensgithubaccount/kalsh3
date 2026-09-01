"""Dedicated append-only SQLite persistence for M27B.2 structural-lead observations.

Mirrors the append-only pattern used throughout this repository's other research evidence
stores (see :mod:`services.perps_shadow_research.store`): WAL journaling, a startup integrity
check, ``BEFORE UPDATE``/``BEFORE DELETE`` triggers that abort any mutation, and idempotent
append keyed by a content-addressed primary key. Every row is permanently
``production_influence='0'``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .domain import OpportunityError
from .structural import RelationshipType
from .structural_measurement import FeeTreatment, LeadObservation, MeasurementState

_COLUMNS = (
    "observation_id",
    "relationship_id",
    "scan_run_id",
    "observed_at",
    "event_ticker",
    "broad_market_ticker",
    "narrow_market_ticker",
    "broad_threshold",
    "narrow_threshold",
    "relationship_type",
    "lead_id",
    "broad_quote_source_hash",
    "narrow_quote_source_hash",
    "gross_apparent_gap",
    "indicative_quantity",
    "confirmed_depth",
    "fee_treatment",
    "formula_adjusted_gap",
    "confirmation_id",
    "state",
    "blocker_reason",
    "source_authority",
    "production_influence",
)


class StructuralMeasurementStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise OpportunityError("structural measurement database integrity check failed")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS structural_lead_observations (
                    observation_id TEXT PRIMARY KEY,
                    relationship_id TEXT NOT NULL,
                    scan_run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    broad_market_ticker TEXT NOT NULL,
                    narrow_market_ticker TEXT NOT NULL,
                    broad_threshold TEXT NOT NULL,
                    narrow_threshold TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    lead_id TEXT,
                    broad_quote_source_hash TEXT,
                    narrow_quote_source_hash TEXT,
                    gross_apparent_gap TEXT,
                    indicative_quantity TEXT,
                    confirmed_depth TEXT,
                    fee_treatment TEXT NOT NULL,
                    formula_adjusted_gap TEXT,
                    confirmation_id TEXT,
                    state TEXT NOT NULL,
                    blocker_reason TEXT,
                    source_authority TEXT NOT NULL,
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0')
                );
                CREATE INDEX IF NOT EXISTS structural_lead_observations_relationship
                    ON structural_lead_observations(relationship_id, observed_at);
                CREATE INDEX IF NOT EXISTS structural_lead_observations_scan
                    ON structural_lead_observations(scan_run_id);
                CREATE TRIGGER IF NOT EXISTS structural_lead_observations_no_update
                BEFORE UPDATE ON structural_lead_observations
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS structural_lead_observations_no_delete
                BEFORE DELETE ON structural_lead_observations
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _text(value: Decimal | datetime | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(timespec="microseconds")
        return str(value)

    def _values(self, observation: LeadObservation) -> tuple[str | None, ...]:
        return (
            observation.observation_id,
            observation.relationship_id,
            observation.scan_run_id,
            self._text(observation.observed_at),
            observation.event_ticker,
            observation.broad_market_ticker,
            observation.narrow_market_ticker,
            self._text(observation.broad_threshold),
            self._text(observation.narrow_threshold),
            observation.relationship_type.value,
            observation.lead_id,
            observation.broad_quote_source_hash,
            observation.narrow_quote_source_hash,
            self._text(observation.gross_apparent_gap),
            self._text(observation.indicative_quantity),
            self._text(observation.confirmed_depth),
            observation.fee_treatment.value,
            self._text(observation.formula_adjusted_gap),
            observation.confirmation_id,
            observation.state.value,
            observation.blocker_reason,
            observation.source_authority,
            str(observation.production_influence),
        )

    def append(self, observation: LeadObservation) -> bool:
        """Insert one observation. Returns ``False`` (no-op) if an identical row already exists
        (idempotent replay of the same scan), raises if a different row claims the same
        content-addressed ``observation_id`` (a collision, never silently overwritten)."""
        if not isinstance(observation, LeadObservation):
            raise OpportunityError("only LeadObservation may be persisted")
        values = self._values(observation)
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT * FROM structural_lead_observations WHERE observation_id=?",
                    (observation.observation_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing[column] for column in _COLUMNS) == values:
                        return False
                    raise OpportunityError("observation_id collision with differing content")
                placeholders = ",".join("?" for _ in _COLUMNS)
                insert = (
                    f"INSERT INTO structural_lead_observations ({','.join(_COLUMNS)}) "  # noqa: S608
                    f"VALUES ({placeholders})"
                )
                db.execute(insert, values)
        except sqlite3.Error as exc:
            raise OpportunityError("structural measurement persistence rejected") from exc
        return True

    def for_relationship(self, relationship_id: str) -> list[LeadObservation]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM structural_lead_observations WHERE relationship_id=? "
                "ORDER BY observed_at ASC",
                (relationship_id,),
            ).fetchall()
        return [self._restore(row) for row in rows]

    def all_observations(self) -> list[LeadObservation]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM structural_lead_observations ORDER BY observed_at ASC"
            ).fetchall()
        return [self._restore(row) for row in rows]

    def relationship_ids(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT relationship_id FROM structural_lead_observations"
            ).fetchall()
        return [row["relationship_id"] for row in rows]

    @staticmethod
    def _restore(row: sqlite3.Row) -> LeadObservation:
        def decimal(value: str | None) -> Decimal | None:
            return None if value is None else Decimal(value)

        observed_at = datetime.fromisoformat(row["observed_at"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return LeadObservation(
            row["observation_id"],
            row["relationship_id"],
            row["scan_run_id"],
            observed_at,
            row["event_ticker"],
            row["broad_market_ticker"],
            row["narrow_market_ticker"],
            Decimal(row["broad_threshold"]),
            Decimal(row["narrow_threshold"]),
            RelationshipType(row["relationship_type"]),
            row["lead_id"],
            row["broad_quote_source_hash"],
            row["narrow_quote_source_hash"],
            decimal(row["gross_apparent_gap"]),
            decimal(row["indicative_quantity"]),
            decimal(row["confirmed_depth"]),
            FeeTreatment(row["fee_treatment"]),
            decimal(row["formula_adjusted_gap"]),
            row["confirmation_id"],
            MeasurementState(row["state"]),
            row["blocker_reason"],
            row["source_authority"],
            True,
            Decimal(row["production_influence"]),
        )
