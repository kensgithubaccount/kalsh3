"""CPI-E1-P10D Phase 1 sealed historical diagnostic scorer.

This module consumes only the committed Phase 0 specification and the frozen
P10A/P10C artifacts.  It has no acquisition, live-data, execution, fee, or
production-influence capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.forecasting.cpi_p10a_binding import P9A_ROOT, build_binding
from services.forecasting.cpi_p10c_manifest import (
    FROZEN_MANIFEST_DIGEST_SHA256,
    build_phase1_manifest,
)
from services.forecasting.cpi_p10d_scoring_spec import (
    FROZEN_FOUR_EVENT_TICKERS,
    build_phase0_spec,
)

PHASE0_SPEC_PATH = Path("docs/reviews/artifacts/cpi-p10d-phase0-scoring-spec/spec.json")
PHASE0_SPEC_DIGEST = "519d41bad86f3d1f4d19afa9270e62bcc1f836de7a451d9784ba4d09bb1adb77"
CANONICAL_MAIN_SHA = "5a1b268a93eaa7d8b0d0b8e0c7b35993ff0e8aef"
CANONICAL_MAIN_TREE = "6a7ed7f1ac2f88e7be7f33e99c21d8b5569058cc"
SCHEMA = "cpi-e1-p10d-phase1-sealed-score/v1"


class SealedScoringError(ValueError):
    """Raised when a frozen identity or scoring invariant fails closed."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise SealedScoringError("cannot average an empty set")
    return sum(values, Decimal(0)) / Decimal(len(values))


def _log_loss(probability: Decimal, outcome: int) -> Decimal:
    clipped = min(max(float(probability), 0.01), 0.99)
    return Decimal(str(-(math.log(clipped) if outcome else math.log1p(-clipped))))


def score_rows(rows: list[dict[str, Any]], event_order: tuple[str, ...]) -> dict[str, Any]:
    """Score normalized rows; deliberately usable with synthetic fixtures."""
    if set(event_order) != set(FROZEN_FOUR_EVENT_TICKERS):
        raise SealedScoringError("event roster is not the frozen four-event roster")
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["event_ticker"] not in event_order:
            raise SealedScoringError("row contains an event outside the frozen roster")
        by_event[row["event_ticker"]].append(row)

    event_scores: list[dict[str, Any]] = []
    primary_rows: list[dict[str, Any]] = []
    applicable_rows: list[dict[str, Any]] = []
    for event in event_order:
        members = by_event[event]
        common: list[dict[str, Any]] = []
        for row in sorted(members, key=lambda item: item["market_ticker"]):
            ask = Decimal(str(row["kalshi_yes_ask"]))
            valid = row.get("quote_valid", True)
            if not valid:
                reason = "frozen_boundary_quote_exclusion"
            else:
                applicable_rows.append(row)
                reason = "included"
                if ask == Decimal("0.5"):
                    reason = "exact_0.5_kalshi_tie_excluded_from_primary"
                else:
                    common.append(row)
                    primary_rows.append(row)
            row["inclusion_exclusion_reason"] = reason
            row["reuters_direction"] = int(
                Decimal(str(row["reuters_point_forecast"])) > Decimal(str(row["threshold"]))
            )
            row["kalshi_direction"] = None if ask == Decimal("0.5") else int(ask > Decimal("0.5"))
            row["reuters_correctness"] = int(row["reuters_direction"] == row["canonical_outcome"])
            row["kalshi_correctness"] = (
                None
                if row["kalshi_direction"] is None
                else int(row["kalshi_direction"] == row["canonical_outcome"])
            )
        if not common:
            raise SealedScoringError(f"{event} has zero common primary rows")
        event_scores.append(
            {
                "event_ticker": event,
                "common_primary_rows": len(common),
                "reuters_directional_score": str(
                    _mean([Decimal(row["reuters_correctness"]) for row in common])
                ),
                "kalshi_directional_score": str(
                    _mean([Decimal(row["kalshi_correctness"]) for row in common])
                ),
            }
        )

    def diagnostic(field: str) -> Decimal:
        values = []
        for row in applicable_rows:
            probability = Decimal(
                str(row["kalshi_yes_ask"] if field == "ask" else row["kalshi_midpoint"])
            )
            values.append((probability - Decimal(row["canonical_outcome"])) ** 2)
        return _mean(values)

    def log_diagnostic() -> Decimal:
        return _mean(
            [
                _log_loss(Decimal(str(row["kalshi_yes_ask"])), row["canonical_outcome"])
                for row in applicable_rows
            ]
        )

    raw_reuters = _mean([Decimal(row["reuters_correctness"]) for row in primary_rows])
    raw_kalshi = _mean([Decimal(row["kalshi_correctness"]) for row in primary_rows])
    result_rows = []
    for row in sorted(rows, key=lambda item: (item["event_ticker"], item["market_ticker"])):
        result_rows.append(
            {
                key: row.get(key)
                for key in (
                    "event_ticker",
                    "market_ticker",
                    "threshold",
                    "reuters_point_forecast",
                    "reuters_direction",
                    "kalshi_yes_ask",
                    "kalshi_direction",
                    "canonical_outcome",
                    "reuters_correctness",
                    "kalshi_correctness",
                    "inclusion_exclusion_reason",
                )
            }
        )
    reuters_event_equal = _mean(
        [Decimal(item["reuters_directional_score"]) for item in event_scores]
    )
    kalshi_event_equal = _mean([Decimal(item["kalshi_directional_score"]) for item in event_scores])
    return {
        "per_sibling_result_rows": result_rows,
        "per_event_scores": event_scores,
        "event_equal_reuters_score": str(reuters_event_equal),
        "event_equal_kalshi_score": str(kalshi_event_equal),
        "reuters_minus_kalshi_difference": str(reuters_event_equal - kalshi_event_equal),
        "secondary_diagnostics": {
            "kalshi_yes_ask_crossing_brier": str(diagnostic("ask")),
            "kalshi_yes_ask_clipped_log_loss": str(log_diagnostic()),
            "kalshi_two_sided_midpoint_brier": str(diagnostic("midpoint")),
            "raw_sibling_level_directional_hit_rate_non_independent": {
                "reuters": str(raw_reuters),
                "kalshi": str(raw_kalshi),
                "rows": len(primary_rows),
            },
        },
        "counts": {
            "accepted_siblings": len(rows),
            "temporally_eligible": len(rows),
            "primary_before_tie": len(applicable_rows),
            "common_primary_after_tie": len(primary_rows),
            "exact_0_5_tie_exclusions": sum(
                row["inclusion_exclusion_reason"] == "exact_0.5_kalshi_tie_excluded_from_primary"
                for row in rows
            ),
        },
    }


def _verify_phase0(root: Path) -> dict[str, Any]:
    committed = json.loads((root / PHASE0_SPEC_PATH).read_bytes())
    generated = build_phase0_spec(root)
    if committed != generated or committed.get("spec_digest_sha256") != PHASE0_SPEC_DIGEST:
        raise SealedScoringError("Phase 0 spec regeneration is not byte/object identical")
    return committed


def build_real_result(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    spec = _verify_phase0(root_path)
    build_binding(root_path)
    manifest = build_phase1_manifest(root_path)
    if manifest["manifest_digest_sha256"] != FROZEN_MANIFEST_DIGEST_SHA256:
        raise SealedScoringError("P10C manifest identity mismatch")
    p9a = json.loads((root_path / P9A_ROOT / "manifest.json").read_bytes())
    rows: list[dict[str, Any]] = []
    for event in spec["reuters_proven_events"]:
        event_manifest = next(
            item for item in manifest["events"] if item["event_ticker"] == event["event_ticker"]
        )
        for sibling in event_manifest["accepted_siblings"]:
            market = next(
                item for item in p9a["markets"] if item["market_ticker"] == sibling["market_ticker"]
            )
            quote_valid = market.get("yes_bid") not in (None, "0.0000") and market.get(
                "yes_ask"
            ) not in (None, "1.0000")
            outcome = int(market["result"] == "yes")
            midpoint = None
            if quote_valid:
                bid, ask = Decimal(str(market["yes_bid"])), Decimal(str(market["yes_ask"]))
                midpoint = str((bid + ask) / 2)
            rows.append(
                {
                    "event_ticker": event["event_ticker"],
                    "market_ticker": sibling["market_ticker"],
                    "threshold": sibling["threshold"],
                    "reuters_point_forecast": event["reuters_value"],
                    "kalshi_yes_ask": market["yes_ask"],
                    "kalshi_midpoint": midpoint,
                    "canonical_outcome": outcome,
                    "quote_valid": quote_valid,
                }
            )
    scored = score_rows(rows, tuple(item["event_ticker"] for item in spec["reuters_proven_events"]))
    result = {
        "schema": SCHEMA,
        "canonical_main_sha": CANONICAL_MAIN_SHA,
        "canonical_main_tree": CANONICAL_MAIN_TREE,
        "phase0_spec_path": str(PHASE0_SPEC_PATH),
        "phase0_spec_digest": PHASE0_SPEC_DIGEST,
        "exact_four_event_roster": [item["event_ticker"] for item in spec["reuters_proven_events"]],
        "research_only": True,
        "production_influence": "0",
        **scored,
    }
    result["result_digest_sha256"] = hashlib.sha256(_canonical_json(result)).hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_real_result(args.root)
    args.output.write_bytes(json.dumps(result, sort_keys=True, indent=2).encode() + b"\n")


if __name__ == "__main__":
    main()
