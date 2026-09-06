"""CPI-E1-P10D Phase 0 -- predictive-edge scoring specification freeze.

This module freezes the *procedure* for the first Reuters-vs-Kalshi
predictive comparison. It is deliberately a reader of already-reviewed P10A/
P10B/P10C artifacts. It has no network, account, execution, fee, or
production influence capability, and -- this is the load-bearing constraint
of this phase -- it does not compute, return, or persist any Reuters-vs-
Kalshi comparative score, edge, or P&L figure. `build_phase0_spec` binds
identity and eligibility only; `classify_directional_call` is a frozen pure
formula exposed for future phases and is exercised in tests only against
synthetic fixtures, never against this module's own frozen cohort data.

Existing scoring authority recovered and reused (see docs/reviews/
CPI_E1_P10A_HISTORICAL_EVIDENCE_BINDING.md and
services/forecasting/cpi_p10a_binding.py):

- Kalshi price convention: primary = YES ask ("crossing price"), explicitly
  diagnostic-labeled "not a neutral probability estimate"; two-sided
  midpoint is a non-executable diagnostic only, available when both a valid
  non-boundary bid and ask exist. This freeze reuses that convention
  unmodified.
- Independence/aggregation treatment: sibling rows are clustered by event;
  a per-event mean is taken first, then events are weighted equally
  ("event-equal aggregation"). This freeze reuses that convention
  unmodified.
- Truth authority: outcome = int(initial_release_value > threshold), sourced
  from the frozen P7/P8 initial-release truth already accepted by P10A. This
  freeze does not reacquire or recompute truth, and (see module docstring)
  does not read outcome/truth data at all -- eligibility here is structural
  only (temporal + non-boundary quote), never outcome-comparative.

No existing reviewed authority defines a Reuters-point-forecast-vs-market
comparison metric (grep of the repository at the frozen SHA below found no
P10D/P10E precedent and no brier/proper-scoring/calibration artifact scoped
to a Reuters predictor). This is therefore a new policy decision, frozen
below as Question A/B/C/D/E resolutions, not silently invented mid-scoring.

Narrow adaptation required and applied: P10B's Reuters receipts record a
single event-level `decision_cutoff` (or, for CPI-23AUG, a per-sibling
`sibling_temporal_eligibility` list). This cohort's manifest has no
event-level cutoff at all (`cutoff_semantics: per_sibling_market`). For the
3 P10B-reused events, this module verifies -- rather than assumes -- that
every accepted sibling of that event shares one uniform `sibling_cutoff`
equal to the receipt's own event-level timestamp-comparison field, so that
per-sibling eligibility can be established without synthesizing a new
event-level authority and without silently trusting the receipt's own
boolean claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.forecasting.cpi_p10a_binding import P9A_ROOT, _time
from services.forecasting.cpi_p10c_manifest import (
    FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST,
    FROZEN_MANIFEST_DIGEST_SHA256,
    build_phase1_manifest,
)

CANONICAL_MAIN_SHA = "9169160c4583c4a6e353c5eb6c01eb59b816d31c"
CANONICAL_MAIN_TREE = "114569cd2ab1c86f892a529d1c1b6eb24457bff7"
P10C_PHASE2_MERGE_PR = 132
P10C_PHASE2_BRANCH_HEAD_SHA = "69d250e47e5f16436a4156e55a173ab54b18c023"
P10C_COVERAGE_PATH = Path("docs/reviews/artifacts/cpi-p10c-reuters-phase2/coverage.json")
P10C_COVERAGE_FROZEN_MANIFEST_SHA256 = FROZEN_MANIFEST_DIGEST_SHA256


class CPIP10DScoringSpecError(ValueError):
    """Raised when a bound identity, temporal, or schema invariant fails closed."""


@dataclass(frozen=True, slots=True)
class ReutersEventBinding:
    event_ticker: str
    reference_month: str
    value: Decimal
    receipt_path: Path
    receipt_sha256: str
    timestamp_field: str
    timestamp_value: datetime


# Exact 4-event roster, field-name binding, and frozen receipt identity.
# The `timestamp_field` varies per receipt schema (P10B vs Phase-2-R1 style)
# -- this is bound explicitly per event rather than assumed uniform, because
# a naive single field name (e.g. always `published_at`) silently resolves
# to `None` for 2 of these 4 receipts.
REUTERS_EVENT_BINDINGS: tuple[ReutersEventBinding, ...] = (
    ReutersEventBinding(
        event_ticker="CPI-23AUG",
        reference_month="2023-08",
        value=Decimal("0.6"),
        receipt_path=Path("docs/reviews/artifacts/cpi-p10c-reuters-phase2/CPI-23AUG/receipt.json"),
        receipt_sha256="d4e0cb79f7c14536bf8051effd6d62acd93aa11fcc285c54afcd05f5e1910a56",
        timestamp_field="published_at",
        timestamp_value=datetime(2023, 9, 13, 10, 7, 35, tzinfo=UTC),
    ),
    ReutersEventBinding(
        event_ticker="KXCPI-25JUL",
        reference_month="2025-07",
        value=Decimal("0.2"),
        receipt_path=Path("docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-25JUL/receipt.json"),
        receipt_sha256="3a0c265dac5ffa61aadd76838a62c9948d4beb64ff4db131f3d407cd48bb69fc",
        timestamp_field="published_at",
        timestamp_value=datetime(2025, 8, 12, 4, 2, 11, tzinfo=UTC),
    ),
    ReutersEventBinding(
        event_ticker="KXCPI-25DEC",
        reference_month="2025-12",
        value=Decimal("0.3"),
        receipt_path=Path("docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-25DEC/receipt.json"),
        receipt_sha256="0d49e5e46dc0c2c4269147ebd76de8cde7025517b325dbaa8f3deb4541e022fa",
        timestamp_field="governing_published_at",
        timestamp_value=datetime(2026, 1, 13, 5, 3, 53, tzinfo=UTC),
    ),
    ReutersEventBinding(
        event_ticker="KXCPI-26JAN",
        reference_month="2026-01",
        value=Decimal("0.3"),
        receipt_path=Path("docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-26JAN/receipt.json"),
        receipt_sha256="95648c04529fa46095649d6f95c1191fc8884db23cb7616f9caff8df5a0a61b9",
        # This receipt's earliest bound ("observed_publication_range") is
        # 2026-02-13T05:00:01Z; the receipt conservatively binds on the
        # later, weaker-margin instant. This module reuses that conservative
        # choice rather than the wider margin.
        timestamp_field="conservative_admissibility_time",
        timestamp_value=datetime(2026, 2, 13, 5, 12, 31, tzinfo=UTC),
    ),
)
FROZEN_FOUR_EVENT_TICKERS = frozenset(binding.event_ticker for binding in REUTERS_EVENT_BINDINGS)

PRIMARY_MARKET_PRICE_CONVENTION = "yes_ask"  # reused verbatim from P10A
DIAGNOSTIC_MARKET_PRICE_CONVENTION = "two_sided_midpoint"  # reused verbatim from P10A
UNIT_OF_INDEPENDENCE = "event"
AGGREGATION_RULE = "event_equal_mean_of_per_event_sibling_mean"  # reused verbatim from P10A

PRIMARY_METRIC = "sibling_level_threshold_directional_correctness_event_equal_aggregated"
PRIMARY_METRIC_DEFINITION = (
    "Per eligible sibling s of event e with strict-GT threshold t_s: "
    "reuters_direction(s) = 1 if reuters_value(e) > t_s else 0 (identical strict "
    "comparator to the contract's own settlement predicate -- no probability is "
    "synthesized from the point forecast). kalshi_direction(s) = 1 if yes_ask(s) > "
    "Decimal('0.5'), 0 if yes_ask(s) < Decimal('0.5'); yes_ask(s) == Decimal('0.5') "
    "exactly is a tie and is EXCLUDED from this metric's denominator for that "
    "sibling (see tie_rule). correctness(source, s) = int(direction(source, s) == "
    "outcome(s)). Per-event score = mean(correctness) over that event's eligible, "
    "non-tied siblings. Primary statistic = mean of per-event scores across the 4 "
    "events, computed separately for reuters_direction and kalshi_direction "
    "(event-equal weighting; NOT a single pooled mean over all eligible siblings)."
)
TIE_RULE = (
    "A Kalshi yes_ask of exactly Decimal('0.5000') is a directional tie and is "
    "excluded from the primary metric's denominator for that sibling only "
    "(retained in any Brier/log-loss diagnostic). Reuters never ties by this "
    "definition: reuters_value(e) == t_s resolves to direction 0, identically to "
    "how the contract's own settlement predicate (value > threshold, strict) "
    "resolves an exact-equality outcome -- no separate Reuters tie rule is "
    "introduced."
)
MISSING_DATA_RULE = (
    "A sibling with no valid non-boundary yes_ask (yes_bid in (None, '0.0000') or "
    "yes_ask in (None, '1.0000'), reusing P10A's own boundary/None exclusion "
    "verbatim) is excluded from the primary metric entirely for BOTH sources on "
    "that sibling -- never imputed, never scored one-sided. A sibling whose "
    "Reuters observation is not positively proven available before that sibling's "
    "own frozen sibling_cutoff is excluded entirely. No interpolation, smoothing, "
    "or synthesized event-level cutoff is permitted at any step."
)

SECONDARY_DIAGNOSTICS: tuple[str, ...] = (
    "kalshi_ask_crossing_brier_and_log_loss_restricted_to_the_4_event_cohort "
    "(P10A's own existing Brier/log-loss formula, unmodified, scoped to this "
    "roster only -- not computed for Reuters, which has no probability)",
    "kalshi_midpoint_two_sided_diagnostic_where_both_quote_sides_are_valid",
    "raw_sibling_level_hit_rate_unweighted (explicitly non-independent; diagnostic "
    "only, never the primary statistic)",
    "per_event_breakdown_table (4 rows; never collapsed into the primary statistic "
    "without the event-equal weighting step)",
)

SMALL_SAMPLE_INTERPRETATION_RULE = (
    "N=4 independent Reuters-proven events cannot by itself establish durable "
    "predictive edge. This checkpoint may conclude only one of: "
    "'promising diagnostic', 'no apparent advantage', or 'inconclusive'. It may "
    "NOT conclude, imply, or be used as grounds for promotion to live trading, "
    "capital allocation, or any change to execution/production authority, "
    "regardless of the primary statistic's value."
)

ANTI_OVERFITTING_FAIL_CLOSED_RULES: tuple[str, ...] = (
    "the 4-event set is exactly FROZEN_FOUR_EVENT_TICKERS; a 5th event (including "
    "any future re-classified UNKNOWN->PASS) requires a new, explicitly reviewed "
    "spec revision, never silent inclusion",
    "each event's Reuters timestamp field name and value are bound exactly per "
    "ReutersEventBinding; a receipt field-name or value drift fails closed",
    "temporal eligibility is recomputed from the frozen per-sibling manifest "
    "cutoff on every build, never read from a receipt's own boolean claim",
    "the market price convention (yes_ask primary, midpoint diagnostic) is a "
    "frozen constant; changing it requires a new spec revision",
    "sibling rows are never treated as independent events; the primary statistic "
    "is always event-equal aggregated, never a flat mean over all eligible "
    "siblings",
    "the primary metric definition is fixed before any comparative score exists; "
    "this module contains no function that reads Reuters value, yes_ask, AND "
    "outcome/truth together -- that composition is deliberately deferred to a "
    "future, separately reviewed phase",
)


def _load_and_verify_receipt(binding: ReutersEventBinding, root: Path) -> dict[str, Any]:
    raw = (root / binding.receipt_path).read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != binding.receipt_sha256:
        raise CPIP10DScoringSpecError(
            f"{binding.event_ticker} receipt SHA-256 mismatch: got {actual_sha256}"
        )
    receipt: dict[str, Any] = json.loads(raw)
    if receipt.get("event_ticker") != binding.event_ticker:
        raise CPIP10DScoringSpecError(f"{binding.event_ticker} receipt event_ticker mismatch")
    if receipt.get("reference_month") != binding.reference_month:
        raise CPIP10DScoringSpecError(f"{binding.event_ticker} receipt reference_month mismatch")
    if Decimal(str(receipt.get("value"))) != binding.value:
        raise CPIP10DScoringSpecError(f"{binding.event_ticker} receipt value mismatch")
    if receipt.get("vintage_status") != "PASS":
        raise CPIP10DScoringSpecError(f"{binding.event_ticker} receipt is not a PASS")
    field_value = receipt.get(binding.timestamp_field)
    if not isinstance(field_value, str):
        raise CPIP10DScoringSpecError(
            f"{binding.event_ticker} receipt field {binding.timestamp_field!r} is absent"
        )
    parsed = datetime.fromisoformat(field_value.replace("Z", "+00:00")).astimezone(UTC)
    if parsed != binding.timestamp_value:
        raise CPIP10DScoringSpecError(
            f"{binding.event_ticker} bound timestamp does not match receipt field "
            f"{binding.timestamp_field!r}"
        )
    return receipt


def _verify_coverage_authority(root: Path) -> dict[str, Any]:
    coverage: dict[str, Any] = json.loads((root / P10C_COVERAGE_PATH).read_bytes())
    if coverage.get("frozen_manifest_sha256") != P10C_COVERAGE_FROZEN_MANIFEST_SHA256:
        raise CPIP10DScoringSpecError("P10C coverage frozen_manifest_sha256 mismatch")
    if coverage.get("cutoff_semantics") != "per_sibling_market":
        raise CPIP10DScoringSpecError("P10C coverage cutoff_semantics is not per_sibling_market")
    pass_tickers = {
        event["event_ticker"]
        for event in coverage["events"]
        if event.get("terminal_state") == "PASS"
    }
    if pass_tickers != FROZEN_FOUR_EVENT_TICKERS:
        raise CPIP10DScoringSpecError(
            f"P10C coverage PASS roster does not match the frozen 4-event set: {pass_tickers}"
        )
    return coverage


def _eligible_sibling_counts(
    binding: ReutersEventBinding, manifest_events: dict[str, dict[str, Any]], root: Path
) -> dict[str, Any]:
    event_manifest = manifest_events[binding.event_ticker]
    siblings = event_manifest["accepted_siblings"]
    cutoffs = {sibling["sibling_cutoff"] for sibling in siblings}
    if len(cutoffs) != 1:
        raise CPIP10DScoringSpecError(
            f"{binding.event_ticker} does not have a uniform per-sibling cutoff: {cutoffs}"
        )
    (cutoff_str,) = cutoffs
    cutoff = _time(cutoff_str)
    if not binding.timestamp_value < cutoff:
        raise CPIP10DScoringSpecError(
            f"{binding.event_ticker} Reuters observation is not proven available before "
            f"every accepted sibling's cutoff ({binding.timestamp_value} >= {cutoff})"
        )

    p9a_manifest = json.loads((root / P9A_ROOT / "manifest.json").read_bytes())
    p9a_by_ticker = {row["market_ticker"]: row for row in p9a_manifest["markets"]}
    eligible = 0
    boundary_excluded = 0
    for sibling in siblings:
        p9a = p9a_by_ticker[sibling["market_ticker"]]
        bid, ask = p9a.get("yes_bid"), p9a.get("yes_ask")
        if bid in (None, "0.0000") or ask in (None, "1.0000"):
            boundary_excluded += 1
        else:
            eligible += 1
    return {
        "event_ticker": binding.event_ticker,
        "accepted_sibling_count": len(siblings),
        "temporally_eligible_sibling_count": len(siblings),
        "boundary_excluded_sibling_count": boundary_excluded,
        "primary_metric_eligible_sibling_count": eligible,
        "uniform_sibling_cutoff": cutoff_str,
    }


def build_phase0_spec(repository_root: str | Path) -> dict[str, Any]:
    """Freeze the Phase 0 scoring spec. Computes no Reuters-vs-Kalshi score."""
    root = Path(repository_root)

    phase1_manifest = build_phase1_manifest(root)
    if (
        phase1_manifest["accepted_event_count"] != 42
        or phase1_manifest["accepted_sibling_row_count"] != 341
        or phase1_manifest["frozen_accepted_threshold_identity_digest"]
        != FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST
        or phase1_manifest["manifest_digest_sha256"] != FROZEN_MANIFEST_DIGEST_SHA256
    ):
        raise CPIP10DScoringSpecError("upstream P10C Phase 1 manifest identity drifted")
    manifest_events = {event["event_ticker"]: event for event in phase1_manifest["events"]}

    coverage = _verify_coverage_authority(root)

    event_bindings = []
    for binding in REUTERS_EVENT_BINDINGS:
        _load_and_verify_receipt(binding, root)
        counts = _eligible_sibling_counts(binding, manifest_events, root)
        event_bindings.append(
            {
                "event_ticker": binding.event_ticker,
                "reference_month": binding.reference_month,
                "reuters_value": str(binding.value),
                "reuters_timestamp_field": binding.timestamp_field,
                "reuters_timestamp_utc": binding.timestamp_value.isoformat(),
                "receipt_path": str(binding.receipt_path),
                "receipt_sha256": binding.receipt_sha256,
                **counts,
            }
        )

    spec: dict[str, Any] = {
        "schema": "cpi-e1-p10d-phase0-scoring-spec/v1",
        "phase": "CPI-E1-P10D Phase 0 - predictive-edge scoring specification freeze",
        "canonical_main_sha": CANONICAL_MAIN_SHA,
        "canonical_main_tree": CANONICAL_MAIN_TREE,
        "p10c_phase2_merge_pr": P10C_PHASE2_MERGE_PR,
        "p10c_phase2_branch_head_sha": P10C_PHASE2_BRANCH_HEAD_SHA,
        "p10c_coverage_frozen_manifest_sha256": coverage["frozen_manifest_sha256"],
        "p10c_accepted_threshold_identity_digest": FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST,
        "existing_scoring_authority_reused": {
            "source": "services/forecasting/cpi_p10a_binding.py (docs/reviews/"
            "CPI_E1_P10A_HISTORICAL_EVIDENCE_BINDING.md)",
            "market_price_convention_reused_verbatim": True,
            "aggregation_convention_reused_verbatim": True,
            "narrow_adaptation": (
                "per_sibling_market cutoff semantics verified (not assumed) for the 3 "
                "P10B-reused events, which carry only a single event-level receipt "
                "timestamp field; no new event-level cutoff authority was created"
            ),
            "new_policy_required_for": (
                "Reuters point-forecast-vs-market comparison metric -- no reviewed "
                "P10D/P10E precedent exists in this repository at the frozen SHA above"
            ),
        },
        "reuters_proven_events": event_bindings,
        "reuters_proven_event_count": len(event_bindings),
        "market_price_convention": {
            "primary": PRIMARY_MARKET_PRICE_CONVENTION,
            "diagnostic": DIAGNOSTIC_MARKET_PRICE_CONVENTION,
        },
        "unit_of_independence": UNIT_OF_INDEPENDENCE,
        "aggregation_rule": AGGREGATION_RULE,
        "primary_metric": PRIMARY_METRIC,
        "primary_metric_definition": PRIMARY_METRIC_DEFINITION,
        "tie_rule": TIE_RULE,
        "missing_data_rule": MISSING_DATA_RULE,
        "secondary_diagnostics": list(SECONDARY_DIAGNOSTICS),
        "small_sample_interpretation_rule": SMALL_SAMPLE_INTERPRETATION_RULE,
        "anti_overfitting_fail_closed_rules": list(ANTI_OVERFITTING_FAIL_CLOSED_RULES),
        "reuters_vs_kalshi_score_computed": False,
        "edge_pnl_fees_computed": False,
        "research_only": True,
        "production_influence": "0",
    }
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    spec["spec_digest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return spec


def canonical_bytes(spec: dict[str, Any]) -> bytes:
    without_digest = {k: v for k, v in spec.items() if k != "spec_digest_sha256"}
    return json.dumps(without_digest, sort_keys=True, separators=(",", ":")).encode()


Direction = Literal[0, 1, None]


def classify_directional_call(
    *, reuters_value: Decimal, kalshi_yes_ask: Decimal, threshold: Decimal
) -> dict[str, Direction]:
    """Frozen pure formula for a future scoring phase.

    Deliberately takes no outcome/truth argument: this function alone cannot
    compute a score, only the two sources' directional calls. Exercise only
    against synthetic fixtures -- never against this module's own frozen
    cohort data, which would constitute computing the prohibited comparative
    result in this phase.
    """
    reuters_direction: Direction = 1 if reuters_value > threshold else 0
    if kalshi_yes_ask == Decimal("0.5"):
        kalshi_direction: Direction = None
    else:
        kalshi_direction = 1 if kalshi_yes_ask > Decimal("0.5") else 0
    return {"reuters_direction": reuters_direction, "kalshi_direction": kalshi_direction}
