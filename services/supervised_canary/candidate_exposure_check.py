"""M27I -- operator-only, additive, GET-only candidate-specific exposure check.

M27F's live read acceptance (:mod:`services.supervised_canary.live_read_acceptance`)
proves the whole subaccount-0 portfolio read set (balance/positions/orders/fills/settlements)
succeeded and reconciled -- but it deliberately never stores the raw response bodies, only
counts and hashes, so it cannot answer a market-specific question: "is there an open order or
a nonzero position in *this exact* candidate market right now?" A count of zero unknown orders
across the whole account does not prove there is no order or position in one particular
market -- see the M27I milestone's own gap analysis.

This module closes exactly that gap without touching M27F: it reuses the same reviewed,
GET-only :class:`services.kalshi_account_gateway.client.KalshiAccountClient` boundary directly
(no new HTTP stack, no mutation capability) to re-read ``orders`` and ``positions`` for
subaccount 0, and reduces them to a secret-free presence/count summary for one market ticker
(market tickers are public market identifiers, never account secrets). The raw account rows are
discarded immediately after filtering; only counts and booleans are ever returned.

Never installs a credential, never arms production, never calls a mutating endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.kalshi_account_gateway.client import (
    AccountGatewayError,
    KalshiAccountClient,
)

SCHEMA = "kalsh3.m27i.candidate-exposure.v1"
SOFTWARE_VERSION = "kalsh3.m27i.candidate-exposure-check/1"

_READ_FAILURES = (AccountGatewayError,)


@dataclass(frozen=True, slots=True)
class CandidateExposureEvidence:
    """Secret-free, market-specific proof of no unknown order/position for one ticker.

    Carries no raw order or position rows -- only the exact market ticker (public, not a
    secret), counts, and booleans.
    """

    schema: str
    software_version: str
    market_ticker: str
    started_at: datetime
    completed_at: datetime
    orders_classification: str
    positions_classification: str
    open_order_count: int | None
    position_nonzero: bool | None
    classification: str
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "market_ticker": self.market_ticker,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "orders_classification": self.orders_classification,
            "positions_classification": self.positions_classification,
            "open_order_count": self.open_order_count,
            "position_nonzero": self.position_nonzero,
            "classification": self.classification,
            "reason": self.reason,
        }


def _open_order_count(rows: list[dict[str, Any]], market_ticker: str) -> int:
    count = 0
    for row in rows:
        ticker = row.get("ticker")
        if not isinstance(ticker, str):
            raise AccountGatewayError("order row missing a ticker field")
        status = row.get("status")
        if ticker == market_ticker and status in {"resting", "pending", None}:
            count += 1
    return count


def _position_nonzero(rows: list[dict[str, Any]], market_ticker: str) -> bool:
    for row in rows:
        ticker = row.get("ticker")
        if not isinstance(ticker, str):
            raise AccountGatewayError("position row missing a ticker field")
        if ticker != market_ticker:
            continue
        quantity = row.get("position")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise AccountGatewayError("position row quantity malformed")
        if quantity != 0:
            return True
    return False


def check_candidate_market_exposure(
    *,
    client: KalshiAccountClient,
    market_ticker: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CandidateExposureEvidence:
    """Fresh GET-only read of orders/positions, reduced to one market's exposure only.

    Never mutates, never touches any endpoint beyond the two reviewed portfolio GETs already
    used by M27F. Raw rows for every *other* market, and every field except ``ticker``/
    ``status``/``position``, are discarded before this function returns.
    """
    started_at = clock()
    try:
        orders = client.get_collection("orders")
    except _READ_FAILURES as exc:
        completed_at = clock()
        return CandidateExposureEvidence(
            SCHEMA,
            SOFTWARE_VERSION,
            market_ticker,
            started_at,
            completed_at,
            type(exc).__name__,
            "NOT_RUN",
            None,
            None,
            "BLOCKED",
            reason="orders read did not complete",
        )
    try:
        positions = client.get_collection("positions")
    except _READ_FAILURES as exc:
        completed_at = clock()
        return CandidateExposureEvidence(
            SCHEMA,
            SOFTWARE_VERSION,
            market_ticker,
            started_at,
            completed_at,
            "SUCCESS",
            type(exc).__name__,
            None,
            None,
            "BLOCKED",
            reason="positions read did not complete",
        )
    try:
        open_orders = _open_order_count(orders, market_ticker)
        nonzero_position = _position_nonzero(positions, market_ticker)
    except AccountGatewayError as exc:
        completed_at = clock()
        return CandidateExposureEvidence(
            SCHEMA,
            SOFTWARE_VERSION,
            market_ticker,
            started_at,
            completed_at,
            "SUCCESS",
            "SUCCESS",
            None,
            None,
            "BLOCKED",
            reason=str(exc),
        )
    completed_at = clock()
    return CandidateExposureEvidence(
        SCHEMA,
        SOFTWARE_VERSION,
        market_ticker,
        started_at,
        completed_at,
        "SUCCESS",
        "SUCCESS",
        open_orders,
        nonzero_position,
        "PASS",
        reason=None,
    )
