"""M27C Part 2B3 forecast-only prospective confirmation boundary.

This module is intentionally independent of the GHCN-Daily acquisition and
residual/evaluation modules.  It accepts only immutable forecast evidence for
the predeclared period and has no market, risk, or execution dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from services.market_universe.domain import stable_hash

from .domain import ForecastError

PROTOCOL_VERSION = "m27c-part2b3-prospective-blind-weather-confirmation-v1"
POLICY_COMMIT_SHA = "76fbb8fea645bb2fffb91899eca4937bc17dac1d"
PROSPECTIVE_START = date(2026, 9, 1)
PROSPECTIVE_END = date(2027, 3, 31)
BLACKOUT_START = date(2026, 8, 1)
BLACKOUT_END = date(2026, 8, 31)
SOURCE = "CLIMDW"
STATION = "KMDW"
GHCND_STATION = "USW00014819"
MEASUREMENT = "DAILY_MAX"
FAMILY = "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z"
SUPPORTED_MIDPOINTS = (54_000, 140_400, 226_800)
FROZEN_MODEL_IDENTITIES = {
    54_000: "bb2758d3dbeac46b6fd92f7bde09549178ca2ac585f447d177cebc44fb758981",
    140_400: "bac5baa32d08c95fb2c3c78a587685d6e28e1f2ae6fa02f0b22029dac91e7a99",
    226_800: "15710083dfcf84d86443f5e1a5295c7fc000cdc54558b393cdbaf746adcbe811",
}
FROZEN_MODEL_TRAINING_START = date(2024, 1, 1)
FROZEN_MODEL_TRAINING_END = date(2025, 12, 31)
FAMILY_WISE_ALPHA = "1/20"
COVERAGE_GATE_COUNT = 9
PER_TEST_ALPHA = "1/180"
TWO_SIDED_TAIL_ALPHA = "1/360"
ADJUSTMENT_METHOD = "BONFERRONI"
CLAIM_TYPE = "GHCND_PHYSICAL_TEMPERATURE_PROXY"
SETTLEMENT_MAPPING_STATUS = "UNVALIDATED_GHCND_PROXY"

_MANIFEST_MATERIAL = (
    PROTOCOL_VERSION,
    POLICY_COMMIT_SHA,
    PROSPECTIVE_START.isoformat(),
    PROSPECTIVE_END.isoformat(),
    BLACKOUT_START.isoformat(),
    BLACKOUT_END.isoformat(),
    SOURCE,
    STATION,
    GHCND_STATION,
    MEASUREMENT,
    FAMILY,
    SUPPORTED_MIDPOINTS,
    tuple(sorted(FROZEN_MODEL_IDENTITIES.items())),
    FROZEN_MODEL_TRAINING_START.isoformat(),
    FROZEN_MODEL_TRAINING_END.isoformat(),
    FAMILY_WISE_ALPHA,
    COVERAGE_GATE_COUNT,
    PER_TEST_ALPHA,
    TWO_SIDED_TAIL_ALPHA,
    ADJUSTMENT_METHOD,
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    True,
    "0",
    "PROHIBITED",
    "PROHIBITED",
)
PROTOCOL_IDENTITY = stable_hash(_MANIFEST_MATERIAL)


@dataclass(frozen=True, slots=True)
class ProspectiveProtocolManifest:
    protocol_version: str
    policy_commit_sha: str
    prospective_start: date
    prospective_end: date
    operations_only_blackout: tuple[date, date]
    source: str
    station: str
    ghcnd_station: str
    measurement: str
    family: str
    exact_midpoint_seconds: tuple[int, ...]
    frozen_model_identities: tuple[tuple[int, str], ...]
    training_start: date
    training_end: date
    family_wise_alpha: str
    coverage_gate_count: int
    per_test_alpha: str
    two_sided_tail_alpha: str
    adjustment_method: str
    claim_type: str
    settlement_mapping_status: str
    research_only: bool
    production_influence: str
    outcomes_during_period: str
    evaluation_during_period: str
    protocol_identity: str


PROSPECTIVE_PROTOCOL = ProspectiveProtocolManifest(
    PROTOCOL_VERSION,
    POLICY_COMMIT_SHA,
    PROSPECTIVE_START,
    PROSPECTIVE_END,
    (BLACKOUT_START, BLACKOUT_END),
    SOURCE,
    STATION,
    GHCND_STATION,
    MEASUREMENT,
    FAMILY,
    SUPPORTED_MIDPOINTS,
    tuple(sorted(FROZEN_MODEL_IDENTITIES.items())),
    FROZEN_MODEL_TRAINING_START,
    FROZEN_MODEL_TRAINING_END,
    FAMILY_WISE_ALPHA,
    COVERAGE_GATE_COUNT,
    PER_TEST_ALPHA,
    TWO_SIDED_TAIL_ALPHA,
    ADJUSTMENT_METHOD,
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    True,
    "0",
    "PROHIBITED",
    "PROHIBITED",
    PROTOCOL_IDENTITY,
)

_EVIDENCE_FIELDS = frozenset(
    {
        "evidence_identity",
        "source",
        "station",
        "ghcnd_station",
        "measurement",
        "family",
        "target_date",
        "exact_midpoint_seconds",
        "model_identity",
        "forecast_reference_time",
        "interval_start",
        "interval_end",
        "midpoint",
        "collection_timestamp",
        "raw_grib_sha256",
        "extraction_sha256",
        "extraction_policy_version",
        "wgrib2_version",
        "central_kelvin",
        "central_deg_f",
        "quality_status",
        "missing_source",
        "research_only",
        "production_influence",
    }
)
_FORBIDDEN_EVIDENCE_FIELDS = frozenset(
    {
        "observed_deg_f",
        "outcome",
        "outcomes",
        "residual",
        "residual_deg_f",
        "residuals",
        "crps",
        "mae",
        "coverage",
        "evaluation",
        "evaluation_metrics",
        "market",
        "market_data",
        "price",
        "fair_value",
        "edge",
        "ev",
    }
)


def validate_prospective_forecast_evidence(payload: Mapping[str, Any]) -> None:
    """Validate one forecast-only observation against the frozen manifest."""
    keys = frozenset(payload)
    if keys & _FORBIDDEN_EVIDENCE_FIELDS:
        raise ForecastError(
            "prospective evidence contains prohibited outcome/evaluation/market data"
        )
    if keys != _EVIDENCE_FIELDS:
        raise ForecastError("prospective evidence schema is not forecast-only and exact")
    target = _parse_date(payload["target_date"], "target date")
    if not PROSPECTIVE_START <= target <= PROSPECTIVE_END:
        raise ForecastError("target date is outside the prospective confirmation period")
    if payload["source"] != SOURCE or payload["station"] != STATION:
        raise ForecastError("prospective source or station conflicts with frozen protocol")
    if payload["ghcnd_station"] != GHCND_STATION or payload["measurement"] != MEASUREMENT:
        raise ForecastError("prospective GHCN-Daily station or measurement conflicts")
    if payload["family"] != FAMILY:
        raise ForecastError("prospective forecast family conflicts with frozen protocol")
    midpoint = payload["exact_midpoint_seconds"]
    if midpoint not in SUPPORTED_MIDPOINTS:
        raise ForecastError("prospective midpoint is unsupported")
    if payload["model_identity"] != FROZEN_MODEL_IDENTITIES[midpoint]:
        raise ForecastError("prospective model identity conflicts with frozen protocol")
    if payload["research_only"] is not True or payload["production_influence"] != Decimal("0"):
        raise ForecastError("prospective evidence must be research-only with zero influence")
    if payload["missing_source"] is not False:
        raise ForecastError("prospective accepted evidence cannot claim a missing source")
    for field in (
        "raw_grib_sha256",
        "extraction_sha256",
        "extraction_policy_version",
        "wgrib2_version",
    ):
        if not isinstance(payload[field], str) or not payload[field]:
            raise ForecastError(f"prospective {field} is required")


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ForecastError(f"invalid {field}") from exc
    raise ForecastError(f"invalid {field}")
