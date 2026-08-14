"""Append-only SQLite ledger for M26C research outcome evaluations."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .evaluation import (
    EvaluationDatasetManifest,
    EvaluationEligibility,
    ReceiptEvaluation,
    effective_evaluations,
    evaluation_logical_material,
    restore_evaluation,
)


class EvaluationStoreError(ValueError):
    pass


class EvaluationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as db:
                check = db.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise EvaluationStoreError("evaluation database integrity check failed")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS receipt_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    market_ticker TEXT NOT NULL,
                    market_family TEXT,
                    policy_version TEXT NOT NULL,
                    settlement_hash TEXT NOT NULL,
                    settlement_track_id TEXT NOT NULL,
                    eligibility TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    canonical_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
                    production_influence TEXT NOT NULL DEFAULT '0' CHECK(production_influence='0')
                );
                CREATE INDEX IF NOT EXISTS receipt_evaluations_receipt
                    ON receipt_evaluations(
                        receipt_id,policy_version,evaluated_at DESC,evaluation_id DESC
                    );
                CREATE INDEX IF NOT EXISTS receipt_evaluations_agent
                    ON receipt_evaluations(
                        agent_id,agent_version,evaluated_at DESC,evaluation_id DESC
                    );
                CREATE INDEX IF NOT EXISTS receipt_evaluations_family
                    ON receipt_evaluations(market_family,evaluated_at DESC,evaluation_id DESC);
                CREATE TRIGGER IF NOT EXISTS receipt_evaluations_no_update
                BEFORE UPDATE ON receipt_evaluations BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS receipt_evaluations_no_delete
                BEFORE DELETE ON receipt_evaluations BEGIN SELECT RAISE(ABORT,'append only'); END;
                """)
        except EvaluationStoreError:
            raise
        except sqlite3.Error as exc:
            raise EvaluationStoreError("evaluation schema initialization rejected") from exc
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def append(self, evaluation: ReceiptEvaluation) -> bool:
        canonical = evaluation.to_json()
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                identity_row = db.execute(
                    "SELECT * FROM receipt_evaluations WHERE evaluation_id=?",
                    (evaluation.evaluation_id,),
                ).fetchone()
                if identity_row is not None:
                    existing = self._restore(identity_row)
                    if existing is None:
                        raise EvaluationStoreError("persisted evaluation disappeared")
                    if evaluation_logical_material(existing) == evaluation_logical_material(
                        evaluation
                    ):
                        return False
                    raise EvaluationStoreError("evaluation logical identity collision")
                track_rows = db.execute(
                    "SELECT * FROM receipt_evaluations "
                    "WHERE receipt_id=? AND policy_version=? "
                    "ORDER BY evaluated_at,evaluation_id",
                    (evaluation.receipt_id, evaluation.policy_version),
                ).fetchall()
                prior = tuple(
                    value for row in track_rows if (value := self._restore(row)) is not None
                )
                if evaluation.eligibility is EvaluationEligibility.ELIGIBLE and any(
                    value.eligibility is EvaluationEligibility.ELIGIBLE for value in prior
                ):
                    raise EvaluationStoreError("contradictory authoritative final evaluation")
                if any(
                    value.settlement_track_id != evaluation.settlement_track_id for value in prior
                ):
                    raise EvaluationStoreError("evaluation settlement lifecycle conflict")
                db.execute(
                    "INSERT INTO receipt_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evaluation.evaluation_id,
                        evaluation.receipt_id,
                        evaluation.agent_id,
                        evaluation.agent_version,
                        evaluation.market_ticker,
                        evaluation.market_family,
                        evaluation.policy_version,
                        evaluation.settlement_hash,
                        evaluation.settlement_track_id,
                        evaluation.eligibility.value,
                        evaluation.evaluated_at.isoformat(timespec="microseconds"),
                        canonical,
                        digest,
                        "0",
                    ),
                )
        except EvaluationStoreError:
            raise
        except sqlite3.Error as exc:
            raise EvaluationStoreError("evaluation persistence rejected") from exc
        return True

    @staticmethod
    def _restore(row: sqlite3.Row | None) -> ReceiptEvaluation | None:
        if row is None:
            return None
        canonical = str(row["canonical_json"])
        if hashlib.sha256(canonical.encode()).hexdigest() != row["content_hash"]:
            raise EvaluationStoreError("persisted evaluation failed integrity check")
        try:
            value = restore_evaluation(canonical)
            if not all(
                (
                    row["evaluation_id"] == value.evaluation_id,
                    row["receipt_id"] == value.receipt_id,
                    row["agent_id"] == value.agent_id,
                    row["agent_version"] == value.agent_version,
                    row["market_ticker"] == value.market_ticker,
                    row["market_family"] == value.market_family,
                    row["policy_version"] == value.policy_version,
                    row["settlement_hash"] == value.settlement_hash,
                    row["settlement_track_id"] == value.settlement_track_id,
                    row["eligibility"] == value.eligibility.value,
                    row["evaluated_at"] == value.evaluated_at.isoformat(timespec="microseconds"),
                    row["production_influence"] == "0",
                )
            ):
                raise EvaluationStoreError("persisted evaluation metadata is inconsistent")
            return value
        except ValueError as exc:
            raise EvaluationStoreError("persisted evaluation is invalid") from exc

    def get(self, evaluation_id: str) -> ReceiptEvaluation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM receipt_evaluations WHERE evaluation_id=?", (evaluation_id,)
            ).fetchone()
        return self._restore(row)

    def for_receipt(self, receipt_id: str) -> tuple[ReceiptEvaluation, ...]:
        return self._query_all("WHERE receipt_id=?", (receipt_id,))

    def effective_evaluation_for_receipt(
        self, receipt_id: str, policy_version: str
    ) -> ReceiptEvaluation | None:
        values = self._query_all(
            "WHERE receipt_id=? AND policy_version=?", (receipt_id, policy_version)
        )
        effective = effective_evaluations(values, policy_version=policy_version)
        return effective[0] if effective else None

    def recent(self, limit: int = 100) -> tuple[ReceiptEvaluation, ...]:
        return self._query("", (), limit)

    def all(self) -> tuple[ReceiptEvaluation, ...]:
        return self._query_all("", ())

    def for_agent(
        self, agent_id: str, agent_version: str | None = None, limit: int = 1000
    ) -> tuple[ReceiptEvaluation, ...]:
        return self._query(
            "WHERE agent_id=?" if agent_version is None else "WHERE agent_id=? AND agent_version=?",
            (agent_id,) if agent_version is None else (agent_id, agent_version),
            limit,
        )

    def all_for_agent(
        self, agent_id: str, agent_version: str | None = None
    ) -> tuple[ReceiptEvaluation, ...]:
        return self._query_all(
            "WHERE agent_id=?" if agent_version is None else "WHERE agent_id=? AND agent_version=?",
            (agent_id,) if agent_version is None else (agent_id, agent_version),
        )

    def for_market_family(self, family: str, limit: int = 1000) -> tuple[ReceiptEvaluation, ...]:
        return self._query("WHERE market_family=?", (family,), limit)

    def _query(
        self, where: str, parameters: tuple[object, ...], limit: int
    ) -> tuple[ReceiptEvaluation, ...]:
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM receipt_evaluations {where} "  # noqa: S608
                "ORDER BY evaluated_at DESC,evaluation_id DESC LIMIT ?",
                (*parameters, min(max(limit, 1), 10000)),
            ).fetchall()
        return tuple(value for row in rows if (value := self._restore(row)) is not None)

    def _query_all(
        self, where: str, parameters: tuple[object, ...]
    ) -> tuple[ReceiptEvaluation, ...]:
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM receipt_evaluations {where} "  # noqa: S608
                "ORDER BY evaluated_at DESC,evaluation_id DESC",
                parameters,
            ).fetchall()
        return tuple(value for row in rows if (value := self._restore(row)) is not None)

    def count_for_agent_version(
        self, agent_id: str, agent_version: str, policy_version: str | None = None
    ) -> int:
        query = "SELECT COUNT(*) FROM receipt_evaluations WHERE agent_id=? AND agent_version=?"
        parameters: tuple[object, ...] = (agent_id, agent_version)
        if policy_version is not None:
            query += " AND policy_version=?"
            parameters = (*parameters, policy_version)
        with self._connect() as db:
            row = db.execute(query, parameters).fetchone()
        return 0 if row is None else int(row[0])

    def build_manifest(
        self,
        *,
        agent_id: str,
        agent_version: str,
        start_at: datetime,
        end_at: datetime,
        policy_version: str,
        generated_at: datetime,
        market_family: str | None = None,
    ) -> EvaluationDatasetManifest:
        clauses = [
            "agent_id=?",
            "agent_version=?",
            "policy_version=?",
            "evaluated_at>=?",
            "evaluated_at<=?",
        ]
        parameters: list[object] = [
            agent_id,
            agent_version,
            policy_version,
            start_at.isoformat(timespec="microseconds"),
            end_at.isoformat(timespec="microseconds"),
        ]
        if market_family is not None:
            clauses.append("market_family=?")
            parameters.append(market_family)
        universe = self._query_all("WHERE " + " AND ".join(clauses), tuple(parameters))
        return EvaluationDatasetManifest._materialize_complete_universe(
            universe,
            start_at=start_at,
            end_at=end_at,
            agent_versions=((agent_id, agent_version),),
            policy_version=policy_version,
            market_family=market_family,
            generated_at=generated_at,
        )

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT eligibility,COUNT(*) count FROM receipt_evaluations "
                "GROUP BY eligibility ORDER BY eligibility"
            ).fetchall()
        result = {str(row["eligibility"]): int(row["count"]) for row in rows}
        result["TOTAL"] = sum(result.values())
        result["ELIGIBLE"] = result.get(EvaluationEligibility.ELIGIBLE.value, 0)
        return result
