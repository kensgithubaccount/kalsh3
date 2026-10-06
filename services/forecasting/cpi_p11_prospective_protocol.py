"""Frozen CPI-E1-P11 prospective replication protocol.

This module freezes the first untouched prospective replication of the reviewed
P10D Reuters-vs-Kalshi directional diagnostic. It performs no network access,
evidence acquisition, outcome read, fee/PnL calculation, or trading action.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

SCHEMA_VERSION: Final = "cpi-e1-p11-phase0-prospective-protocol-v1"
CANONICAL_BASE_SHA: Final = "1dca2f488e950dae86f8a57af556e000b7e3cca5"
CANONICAL_BASE_TREE: Final = "2165899e6770c9b363c2a5c38465d5578e93c911"
TARGET_SERIES: Final = "KXCPI"
TARGET_EVENT: Final = "KXCPI-26SEP"
TARGET_REFERENCE_MONTH: Final = "2026-09"
EXPECTED_SIBLING_COUNT: Final = 14
BLS_RELEASE_AT_UTC: Final = "2026-10-14T12:30:00Z"
KALSHI_CLOSE_AT_UTC: Final = "2026-10-14T12:25:00Z"
MIN_PRIMARY_ELIGIBLE_SIBLINGS: Final = 4

APPROVED_REUTERS_HOSTS: Final = (
    "reuters.com",
    "yahoo.com",
    "ca.finance.yahoo.com",
    "finance.yahoo.com",
    "kfgo.com",
    "tradingview.com",
    "nasdaq.com",
    "investing.com",
    "wmbd.com",
    "aol.com",
)


class ProspectiveDecision(StrEnum):
    SUPPORT = "PROSPECTIVE_SUPPORT_DIAGNOSTIC"
    CONTRADICTION = "PROSPECTIVE_CONTRADICTION"
    TIE = "PROSPECTIVE_TIE_NO_ADVANTAGE"
    INSUFFICIENT = "INCONCLUSIVE_INSUFFICIENT"


def build_phase0_protocol() -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "canonical_base_sha": CANONICAL_BASE_SHA,
        "canonical_base_tree": CANONICAL_BASE_TREE,
        "target": {
            "series_ticker": TARGET_SERIES,
            "event_ticker": TARGET_EVENT,
            "reference_month": TARGET_REFERENCE_MONTH,
            "expected_sibling_count": EXPECTED_SIBLING_COUNT,
            "bls_release_at_utc": BLS_RELEASE_AT_UTC,
            "kalshi_market_close_at_utc": KALSHI_CLOSE_AT_UTC,
            "release_source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
            "market_source_url": "https://kalshi.com/markets/kxcpi/kxcpi-26sep",
        },
        "preflight": {
            "first_party_only": True,
            "must_revalidate_before_collection": True,
            "requirements": [
                "BLS still identifies September 2026 CPI for 2026-10-14 08:30 ET",
                "Kalshi event identity is exactly KXCPI-26SEP in series KXCPI",
                "the complete event contains exactly 14 sibling markets",
                ("every admitted sibling is active/open, simple binary, non-provisional, non-MVE"),
                (
                    "every admitted sibling resolves strict-GT headline CPI "
                    "month-over-month for 2026-09"
                ),
                "every admitted sibling closes at 2026-10-14T12:25:00Z",
                "any mismatch fails closed before Reuters evidence or scoring is used",
            ],
        },
        "market_observation": {
            "source": "Kalshi public historical candlesticks",
            "interval_minutes": 60,
            "selection_rule": (
                "for each sibling select the unique latest 60-minute candle whose "
                "end_period_ts is strictly before that sibling market close"
            ),
            "acquisition_window_utc": [
                "2026-10-14T12:05:00Z",
                "2026-10-14T12:20:00Z",
            ],
            "bounded_retry_rule": (
                "up to 3 acquisition attempts inside the fixed window; no attempts after 12:20Z"
            ),
            "primary_price": "yes_ask",
            "diagnostic_price": ("two_sided_midpoint_only_when_both_sides_are_valid"),
            "missing_quote_rule": (
                "missing or boundary yes_ask is excluded for both sources on that "
                "sibling; never impute, interpolate, use last trade, or substitute midpoint"
            ),
            "fixed_market_snapshot_rule": True,
        },
        "reuters_evidence": {
            "acquisition_must_complete_by_utc": "2026-10-14T12:20:00Z",
            "publication_must_precede_each_scored_sibling_close": True,
            "approved_host_set": list(APPROVED_REUTERS_HOSTS),
            "search_rungs": [
                "Reuters direct exact-release-date search/fetch",
                (
                    "approved syndicated Reuters hosts using exact release-date "
                    "and day-before queries"
                ),
                "Wayback/CDX retrieval over the same approved host set",
            ],
            "pass_requirements": [
                "Reuters attribution is positively established",
                "article body/dateline explicitly identifies reference month 2026-09",
                ("specific headline CPI month-over-month forecast is stated prospectively"),
                "value is preserved as exact Decimal at published precision",
                (
                    "governing publication time is positively evidenced and precedes "
                    "each scored sibling close"
                ),
                ("at least 2 independently operated hosts carry the same Reuters wire revision"),
            ],
            "terminal_states": [
                "PASS",
                "UNKNOWN_SEARCHED_NO_QUALIFYING_OBSERVATION",
                "FAILURE_ACQUISITION_OR_AUTHORITY",
            ],
            "no_single_host_promotion": True,
        },
        "scoring": {
            "unit_of_independence": "event",
            "sibling_rows_independent": False,
            "minimum_primary_eligible_siblings": MIN_PRIMARY_ELIGIBLE_SIBLINGS,
            "reuters_call": ("YES iff Reuters forecast > sibling threshold; otherwise NO"),
            "kalshi_call": ("YES iff yes_ask > 0.5; NO iff yes_ask < 0.5; exact 0.5 is TIE"),
            "truth_call": (
                "YES iff BLS initial-release headline CPI MoM > sibling threshold; otherwise NO"
            ),
            "aggregation_rule": (
                "mean directional correctness across eligible siblings within the single event"
            ),
            "tie_rule": (
                "Kalshi exact-0.5 sibling is excluded from the common primary denominator"
            ),
            "common_denominator_rule": (
                "both Reuters and Kalshi are scored only on the same eligible siblings"
            ),
            "truth_authority": "BLS initial release only; no later revision",
        },
        "decision_gate": {
            ProspectiveDecision.SUPPORT.value: (
                "Reuters primary correct count is strictly greater than Kalshi on "
                "at least 4 common eligible siblings"
            ),
            ProspectiveDecision.CONTRADICTION.value: (
                "Reuters primary correct count is strictly less than Kalshi on "
                "at least 4 common eligible siblings"
            ),
            ProspectiveDecision.TIE.value: (
                "Reuters and Kalshi primary correct counts are equal on at least "
                "4 common eligible siblings"
            ),
            ProspectiveDecision.INSUFFICIENT.value: (
                "Reuters evidence is not PASS, first-party preflight fails, market "
                "evidence is incomplete, truth authority is unavailable, or fewer "
                "than 4 common eligible siblings remain"
            ),
            "promotion_authority": "NONE",
            "phase1_economic_test_authorized": False,
            "support_next_action": (
                "freeze a multi-event prospective extension before the next CPI event"
            ),
        },
        "anti_overfitting": [
            "event KXCPI-26SEP is the only target; no substitution after protocol freeze",
            "include the complete qualifying sibling set; no hand-selected thresholds",
            (
                "market observation time/selection rule cannot change after seeing "
                "Reuters or BLS truth"
            ),
            "Reuters source/admission/corroboration rules cannot change per candidate",
            "no Reuters probability transform is permitted",
            (
                "no fees, PnL, sizing, slippage, fills, queue position, or execution "
                "simulation in this gate"
            ),
            "one supportive event cannot establish durable edge or production readiness",
        ],
        "safety": {
            "research_only": True,
            "production_influence": "0",
            "orders": False,
            "capital_allocation": False,
            "live_trading_promotion": False,
        },
    }
    spec["spec_digest_sha256"] = hashlib.sha256(canonical_bytes(spec)).hexdigest()
    return spec


def canonical_bytes(spec: dict[str, Any]) -> bytes:
    without_digest = {key: value for key, value in spec.items() if key != "spec_digest_sha256"}
    return json.dumps(without_digest, sort_keys=True, separators=(",", ":")).encode()


Direction = int | None


def classify_directional_call(
    *,
    reuters_value: Decimal,
    kalshi_yes_ask: Decimal,
    threshold: Decimal,
) -> tuple[Direction, Direction]:
    reuters = 1 if reuters_value > threshold else 0
    if kalshi_yes_ask == Decimal("0.5"):
        kalshi = None
    else:
        kalshi = 1 if kalshi_yes_ask > Decimal("0.5") else 0
    return reuters, kalshi


def classify_event_result(
    *,
    reuters_correct: int,
    kalshi_correct: int,
    eligible_siblings: int,
) -> ProspectiveDecision:
    if eligible_siblings < MIN_PRIMARY_ELIGIBLE_SIBLINGS:
        return ProspectiveDecision.INSUFFICIENT
    if (
        reuters_correct < 0
        or kalshi_correct < 0
        or reuters_correct > eligible_siblings
        or kalshi_correct > eligible_siblings
    ):
        raise ValueError("correct counts must be within the common denominator")
    if reuters_correct > kalshi_correct:
        return ProspectiveDecision.SUPPORT
    if reuters_correct < kalshi_correct:
        return ProspectiveDecision.CONTRADICTION
    return ProspectiveDecision.TIE
