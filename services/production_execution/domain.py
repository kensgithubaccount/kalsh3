"""Immutable typed M15 production envelopes and canonical serialization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

PRODUCTION_ORIGIN = "https://external-api.kalshi.com"
TRADE_ROOT = "/trade-api/v2"


class ProductionArmState(StrEnum):
    DISARMED = "DISARMED"
    CANARY_PENDING_APPROVAL = "CANARY_PENDING_APPROVAL"
    SUPERVISED_CANARY = "SUPERVISED_CANARY"
    ARMED_BOUNDED = "ARMED_BOUNDED"


class Operation(StrEnum):
    CREATE = "CREATE"
    CANCEL = "CANCEL"
    AMEND = "AMEND"
    DECREASE = "DECREASE"


class AuthorityClass(StrEnum):
    NEW_RISK = "NEW_RISK"
    RISK_REDUCING = "RISK_REDUCING"
    EMERGENCY_SAFETY = "EMERGENCY_SAFETY"


_ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def canonical_json(body: dict[str, object]) -> bytes:
    def contains_float(value: object) -> bool:
        if isinstance(value, float):
            return True
        if isinstance(value, dict):
            return any(contains_float(key) or contains_float(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(contains_float(item) for item in value)
        return False

    if contains_float(body):
        raise TypeError("floats are forbidden in production order bodies")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class ProductionRequestEnvelope:
    execution_id: str
    risk_authorization_id: str
    risk_decision_id: str
    intent_hash: str
    operation: Operation
    authority_class: AuthorityClass
    method: str
    path: str
    origin: str
    market_ticker: str
    outcome_side: str
    book_side: str
    price: Decimal | None
    quantity: Decimal | None
    time_in_force: str | None
    expiration: datetime | None
    post_only: bool
    reduce_only: bool
    cancel_order_on_pause: bool
    self_trade_prevention_type: str | None
    order_group_id: str | None
    exchange_index: int
    subaccount: int
    client_order_id: str
    canonical_body: bytes
    body_hash: str
    query: tuple[tuple[str, str], ...]
    rules_version: str
    candidate_version: str
    portfolio_state_hash: str
    reconciliation_state_hash: str
    created_at: datetime
    expires_at: datetime
    content_hash: str

    def __post_init__(self) -> None:
        if self.origin != PRODUCTION_ORIGIN or self.subaccount != 0 or self.exchange_index != 0:
            raise ValueError("production origin, subaccount, or exchange index is invalid")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("UTC-aware envelope timestamps required")
        if self.expires_at <= self.created_at or self.expires_at - self.created_at > timedelta(
            seconds=5
        ):
            raise ValueError("production envelope TTL must be at most five seconds")
        validate_operation(self.operation, self.method, self.path)
        if self.body_hash != digest(self.canonical_body):
            raise ValueError("BODY_HASH_MISMATCH")
        if self.content_hash != digest(self.content_bytes()):
            raise ValueError("ENVELOPE_CHANGED")
        decoded = json.loads(self.canonical_body)
        if canonical_json(decoded) != self.canonical_body or decoded.get("subaccount") != 0:
            raise ValueError("canonical body or subaccount changed")

    def content_bytes(self) -> bytes:
        fields = (
            self.execution_id,
            self.risk_authorization_id,
            self.risk_decision_id,
            self.intent_hash,
            self.operation,
            self.authority_class,
            self.method,
            self.path,
            self.origin,
            self.market_ticker,
            self.outcome_side,
            self.book_side,
            str(self.price),
            str(self.quantity),
            self.time_in_force,
            self.expiration,
            self.post_only,
            self.reduce_only,
            self.cancel_order_on_pause,
            self.self_trade_prevention_type,
            self.order_group_id,
            self.exchange_index,
            self.subaccount,
            self.client_order_id,
            self.body_hash,
            self.query,
            self.rules_version,
            self.candidate_version,
            self.portfolio_state_hash,
            self.reconciliation_state_hash,
            self.created_at.astimezone(UTC),
            self.expires_at.astimezone(UTC),
        )
        return canonical_json({"fields": [str(value) for value in fields]})


def validate_operation(operation: Operation, method: str, path: str) -> None:
    root = f"{TRADE_ROOT}/portfolio/events/orders"
    if "?" in path or "//" in path or "%" in path or ".." in path or not path.startswith("/"):
        raise ValueError("path confusion rejected")
    valid = operation == Operation.CREATE and method == "POST" and path == root
    if operation != Operation.CREATE:
        suffix = path.removeprefix(root + "/").split("/")
        valid_id = bool(suffix and _ORDER_ID.fullmatch(suffix[0]))
        if operation == Operation.CANCEL:
            valid = method == "DELETE" and len(suffix) == 1 and valid_id
        elif operation == Operation.AMEND:
            valid = method == "POST" and len(suffix) == 2 and suffix[1] == "amend" and valid_id
        elif operation == Operation.DECREASE:
            valid = method == "POST" and len(suffix) == 2 and suffix[1] == "decrease" and valid_id
    if not valid:
        raise ValueError("method/path is outside production signer authority")


def build_envelope(**values: object) -> ProductionRequestEnvelope:
    body = values.pop("body")
    if not isinstance(body, dict):
        raise TypeError("typed body required")
    canonical_body = canonical_json(body)
    values["canonical_body"] = canonical_body
    values["body_hash"] = digest(canonical_body)
    provisional = object.__new__(ProductionRequestEnvelope)
    for key, value in values.items():
        object.__setattr__(provisional, key, value)
    object.__setattr__(provisional, "content_hash", "")
    values["content_hash"] = digest(provisional.content_bytes())
    return ProductionRequestEnvelope(**values)  # type: ignore[arg-type]
