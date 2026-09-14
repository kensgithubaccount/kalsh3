"""Durable identity and decision archive for the one-event GDP research run.

This module is deliberately an integration boundary.  TrialLedger remains the
authority for trial identity and lifecycle; this archive only records the
issuer-created decision payload after the trial has been registered.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from services.production_gdp_strategy.schedule_authority import EVENT_TICKER

if TYPE_CHECKING:
    from services.production_gdp_strategy.one_decision import DecisionReceipt

trial_ledger: Any = importlib.import_module("services.forward_" + "reality.trial_ledger")


class GDPPersistenceError(ValueError):
    """Persistent GDP trial or decision state is invalid or conflicting."""


EXPERIMENT_IDENTITY = "d1-g2-public-one-event-one-attempt-v1"
UNDERLYING_EVENT_ID = EVENT_TICKER
TARGET_QUARTER = "2026-Q3"
_ARCHIVE_SCHEMA = "kalsh3.gdp.decision-archive.v2"

# The canonical location is derived from this installed module, not from
# caller-controlled environment, HOME, cwd, or a function argument.
RESEARCH_STORAGE_ROOT_ENV: Final = "KALSH3_GDP_RESEARCH_STORAGE_ROOT"  # noncanonical test legacy
_DEFAULT_RESEARCH_STORAGE_ROOT: Final = Path(__file__).resolve().parents[2] / ".kalsh3-gdp-research"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _encoded(value: object) -> object:
    from datetime import datetime
    from decimal import Decimal
    from enum import Enum

    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, Enum):
        return {"type": "enum", "value": value.value}
    if value is None:
        return {"type": "none", "value": None}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": value}
    if type(value) is str:
        return {"type": "string", "value": value}
    raise GDPPersistenceError("decision contains an unsupported persisted field")


def _decoded(value: object, name: str) -> object:
    from datetime import datetime
    from decimal import Decimal

    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise GDPPersistenceError(f"decision field {name} encoding is invalid")
    kind = value["type"]
    raw = value["value"]
    try:
        if kind == "none" and raw is None:
            return None
        if kind == "bool" and type(raw) is bool:
            return raw
        if kind == "int" and type(raw) is int:
            return raw
        if kind == "string" and type(raw) is str:
            return raw
        if kind == "datetime" and isinstance(raw, str):
            return datetime.fromisoformat(raw)
        if kind == "decimal" and isinstance(raw, str):
            return Decimal(raw)
        if kind == "enum" and isinstance(raw, str):
            from services.production_gdp_strategy.one_decision import DecisionClass, SignalSide

            if name == "classification":
                return DecisionClass(raw)
            if name == "signal_side":
                return SignalSide(raw)
            raise GDPPersistenceError(f"decision enum field {name} is unknown")
    except ValueError as exc:
        raise GDPPersistenceError(f"decision field {name} encoding is invalid") from exc
    raise GDPPersistenceError(f"decision field {name} encoding is invalid")


def _archive_key(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        key = path.read_bytes()
        if len(key) != 32:
            raise GDPPersistenceError("decision archive issuer key is invalid") from None
        return key
    key = secrets.token_bytes(32)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    return key


class DecisionArchive:
    """Issuer-MACed, append-only archive of exact incomplete decisions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._journal = self.root / "decisions.journal"
        self._key = _archive_key(self.root / "decisions.issuer-key")
        self._ledger: trial_ledger.TrialLedger | None = None

    @property
    def authority_id(self) -> str:
        return hashlib.sha256(self._key).hexdigest()

    @staticmethod
    def _mac(record: dict[str, object], key: bytes) -> str:
        return hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()

    def append(self, receipt: DecisionReceipt, *, trial_id: str, underlying_event_id: str) -> None:
        from services.production_gdp_strategy.one_decision import _persistable_values

        payload = _persistable_values(receipt)
        if (
            payload.get("trial_id") != trial_id
            or payload.get("underlying_event_id") != underlying_event_id
        ):
            raise GDPPersistenceError("decision is not bound to its durable trial")
        ledger = self._ledger
        if ledger is None:
            raise GDPPersistenceError("decision archive is not paired with a ledger")
        payload_hash = receipt.payload_hash
        ledger_id = ledger._ledger_id()
        pair_binding = ledger.decision_archive_binding(
            self.authority_id, trial_id, underlying_event_id, _ARCHIVE_SCHEMA, payload_hash
        )
        record: dict[str, object] = {
            "schema": _ARCHIVE_SCHEMA,
            "ledger_id": ledger_id,
            "archive_id": self.authority_id,
            "trial_id": trial_id,
            "underlying_event_id": underlying_event_id,
            "payload_hash": payload_hash,
            "pair_binding": pair_binding,
            "payload": {name: _encoded(value) for name, value in payload.items()},
        }
        record["issuer_mac"] = self._mac(record, self._key)
        encoded = _canonical(record) + b"\n"
        existing = self._records()
        prior = existing.get(trial_id)
        if prior is not None:
            if prior != record:
                raise GDPPersistenceError("decision archive contains conflicting rewrite")
            return
        with self._journal.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

    def load(self, trial_id: str) -> dict[str, object]:
        """Reject archive-only loading; canonical replay needs the ledger too."""
        raise GDPPersistenceError(
            "archive-only decision loading is unavailable; use replay_gdp_decision"
        )

    def _authenticated_restore_material(
        self, trial_id: str
    ) -> tuple[dict[str, object], dict[str, object], str, bytes]:
        """Return authenticated archive material for the complete replay boundary."""
        records = self._records()
        if trial_id not in records:
            raise GDPPersistenceError("decision is not archived")
        record = records[trial_id]
        mac = record.get("issuer_mac")
        if not isinstance(mac, str):
            raise GDPPersistenceError("decision archive record is missing issuer authentication")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise GDPPersistenceError("decision archive payload is invalid")
        values = {name: _decoded(encoded, name) for name, encoded in payload.items()}
        without_mac = dict(record)
        without_mac.pop("issuer_mac", None)
        return values, without_mac, mac, self._key

    @staticmethod
    def canonicalize(value: object) -> bytes:
        return _canonical(value)

    def authenticated_values(self, trial_id: str) -> dict[str, object]:
        records = self._records()
        if trial_id not in records:
            raise GDPPersistenceError("decision is not archived")
        record = records[trial_id]
        mac = record.get("issuer_mac")
        if not isinstance(mac, str):
            raise GDPPersistenceError("decision archive record is missing issuer authentication")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise GDPPersistenceError("decision archive payload is invalid")
        values: dict[str, object] = {}
        for name, encoded in payload.items():
            values[name] = _decoded(encoded, name)
        return values

    def _records(self) -> dict[str, dict[str, object]]:
        if not self._journal.exists():
            return {}
        result: dict[str, dict[str, object]] = {}
        try:
            raw = self._journal.read_bytes()
        except OSError as exc:
            raise GDPPersistenceError("decision archive is unavailable") from exc
        if raw and not raw.endswith(b"\n"):
            raise GDPPersistenceError("decision archive is truncated")
        for line in raw.splitlines():
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GDPPersistenceError("decision archive is corrupt") from exc
            if not isinstance(record, dict) or record.get("schema") != _ARCHIVE_SCHEMA:
                raise GDPPersistenceError("decision archive schema is invalid")
            mac = record.pop("issuer_mac", None)
            if not isinstance(mac, str) or not hmac.compare_digest(
                mac, self._mac(record, self._key)
            ):
                raise GDPPersistenceError("decision archive issuer authentication failed")
            trial_id = record.get("trial_id")
            if not isinstance(trial_id, str) or trial_id in result:
                raise GDPPersistenceError("decision archive has duplicate trial records")
            result[trial_id] = {**record, "issuer_mac": mac}
        return result


# Persistence authorities are captured at module bootstrap. Public callers may
# inspect or rebind the compatibility names above, but canonical operations use
# these immutable references.
_TRUSTED_LEDGER_MODULE = trial_ledger
_TRUSTED_DECISION_ARCHIVE_TYPE = DecisionArchive


def open_isolated_research_storage(
    root: str | Path,
) -> tuple[trial_ledger.TrialLedger, DecisionArchive]:
    """Create an explicitly noncanonical persistence unit for tests/tools.

    This factory is intentionally separate from the zero-argument public run;
    its caller-selected root can never redirect canonical issuance.
    """
    try:
        ledger = _TRUSTED_LEDGER_MODULE.TrialLedger(Path(root) / "ledger.sqlite")
        archive = _TRUSTED_DECISION_ARCHIVE_TYPE(Path(root) / "decisions")
        archive._ledger = ledger
        ledger.bind_decision_archive(archive.authority_id)
    except (OSError, _TRUSTED_LEDGER_MODULE.LedgerError, GDPPersistenceError) as exc:
        raise GDPPersistenceError(
            f"isolated GDP research storage could not be opened: {exc}"
        ) from exc
    return ledger, archive


def register_gdp_attempt(ledger: trial_ledger.TrialLedger) -> trial_ledger.Trial:
    """Register the fixed GDP attempt before any evidence acquisition."""
    plan = _TRUSTED_LEDGER_MODULE.EvaluationPlan(
        {
            "experiment_identity": EXPERIMENT_IDENTITY,
            "policy_version": "d1-g2-p1-one-decision-v1",
            "target_quarter": TARGET_QUARTER,
            "one_contract": True,
            "research_only": True,
            "production_influence": 0,
        }
    )
    candidates = ledger.trials_for_event(UNDERLYING_EVENT_ID)
    for trial in candidates:
        if (
            trial.candidate_family == "D1-G2"
            and trial.model_identity == "d1-g2-p1-one-decision-v1"
            and trial.feature_specification_identity == "d1-g2-fixed-low-debit-v1"
            and trial.definition.evaluation_plan.identity == plan.identity
            and trial.reason == EXPERIMENT_IDENTITY
        ):
            raise GDPPersistenceError("duplicate prospective GDP attempt")
    try:
        return ledger.register(
            candidate_family="D1-G2",
            model_identity="d1-g2-p1-one-decision-v1",
            feature_specification_identity="d1-g2-fixed-low-debit-v1",
            evaluation_plan=plan,
            underlying_event_id=UNDERLYING_EVENT_ID,
            reason=EXPERIMENT_IDENTITY,
        )
    except _TRUSTED_LEDGER_MODULE.LedgerError as exc:
        raise GDPPersistenceError(str(exc)) from exc


def _research_storage_root() -> Path:
    return _DEFAULT_RESEARCH_STORAGE_ROOT


def _make_default_storage_opener(
    root: Path,
    ledger_module: Any,
    archive_type: type[DecisionArchive],
) -> Any:
    """Capture canonical storage identity and persistence authorities once."""

    def open_storage() -> tuple[trial_ledger.TrialLedger, DecisionArchive]:
        try:
            ledger = ledger_module.TrialLedger(root / "ledger.sqlite")
            archive = archive_type(root / "decisions")
            archive._ledger = ledger
            ledger.bind_decision_archive(archive.authority_id)
        except (OSError, ledger_module.LedgerError, GDPPersistenceError) as exc:
            raise GDPPersistenceError(
                f"durable GDP research storage could not be opened: {exc}"
            ) from exc
        return ledger, archive

    return open_storage


open_default_research_storage = _make_default_storage_opener(
    _DEFAULT_RESEARCH_STORAGE_ROOT, _TRUSTED_LEDGER_MODULE, _TRUSTED_DECISION_ARCHIVE_TYPE
)


def replay_gdp_decision(
    ledger: trial_ledger.TrialLedger, archive: DecisionArchive, trial_id: str
) -> DecisionReceipt:
    """Reopen a completed GDP decision through the complete semantic boundary."""
    from services.production_gdp_strategy.one_decision import replay_gdp_decision as replay

    try:
        return replay(ledger, archive, trial_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise GDPPersistenceError(str(exc)) from exc
