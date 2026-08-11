"""Grouped counts and synthetic walk-forward summaries without false independence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EvaluationCounts:
    market_snapshots: int
    forecast_runs: int
    unique_checkpoints: int
    unique_markets: int
    unique_events: int
    settled_forecasts: int
    settled_unique_events: int
    effective_sample_size: Decimal


def grouped_counts(rows: tuple[tuple[str, str, str, bool], ...]) -> EvaluationCounts:
    # row: market, event, checkpoint, settled
    return EvaluationCounts(
        len(rows),
        len(rows),
        len({(m, c) for m, _, c, _ in rows}),
        len({m for m, _, _, _ in rows}),
        len({e for _, e, _, _ in rows}),
        sum(settled for *_, settled in rows),
        len({e for _, e, _, settled in rows if settled}),
        Decimal(len({e for _, e, _, settled in rows if settled})),
    )
