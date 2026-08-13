"""Dedicated append-only SQLite persistence for research book evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from services.real_time_market_data.orderbook import BookState, PriceMode

from .book_evidence import BookEvidenceObservation, BookUpdateKind
from .domain import ShadowResearchError


class BookEvidenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise ShadowResearchError("book evidence database integrity check failed")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS book_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    update_kind TEXT NOT NULL,
                    ticker TEXT NOT NULL, market_id TEXT NOT NULL,
                    exchange_index INTEGER NOT NULL CHECK(exchange_index >= 0),
                    connection_epoch TEXT NOT NULL,
                    sid INTEGER NOT NULL CHECK(sid >= 0),
                    sequence INTEGER NOT NULL CHECK(sequence >= 0),
                    source_event_fingerprint TEXT NOT NULL
                        CHECK(length(source_event_fingerprint) = 64),
                    book_state TEXT NOT NULL CHECK(book_state = 'CURRENT'),
                    best_yes_bid TEXT, best_yes_ask TEXT,
                    best_bid_size TEXT, best_ask_size TEXT,
                    exchange_at TEXT, received_at TEXT NOT NULL, available_at TEXT NOT NULL,
                    price_mode TEXT NOT NULL, structure_hash TEXT NOT NULL,
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0'),
                    UNIQUE(connection_epoch, sid, sequence, ticker)
                );
                CREATE TRIGGER IF NOT EXISTS book_evidence_no_update
                BEFORE UPDATE ON book_evidence BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS book_evidence_no_delete
                BEFORE DELETE ON book_evidence BEGIN SELECT RAISE(ABORT, 'append only'); END;
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _text(value: Decimal | datetime | UUID | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(timespec="microseconds")
        return str(value)

    def append(self, observation: BookEvidenceObservation) -> bool:
        if not isinstance(observation, BookEvidenceObservation):
            raise ShadowResearchError("only BookEvidenceObservation may be persisted")
        values = (
            observation.evidence_id,
            observation.update_kind.value,
            observation.ticker,
            observation.market_id,
            observation.exchange_index,
            str(observation.connection_epoch),
            observation.sid,
            observation.sequence,
            observation.source_event_fingerprint,
            observation.book_state.value,
            self._text(observation.best_yes_bid),
            self._text(observation.best_yes_ask),
            self._text(observation.best_bid_size),
            self._text(observation.best_ask_size),
            self._text(observation.exchange_at),
            self._text(observation.received_at),
            self._text(observation.available_at),
            observation.price_mode.value,
            observation.structure_hash,
            str(observation.production_influence),
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT evidence_id, source_event_fingerprint FROM book_evidence "
                    "WHERE connection_epoch=? AND sid=? AND sequence=? AND ticker=?",
                    (
                        str(observation.connection_epoch),
                        observation.sid,
                        observation.sequence,
                        observation.ticker,
                    ),
                ).fetchone()
                if row is not None:
                    if row["source_event_fingerprint"] == observation.source_event_fingerprint:
                        return False
                    raise ShadowResearchError("book source-event logical identity collision")
                db.execute(
                    "INSERT INTO book_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
        except sqlite3.Error as exc:
            raise ShadowResearchError("book evidence persistence rejected") from exc
        return True

    def get_by_source_identity(
        self, connection_epoch: UUID, sid: int, sequence: int, ticker: str
    ) -> BookEvidenceObservation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT evidence_id FROM book_evidence "
                "WHERE connection_epoch=? AND sid=? AND sequence=? AND ticker=?",
                (str(connection_epoch), sid, sequence, ticker),
            ).fetchone()
        return None if row is None else self.get(row["evidence_id"])

    def get(self, evidence_id: str) -> BookEvidenceObservation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM book_evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
        if row is None:
            return None

        def decimal(value: str | None) -> Decimal | None:
            return None if value is None else Decimal(value)

        return BookEvidenceObservation(
            evidence_id=row["evidence_id"],
            update_kind=BookUpdateKind(row["update_kind"]),
            ticker=row["ticker"],
            market_id=row["market_id"],
            exchange_index=row["exchange_index"],
            connection_epoch=UUID(row["connection_epoch"]),
            sid=row["sid"],
            sequence=row["sequence"],
            source_event_fingerprint=row["source_event_fingerprint"],
            book_state=BookState(row["book_state"]),
            best_yes_bid=decimal(row["best_yes_bid"]),
            best_yes_ask=decimal(row["best_yes_ask"]),
            best_bid_size=decimal(row["best_bid_size"]),
            best_ask_size=decimal(row["best_ask_size"]),
            exchange_at=None
            if row["exchange_at"] is None
            else datetime.fromisoformat(row["exchange_at"]),
            received_at=datetime.fromisoformat(row["received_at"]),
            available_at=datetime.fromisoformat(row["available_at"]),
            price_mode=PriceMode(row["price_mode"]),
            structure_hash=row["structure_hash"],
            production_influence=Decimal(row["production_influence"]),
        )

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM book_evidence").fetchone()[0])
