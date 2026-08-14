"""Dedicated append-only SQLite persistence for M26B research receipts."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from pathlib import Path

from .attribution import (
    OpportunityAttribution,
    receipt_identity,
    require_current_attribution_authority,
)
from .domain import DecisionReceipt, _restore_persisted_receipt


class DecisionReceiptStoreError(ValueError):
    pass


class DecisionReceiptStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as db:
                result = db.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise DecisionReceiptStoreError(
                        "decision receipt database integrity check failed"
                    )
                db.executescript("""
                CREATE TABLE IF NOT EXISTS decision_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    canonical_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0'),
                    UNIQUE(source_kind, source_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS decision_receipts_source_identity
                    ON decision_receipts(source_kind, source_id);
                CREATE INDEX IF NOT EXISTS decision_receipts_agent_time
                    ON decision_receipts(agent_id, created_at DESC, receipt_id DESC);
                CREATE INDEX IF NOT EXISTS decision_receipts_instrument_time
                    ON decision_receipts(instrument_id, created_at DESC, receipt_id DESC);
                CREATE INDEX IF NOT EXISTS decision_receipts_decision
                    ON decision_receipts(decision, agent_id);
                CREATE TRIGGER IF NOT EXISTS decision_receipts_no_update
                BEFORE UPDATE ON decision_receipts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS decision_receipts_no_delete
                BEFORE DELETE ON decision_receipts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                """)
        except DecisionReceiptStoreError:
            raise
        except sqlite3.Error as exc:
            raise DecisionReceiptStoreError(
                "decision receipt schema initialization rejected"
            ) from exc
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def append(self, attribution: OpportunityAttribution) -> bool:
        if not isinstance(attribution, OpportunityAttribution):
            raise DecisionReceiptStoreError("explicit OpportunityAttribution is required")
        try:
            require_current_attribution_authority(attribution.agent_id)
        except ValueError as exc:
            raise DecisionReceiptStoreError(
                "new attribution agent lacks current authority"
            ) from exc
        receipt = attribution.receipt
        canonical = receipt.to_json()
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT receipt_id,content_hash FROM decision_receipts "
                    "WHERE receipt_id=? OR (source_kind=? AND source_id=?)",
                    (
                        receipt.receipt_id,
                        attribution.source_kind,
                        attribution.source_id,
                    ),
                ).fetchone()
                if row is not None:
                    if row["receipt_id"] == receipt.receipt_id and row["content_hash"] == digest:
                        return False
                    raise DecisionReceiptStoreError("decision receipt logical identity collision")
                db.execute(
                    "INSERT INTO decision_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt.receipt_id,
                        receipt.agent_id,
                        receipt.instrument_id,
                        receipt.created_at.isoformat(timespec="microseconds"),
                        receipt.decision.value,
                        attribution.source_kind,
                        attribution.source_id,
                        canonical,
                        digest,
                        "0",
                    ),
                )
        except DecisionReceiptStoreError:
            raise
        except sqlite3.Error as exc:
            raise DecisionReceiptStoreError("decision receipt persistence rejected") from exc
        return True

    @staticmethod
    def _restore(row: sqlite3.Row | None) -> DecisionReceipt | None:
        if row is None:
            return None
        canonical = str(row["canonical_json"])
        if hashlib.sha256(canonical.encode()).hexdigest() != row["content_hash"]:
            raise DecisionReceiptStoreError("persisted decision receipt failed integrity check")
        try:
            receipt = _restore_persisted_receipt(canonical)
            expected_identity = receipt_identity(
                receipt.agent_id,
                receipt.agent_version,
                str(row["source_kind"]),
                str(row["source_id"]),
            )
            metadata = (
                row["receipt_id"] == receipt.receipt_id,
                row["agent_id"] == receipt.agent_id,
                row["instrument_id"] == receipt.instrument_id,
                row["created_at"] == receipt.created_at.isoformat(timespec="microseconds"),
                row["decision"] == receipt.decision.value,
                row["production_influence"] == "0",
                receipt.receipt_id == expected_identity,
            )
            if not all(metadata):
                raise ValueError("persisted decision receipt metadata is inconsistent")
            return receipt
        except (TypeError, ValueError) as exc:
            raise DecisionReceiptStoreError("persisted decision receipt is invalid") from exc

    def get(self, receipt_id: str) -> DecisionReceipt | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM decision_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
        return self._restore(row)

    def latest_for_agent(self, agent_id: str) -> DecisionReceipt | None:
        rows = self.recent_for_agent(agent_id, 1)
        return rows[0] if rows else None

    def recent_for_agent(self, agent_id: str, limit: int = 20) -> tuple[DecisionReceipt, ...]:
        return self._query("WHERE agent_id=?", (agent_id,), limit)

    def recent(self, limit: int = 20) -> tuple[DecisionReceipt, ...]:
        return self._query("", (), limit)

    def for_instrument(self, instrument_id: str, limit: int = 100) -> tuple[DecisionReceipt, ...]:
        return self._query("WHERE instrument_id=?", (instrument_id,), limit)

    def _query(
        self, where: str, parameters: tuple[object, ...], limit: int
    ) -> tuple[DecisionReceipt, ...]:
        bounded = min(max(limit, 1), 1000)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM decision_receipts {where} "  # noqa: S608
                "ORDER BY created_at DESC,receipt_id DESC LIMIT ?",
                (*parameters, bounded),
            ).fetchall()
        return tuple(receipt for row in rows if (receipt := self._restore(row)) is not None)

    def counts(self) -> dict[tuple[str, str], int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT agent_id,decision,COUNT(*) count FROM decision_receipts "
                "GROUP BY agent_id,decision ORDER BY agent_id,decision"
            ).fetchall()
        return dict(Counter({(row["agent_id"], row["decision"]): row["count"] for row in rows}))
