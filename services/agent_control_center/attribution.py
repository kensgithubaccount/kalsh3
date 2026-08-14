"""Explicit immutable source-to-agent attribution for research decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .domain import (
    ZERO_INFLUENCE,
    AutonomyMode,
    DecisionReceipt,
    ImplementationAvailability,
    agent_by_id,
)


def require_current_attribution_authority(agent_id: str) -> None:
    """Fail closed unless the current registry permits a new research attribution."""
    definition = agent_by_id(agent_id)
    if definition is None:
        raise ValueError("new attribution agent is not registered")
    if definition.availability is not ImplementationAvailability.AVAILABLE:
        raise ValueError("new attribution agent is not available")
    if definition.autonomy_mode is AutonomyMode.DISABLED:
        raise ValueError("new attribution agent autonomy is disabled")


@dataclass(frozen=True, slots=True)
class OpportunityAttribution:
    source_kind: str
    source_id: str
    agent_id: str
    evidence_references: tuple[str, ...]
    receipt: DecisionReceipt
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if not self.source_kind or not self.source_id:
            raise ValueError("explicit source kind and identity are required")
        require_current_attribution_authority(self.agent_id)
        if self.receipt.agent_id != self.agent_id:
            raise ValueError("attribution agent must match the receipt")
        if self.receipt.evidence_references != self.evidence_references:
            raise ValueError("attribution evidence must match the receipt")
        if self.production_influence != ZERO_INFLUENCE:
            raise ValueError("attribution must have exactly zero production influence")
        if self.receipt.receipt_id != receipt_identity(
            self.agent_id, self.receipt.agent_version, self.source_kind, self.source_id
        ):
            raise ValueError("receipt identity does not match its immutable source decision")


def receipt_identity(agent_id: str, agent_version: str, source_kind: str, source_id: str) -> str:
    """One logical source evaluation has one identity; changed sources get new identities."""
    material = json.dumps(
        ["m26b", agent_id, agent_version, source_kind, source_id],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()
