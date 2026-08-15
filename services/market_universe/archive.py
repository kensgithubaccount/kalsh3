"""Append-only, reconstructable market-universe acquisition archive.

Authority begins only when :class:`UniverseSynchronizer` archives the decoded
response returned by its configured transport.  This store performs its own
canonicalization, parsing, identity calculation, persistence, and verification.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import Event, Market, Series, UniverseValidationError

ARCHIVE_SCHEMA_VERSION = "m26f-universe-archive-schema-v1"
ARCHIVE_IDENTITY_POLICY_VERSION = "m26f-canonical-json-sha256-v1"
ACQUISITION_POLICY_VERSION = "m26f-synchronizer-transport-response-v1"
HISTORICAL_SELECTION_POLICY_VERSION = "m26f-latest-acquired-at-or-before-v1"
ARCHIVE_VERIFICATION_POLICY_VERSION = "m26f-reparse-and-rehash-v1"
UNIVERSE_PARSER_VERSION = "m2-market-universe-parser-v1"


class ArchiveError(ValueError):
    """Archive is unavailable, incompatible, ambiguous, or corrupt."""


class EntityKind(StrEnum):
    SERIES = "series"
    EVENT = "event"
    MARKET = "market"


def _utc(value: datetime, name: str) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ArchiveError(f"{name} must be UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ArchiveError(f"persisted {name} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError(f"persisted {name} is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ArchiveError(f"persisted {name} is not UTC")
    if _utc(result, name) != value:
        raise ArchiveError(f"persisted {name} is not canonical")
    return result


def _canonical(value: object) -> str:
    """Identity-critical JSON; accepted values cannot require ``default=str``."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ArchiveError("acquisition content is not canonical JSON material") from exc


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: object) -> str:
    return _hash_bytes(_canonical(value).encode("utf-8"))


def _identity(domain: str, material: object) -> str:
    return _hash_json({"domain": domain, "material": material})


def _parser(kind: EntityKind) -> type[Series] | type[Event] | type[Market]:
    return {EntityKind.SERIES: Series, EntityKind.EVENT: Event, EntityKind.MARKET: Market}[kind]


@dataclass(frozen=True, slots=True)
class ArchivedObservation:
    observation_id: str
    page_id: str
    archive_authority_id: str
    kind: EntityKind
    ticker: str
    entity: Series | Event | Market
    canonical_source_hash: str
    metadata_hash: str
    rules_hash: str | None
    acquired_at: datetime
    source_updated_at: datetime | None
    provider: str
    endpoint: str
    parser_version: str
    archive_schema_version: str
    archive_policy_version: str
    production_influence: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ArchiveStatus:
    available: bool
    archive_authority_id: str
    total_pages: int
    total_market_observations: int
    total_event_observations: int
    earliest_acquisition: datetime | None
    latest_acquisition: datetime | None
    corruption: bool
    schema_version: str
    policy_version: str


_SCHEMA_SQL = """
CREATE TABLE archive_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    archive_authority_id TEXT NOT NULL CHECK(length(archive_authority_id)=64),
    schema_version TEXT NOT NULL,
    identity_policy_version TEXT NOT NULL,
    acquisition_policy_version TEXT NOT NULL,
    selection_policy_version TEXT NOT NULL,
    verification_policy_version TEXT NOT NULL
);
CREATE TABLE acquisition_pages (
    page_id TEXT PRIMARY KEY, archive_authority_id TEXT NOT NULL,
    provider TEXT NOT NULL, endpoint TEXT NOT NULL, parameters_json TEXT NOT NULL,
    acquired_at TEXT NOT NULL, page_number INTEGER NOT NULL, cursor_in TEXT,
    cursor_out TEXT, run_id TEXT NOT NULL, entity_kind TEXT NOT NULL,
    canonical_payload TEXT NOT NULL, raw_content_hash TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL, parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL, identity_policy_version TEXT NOT NULL,
    acquisition_policy_version TEXT NOT NULL, succeeded INTEGER NOT NULL,
    failure TEXT, production_influence TEXT NOT NULL CHECK(production_influence='0')
);
CREATE TABLE entity_observations (
    observation_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES acquisition_pages(page_id),
    archive_authority_id TEXT NOT NULL, entity_kind TEXT NOT NULL, ticker TEXT NOT NULL,
    event_ticker TEXT, canonical_source TEXT NOT NULL, canonical_source_hash TEXT NOT NULL,
    metadata_hash TEXT NOT NULL, rules_hash TEXT, acquired_at TEXT NOT NULL,
    source_updated_at TEXT, provider TEXT NOT NULL, endpoint TEXT NOT NULL,
    parser_version TEXT NOT NULL, schema_version TEXT NOT NULL,
    identity_policy_version TEXT NOT NULL, verification_policy_version TEXT NOT NULL,
    production_influence TEXT NOT NULL CHECK(production_influence='0')
);
CREATE INDEX observations_point_in_time
    ON entity_observations(entity_kind,ticker,acquired_at DESC,observation_id);
CREATE TABLE acquisition_run_results (
    result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, archive_authority_id TEXT NOT NULL,
    completeness TEXT NOT NULL, pages INTEGER NOT NULL, records_received INTEGER NOT NULL,
    malformed INTEGER NOT NULL, failure TEXT, finished_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    production_influence TEXT NOT NULL CHECK(production_influence='0')
);
CREATE UNIQUE INDEX acquisition_run_once ON acquisition_run_results(run_id);
CREATE TRIGGER metadata_no_update BEFORE UPDATE ON archive_metadata
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER metadata_no_delete BEFORE DELETE ON archive_metadata
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER pages_no_update BEFORE UPDATE ON acquisition_pages
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER pages_no_delete BEFORE DELETE ON acquisition_pages
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER observations_no_update BEFORE UPDATE ON entity_observations
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON entity_observations
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER results_no_update BEFORE UPDATE ON acquisition_run_results
    BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER results_no_delete BEFORE DELETE ON acquisition_run_results
    BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


def _normalized_sql(value: object) -> str:
    if not isinstance(value, str):
        raise ArchiveError("archive schema object has no SQL definition")
    return " ".join(value.split()).lower()


def _expected_schema() -> dict[tuple[str, str], str]:
    with sqlite3.connect(":memory:") as db:
        db.executescript(_SCHEMA_SQL)
        rows = db.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    return {(str(row[0]), str(row[1])): _normalized_sql(row[2]) for row in rows}


_EXPECTED_SCHEMA = _expected_schema()
_ACQUISITION_CAPABILITY = object()


class UniverseObservationArchive:
    """Read/verification facade for one durable append-only archive."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        prior_size = self.path.stat().st_size if existed else 0
        try:
            if existed and prior_size > 0:
                with self._connect(read_only=True) as db:
                    check = db.execute("PRAGMA quick_check").fetchone()
                    if check is None or check[0] != "ok":
                        raise ArchiveError("archive database integrity check failed")
                    self._validate_schema(db)
            else:
                with self._connect() as db:
                    check = db.execute("PRAGMA quick_check").fetchone()
                    if check is None or check[0] != "ok":
                        raise ArchiveError("archive database integrity check failed")
                    self._initialize_new(db)
                    self._validate_schema(db)
        except ArchiveError:
            raise
        except sqlite3.Error as exc:
            raise ArchiveError("archive schema initialization rejected") from exc
        self.path.chmod(0o600)

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        else:
            db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        if not read_only:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _initialize_new(db: sqlite3.Connection) -> None:
        existing = db.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if existing:
            raise ArchiveError("new archive path already contains application schema")
        db.executescript(_SCHEMA_SQL)
        authority = _identity("m26f-archive-authority-v1", str(uuid4()))
        db.execute(
            "INSERT INTO archive_metadata VALUES(1,?,?,?,?,?,?)",
            (
                authority,
                ARCHIVE_SCHEMA_VERSION,
                ARCHIVE_IDENTITY_POLICY_VERSION,
                ACQUISITION_POLICY_VERSION,
                HISTORICAL_SELECTION_POLICY_VERSION,
                ARCHIVE_VERIFICATION_POLICY_VERSION,
            ),
        )

    @staticmethod
    def _validate_schema(db: sqlite3.Connection) -> None:
        actual_rows = db.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        actual = {(str(row[0]), str(row[1])): _normalized_sql(row[2]) for row in actual_rows}
        if actual != _EXPECTED_SCHEMA:
            raise ArchiveError("archive schema definition is incompatible or corrupt")
        try:
            rows = db.execute("SELECT * FROM archive_metadata").fetchall()
        except sqlite3.Error as exc:
            raise ArchiveError("archive metadata is unavailable") from exc
        if len(rows) != 1 or rows[0]["singleton"] != 1:
            raise ArchiveError("archive metadata singleton is missing or ambiguous")
        row = rows[0]
        expected = (
            ARCHIVE_SCHEMA_VERSION,
            ARCHIVE_IDENTITY_POLICY_VERSION,
            ACQUISITION_POLICY_VERSION,
            HISTORICAL_SELECTION_POLICY_VERSION,
            ARCHIVE_VERIFICATION_POLICY_VERSION,
        )
        if (
            row is None
            or tuple(
                row[key]
                for key in (
                    "schema_version",
                    "identity_policy_version",
                    "acquisition_policy_version",
                    "selection_policy_version",
                    "verification_policy_version",
                )
            )
            != expected
        ):
            raise ArchiveError("archive schema or policy version is incompatible")

    @property
    def authority_id(self) -> str:
        with self._connect(read_only=True) as db:
            self._validate_schema(db)
            row = db.execute(
                "SELECT archive_authority_id FROM archive_metadata WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise ArchiveError("archive authority is unavailable")
        return str(row[0])

    def _archive_acquired_page(
        self,
        capability: object,
        *,
        provider: str,
        endpoint: str,
        parameters: dict[str, str],
        acquired_at: datetime,
        page_number: int,
        cursor_in: str | None,
        cursor_out: str | None,
        run_id: str,
        kind: EntityKind,
        payload: dict[str, Any],
        succeeded: bool = True,
        failure: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Archive one trusted transport response (internal synchronizer API)."""
        if capability is not _ACQUISITION_CAPABILITY:
            raise ArchiveError("authoritative archive writes require acquisition capability")
        acquired = _utc(acquired_at, "acquired_at")
        if not provider or not endpoint or not run_id or page_number < 1:
            raise ArchiveError("acquisition provenance is incomplete")
        canonical_payload = _canonical(payload)
        parameters_json = _canonical(parameters)
        payload_hash = _hash_bytes(canonical_payload.encode("utf-8"))
        page_material = {
            "acquired_at": acquired,
            "cursor_in": cursor_in,
            "cursor_out": cursor_out,
            "endpoint": endpoint,
            "entity_kind": kind.value,
            "page_number": page_number,
            "parameters": parameters,
            "payload_hash": payload_hash,
            "provider": provider,
            "run_id": run_id,
        }
        page_id = _identity("m26f-acquisition-page-v1", page_material)
        field = {
            EntityKind.SERIES: "series",
            EntityKind.EVENT: "events",
            EntityKind.MARKET: "markets",
        }[kind]
        records: object = payload.get(field)
        if kind is EntityKind.EVENT and endpoint.startswith("events/"):
            singleton = payload.get("event")
            records = [singleton] if isinstance(singleton, dict) else []
        if not succeeded or not isinstance(records, list):
            records = []
        parsed_rows: list[tuple[object, ...]] = []
        observation_ids: list[str] = []
        for raw in records:
            if not isinstance(raw, dict):
                continue
            try:
                entity = _parser(kind).parse(raw)
            except UniverseValidationError:
                continue
            source = _canonical(raw)
            source_hash = _hash_bytes(source.encode("utf-8"))
            observation_id = _identity(
                "m26f-entity-observation-v1",
                {
                    "acquired_at": acquired,
                    "entity_kind": kind.value,
                    "page_id": page_id,
                    "source_hash": source_hash,
                    "ticker": entity.ticker,
                },
            )
            event_ticker = (
                entity.event_ticker
                if isinstance(entity, Market)
                else (entity.ticker if isinstance(entity, Event) else None)
            )
            source_updated = (
                None
                if entity.source_updated_at is None
                else _utc(entity.source_updated_at, "source_updated_at")
            )
            parsed_rows.append(
                (
                    observation_id,
                    page_id,
                    self.authority_id,
                    kind.value,
                    entity.ticker,
                    event_ticker,
                    source,
                    source_hash,
                    entity.metadata_hash,
                    entity.rules_hash if isinstance(entity, Market) else None,
                    acquired,
                    source_updated,
                    provider,
                    endpoint,
                    UNIVERSE_PARSER_VERSION,
                    ARCHIVE_SCHEMA_VERSION,
                    ARCHIVE_IDENTITY_POLICY_VERSION,
                    ARCHIVE_VERIFICATION_POLICY_VERSION,
                    "0",
                )
            )
            observation_ids.append(observation_id)
        page_row = (
            page_id,
            self.authority_id,
            provider,
            endpoint,
            parameters_json,
            acquired,
            page_number,
            cursor_in,
            cursor_out,
            run_id,
            kind.value,
            canonical_payload,
            payload_hash,
            payload_hash,
            UNIVERSE_PARSER_VERSION,
            ARCHIVE_SCHEMA_VERSION,
            ARCHIVE_IDENTITY_POLICY_VERSION,
            ACQUISITION_POLICY_VERSION,
            int(succeeded),
            failure,
            "0",
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._insert_identical_or_reject(
                    db, "acquisition_pages", "page_id", page_id, page_row
                )
                for row in parsed_rows:
                    self._insert_identical_or_reject(
                        db, "entity_observations", "observation_id", str(row[0]), row
                    )
        except ArchiveError:
            raise
        except sqlite3.Error as exc:
            raise ArchiveError("archive append rejected") from exc
        return page_id, tuple(observation_ids)

    @staticmethod
    def _insert_identical_or_reject(
        db: sqlite3.Connection, table: str, key: str, identity: str, values: tuple[object, ...]
    ) -> None:
        prior = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (identity,)).fetchone()  # noqa: S608
        if prior is not None:
            if tuple(prior) == values:
                return
            raise ArchiveError(f"{table} identity collision")
        placeholders = ",".join("?" for _ in values)
        db.execute(f"INSERT INTO {table} VALUES({placeholders})", values)

    def _record_run_result(
        self,
        capability: object,
        *,
        run_id: str,
        completeness: str,
        pages: int,
        records_received: int,
        malformed: int,
        failure: str | None,
        finished_at: datetime,
    ) -> str:
        if capability is not _ACQUISITION_CAPABILITY:
            raise ArchiveError("authoritative archive writes require acquisition capability")
        finished = _utc(finished_at, "finished_at")
        material = {
            "completeness": completeness,
            "failure": failure,
            "finished_at": finished,
            "malformed": malformed,
            "pages": pages,
            "records_received": records_received,
            "run_id": run_id,
        }
        result_id = _identity("m26f-acquisition-run-result-v1", material)
        values = (
            result_id,
            run_id,
            self.authority_id,
            completeness,
            pages,
            records_received,
            malformed,
            failure,
            finished,
            ARCHIVE_SCHEMA_VERSION,
            "0",
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._insert_identical_or_reject(
                    db, "acquisition_run_results", "result_id", result_id, values
                )
        except sqlite3.IntegrityError as exc:
            raise ArchiveError("acquisition run already has a conflicting terminal result") from exc
        return result_id

    def get(self, observation_id: str) -> ArchivedObservation:
        with self._connect(read_only=True) as db:
            self._validate_schema(db)
            row = db.execute(
                "SELECT * FROM entity_observations WHERE observation_id=?", (observation_id,)
            ).fetchone()
            if row is None:
                raise ArchiveError("archive observation is unavailable")
            page = db.execute(
                "SELECT * FROM acquisition_pages WHERE page_id=?", (row["page_id"],)
            ).fetchone()
        if page is None:
            raise ArchiveError("archive observation page is unavailable")
        return self._restore(row, page)

    def _restore(self, row: sqlite3.Row, page: sqlite3.Row) -> ArchivedObservation:
        authority = self.authority_id
        fixed = (
            row["archive_authority_id"] == authority == page["archive_authority_id"],
            row["schema_version"] == page["schema_version"] == ARCHIVE_SCHEMA_VERSION,
            row["identity_policy_version"]
            == page["identity_policy_version"]
            == ARCHIVE_IDENTITY_POLICY_VERSION,
            row["verification_policy_version"] == ARCHIVE_VERIFICATION_POLICY_VERSION,
            row["parser_version"] == page["parser_version"] == UNIVERSE_PARSER_VERSION,
            page["acquisition_policy_version"] == ACQUISITION_POLICY_VERSION,
            row["provider"] == page["provider"],
            row["endpoint"] == page["endpoint"],
            row["acquired_at"] == page["acquired_at"],
            row["production_influence"] == page["production_influence"] == "0",
        )
        if not all(fixed):
            raise ArchiveError("persisted archive provenance is inconsistent")
        payload = str(page["canonical_payload"])
        try:
            decoded_payload = json.loads(payload)
            parameters = json.loads(str(page["parameters_json"]))
        except json.JSONDecodeError as exc:
            raise ArchiveError("persisted page JSON is corrupt") from exc
        if (
            _canonical(decoded_payload) != payload
            or _canonical(parameters) != page["parameters_json"]
        ):
            raise ArchiveError("persisted page material is not canonical")
        payload_hash = _hash_bytes(payload.encode("utf-8"))
        if (
            payload_hash != page["raw_content_hash"]
            or payload_hash != page["normalized_content_hash"]
        ):
            raise ArchiveError("persisted page hash mismatch")
        page_material = {
            "acquired_at": page["acquired_at"],
            "cursor_in": page["cursor_in"],
            "cursor_out": page["cursor_out"],
            "endpoint": page["endpoint"],
            "entity_kind": page["entity_kind"],
            "page_number": page["page_number"],
            "parameters": parameters,
            "payload_hash": payload_hash,
            "provider": page["provider"],
            "run_id": page["run_id"],
        }
        if (
            _identity("m26f-acquisition-page-v1", page_material) != page["page_id"]
            or row["page_id"] != page["page_id"]
        ):
            raise ArchiveError("persisted page identity mismatch")
        source = str(row["canonical_source"])
        try:
            raw = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ArchiveError("persisted entity source is corrupt") from exc
        if not isinstance(raw, dict) or _canonical(raw) != source:
            raise ArchiveError("persisted entity source is not canonical")
        field = {
            EntityKind.SERIES.value: "series",
            EntityKind.EVENT.value: "events",
            EntityKind.MARKET.value: "markets",
        }.get(str(row["entity_kind"]))
        page_records: object = (
            decoded_payload.get(field) if isinstance(decoded_payload, dict) else None
        )
        if (
            row["entity_kind"] == EntityKind.EVENT.value
            and str(page["endpoint"]).startswith("events/")
            and isinstance(decoded_payload, dict)
        ):
            singleton = decoded_payload.get("event")
            page_records = [singleton] if isinstance(singleton, dict) else None
        if not isinstance(page_records, list) or raw not in page_records:
            raise ArchiveError("persisted entity is not present in its acquisition page")
        source_hash = _hash_bytes(source.encode("utf-8"))
        if source_hash != row["canonical_source_hash"]:
            raise ArchiveError("persisted entity source hash mismatch")
        try:
            kind = EntityKind(str(row["entity_kind"]))
            entity = _parser(kind).parse(raw)
        except (ValueError, UniverseValidationError) as exc:
            raise ArchiveError("persisted entity cannot be reconstructed") from exc
        acquired_at = _parse_utc(row["acquired_at"], "acquired_at")
        source_updated = (
            None
            if row["source_updated_at"] is None
            else _parse_utc(row["source_updated_at"], "source_updated_at")
        )
        expected_event = (
            entity.event_ticker
            if isinstance(entity, Market)
            else (entity.ticker if isinstance(entity, Event) else None)
        )
        expected_rules = entity.rules_hash if isinstance(entity, Market) else None
        if not all(
            (
                row["ticker"] == entity.ticker,
                row["event_ticker"] == expected_event,
                row["metadata_hash"] == entity.metadata_hash,
                row["rules_hash"] == expected_rules,
                source_updated == entity.source_updated_at,
                row["entity_kind"] == page["entity_kind"],
            )
        ):
            raise ArchiveError("persisted parsed entity metadata is inconsistent")
        observation_material = {
            "acquired_at": row["acquired_at"],
            "entity_kind": kind.value,
            "page_id": row["page_id"],
            "source_hash": source_hash,
            "ticker": entity.ticker,
        }
        if _identity("m26f-entity-observation-v1", observation_material) != row["observation_id"]:
            raise ArchiveError("persisted observation identity mismatch")
        return ArchivedObservation(
            str(row["observation_id"]),
            str(row["page_id"]),
            authority,
            kind,
            entity.ticker,
            entity,
            source_hash,
            entity.metadata_hash,
            expected_rules,
            acquired_at,
            source_updated,
            str(row["provider"]),
            str(row["endpoint"]),
            str(row["parser_version"]),
            str(row["schema_version"]),
            str(row["identity_policy_version"]),
        )

    def at_or_before(self, kind: EntityKind, ticker: str, as_of: datetime) -> ArchivedObservation:
        cutoff = _utc(as_of, "as_of")
        with self._connect(read_only=True) as db:
            rows = db.execute(
                "SELECT o.*,p.canonical_payload AS p_payload FROM entity_observations o "
                "JOIN acquisition_pages p ON p.page_id=o.page_id "
                "WHERE o.entity_kind=? AND o.ticker=? "
                "AND o.acquired_at<=? ORDER BY o.acquired_at DESC,o.observation_id",
                (kind.value, ticker, cutoff),
            ).fetchall()
        if not rows:
            raise ArchiveError("no archived observation exists at or before as-of")
        latest = str(rows[0]["acquired_at"])
        candidates = [
            self.get(str(row["observation_id"])) for row in rows if row["acquired_at"] == latest
        ]
        if (
            len(
                {
                    (row.canonical_source_hash, row.metadata_hash, row.rules_hash)
                    for row in candidates
                }
            )
            != 1
        ):
            raise ArchiveError("conflicting observations share the authoritative acquisition time")
        return candidates[0]

    def status(self) -> ArchiveStatus:
        try:
            with self._connect(read_only=True) as db:
                self._validate_schema(db)
                counts = db.execute(
                    "SELECT COUNT(*),MIN(acquired_at),MAX(acquired_at) FROM acquisition_pages"
                ).fetchone()
                markets = db.execute(
                    "SELECT COUNT(*) FROM entity_observations WHERE entity_kind='market'"
                ).fetchone()[0]
                events = db.execute(
                    "SELECT COUNT(*) FROM entity_observations WHERE entity_kind='event'"
                ).fetchone()[0]
                ids = [
                    str(row[0])
                    for row in db.execute("SELECT observation_id FROM entity_observations")
                ]
            for identity in ids:
                self.get(identity)
            return ArchiveStatus(
                True,
                self.authority_id,
                int(counts[0]),
                int(markets),
                int(events),
                None if counts[1] is None else _parse_utc(counts[1], "earliest acquisition"),
                None if counts[2] is None else _parse_utc(counts[2], "latest acquisition"),
                False,
                ARCHIVE_SCHEMA_VERSION,
                ARCHIVE_IDENTITY_POLICY_VERSION,
            )
        except (ArchiveError, sqlite3.Error):
            return ArchiveStatus(
                False,
                "",
                0,
                0,
                0,
                None,
                None,
                True,
                ARCHIVE_SCHEMA_VERSION,
                ARCHIVE_IDENTITY_POLICY_VERSION,
            )


class _ArchiveAcquisitionWriter:
    """Opaque writer held by the configured synchronizer acquisition boundary."""

    __slots__ = ("__archive",)

    def __init__(self, archive: UniverseObservationArchive, capability: object) -> None:
        if capability is not _ACQUISITION_CAPABILITY:
            raise ArchiveError("archive writer capability creation rejected")
        self.__archive = archive

    def append_page(
        self,
        *,
        provider: str,
        endpoint: str,
        parameters: dict[str, str],
        acquired_at: datetime,
        page_number: int,
        cursor_in: str | None,
        cursor_out: str | None,
        run_id: str,
        kind: EntityKind,
        payload: dict[str, Any],
        succeeded: bool = True,
        failure: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        return self.__archive._archive_acquired_page(
            _ACQUISITION_CAPABILITY,
            provider=provider,
            endpoint=endpoint,
            parameters=parameters,
            acquired_at=acquired_at,
            page_number=page_number,
            cursor_in=cursor_in,
            cursor_out=cursor_out,
            run_id=run_id,
            kind=kind,
            payload=payload,
            succeeded=succeeded,
            failure=failure,
        )

    def record_run_result(
        self,
        *,
        run_id: str,
        completeness: str,
        pages: int,
        records_received: int,
        malformed: int,
        failure: str | None,
        finished_at: datetime,
    ) -> str:
        return self.__archive._record_run_result(
            _ACQUISITION_CAPABILITY,
            run_id=run_id,
            completeness=completeness,
            pages=pages,
            records_received=records_received,
            malformed=malformed,
            failure=failure,
            finished_at=finished_at,
        )


def _acquisition_writer_for_synchronizer(
    archive: UniverseObservationArchive,
) -> _ArchiveAcquisitionWriter:
    """Internal composition hook; not part of the archive's ordinary API."""
    return _ArchiveAcquisitionWriter(archive, _ACQUISITION_CAPABILITY)
