"""Append-only SQLite evidence store for RT-A1."""
# SQL schema strings intentionally mirror the durable column layout.
# ruff: noqa: E501

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collector import ExternalReviewEvidence, ReviewRecord, RtSnapshot, Transition
from .domain import ActiveKxrtMarket


class StoreError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise StoreError("timestamps must be UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class RtA1Store:
    """Create-only archive. Each acquisition attempt is durable before its result is written."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, research_only INTEGER NOT NULL,
                    production_influence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    resource TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, error TEXT, raw_body_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS universe (
                    identity TEXT PRIMARY KEY, observed_at TEXT NOT NULL, event_ticker TEXT NOT NULL,
                    market_ticker TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rt_snapshots (
                    snapshot_id TEXT PRIMARY KEY, film_url TEXT NOT NULL, observed_at TEXT NOT NULL,
                    raw_body BLOB NOT NULL, raw_body_sha256 TEXT NOT NULL, score INTEGER,
                    review_count INTEGER, reviews_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_reviews (
                    evidence_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, source_url TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orderbooks (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
                    observed_at TEXT NOT NULL, raw_body BLOB NOT NULL, raw_body_sha256 TEXT NOT NULL,
                    yes_levels_json TEXT, no_levels_json TEXT, status TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS first_inclusion_events (
                    event_id TEXT PRIMARY KEY, film_url TEXT NOT NULL, review_id TEXT NOT NULL,
                    first_present_observed_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT, film_url TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                """
            )

    def register_run(self, run_id: str, *, started_at: datetime) -> None:
        if not run_id:
            raise StoreError("run_id is required")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs VALUES (?, ?, 1, '0')", (run_id, _iso(started_at)))

    def begin_attempt(self, run_id: str, resource: str, *, started_at: datetime) -> int:
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT INTO attempts(run_id,resource,started_at,status) VALUES (?,?,?,?)",
                (run_id, resource, _iso(started_at), "STARTED"),
            )
            if cursor.lastrowid is None:
                raise StoreError("attempt insert did not return an identity")
            return int(cursor.lastrowid)

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        finished_at: datetime,
        status: str,
        error: str | None = None,
        raw_body_sha256: str | None = None,
    ) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE attempts SET finished_at=?,status=?,error=?,raw_body_sha256=? WHERE attempt_id=?",
                (_iso(finished_at), status, error, raw_body_sha256, attempt_id),
            )

    def append_rt_snapshot(self, snapshot: RtSnapshot) -> str:
        snapshot_id = snapshot.raw_body_sha256 + ":" + _iso(snapshot.observed_at)
        reviews = [self._review_json(review) for review in snapshot.reviews]
        with sqlite3.connect(self.path) as db:
            try:
                db.execute(
                    "INSERT INTO rt_snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        snapshot.film_url,
                        _iso(snapshot.observed_at),
                        snapshot.raw_body,
                        snapshot.raw_body_sha256,
                        snapshot.score,
                        snapshot.review_count,
                        json.dumps(reviews, sort_keys=True),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError("duplicate exact RT acquisition") from exc
        return snapshot_id

    @staticmethod
    def _review_json(review: ReviewRecord) -> dict[str, Any]:
        return {
            "review_id": review.review_id,
            "critic": review.critic,
            "outlet": review.outlet,
            "source_url": review.source_url,
            "classification": review.classification.value,
            "eligibility": review.eligibility.value,
            "raw": review.raw,
        }

    def snapshots_for(self, film_url: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT snapshot_id,observed_at,raw_body_sha256,score,review_count,reviews_json "
                "FROM rt_snapshots WHERE film_url=? ORDER BY observed_at",
                (film_url,),
            ).fetchall()
        return [
            {
                "snapshot_id": row[0],
                "observed_at": row[1],
                "raw_body_sha256": row[2],
                "score": row[3],
                "review_count": row[4],
                "reviews": json.loads(row[5]),
            }
            for row in rows
        ]

    def latest_snapshot(self, film_url: str) -> RtSnapshot | None:
        """Return the last parsed snapshot for a film, including its raw bytes."""
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT observed_at,raw_body,raw_body_sha256,score,review_count,reviews_json "
                "FROM rt_snapshots WHERE film_url=? ORDER BY observed_at DESC LIMIT 1",
                (film_url,),
            ).fetchone()
        if row is None:
            return None
        from .collector import Classification, RtEligibility

        reviews = tuple(
            ReviewRecord(
                item["review_id"],
                item.get("critic"),
                item.get("outlet"),
                item.get("source_url"),
                Classification(item["classification"]),
                RtEligibility(item["eligibility"]),
                item.get("raw", {}),
            )
            for item in json.loads(row[5])
        )
        return RtSnapshot(
            film_url,
            datetime.fromisoformat(row[0].replace("Z", "+00:00")),
            row[2],
            row[1],
            row[3],
            row[4],
            reviews,
        )

    def append_orderbook(
        self,
        ticker: str,
        observed_at: datetime,
        raw_body: bytes,
        body_sha256: str,
        yes_levels: object,
        no_levels: object,
    ) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO orderbooks(ticker,observed_at,raw_body,raw_body_sha256,yes_levels_json,no_levels_json,status) VALUES (?,?,?,?,?,?,?)",
                (
                    ticker,
                    _iso(observed_at),
                    raw_body,
                    body_sha256,
                    json.dumps(yes_levels),
                    json.dumps(no_levels),
                    "SUCCESS",
                ),
            )

    def append_source_review(
        self,
        *,
        review_id: str,
        source_url: str,
        first_observed_at: datetime,
        status: str,
        payload: dict[str, Any],
    ) -> str:
        import hashlib

        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        evidence_id = hashlib.sha256(
            (
                review_id + "\0" + source_url + "\0" + _iso(first_observed_at) + "\0" + encoded
            ).encode()
        ).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO source_reviews VALUES (?,?,?,?,?,?)",
                (evidence_id, review_id, source_url, _iso(first_observed_at), status, encoded),
            )
        return evidence_id

    def append_external_review(self, evidence: ExternalReviewEvidence) -> str:
        """Persist typed source evidence; stated publication date is descriptive only."""
        payload = {
            "critic": evidence.critic,
            "outlet": evidence.outlet,
            "title": evidence.title,
            "rating_or_grade": evidence.rating_or_grade,
            "stated_publication_timestamp": evidence.stated_publication_timestamp,
            "raw_body_sha256": evidence.raw_body_sha256,
            "raw_body": evidence.raw_body.hex(),
        }
        return self.append_source_review(
            review_id=evidence.review_id,
            source_url=evidence.source_url,
            first_observed_at=evidence.first_observed_at,
            status="SOURCE_REVIEW_OBSERVED",
            payload=payload,
        )

    def append_universe(
        self, market: ActiveKxrtMarket, *, identity: str, observed_at: datetime
    ) -> None:
        payload = json.dumps(market, default=str, sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO universe VALUES (?,?,?,?,?)",
                (identity, _iso(observed_at), market.event_ticker, market.market_ticker, payload),
            )

    def append_transition(self, transition: Transition) -> None:
        payload = dataclasses.asdict(transition)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO transitions(film_url,observed_at,payload_json) VALUES (?,?,?)",
                (
                    transition.film_url,
                    _iso(transition.observed_at),
                    json.dumps(payload, default=str, sort_keys=True),
                ),
            )

    def append_first_inclusion(self, event: dict[str, Any]) -> None:
        import hashlib

        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(payload.encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO first_inclusion_events VALUES (?,?,?,?,?)",
                (
                    identity,
                    event["film_url"],
                    event["review_id"],
                    event["first_present_observed_at"],
                    payload,
                ),
            )

    def counts(self, table: str) -> int:
        queries = {
            "runs": "SELECT COUNT(*) FROM runs",
            "attempts": "SELECT COUNT(*) FROM attempts",
            "rt_snapshots": "SELECT COUNT(*) FROM rt_snapshots",
            "orderbooks": "SELECT COUNT(*) FROM orderbooks",
            "transitions": "SELECT COUNT(*) FROM transitions",
            "first_inclusion_events": "SELECT COUNT(*) FROM first_inclusion_events",
        }
        if table not in queries:
            raise StoreError("table is not a permitted diagnostic table")
        with sqlite3.connect(self.path) as db:
            row = db.execute(queries[table]).fetchone()
            if row[0] is None:
                raise StoreError("diagnostic count unavailable")
            return int(row[0])
