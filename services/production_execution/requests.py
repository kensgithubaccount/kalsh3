"""Outcome-oriented canonical V2 production envelope builders."""

from datetime import datetime
from decimal import Decimal

from .domain import (
    PRODUCTION_ORIGIN,
    TRADE_ROOT,
    AuthorityClass,
    Operation,
    ProductionRequestEnvelope,
    build_envelope,
)


def _fixed(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError("finite Decimal required")
    return format(value, "f")


def create_envelope(
    *,
    execution_id: str,
    authorization_id: str,
    decision_id: str,
    intent_hash: str,
    ticker: str,
    outcome_side: str,
    price: Decimal,
    quantity: Decimal,
    tif: str,
    expiration: datetime | None,
    post_only: bool,
    reduce_only: bool,
    cancel_on_pause: bool,
    stp: str,
    order_group_id: str | None,
    client_order_id: str,
    rules_version: str,
    candidate_version: str,
    portfolio_hash: str,
    reconciliation_hash: str,
    created_at: datetime,
    expires_at: datetime,
) -> ProductionRequestEnvelope:
    if outcome_side not in {"YES", "NO"}:
        raise ValueError("canonical YES or NO outcome required")
    # V2 quotes YES. Buying NO is economically selling YES at the complement.
    side = "bid" if outcome_side == "YES" else "ask"
    v2_price = price if outcome_side == "YES" else Decimal(1) - price
    if not Decimal(0) < v2_price < Decimal(1) or quantity <= 0:
        raise ValueError("invalid fixed-point order economics")
    body: dict[str, object] = {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": side,
        "count": _fixed(quantity),
        "price": _fixed(v2_price),
        "time_in_force": tif,
        "self_trade_prevention_type": stp,
        "post_only": post_only,
        "cancel_order_on_pause": cancel_on_pause,
        "reduce_only": reduce_only,
        "subaccount": 0,
        "exchange_index": 0,
    }
    if expiration is not None:
        body["expiration_time"] = int(expiration.timestamp())
    if order_group_id is not None:
        body["order_group_id"] = order_group_id
    return build_envelope(
        execution_id=execution_id,
        risk_authorization_id=authorization_id,
        risk_decision_id=decision_id,
        intent_hash=intent_hash,
        operation=Operation.CREATE,
        authority_class=AuthorityClass.NEW_RISK
        if not reduce_only
        else AuthorityClass.RISK_REDUCING,
        method="POST",
        path=f"{TRADE_ROOT}/portfolio/events/orders",
        origin=PRODUCTION_ORIGIN,
        market_ticker=ticker,
        outcome_side=outcome_side,
        book_side=side,
        price=price,
        quantity=quantity,
        time_in_force=tif,
        expiration=expiration,
        post_only=post_only,
        reduce_only=reduce_only,
        cancel_order_on_pause=cancel_on_pause,
        self_trade_prevention_type=stp,
        order_group_id=order_group_id,
        exchange_index=0,
        subaccount=0,
        client_order_id=client_order_id,
        body=body,
        query=(),
        rules_version=rules_version,
        candidate_version=candidate_version,
        portfolio_state_hash=portfolio_hash,
        reconciliation_state_hash=reconciliation_hash,
        created_at=created_at,
        expires_at=expires_at,
    )


def lifecycle_envelope(
    *,
    operation: Operation,
    order_id: str,
    authority: AuthorityClass,
    replacement_body: dict[str, object],
    template: ProductionRequestEnvelope,
    authorization_id: str,
    decision_id: str,
    intent_hash: str,
) -> ProductionRequestEnvelope:
    if operation not in {Operation.CANCEL, Operation.AMEND, Operation.DECREASE}:
        raise ValueError("lifecycle operation required")
    suffix = "" if operation == Operation.CANCEL else f"/{operation.lower()}"
    method = "DELETE" if operation == Operation.CANCEL else "POST"
    body = {**replacement_body, "subaccount": 0}
    return build_envelope(
        **{
            **{
                name: getattr(template, name)
                for name in (
                    "execution_id",
                    "market_ticker",
                    "outcome_side",
                    "book_side",
                    "price",
                    "quantity",
                    "time_in_force",
                    "expiration",
                    "post_only",
                    "reduce_only",
                    "cancel_order_on_pause",
                    "self_trade_prevention_type",
                    "order_group_id",
                    "exchange_index",
                    "subaccount",
                    "client_order_id",
                    "query",
                    "rules_version",
                    "candidate_version",
                    "portfolio_state_hash",
                    "reconciliation_state_hash",
                    "created_at",
                    "expires_at",
                )
            },
            "risk_authorization_id": authorization_id,
            "risk_decision_id": decision_id,
            "intent_hash": intent_hash,
            "operation": operation,
            "authority_class": authority,
            "method": method,
            "path": f"{TRADE_ROOT}/portfolio/events/orders/{order_id}{suffix}",
            "origin": PRODUCTION_ORIGIN,
            "body": body,
        }
    )
