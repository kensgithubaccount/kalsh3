"""M16 orchestration stops before production transmission in the offline release."""

from dataclasses import dataclass
from datetime import datetime

from .domain import ApprovalState, CanaryState, HumanCanaryApproval, HumanCanaryPreview
from .readiness import FinalCanaryState, ReadinessSnapshot


@dataclass(frozen=True, slots=True)
class CanaryDecision:
    state: CanaryState
    missing_gates: tuple[str, ...]
    production_state: str = "DISARMED"
    send_permitted: bool = False


def evaluate_preview(
    preview: HumanCanaryPreview, readiness: ReadinessSnapshot, now: datetime
) -> CanaryDecision:
    if now >= preview.expires_at:
        return CanaryDecision(CanaryState.EXPIRED, ("preview_expired",))
    missing = readiness.missing(now)
    return CanaryDecision(
        CanaryState.READY_FOR_APPROVAL if not missing else CanaryState.APPROVAL_UNAVAILABLE,
        missing,
    )


def final_revalidation(
    preview: HumanCanaryPreview,
    approval: HumanCanaryApproval,
    final: FinalCanaryState,
    now: datetime,
) -> CanaryDecision:
    reasons: list[str] = []
    if approval.state != ApprovalState.ISSUED or now >= approval.expires_at:
        reasons.append("approval_expired_or_used")
    if approval.preview_hash != preview.content_hash:
        reasons.append("preview_changed")
    if final.preview_hash != preview.content_hash or final.approval_hash != approval.content_hash:
        reasons.append("final_binding_changed")
    if approval.exact_price != preview.limit_price or approval.exact_quantity != preview.quantity:
        reasons.append("price_or_quantity_changed")
    if approval.rules_hash != preview.rules_hash:
        reasons.append("rules_changed")
    if not final.permits_boundary(now):
        reasons.extend(final.readiness.missing(now) or ("final_preflight_failed",))
    # M16 implementation task has no credential and cannot transmit even if fixture gates are true.
    if reasons:
        return CanaryDecision(CanaryState.CANARY_FAILED, tuple(sorted(set(reasons))))
    return CanaryDecision(CanaryState.CANARY_AUTHORIZED, (), send_permitted=False)


@dataclass(frozen=True, slots=True)
class CanaryAcceptanceReport:
    report_id: str
    execution_state: str
    fill_quantity: str
    fill_price: str
    fees: str
    cash_reconciled: bool
    position_reconciled: bool
    reserve_preserved: bool
    risk_limits_preserved: bool
    no_unknown_order: bool
    signer_state: str
    websocket_state: str
    rest_state: str
    journal_state: str
    strategy_outcome: str = "NOT YET KNOWABLE"

    @property
    def operationally_complete(self) -> bool:
        return (
            all(
                (
                    self.cash_reconciled,
                    self.position_reconciled,
                    self.reserve_preserved,
                    self.risk_limits_preserved,
                    self.no_unknown_order,
                )
            )
            and self.execution_state == "KNOWN"
        )
