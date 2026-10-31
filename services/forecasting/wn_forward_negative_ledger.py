"""Local SQLite create-only five-city negative attempt journal (research-only).

This is a bounded local engineering custody mechanism, not externally immutable
source custody or independently trusted time. Callers may supply clocks, the
owner can alter/copy the SQLite file, and a host crash can leave RESERVED rows.
No source, probability, market economics, paper, order or trading authority.
No network calls, scheduler, CLI side effects or default database path.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from .wn_p3_sibling_preflight import CITY_SERIES, event_ticker_for

POLICY = "wn-forward-negative-sqlite-journal-local-only-v1"
FIRST_DATE = date(2026, 11, 1)
REASONS = frozenset(
    {
        "NO_MODEL",
        "FORECAST_MISSING",
        "FORECAST_LATE",
        "MARKET_MISSING",
        "STALE_BOOK",
        "INSUFFICIENT_DEPTH",
        "FEE_UNKNOWN",
        "NO_EDGE",
        "COLLECTOR_FAILURE",
    }
)


class NegativeJournalError(ValueError):
    """Invalid, late, conflicting or duplicate locally persisted attempt."""


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise NegativeJournalError("clock must be timezone-aware datetime")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise NegativeJournalError("clock outside representable UTC range") from exc


def _date(value: date) -> str:
    if type(value) is not date or value < FIRST_DATE or value.year > 2099:
        raise NegativeJournalError("target date outside prospective roster")
    return value.isoformat()


def _db(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if not isinstance(path, Path) or path.is_symlink() or not path.parent.is_dir():
        raise NegativeJournalError("local journal path must have an existing parent")
    if read_only and not path.is_file():
        raise NegativeJournalError("unreserved local journal does not exist")
    if read_only:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1, isolation_level=None
        )
        return connection
    connection = sqlite3.connect(path, timeout=1, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dates (
                target_date TEXT PRIMARY KEY,
                reserved_utc TEXT NOT NULL,
                policy TEXT NOT NULL
                    CHECK (policy = 'wn-forward-negative-sqlite-journal-local-only-v1')
            );
            CREATE TABLE IF NOT EXISTS attempts (
                target_date TEXT NOT NULL,
                city_series TEXT NOT NULL,
                event_ticker TEXT NOT NULL,
                sibling_slots INTEGER NOT NULL CHECK (sibling_slots = 6),
                PRIMARY KEY (target_date, city_series),
                FOREIGN KEY (target_date) REFERENCES dates(target_date)
            );
            CREATE TABLE IF NOT EXISTS negatives (
                target_date TEXT NOT NULL,
                city_series TEXT NOT NULL,
                reason TEXT NOT NULL,
                classified_utc TEXT NOT NULL,
                PRIMARY KEY (target_date, city_series),
                FOREIGN KEY (target_date, city_series)
                    REFERENCES attempts(target_date, city_series)
            );
            """
        )
        for table in ("dates", "attempts", "negatives"):
            for operation in ("UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS no_{operation.lower()}_{table} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only journal'); END"
                )
        return connection
    except BaseException:
        connection.close()
        raise


def reserve_date(path: Path, target: date, *, stamped_at: datetime) -> None:
    """Commit all five local negative-only reservation identities atomically.

    The timestamp is a caller-influenced **local** stamp, never provider proof.
    Cannot reserve after the target's fixed 09:05Z intake cutoff; failure or
    duplicate leaves previous rows intact, never retries or updates them.
    """
    key = _date(target)
    utc = _utc(stamped_at)
    cutoff = datetime.combine(target, time(9, 5), tzinfo=UTC)
    if utc >= cutoff or utc < cutoff - timedelta(days=2):
        raise NegativeJournalError("reservation outside bounded prospective intake")
    connection = _db(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO dates (target_date, reserved_utc, policy) VALUES (?, ?, ?)",
            (key, utc.isoformat(), POLICY),
        )
        connection.executemany(
            "INSERT INTO attempts "
            "(target_date, city_series, event_ticker, sibling_slots) VALUES (?, ?, ?, 6)",
            [(key, series, event_ticker_for(series, target)) for series in sorted(CITY_SERIES)],
        )
        connection.execute("COMMIT")
    except (sqlite3.Error, ValueError) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise NegativeJournalError("reservation rejected without replacing earlier state") from exc
    finally:
        connection.close()


def classify_negative(
    path: Path,
    target: date,
    series: str,
    *,
    reason: str,
    stamped_at: datetime,
) -> None:
    """Append one terminal negative after prior reservation; never change prior."""
    key = _date(target)
    when = _utc(stamped_at)
    if series not in CITY_SERIES or reason not in REASONS:
        raise NegativeJournalError("noncanonical city or positive/unknown state")
    connection = _db(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT d.reserved_utc, a.event_ticker, a.sibling_slots "
            "FROM dates d JOIN attempts a ON a.target_date = d.target_date "
            "WHERE d.target_date = ? AND a.city_series = ?",
            (key, series),
        ).fetchone()
        if row is None or row[1] != event_ticker_for(series, target) or row[2] != 6:
            raise NegativeJournalError("missing or invalid original reservation")
        if when < _utc(datetime.fromisoformat(row[0])):
            raise NegativeJournalError("classification predates local reservation")
        connection.execute(
            "INSERT INTO negatives "
            "(target_date, city_series, reason, classified_utc) VALUES (?, ?, ?, ?)",
            (key, series, reason, when.isoformat()),
        )
        connection.execute("COMMIT")
    except (sqlite3.Error, ValueError) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise NegativeJournalError("negative transition rejected without overwrite") from exc
    finally:
        connection.close()


def read_negative_date(path: Path, target: date) -> dict[str, object]:
    """Read the full expected denominator; no successful source/market authority."""
    key = _date(target)
    connection = _db(path, read_only=True)
    try:
        dates = connection.execute(
            "SELECT reserved_utc, policy FROM dates WHERE target_date = ?", (key,)
        ).fetchall()
        rows = connection.execute(
            "SELECT a.city_series, a.event_ticker, a.sibling_slots, "
            "n.reason, n.classified_utc FROM attempts a "
            "LEFT JOIN negatives n ON n.target_date = a.target_date "
            "AND n.city_series = a.city_series WHERE a.target_date = ? "
            "ORDER BY a.city_series",
            (key,)
        ).fetchall()
        if len(dates) != 1 or dates[0][1] != POLICY or len(rows) != 5:
            raise NegativeJournalError("local journal incomplete or policy mismatch")
        _utc(datetime.fromisoformat(dates[0][0]))
        if tuple(row[0] for row in rows) != tuple(sorted(CITY_SERIES)):
            raise NegativeJournalError("local journal city roster inconsistent")
        for city, ticker, slots, reason, classified in rows:
            if ticker != event_ticker_for(city, target) or slots != 6:
                raise NegativeJournalError("local journal sibling/identity inconsistency")
            if (reason is None) != (classified is None) or (
                reason is not None and reason not in REASONS
            ):
                raise NegativeJournalError("local journal terminal state invalid")
            if classified is not None and _utc(datetime.fromisoformat(classified)) < _utc(
                datetime.fromisoformat(dates[0][0])
            ):
                raise NegativeJournalError("local journal clock inconsistent")
        return {
            "record_type": "WN-P4C-LOCAL-NEGATIVE-LEDGER-v1",
            "policy": POLICY,
            "target_date": key,
            "local_reservation_utc": dates[0][0],
            "denominator_date_clusters": 1,
            "denominator_city_attempts": 5,
            "denominator_sibling_slots": 30,
            "rows": [
                {
                    "series": city,
                    "event_ticker": ticker,
                    "expected_sibling_slots_not_acquired": slots,
                    "status": reason if reason is not None else "ATTEMPT_RESERVED",
                    "local_classification_utc": classified,
                }
                for city, ticker, slots, reason, classified in rows
            ],
            "source_attested": False,
            "market_economics_issued": False,
            "model_probability_authorized": False,
            "paper_authorized": False,
            "trading_authority": "NONE",
            "production_influence": "0",
            "research_only": True,
            "local_storage_not_external_custody": True,
        }
    finally:
        connection.close()
