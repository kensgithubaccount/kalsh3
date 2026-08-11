"""Fail-closed live-evidence and final-preflight gates for M16."""

from dataclasses import dataclass, fields
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    m13_verified: bool
    m14_live_demo_verified: bool
    m15_path_verified: bool
    production_deployment_live_verified: bool
    production_read_live_verified: bool
    real_reconciliation_live_verified: bool
    api_compatibility_live_verified: bool
    postgres_concurrency_live_verified: bool
    signer_runtime_live_verified: bool
    model_eligible: bool
    source_evidence_ready: bool
    write_credential_installed: bool
    compliance_clear: bool
    global_halt_clear: bool
    kills_clear: bool
    exchange_active: bool
    trading_active: bool
    market_tradable: bool
    rules_current: bool
    candidate_current: bool
    fee_verified: bool
    account_reconciled: bool
    no_unknown_orders: bool
    no_unknown_positions: bool
    clock_safe: bool
    write_budget_safe: bool
    read_budget_safe: bool
    user_data_timestamp: datetime | None
    observed_at: datetime

    def missing(
        self, now: datetime, maximum_user_data_age: timedelta = timedelta(seconds=30)
    ) -> tuple[str, ...]:
        missing = [
            field.name
            for field in fields(self)
            if field.type is bool and getattr(self, field.name) is not True
        ]
        if (
            self.user_data_timestamp is None
            or now - self.user_data_timestamp > maximum_user_data_age
        ):
            missing.append("user_data_fresh")
        return tuple(sorted(missing))

    def approvable(self, now: datetime) -> bool:
        return not self.missing(now)


@dataclass(frozen=True, slots=True)
class FinalCanaryState:
    preview_hash: str
    approval_hash: str
    exact_price_unchanged: bool
    exact_quantity_unchanged: bool
    rules_hash_unchanged: bool
    candidate_unchanged: bool
    forecast_current: bool
    fresh_m13_passed: bool
    fresh_m13_authorization_issued: bool
    m15_body_hash_matches: bool
    readiness: ReadinessSnapshot

    def permits_boundary(self, now: datetime) -> bool:
        return all(
            (
                self.exact_price_unchanged,
                self.exact_quantity_unchanged,
                self.rules_hash_unchanged,
                self.candidate_unchanged,
                self.forecast_current,
                self.fresh_m13_passed,
                self.fresh_m13_authorization_issued,
                self.m15_body_hash_matches,
                self.readiness.approvable(now),
            )
        )
