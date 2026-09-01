"""Offline CPI-E1-P10A historical binding and market-baseline gate.

This module is deliberately a reader of the immutable P8/P9A artifacts.  It has
no network, account, execution, fee, or production influence capability.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.forecasting.cpi_annual_archive_acquisition import load_frozen_target_cohort
from services.historical_replay.cpi_price_evidence import validate_frozen_cohort

P9A_ROOT = Path("evidence/cpi_p9a_historical_price")
P7_VALUES: dict[tuple[int, int], Decimal] = {
    (2025, 7): Decimal("0.2"),
    (2025, 12): Decimal("0.3"),
    (2026, 1): Decimal("0.2"),
}
P7_OBSERVATION_IDS = {
    (2025, 7): "74b5c6f504d448ac475a5598e50a0602b249368acd26b90642c066ecd96f4c65",
    (2025, 12): "9cbc587c2fe7a8664e9a9546ad6a672e7914719cadc62a9cf03025affc4be0af",
    (2026, 1): "6b566274e63c5c6d65f11ab193c0275b30264cdeae428bb24a442dde0bfdbbda",
}
MONTHS = {
    name: i
    for i, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
EVENT_RE = re.compile(r"^(?:CPI|KXCPI)-(\d{2})([A-Z]{3})$")
PREDICATE_RE = re.compile(r"more than\s+(-?\d+(?:\.\d+)?)%.*?in\s+([A-Za-z]+)\s+(\d{4})", re.I)


class P10ABindingError(ValueError):
    """A required identity or immutable artifact check failed."""


@dataclass(frozen=True, slots=True)
class EventRow:
    event_ticker: str
    underlying_event_id: str
    reference_month: tuple[int, int]
    release_at: datetime
    cutoff_at: datetime
    market_ticker: str
    threshold: Decimal
    outcome: int
    probability: Decimal | None
    quote_age_seconds: int | None
    staleness: str
    p9a_evidence_id: str
    request_identity: str
    rejection: str | None


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise P10ABindingError("timestamp is malformed")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise P10ABindingError("timestamp is not timezone-aware")
    return result.astimezone(UTC)


def _event_month(event_ticker: str) -> tuple[int, int]:
    match = EVENT_RE.fullmatch(event_ticker)
    if match is None:
        raise P10ABindingError("event ticker grammar is invalid")
    return 2000 + int(match.group(1)), MONTHS[match.group(2)]


def _predicate(row: dict[str, Any]) -> tuple[Decimal, tuple[int, int]]:
    match = PREDICATE_RE.search(str(row.get("rules_primary", "")))
    if match is None:
        raise P10ABindingError("contract predicate is absent or ambiguous")
    return Decimal(match.group(1)), (int(match.group(3)), MONTHS[match.group(2)[:3].upper()])


def _logloss(probability: Decimal, outcome: int) -> float:
    clipped = min(max(float(probability), 0.01), 0.99)
    return -(math.log(clipped) if outcome else math.log1p(-clipped))


def _probability(row: EventRow) -> Decimal:
    if row.probability is None:
        raise P10ABindingError("internal non-executable quote reached scorer")
    return row.probability


def build_binding(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root)
    p9a_root = root / P9A_ROOT
    validate_frozen_cohort(p9a_root)
    manifest = json.loads((p9a_root / "manifest.json").read_bytes())
    inventory = json.loads((p9a_root / "market_inventory.json").read_bytes())["markets"]
    truth: dict[tuple[int, int], tuple[Decimal, str, datetime]] = {}
    for observation in load_frozen_target_cohort(root):
        truth[(observation.reference_year, observation.reference_month)] = (
            observation.value,
            observation.member_sha256,
            observation.release_instant.astimezone(UTC),
        )
    truth.update(
        {
            month: (
                value,
                P7_OBSERVATION_IDS[month],
                datetime(
                    month[0] + (month[1] == 12),
                    1 if month[1] == 12 else month[1] + 1,
                    10,
                    13,
                    30,
                    tzinfo=UTC,
                ),
            )
            for month, value in P7_VALUES.items()
        }
    )
    p9a_by_ticker = {row["market_ticker"]: row for row in manifest["markets"]}
    rows: list[EventRow] = []
    for market in inventory:
        ticker = market.get("ticker")
        event = market.get("event_ticker")
        rejection: str | None = None
        try:
            if not isinstance(ticker, str) or not isinstance(event, str):
                raise P10ABindingError("missing market/event identity")
            reference = _event_month(event)
            p9a = p9a_by_ticker.get(ticker)
            if p9a is None:
                raise P10ABindingError("missing P9A evidence identity")
            if p9a.get("underlying_event_id") != f"kalshi:{event}":
                raise P10ABindingError("underlying event identity mismatch")
            try:
                predicate, predicate_month = _predicate(market)
            except P10ABindingError:
                # Three historical rows contain a known Kalshi placeholder in
                # rules_primary.  The frozen P9A semantic parser already bound
                # their exact GT threshold; the event ticker supplies only the
                # independently checked reference month.
                if p9a.get("comparator") != "GT":
                    raise
                predicate = Decimal(str(p9a["threshold"]))
                predicate_month = reference
            if predicate_month != reference:
                raise P10ABindingError("predicate/reference month mismatch")
            if p9a.get("comparator") != "GT" or p9a.get("comparator_symbol") != ">":
                raise P10ABindingError("threshold/predicate mismatch")
            if reference not in truth:
                raise P10ABindingError("missing authoritative initial-release truth")
            value, _truth_id, release_at = truth[reference]
            outcome = int(value > predicate)
            if (
                market.get("result") not in {"yes", "no"}
                or int(market["result"] == "yes") != outcome
            ):
                raise P10ABindingError("settlement outcome does not match bound predicate")
            probability = (
                None
                if p9a.get("yes_ask") in (None, "1.0000") or p9a.get("yes_bid") in (None, "0.0000")
                else Decimal(str(p9a["yes_ask"]))
            )
            age = p9a.get("quote_age_seconds")
            if p9a.get("candle_end_period_ts") is not None and p9a["candle_end_period_ts"] >= int(
                _time(p9a["market_close"]).timestamp()
            ):
                raise P10ABindingError("post-cutoff quote")
            cutoff = _time(p9a["market_close"])
            rows.append(
                EventRow(
                    event,
                    f"kalshi:{event}",
                    reference,
                    release_at,
                    cutoff,
                    ticker,
                    predicate,
                    outcome,
                    probability,
                    age,
                    str(p9a.get("staleness_state")),
                    str(p9a["evidence_id"]),
                    str(p9a["request_identity"]),
                    None,
                )
            )
        except (KeyError, TypeError, ValueError, P10ABindingError) as exc:
            rejection = str(exc)
            if isinstance(event, str) and isinstance(ticker, str):
                rows.append(
                    EventRow(
                        event,
                        f"kalshi:{event}",
                        _event_month(event) if EVENT_RE.fullmatch(event) else (0, 0),
                        datetime.min.replace(tzinfo=UTC),
                        datetime.min.replace(tzinfo=UTC),
                        ticker,
                        Decimal(0),
                        0,
                        None,
                        None,
                        "REJECTED",
                        str(p9a_by_ticker.get(ticker, {}).get("evidence_id", "")),
                        str(p9a_by_ticker.get(ticker, {}).get("request_identity", "")),
                        rejection,
                    )
                )
    groups: dict[str, list[EventRow]] = {}
    for row in rows:
        if row.rejection is None:
            groups.setdefault(row.event_ticker, []).append(row)
    usable_events = {
        event: members
        for event, members in groups.items()
        if any(row.probability is not None for row in members)
    }
    event_scores: list[dict[str, Any]] = []
    for event, members in sorted(usable_events.items()):
        scored = [row for row in members if row.probability is not None]
        event_scores.append(
            {
                "event_ticker": event,
                "siblings": len(members),
                "scored_siblings": len(scored),
                "brier": sum((float(_probability(row)) - row.outcome) ** 2 for row in scored)
                / len(scored),
                "log_loss": sum(_logloss(_probability(row), row.outcome) for row in scored)
                / len(scored),
            }
        )
    bound = [row for row in rows if row.rejection is None]
    scored_rows = [row for row in bound if row.probability is not None]

    def subgroup(key: Callable[[EventRow], bool]) -> dict[str, float | int]:
        selected = [row for row in scored_rows if key(row)]
        return {
            "rows": len(selected),
            "events": len({row.event_ticker for row in selected}),
            "brier": (
                sum((float(_probability(row)) - row.outcome) ** 2 for row in selected)
                / len(selected)
                if selected
                else float("nan")
            ),
            "log_loss": (
                sum(_logloss(_probability(row), row.outcome) for row in selected) / len(selected)
                if selected
                else float("nan")
            ),
        }

    calibration = []
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        selected = [row for row in scored_rows if lower <= float(_probability(row)) < lower + 0.2]
        calibration.append(
            {
                "bin": f"[{lower:.1f},{lower + 0.2:.1f})",
                "rows": len(selected),
                "mean_prediction": sum(float(_probability(row)) for row in selected) / len(selected)
                if selected
                else None,
                "event_rate": sum(row.outcome for row in selected) / len(selected)
                if selected
                else None,
            }
        )
    return {
        "schema_version": "cpi-e1-p10a-binding-v1",
        "p9a_events": len(manifest["events"]),
        "p9a_siblings": len(manifest["markets"]),
        "bound_rows": len(bound),
        "quote_usable_rows": sum(row.probability is not None for row in bound),
        "quote_evidence_vs_executable": {
            "quote_evidence_rows": len(bound),
            "executable_quote_rows": len(scored_rows),
            "executable_definition": (
                "strictly pre-cutoff YES ask with non-boundary YES bid and ask; "
                "no midpoint or last trade"
            ),
        },
        "bound_events": len(groups),
        "usable_events": len(usable_events),
        "rejected_rows": [
            {"market_ticker": row.market_ticker, "reason": row.rejection}
            for row in rows
            if row.rejection
        ],
        "event_scores": event_scores,
        "brier_score": sum(item["brier"] for item in event_scores) / len(event_scores),
        "log_loss": sum(item["log_loss"] for item in event_scores) / len(event_scores),
        "log_loss_clipping": "probabilities clipped to [0.01, 0.99] before natural-log scoring",
        "calibration": calibration,
        "performance_by_time_to_cutoff": {
            "fresh_age_le_1h": subgroup(
                lambda row: row.quote_age_seconds is not None and row.quote_age_seconds <= 3600
            ),
            "stale_age_gt_1h": subgroup(
                lambda row: row.quote_age_seconds is not None and row.quote_age_seconds > 3600
            ),
        },
        "performance_by_threshold_location": {
            "negative_threshold": subgroup(lambda row: row.threshold < 0),
            "zero_to_half_threshold": subgroup(lambda row: 0 <= row.threshold < Decimal("0.5")),
            "half_or_more_threshold": subgroup(lambda row: row.threshold >= Decimal("0.5")),
        },
        "predictor_inventory": {
            "admissible": [
                "prior P8 initial-release values with release_instant strictly before cutoff"
            ],
            "missing": [
                (
                    "independent contemporaneous forecast vintages or survey consensus "
                    "snapshots with exact publication timestamps"
                ),
                "point-in-time release-calendar snapshots for every cutoff",
            ],
        },
        "modelability": "PARTIAL_PREDICTOR_EVIDENCE_REQUIRED",
        "research_only": True,
        "production_influence": "0",
    }
