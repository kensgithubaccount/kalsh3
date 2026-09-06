"""CPI-E1-P10D Phase 1R sealed historical scorer.

The only outcome authority accepted here is the reviewed P10A binding plus
the canonical P9A market-inventory settlement row.  This module has no
acquisition, live-data, execution, fee, or production-influence capability.
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
PRIOR_INVALID_SEALED_SHA = "6796f84d6455d44ca6b8851e4046ff969d9561ca"
PRIOR_INVALID_SEALED_TREE = "bac08dfdfce18301050b40d6cd8da1455608baf3"
SCHEMA = "cpi-e1-p10d-phase1r-sealed-score/v1"


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


def _verify_phase0(root: Path) -> dict[str, Any]:
    committed = json.loads((root / PHASE0_SPEC_PATH).read_bytes())
    generated = build_phase0_spec(root)
    if committed != generated or committed.get("spec_digest_sha256") != PHASE0_SPEC_DIGEST:
        raise SealedScoringError("Phase 0 spec regeneration is not byte/object identical")
    return committed


def _accepted_identity(binding: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    accepted = binding.get("accepted_threshold_identity")
    if not isinstance(accepted, list):
        raise SealedScoringError("P10A accepted threshold identity is missing")
    result: dict[str, tuple[str, str, str]] = {}
    for item in accepted:
        if not isinstance(item, dict):
            raise SealedScoringError("P10A accepted threshold identity row is malformed")
        try:
            ticker = str(item["market_ticker"])
            identity = (
                str(item["event_ticker"]),
                str(item["threshold"]),
                str(item["predicate_identity"]),
            )
        except KeyError as exc:
            raise SealedScoringError("P10A accepted threshold identity is incomplete") from exc
        if ticker in result or identity[2] != f"GT:{identity[1]}":
            raise SealedScoringError("P10A accepted threshold identity is ambiguous")
        result[ticker] = identity
    return result


def _inventory_by_ticker(root: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads((root / P9A_ROOT / "market_inventory.json").read_bytes())
    markets = raw.get("markets")
    if not isinstance(markets, list):
        raise SealedScoringError("market inventory markets are missing")
    result: dict[str, dict[str, Any]] = {}
    for market in markets:
        if not isinstance(market, dict) or not isinstance(market.get("ticker"), str):
            raise SealedScoringError("market inventory row is malformed")
        ticker = market["ticker"]
        if ticker in result:
            raise SealedScoringError("market inventory ticker is duplicated")
        result[ticker] = market
    return result


def _canonical_outcome_for_row(
    row: dict[str, Any],
    accepted: dict[str, tuple[str, str, str]],
    inventory: dict[str, dict[str, Any]],
) -> int:
    """Return settlement outcome only after all P10A/inventory identity gates."""
    ticker = row.get("market_ticker")
    if not isinstance(ticker, str) or ticker not in accepted:
        raise SealedScoringError("ticker is not in P10A accepted threshold identity")
    expected_event, expected_threshold, expected_predicate = accepted[ticker]
    if row.get("event_ticker") != expected_event:
        raise SealedScoringError("event identity does not match P10A accepted identity")
    if str(row.get("threshold")) != expected_threshold:
        raise SealedScoringError("threshold identity does not match P10A accepted identity")
    if expected_predicate != f"GT:{expected_threshold}":
        raise SealedScoringError("predicate identity is not canonical strict-GT")
    market = inventory.get(ticker)
    if market is None:
        raise SealedScoringError("canonical market inventory row is missing")
    if market.get("ticker") != ticker or market.get("event_ticker") != expected_event:
        raise SealedScoringError("market inventory identity mismatch")
    if str(market.get("rules_primary", "")).find(f"{expected_threshold}%") < 0:
        raise SealedScoringError("market inventory threshold identity mismatch")
    result = market.get("result")
    if result not in {"yes", "no"}:
        raise SealedScoringError("market inventory settlement result is not yes/no")
    return int(result == "yes")


def preflight_real_structure(root: str | Path) -> dict[str, Any]:
    """Validate frozen structure and authority without calculating a score."""
    root_path = Path(root)
    spec = _verify_phase0(root_path)
    binding = build_binding(root_path)
    accepted = _accepted_identity(binding)
    inventory = _inventory_by_ticker(root_path)
    manifest = build_phase1_manifest(root_path)
    if manifest.get("manifest_digest_sha256") != FROZEN_MANIFEST_DIGEST_SHA256:
        raise SealedScoringError("P10C manifest identity mismatch")
    roster = tuple(item["event_ticker"] for item in spec["reuters_proven_events"])
    if set(roster) != set(FROZEN_FOUR_EVENT_TICKERS) or len(roster) != 4:
        raise SealedScoringError("event roster is not the frozen four-event roster")
    manifest_events = {item["event_ticker"]: item for item in manifest["events"]}
    if not set(roster).issubset(manifest_events):
        raise SealedScoringError("P10C manifest is missing a frozen scoring event")
    scored_tickers: list[str] = []
    primary_by_event: dict[str, int] = {}
    accepted_count = temporal_count = primary_before_tie = 0
    p9a_manifest = json.loads((root_path / P9A_ROOT / "manifest.json").read_bytes())
    p9a_by_ticker = {row["market_ticker"]: row for row in p9a_manifest["markets"]}
    for event in spec["reuters_proven_events"]:
        event_ticker = event["event_ticker"]
        siblings = manifest_events[event_ticker].get("accepted_siblings")
        if not isinstance(siblings, list):
            raise SealedScoringError("accepted sibling list is missing")
        event_primary = 0
        for sibling in siblings:
            ticker = sibling.get("market_ticker")
            scored_tickers.append(ticker)
            accepted_count += 1
            if ticker not in accepted:
                raise SealedScoringError("scored ticker is not in P10A accepted identity")
            if accepted[ticker][:2] != (event_ticker, str(sibling["threshold"])):
                raise SealedScoringError("scored event/threshold identity mismatch")
            p9a = p9a_by_ticker.get(ticker)
            if p9a is None:
                raise SealedScoringError("required P9A market row is missing")
            _canonical_outcome_for_row(
                {
                    "market_ticker": ticker,
                    "event_ticker": event_ticker,
                    "threshold": sibling["threshold"],
                },
                accepted,
                inventory,
            )
            if p9a.get("event_ticker") != event_ticker:
                raise SealedScoringError("P9A event identity mismatch")
            for field in ("yes_bid", "yes_ask"):
                try:
                    Decimal(str(p9a[field]))
                except (KeyError, ValueError, TypeError) as exc:
                    raise SealedScoringError(f"P9A numeric field {field} is malformed") from exc
            temporal_count += 1
            quote_valid = p9a.get("yes_bid") not in (None, "0.0000") and p9a.get("yes_ask") not in (
                None,
                "1.0000",
            )
            if quote_valid:
                primary_before_tie += 1
                event_primary += 1
        primary_by_event[event_ticker] = event_primary
        if event_primary == 0:
            raise SealedScoringError(f"{event_ticker} has zero common primary rows")
    if accepted_count != 27 or temporal_count != 27 or primary_before_tie != 19:
        raise SealedScoringError("frozen structural counts do not match 27/27/19")
    if sorted(primary_by_event.values()) != [4, 4, 5, 6]:
        raise SealedScoringError("per-event primary-before-tie counts are not 5,4,4,6")
    if len(scored_tickers) != len(set(scored_tickers)):
        raise SealedScoringError("scored market ticker is duplicated")
    return {
        "exact_four_event_roster": list(roster),
        "accepted_siblings": accepted_count,
        "temporal_eligibility": temporal_count,
        "primary_before_tie": primary_before_tie,
        "primary_before_tie_by_event": primary_by_event,
    }


def score_rows(rows: list[dict[str, Any]], event_order: tuple[str, ...]) -> dict[str, Any]:
    """Score normalized synthetic or real rows using frozen Phase 0 formulas."""
    if set(event_order) != set(FROZEN_FOUR_EVENT_TICKERS) or len(event_order) != 4:
        raise SealedScoringError("event roster is not the frozen four-event roster")
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("event_ticker") not in event_order:
            raise SealedScoringError("row contains an event outside the frozen roster")
        by_event[row["event_ticker"]].append(row)
    event_scores: list[dict[str, Any]] = []
    primary_rows: list[dict[str, Any]] = []
    diagnostic_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in event_order:
        common: list[dict[str, Any]] = []
        for row in sorted(by_event[event], key=lambda item: item["market_ticker"]):
            ask = Decimal(str(row["kalshi_yes_ask"]))
            quote_valid = bool(row.get("quote_valid", True))
            if not quote_valid:
                reason = "frozen_boundary_quote_exclusion"
            else:
                diagnostic_rows[event].append(row)
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

    def event_diagnostic(field: str) -> Decimal:
        event_means = []
        for event in event_order:
            members = diagnostic_rows[event]
            values = []
            for row in members:
                probability = Decimal(
                    str(row["kalshi_yes_ask"] if field == "ask" else row["kalshi_midpoint"])
                )
                values.append((probability - Decimal(row["canonical_outcome"])) ** 2)
            if not values:
                raise SealedScoringError(f"{event} has no rows for {field} diagnostic")
            event_means.append(_mean(values))
        return _mean(event_means)

    log_event_means = []
    for event in event_order:
        members = diagnostic_rows[event]
        if not members:
            raise SealedScoringError(f"{event} has no rows for log-loss diagnostic")
        log_event_means.append(
            _mean(
                [
                    _log_loss(Decimal(str(row["kalshi_yes_ask"])), row["canonical_outcome"])
                    for row in members
                ]
            )
        )
    event_equal_reuters = _mean(
        [Decimal(item["reuters_directional_score"]) for item in event_scores]
    )
    event_equal_kalshi = _mean([Decimal(item["kalshi_directional_score"]) for item in event_scores])
    result_rows = [
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
        for row in sorted(rows, key=lambda item: (item["event_ticker"], item["market_ticker"]))
    ]
    return {
        "per_sibling_result_rows": result_rows,
        "per_event_scores": event_scores,
        "event_equal_reuters_score": str(event_equal_reuters),
        "event_equal_kalshi_score": str(event_equal_kalshi),
        "reuters_minus_kalshi_difference": str(event_equal_reuters - event_equal_kalshi),
        "secondary_diagnostics": {
            "kalshi_yes_ask_crossing_brier": str(event_diagnostic("ask")),
            "kalshi_yes_ask_clipped_log_loss": str(_mean(log_event_means)),
            "kalshi_two_sided_midpoint_brier": str(event_diagnostic("midpoint")),
            "raw_sibling_level_directional_hit_rate_non_independent": {
                "reuters": str(
                    _mean([Decimal(row["reuters_correctness"]) for row in primary_rows])
                ),
                "kalshi": str(_mean([Decimal(row["kalshi_correctness"]) for row in primary_rows])),
                "rows": len(primary_rows),
            },
        },
        "counts": {
            "accepted_siblings": len(rows),
            "temporally_eligible": len(rows),
            "primary_before_tie": sum(bool(row.get("quote_valid", True)) for row in rows),
            "common_primary_after_tie": len(primary_rows),
            "exact_0_5_tie_exclusions": sum(
                row["inclusion_exclusion_reason"] == "exact_0.5_kalshi_tie_excluded_from_primary"
                for row in rows
            ),
            "brier_diagnostic_rows": sum(len(value) for value in diagnostic_rows.values()),
            "log_loss_diagnostic_rows": sum(len(value) for value in diagnostic_rows.values()),
            "midpoint_brier_diagnostic_rows": sum(len(value) for value in diagnostic_rows.values()),
        },
    }


def build_real_result(root: str | Path, *, preflight: bool = False) -> dict[str, Any]:
    root_path = Path(root)
    structure = preflight_real_structure(root_path)
    if preflight:
        return structure
    spec = _verify_phase0(root_path)
    binding = build_binding(root_path)
    accepted = _accepted_identity(binding)
    inventory = _inventory_by_ticker(root_path)
    manifest = build_phase1_manifest(root_path)
    manifest_events = {item["event_ticker"]: item for item in manifest["events"]}
    p9a = json.loads((root_path / P9A_ROOT / "manifest.json").read_bytes())
    p9a_by_ticker = {row["market_ticker"]: row for row in p9a["markets"]}
    rows: list[dict[str, Any]] = []
    for event in spec["reuters_proven_events"]:
        for sibling in manifest_events[event["event_ticker"]]["accepted_siblings"]:
            ticker = sibling["market_ticker"]
            market = p9a_by_ticker[ticker]
            outcome = _canonical_outcome_for_row(
                {
                    "market_ticker": ticker,
                    "event_ticker": event["event_ticker"],
                    "threshold": sibling["threshold"],
                },
                accepted,
                inventory,
            )
            quote_valid = market.get("yes_bid") not in (None, "0.0000") and market.get(
                "yes_ask"
            ) not in (None, "1.0000")
            midpoint = None
            if quote_valid:
                midpoint = str(
                    (Decimal(str(market["yes_bid"])) + Decimal(str(market["yes_ask"]))) / 2
                )
            rows.append(
                {
                    "event_ticker": event["event_ticker"],
                    "market_ticker": ticker,
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
        "live_main_sha": CANONICAL_MAIN_SHA,
        "live_main_tree": CANONICAL_MAIN_TREE,
        "phase0_spec_path": str(PHASE0_SPEC_PATH),
        "phase0_spec_digest": PHASE0_SPEC_DIGEST,
        "prior_invalid_sealed_sha": PRIOR_INVALID_SEALED_SHA,
        "prior_invalid_sealed_tree": PRIOR_INVALID_SEALED_TREE,
        "exact_four_event_roster": structure["exact_four_event_roster"],
        "research_only": True,
        "production_influence": "0",
        **structure,
        **scored,
    }
    result["result_digest_sha256"] = hashlib.sha256(_canonical_json(result)).hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    result = build_real_result(args.root, preflight=args.preflight)
    args.output.write_bytes(json.dumps(result, sort_keys=True, indent=2).encode() + b"\n")


if __name__ == "__main__":
    main()
