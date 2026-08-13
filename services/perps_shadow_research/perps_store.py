"""Append-only SQLite store dedicated to Perps research evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from .domain import ShadowResearchError
from .perps_evidence import PerpsBookEvidenceObservation, PerpsMarketStateObservation
from .perps_metadata import PerpsMarketMetadata, canonical_value


class PerpsEvidenceStore:
    TABLES = ("perps_market_metadata", "perps_book_evidence", "perps_market_state")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            checked = db.execute("PRAGMA quick_check").fetchone()
            if checked is None or checked[0] != "ok":
                raise ShadowResearchError("Perps evidence database integrity check failed")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS perps_market_metadata (
                    market_metadata_hash TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    exchange_index INTEGER NOT NULL CHECK(exchange_index >= 0),
                    observed_at TEXT NOT NULL,
                    contract_size TEXT NOT NULL,
                    tick_size TEXT NOT NULL,
                    fractional_trading_enabled INTEGER NOT NULL
                        CHECK(fractional_trading_enabled IN (0,1)),
                    perps_contract_hash TEXT NOT NULL,
                    normalized_snapshot TEXT NOT NULL,
                    source_contract_url TEXT NOT NULL,
                    source_retrieved_on TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
                    parser_version TEXT NOT NULL,
                    production_influence TEXT NOT NULL DEFAULT '0' CHECK(production_influence = '0')
                );
                CREATE TABLE IF NOT EXISTS perps_book_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    update_kind TEXT NOT NULL CHECK(update_kind IN ('SNAPSHOT','DELTA')),
                    ticker TEXT NOT NULL,
                    exchange_index INTEGER NOT NULL CHECK(exchange_index >= 0),
                    connection_epoch TEXT NOT NULL,
                    sid INTEGER NOT NULL CHECK(sid >= 1),
                    sequence INTEGER NOT NULL CHECK(sequence >= 1),
                    source_event_fingerprint TEXT NOT NULL
                        CHECK(length(source_event_fingerprint)=64),
                    book_state TEXT NOT NULL CHECK(book_state='CURRENT'),
                    best_bid TEXT, best_ask TEXT, best_bid_size TEXT, best_ask_size TEXT,
                    exchange_at TEXT, received_at TEXT NOT NULL, available_at TEXT NOT NULL,
                    tick_size TEXT NOT NULL, contract_size TEXT NOT NULL,
                    fractional_trading_enabled INTEGER NOT NULL
                        CHECK(fractional_trading_enabled IN (0,1)),
                    perps_contract_hash TEXT NOT NULL, market_metadata_hash TEXT NOT NULL,
                    full_book_hash TEXT NOT NULL,
                    production_influence TEXT NOT NULL DEFAULT '0' CHECK(production_influence='0'),
                    UNIQUE(connection_epoch, sid, sequence, ticker),
                    FOREIGN KEY(market_metadata_hash)
                        REFERENCES perps_market_metadata(market_metadata_hash)
                );
                CREATE TABLE IF NOT EXISTS perps_market_state (
                    evidence_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    exchange_index INTEGER NOT NULL CHECK(exchange_index >= 0),
                    connection_epoch TEXT NOT NULL, sid INTEGER NOT NULL CHECK(sid >= 1),
                    source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),
                    ticker_ts_ms INTEGER NOT NULL CHECK(ticker_ts_ms >= 0),
                    received_at TEXT NOT NULL, available_at TEXT NOT NULL,
                    price TEXT NOT NULL, bid TEXT NOT NULL, ask TEXT NOT NULL,
                    bid_size TEXT NOT NULL, ask_size TEXT NOT NULL, last_trade_size TEXT NOT NULL,
                    volume TEXT NOT NULL, volume_notional_value_dollars TEXT NOT NULL,
                    volume_24h TEXT NOT NULL, volume_24h_notional_value_dollars TEXT NOT NULL,
                    open_interest TEXT NOT NULL, open_interest_notional_value_dollars TEXT NOT NULL,
                    reference_price TEXT, reference_price_ts_ms INTEGER,
                    settlement_mark_price TEXT, settlement_mark_price_ts_ms INTEGER,
                    liquidation_mark_price TEXT, liquidation_mark_price_ts_ms INTEGER,
                    funding_rate TEXT, funding_observed_ts_ms INTEGER, next_funding_time_ms INTEGER,
                    market_metadata_hash TEXT NOT NULL,
                    production_influence TEXT NOT NULL DEFAULT '0' CHECK(production_influence='0'),
                    UNIQUE(connection_epoch, sid, ticker, ticker_ts_ms, source_fingerprint),
                    FOREIGN KEY(market_metadata_hash)
                        REFERENCES perps_market_metadata(market_metadata_hash)
                );
            """)
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS perps_market_state_source_replay "
                "ON perps_market_state(connection_epoch, sid, ticker, ticker_ts_ms, "
                "source_fingerprint)"
            )
            for table in self.TABLES:
                db.executescript(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'append only'); END;
                    CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'append only'); END;
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

    def append_metadata(self, market: PerpsMarketMetadata) -> bool:
        snapshot = json.dumps(
            canonical_value(market.normalized_snapshot), sort_keys=True, separators=(",", ":")
        )
        values = (
            market.market_metadata_hash,
            market.ticker,
            market.exchange_index,
            self._text(market.observed_at),
            self._text(market.contract_size),
            self._text(market.tick_size),
            int(market.fractional_trading_enabled),
            market.perps_contract_hash,
            snapshot,
            market.source_provenance.source_url,
            market.source_provenance.retrieved_on.isoformat(),
            market.source_provenance.sha256,
            market.source_provenance.parser_version,
            "0",
        )
        return self._insert_idempotent(
            "perps_market_metadata",
            "market_metadata_hash",
            market.market_metadata_hash,
            "INSERT INTO perps_market_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    def append_book(self, item: PerpsBookEvidenceObservation) -> bool:
        existing = self.get_book_by_source(
            item.connection_epoch, item.sid, item.sequence, item.ticker
        )
        if existing is not None:
            if existing["source_event_fingerprint"] == item.source_event_fingerprint:
                return False
            raise ShadowResearchError("Perps book source-event logical identity collision")
        values = (
            item.evidence_id,
            item.update_kind.value,
            item.ticker,
            item.exchange_index,
            str(item.connection_epoch),
            item.sid,
            item.sequence,
            item.source_event_fingerprint,
            item.book_state.value,
            self._text(item.best_bid),
            self._text(item.best_ask),
            self._text(item.best_bid_size),
            self._text(item.best_ask_size),
            self._text(item.exchange_at),
            self._text(item.received_at),
            self._text(item.available_at),
            self._text(item.tick_size),
            self._text(item.contract_size),
            int(item.fractional_trading_enabled),
            item.perps_contract_hash,
            item.market_metadata_hash,
            item.full_book_hash,
            "0",
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT source_event_fingerprint FROM perps_book_evidence "
                    "WHERE connection_epoch=? AND sid=? AND sequence=? AND ticker=?",
                    (str(item.connection_epoch), item.sid, item.sequence, item.ticker),
                ).fetchone()
                if row is not None:
                    if row[0] == item.source_event_fingerprint:
                        return False
                    raise ShadowResearchError("Perps book source-event logical identity collision")
                db.execute(
                    "INSERT INTO perps_book_evidence VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
        except sqlite3.Error as exc:
            raise ShadowResearchError("Perps book evidence persistence rejected") from exc
        return True

    def append_market_state(self, item: PerpsMarketStateObservation) -> bool:
        if (
            self.get_market_state_by_source(
                item.connection_epoch,
                item.sid,
                item.ticker,
                item.ticker_ts_ms,
                item.source_fingerprint,
            )
            is not None
        ):
            return False

        def mark(value: Any) -> tuple[str | None, int | None]:
            return (None, None) if value is None else (self._text(value.price), value.ts_ms)

        reference, reference_ts = mark(item.reference_price)
        settlement, settlement_ts = mark(item.settlement_mark_price)
        liquidation, liquidation_ts = mark(item.liquidation_mark_price)
        values = (
            item.evidence_id,
            item.ticker,
            item.exchange_index,
            str(item.connection_epoch),
            item.sid,
            item.source_fingerprint,
            item.ticker_ts_ms,
            self._text(item.received_at),
            self._text(item.available_at),
            self._text(item.price),
            self._text(item.bid),
            self._text(item.ask),
            self._text(item.bid_size),
            self._text(item.ask_size),
            self._text(item.last_trade_size),
            self._text(item.volume),
            self._text(item.volume_notional_value_dollars),
            self._text(item.volume_24h),
            self._text(item.volume_24h_notional_value_dollars),
            self._text(item.open_interest),
            self._text(item.open_interest_notional_value_dollars),
            reference,
            reference_ts,
            settlement,
            settlement_ts,
            liquidation,
            liquidation_ts,
            self._text(item.funding_rate),
            item.funding_observed_ts_ms,
            item.next_funding_time_ms,
            item.market_metadata_hash,
            "0",
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT 1 FROM perps_market_state WHERE connection_epoch=? AND sid=? "
                    "AND ticker=? AND ticker_ts_ms=? AND source_fingerprint=?",
                    (
                        str(item.connection_epoch),
                        item.sid,
                        item.ticker,
                        item.ticker_ts_ms,
                        item.source_fingerprint,
                    ),
                ).fetchone()
                if row is not None:
                    return False
                db.execute(
                    "INSERT INTO perps_market_state VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
        except sqlite3.Error as exc:
            raise ShadowResearchError("Perps market-state persistence rejected") from exc
        return True

    def _insert_idempotent(
        self, table: str, key: str, value: str, statement: str, values: tuple[Any, ...]
    ) -> bool:
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    db.execute(
                        f"SELECT 1 FROM {table} WHERE {key}=?",  # noqa: S608
                        (value,),
                    ).fetchone()
                    is not None
                ):
                    return False
                db.execute(statement, values)
        except sqlite3.Error as exc:
            raise ShadowResearchError(f"{table} persistence rejected") from exc
        return True

    def get_book_by_source(
        self, epoch: UUID, sid: int, sequence: int, ticker: str
    ) -> sqlite3.Row | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM perps_book_evidence "
                "WHERE connection_epoch=? AND sid=? AND sequence=? AND ticker=?",
                (str(epoch), sid, sequence, ticker),
            ).fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def get_market_state_by_source(
        self,
        epoch: UUID,
        sid: int,
        ticker: str,
        ticker_ts_ms: int,
        source_fingerprint: str,
    ) -> sqlite3.Row | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM perps_market_state WHERE connection_epoch=? AND sid=? "
                "AND ticker=? AND ticker_ts_ms=? AND source_fingerprint=?",
                (str(epoch), sid, ticker, ticker_ts_ms, source_fingerprint),
            ).fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def count(self, table: str) -> int:
        if table not in self.TABLES:
            raise ShadowResearchError("unknown Perps evidence table")
        with self._connect() as db:
            return int(
                db.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608
                ).fetchone()[0]
            )
