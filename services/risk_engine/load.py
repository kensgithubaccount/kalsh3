"""Bounded-memory deterministic M13 risk-evaluation fixture."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskLoadSummary:
    evaluations: int
    markets: int
    events: int
    correlated_clusters: int
    rejected_market: int
    rejected_event: int
    rejected_aggregate: int
    loss_transitions: int
    authorization_expiries: int
    checksum: str


def stream_risk_load(count: int = 50_000) -> RiskLoadSummary:
    digest = hashlib.sha256()
    markets: set[int] = set()
    events: set[int] = set()
    clusters: set[int] = set()
    market_rejects = event_rejects = aggregate_rejects = transitions = expiries = 0
    for index in range(count):
        market, event, cluster = index % 5_000, index % 2_000, index % 500
        markets.add(market)
        events.add(event)
        clusters.add(cluster)
        current_market = Decimal(index % 10)
        current_event = Decimal(index % 25)
        current_aggregate = Decimal(index % 100)
        intended = Decimal(index % 4 + 1)
        market_rejects += current_market + intended > Decimal(10)
        event_rejects += current_event + intended > Decimal(25)
        aggregate_rejects += current_aggregate + intended > Decimal(100)
        transitions += index % 997 == 0
        expiries += index % 499 == 0
        digest.update(f"{index}:{market}:{event}:{cluster}:{current_market}:{intended}".encode())
    return RiskLoadSummary(
        count,
        len(markets),
        len(events),
        len(clusters),
        market_rejects,
        event_rejects,
        aggregate_rejects,
        transitions,
        expiries,
        digest.hexdigest(),
    )
