"""Canonical, versioned YES/NO settlement semantics independent of forecasts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from services.market_universe.domain import UniverseValidationError, exact, parse_time


class Approval(StrEnum):
    APPROVED = "APPROVED"
    AMBIGUOUS = "AMBIGUOUS"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    RULES_CHANGED = "RULES_CHANGED"


class Comparator(StrEnum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "="
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SettlementSource:
    name: str
    url: str | None
    authority: str
    primary: bool


@dataclass(frozen=True, slots=True)
class ContractSemantics:
    market_ticker: str
    question: str
    yes_meaning: str
    no_meaning: str
    authority: str
    settlement_sources: tuple[SettlementSource, ...]
    deadline: datetime
    timezone: str
    comparator: Comparator
    threshold: Decimal | None
    unit: str | None
    rounding: str | None
    revision_policy: str | None
    recount_policy: str | None
    cancellation_policy: str | None
    postponement_policy: str | None
    early_close_condition: str | None
    exceptions: tuple[str, ...]
    rules_hash: str
    semantics_hash: str
    parser_name: str
    parser_version: str


class SemanticsParser(Protocol):
    name: str
    version: str

    def parse(self, market: dict[str, Any]) -> ContractSemantics: ...


def canonical_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class StructuredRulesParser:
    name = "deterministic_structured"
    version = "1"

    def parse(self, market: dict[str, Any]) -> ContractSemantics:
        required = (
            "ticker",
            "title",
            "rules_primary",
            "yes_meaning",
            "no_meaning",
            "settlement_authority",
            "settlement_sources",
            "deadline",
            "timezone",
        )
        for key in required:
            if market.get(key) in (None, "", []):
                raise UniverseValidationError(f"contract semantics missing {key}")
        sources = market["settlement_sources"]
        if not isinstance(sources, list) or any(not isinstance(x, dict) for x in sources):
            raise UniverseValidationError("settlement sources malformed")
        normalized = []
        for source in sources:
            if (
                not isinstance(source.get("name"), str)
                or not isinstance(source.get("authority"), str)
                or not isinstance(source.get("primary"), bool)
            ):
                raise UniverseValidationError("settlement source incomplete")
            normalized.append(
                SettlementSource(
                    source["name"], source.get("url"), source["authority"], source["primary"]
                )
            )
        if sum(source.primary for source in normalized) != 1:
            raise UniverseValidationError("exactly one primary settlement source required")
        try:
            comparator = Comparator(str(market.get("comparator", "none")))
        except ValueError as exc:
            raise UniverseValidationError("unsupported threshold comparator") from exc
        threshold = (
            None if market.get("threshold") is None else exact(market["threshold"], "threshold")
        )
        material = {
            key: market.get(key)
            for key in (
                "ticker",
                "title",
                "rules_primary",
                "rules_secondary",
                "yes_meaning",
                "no_meaning",
                "settlement_authority",
                "settlement_sources",
                "deadline",
                "timezone",
                "comparator",
                "threshold",
                "unit",
                "rounding",
                "revision_policy",
                "recount_policy",
                "cancellation_policy",
                "postponement_policy",
                "early_close_condition",
                "exceptions",
            )
        }
        rules_hash = canonical_hash(
            {
                key: market.get(key)
                for key in (
                    "rules_primary",
                    "rules_secondary",
                    "settlement_sources",
                    "comparator",
                    "threshold",
                    "rounding",
                    "revision_policy",
                    "early_close_condition",
                    "exceptions",
                )
            }
        )
        deadline = parse_time(market["deadline"])
        if deadline is None:
            raise UniverseValidationError("deadline missing")
        return ContractSemantics(
            market["ticker"],
            market["title"],
            market["yes_meaning"],
            market["no_meaning"],
            market["settlement_authority"],
            tuple(normalized),
            deadline,
            market["timezone"],
            comparator,
            threshold,
            market.get("unit"),
            market.get("rounding"),
            market.get("revision_policy"),
            market.get("recount_policy"),
            market.get("cancellation_policy"),
            market.get("postponement_policy"),
            market.get("early_close_condition"),
            tuple(market.get("exceptions", [])),
            rules_hash,
            canonical_hash(material),
            self.name,
            self.version,
        )


@dataclass(frozen=True, slots=True)
class Interpretation:
    semantics: ContractSemantics | None
    approval: Approval
    reasons: tuple[str, ...]


def reconcile(
    candidates: list[ContractSemantics], previous: ContractSemantics | None = None
) -> Interpretation:
    if not candidates:
        return Interpretation(None, Approval.AMBIGUOUS, ("NO_VALID_INTERPRETATION",))
    hashes = {candidate.semantics_hash for candidate in candidates}
    if len(hashes) > 1:
        return Interpretation(None, Approval.CONTRADICTED, ("PARSERS_DISAGREE",))
    candidate = candidates[0]
    if not candidate.yes_meaning or not candidate.no_meaning or not candidate.settlement_sources:
        return Interpretation(None, Approval.AMBIGUOUS, ("INCOMPLETE_SEMANTICS",))
    if previous is not None and previous.rules_hash != candidate.rules_hash:
        return Interpretation(
            candidate, Approval.RULES_CHANGED, ("MATERIAL_RULES_CHANGED_REVALIDATION_REQUIRED",)
        )
    return Interpretation(candidate, Approval.APPROVED, ())
