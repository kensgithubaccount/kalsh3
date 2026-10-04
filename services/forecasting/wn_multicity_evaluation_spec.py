"""Outcome-blind freeze for WN multicity prospective-block evaluation.

This module defines only the frozen cohort identity, dependence unit,
operational inventory classification, sealed-packet validation, and continuous
point-forecast scoring for the 2026-10-03..2026-10-14 multicity block.

It has no network, outcome acquisition, model fitting, probability
reconstruction, market scoring, P&L, fee, execution, or production-authority
capability. The five city-days on one target date are correlated diagnostics,
not five independent experiments. The primary independence unit is the
target-date cluster.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

FROZEN_PROTOCOL_SHA256 = "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
PRE_FIRST_EVENT_AMENDMENT_SHA256 = (
    "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
)
D1_SHA256SUMS_SHA256 = "6d2c618e2ef0d99f87c12d50f41dfefa617f43cd7347aeef4c0a1d37c3ba3891"
REVIEWED_IMAGE_DIGEST = "sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08"

PROSPECTIVE_START = date(2026, 10, 3)
PROSPECTIVE_END = date(2026, 10, 14)
EXPECTED_CITY_IDS = ("boston", "miami", "denver", "los_angeles", "seattle")
OBSERVATIONAL_UNIT = "CITY_DAY"
PRIMARY_INDEPENDENCE_UNIT = "TARGET_DATE_CLUSTER"
CITY_DAYS_INDEPENDENT = False
RESEARCH_ONLY = True
PRODUCTION_INFLUENCE = "0"

FROZEN_FORECAST_RULE_IDENTITY = (
    "daily_max_p50_proxy = max(hourly station_head_temperature_2m_p50) over exactly the city's "
    "24 authoritative CLI valid hours; no bias correction, city tuning, probability model, or "
    "rounding"
)
PACKET_RECORD_TYPE = "WN-MULTICITY-C1-CLOUD-PACKET-v1"
FORECAST_RECORD_TYPE = "WN-MULTICITY-C1-CLOUD-FORECAST-RECEIPT-v1"
MARKET_RECORD_TYPE = "WN-MULTICITY-C1-CLOUD-MARKET-v1"
WEATHER_RECORD_TYPE = "WN-MULTICITY-C1-CLOUD-WEATHER-v1"

_CITY_DAY_RE = re.compile(r"(?:^|/)city_days/(?P<city>[a-z_]+)/(?P<day>\d{8})/(?P<leaf>.+)$")


class MulticityEvaluationSpecError(ValueError):
    """Raised when frozen cohort, packet, or scoring invariants are violated."""


class CaptureState(StrEnum):
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True, order=True)
class CityDayKey:
    city_id: str
    target_date: date


@dataclass(frozen=True, slots=True)
class CoverageReport:
    as_of: date
    expected_city_days: int
    captured_city_days: int
    failed_city_days: int
    missing_city_days: int
    conflicted_city_days: int
    complete_date_clusters: int
    expected_date_clusters: int
    unexpected_paths: tuple[str, ...]
    states: tuple[tuple[CityDayKey, CaptureState], ...]

    @property
    def operationally_clean(self) -> bool:
        return (
            self.failed_city_days == 0
            and self.missing_city_days == 0
            and self.conflicted_city_days == 0
            and not self.unexpected_paths
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "record_type": "WN-MULTICITY-PREOUTCOME-COVERAGE-v1",
            "as_of": self.as_of.isoformat(),
            "expected_city_days": self.expected_city_days,
            "captured_city_days": self.captured_city_days,
            "failed_city_days": self.failed_city_days,
            "missing_city_days": self.missing_city_days,
            "conflicted_city_days": self.conflicted_city_days,
            "complete_date_clusters": self.complete_date_clusters,
            "expected_date_clusters": self.expected_date_clusters,
            "operationally_clean": self.operationally_clean,
            "primary_independence_unit": PRIMARY_INDEPENDENCE_UNIT,
            "city_days_independent": CITY_DAYS_INDEPENDENT,
            "research_only": RESEARCH_ONLY,
            "production_influence": PRODUCTION_INFLUENCE,
            "unexpected_paths": list(self.unexpected_paths),
            "states": [
                {
                    "city_id": key.city_id,
                    "target_date": key.target_date.isoformat(),
                    "state": state.value,
                }
                for key, state in self.states
            ],
        }


@dataclass(frozen=True, slots=True)
class SealedForecast:
    city_id: str
    target_date: date
    same_date_cluster: str
    forecast_p50_fahrenheit: Decimal
    decision_at_utc: str


@dataclass(frozen=True, slots=True)
class PointForecastScore:
    city_id: str
    target_date: date
    forecast_fahrenheit: Decimal
    finalized_fahrenheit: Decimal
    signed_error_fahrenheit: Decimal
    absolute_error_fahrenheit: Decimal
    squared_error_fahrenheit: Decimal


@dataclass(frozen=True, slots=True)
class ClusterEqualPointSummary:
    expected_date_clusters: int
    complete_date_clusters: int
    scored_city_days: int
    cluster_equal_signed_error_fahrenheit: Decimal | None
    cluster_equal_mae_fahrenheit: Decimal | None
    cluster_equal_mse_fahrenheit2: Decimal | None


def expected_target_dates(*, through: date | None = None) -> tuple[date, ...]:
    """Return the frozen target-date roster, optionally truncated through a date."""
    end = PROSPECTIVE_END if through is None else min(through, PROSPECTIVE_END)
    if end < PROSPECTIVE_START:
        return ()
    count = (end - PROSPECTIVE_START).days + 1
    return tuple(PROSPECTIVE_START + timedelta(days=offset) for offset in range(count))


def expected_city_days(*, through: date | None = None) -> tuple[CityDayKey, ...]:
    """Return the exact frozen city-day roster in date-major order."""
    return tuple(
        CityDayKey(city_id, target_date)
        for target_date in expected_target_dates(through=through)
        for city_id in EXPECTED_CITY_IDS
    )


def classify_city_day_objects(objects: Iterable[str]) -> CaptureState:
    """Classify one city-day without reading outcome or forecast contents."""
    material = tuple(objects)
    if len(material) != len(set(material)):
        raise MulticityEvaluationSpecError("duplicate object path in city-day inventory")
    leaves = {item.rsplit("/", 1)[-1] for item in material}
    has_packet = "packet.json" in leaves
    has_failure = "failure.json" in leaves
    if has_packet and has_failure:
        return CaptureState.CONFLICT
    if has_packet:
        return CaptureState.CAPTURED
    if has_failure:
        return CaptureState.FAILED
    return CaptureState.MISSING


def audit_inventory(paths: Iterable[str], *, as_of: date) -> CoverageReport:
    """Audit expected prospective object presence without acquiring or scoring outcomes."""
    material = tuple(line.strip() for line in paths if line.strip())
    if len(material) != len(set(material)):
        raise MulticityEvaluationSpecError("duplicate object path in inventory")

    expected = expected_city_days(through=as_of)
    expected_set = set(expected)
    grouped: dict[CityDayKey, list[str]] = {key: [] for key in expected}
    unexpected: list[str] = []

    for path in material:
        match = _CITY_DAY_RE.search(path)
        if match is None:
            continue
        day = match.group("day")
        try:
            target_date = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        except ValueError as exc:
            raise MulticityEvaluationSpecError("malformed city-day date") from exc
        key = CityDayKey(match.group("city"), target_date)
        if key not in expected_set:
            unexpected.append(path)
            continue
        grouped[key].append(path)

    states = tuple((key, classify_city_day_objects(grouped[key])) for key in expected)
    counts = {state: 0 for state in CaptureState}
    for _key, state in states:
        counts[state] += 1

    complete_clusters = 0
    for target_date in expected_target_dates(through=as_of):
        cluster = [state for key, state in states if key.target_date == target_date]
        if len(cluster) == len(EXPECTED_CITY_IDS) and all(
            state is CaptureState.CAPTURED for state in cluster
        ):
            complete_clusters += 1

    return CoverageReport(
        as_of=as_of,
        expected_city_days=len(expected),
        captured_city_days=counts[CaptureState.CAPTURED],
        failed_city_days=counts[CaptureState.FAILED],
        missing_city_days=counts[CaptureState.MISSING],
        conflicted_city_days=counts[CaptureState.CONFLICT],
        complete_date_clusters=complete_clusters,
        expected_date_clusters=len(expected_target_dates(through=as_of)),
        unexpected_paths=tuple(sorted(unexpected)),
        states=states,
    )


def _json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityEvaluationSpecError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MulticityEvaluationSpecError(f"{label} must be a JSON object")
    return value


def _exact_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise MulticityEvaluationSpecError(f"{label} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise MulticityEvaluationSpecError(f"{label} is not a decimal") from exc
    if not parsed.is_finite():
        raise MulticityEvaluationSpecError(f"{label} must be finite")
    return parsed


def _expect_common_identity(
    artifact: dict[str, object], *, label: str, city_id: str, target: date
) -> None:
    expected = {
        "city_id": city_id,
        "target_date_utc": target.isoformat(),
        "same_date_cluster": target.isoformat(),
        "observational_unit": OBSERVATIONAL_UNIT,
        "city_days_independent": False,
        "development_only": True,
        "protocol_sha256": FROZEN_PROTOCOL_SHA256,
        "pre_first_event_amendment_sha256": PRE_FIRST_EVENT_AMENDMENT_SHA256,
        "d1_sha256sums_sha256": D1_SHA256SUMS_SHA256,
    }
    for field, value in expected.items():
        if artifact.get(field) != value:
            raise MulticityEvaluationSpecError(f"{label} has invalid {field}")


def validate_sealed_preoutcome_packet(
    *,
    packet_bytes: bytes,
    forecast_bytes: bytes,
    market_bytes: bytes,
    weather_bytes: bytes,
) -> SealedForecast:
    """Validate immutable pre-outcome bytes without reading an outcome.

    The frozen experiment expressly forbids both rounding the continuous p50
    proxy into the Kalshi ladder and constructing a probability model.
    """
    packet = _json_object(packet_bytes, "packet")
    forecast = _json_object(forecast_bytes, "forecast")
    market = _json_object(market_bytes, "market")
    weather = _json_object(weather_bytes, "weather")

    if packet.get("record_type") != PACKET_RECORD_TYPE:
        raise MulticityEvaluationSpecError("unexpected packet record type")
    if forecast.get("record_type") != FORECAST_RECORD_TYPE:
        raise MulticityEvaluationSpecError("unexpected forecast record type")
    if market.get("record_type") != MARKET_RECORD_TYPE:
        raise MulticityEvaluationSpecError("unexpected market record type")
    if weather.get("record_type") != WEATHER_RECORD_TYPE:
        raise MulticityEvaluationSpecError("unexpected weather record type")

    city_id = packet.get("city_id")
    target_text = packet.get("target_date_utc")
    if not isinstance(city_id, str) or city_id not in EXPECTED_CITY_IDS:
        raise MulticityEvaluationSpecError("packet city is outside frozen roster")
    if not isinstance(target_text, str):
        raise MulticityEvaluationSpecError("packet target date is invalid")
    try:
        target = date.fromisoformat(target_text)
    except ValueError as exc:
        raise MulticityEvaluationSpecError("packet target date is invalid") from exc
    if target not in expected_target_dates():
        raise MulticityEvaluationSpecError("packet target date is outside frozen roster")

    for artifact, label in (
        (packet, "packet"),
        (forecast, "forecast"),
        (market, "market"),
        (weather, "weather"),
    ):
        _expect_common_identity(artifact, label=label, city_id=city_id, target=target)

    if (
        packet.get("no_trade") is not True
        or packet.get("no_alert") is not True
        or packet.get("no_probability_or_edge_calculation") is not True
        or packet.get("weather_development_observation") != "VALID_PRE_OUTCOME_SEALED"
    ):
        raise MulticityEvaluationSpecError("packet violates frozen development-only authority")
    if (
        forecast.get("forecast_status") != "PRE_OUTCOME_FROZEN"
        or forecast.get("outcome_not_consulted") is not True
        or forecast.get("no_probability") is not True
        or forecast.get("no_trade") is not True
        or forecast.get("no_bias_correction") is not True
        or forecast.get("no_city_specific_tuning") is not True
        or forecast.get("frozen_rule_identity") != FROZEN_FORECAST_RULE_IDENTITY
    ):
        raise MulticityEvaluationSpecError("forecast violates frozen pre-outcome rule")

    component = packet.get("component_sha256")
    if not isinstance(component, dict):
        raise MulticityEvaluationSpecError("packet component hashes are missing")
    expected_hashes = {
        "forecast.json": hashlib.sha256(forecast_bytes).hexdigest(),
        "market.json": hashlib.sha256(market_bytes).hexdigest(),
        "weather.json": hashlib.sha256(weather_bytes).hexdigest(),
    }
    if component != expected_hashes:
        raise MulticityEvaluationSpecError("packet component hash mismatch")
    if forecast.get("market_sha256") != expected_hashes["market.json"]:
        raise MulticityEvaluationSpecError("forecast market hash mismatch")
    if forecast.get("weather_sha256") != expected_hashes["weather.json"]:
        raise MulticityEvaluationSpecError("forecast weather hash mismatch")

    value = _exact_decimal(
        forecast.get("forecast_p50_proxy_fahrenheit"),
        "forecast_p50_proxy_fahrenheit",
    )
    packet_value = _exact_decimal(
        packet.get("forecast_p50_proxy_fahrenheit"),
        "packet forecast_p50_proxy_fahrenheit",
    )
    if value != packet_value:
        raise MulticityEvaluationSpecError("packet and forecast p50 values disagree")

    decision = forecast.get("decision_at_utc")
    if not isinstance(decision, str) or decision != packet.get("decision_at_utc"):
        raise MulticityEvaluationSpecError("packet and forecast decision time disagree")

    return SealedForecast(city_id, target, target.isoformat(), value, decision)


def score_point_forecast(
    sealed: SealedForecast,
    *,
    finalized_fahrenheit: Decimal,
) -> PointForecastScore:
    """Score continuous p50 against authoritative full-precision Fahrenheit truth."""
    if not isinstance(finalized_fahrenheit, Decimal) or not finalized_fahrenheit.is_finite():
        raise MulticityEvaluationSpecError("finalized temperature must be finite Decimal")
    signed = sealed.forecast_p50_fahrenheit - finalized_fahrenheit
    return PointForecastScore(
        city_id=sealed.city_id,
        target_date=sealed.target_date,
        forecast_fahrenheit=sealed.forecast_p50_fahrenheit,
        finalized_fahrenheit=finalized_fahrenheit,
        signed_error_fahrenheit=signed,
        absolute_error_fahrenheit=abs(signed),
        squared_error_fahrenheit=signed * signed,
    )


def aggregate_cluster_equal_point_scores(
    scores: Iterable[PointForecastScore],
) -> ClusterEqualPointSummary:
    """Equal-weight complete target-date clusters; retain partial rows as diagnostics."""
    material = tuple(scores)
    identities = {(item.city_id, item.target_date) for item in material}
    if len(identities) != len(material):
        raise MulticityEvaluationSpecError("duplicate city-day score")
    for item in material:
        if item.city_id not in EXPECTED_CITY_IDS or item.target_date not in expected_target_dates():
            raise MulticityEvaluationSpecError("score outside frozen roster")

    complete: list[tuple[Decimal, Decimal, Decimal]] = []
    by_date: dict[date, dict[str, PointForecastScore]] = {}
    for item in material:
        by_date.setdefault(item.target_date, {})[item.city_id] = item

    for target in expected_target_dates():
        cluster = by_date.get(target, {})
        if set(cluster) != set(EXPECTED_CITY_IDS):
            continue
        members = tuple(cluster[city] for city in EXPECTED_CITY_IDS)
        denominator = Decimal(len(members))
        complete.append(
            (
                sum((item.signed_error_fahrenheit for item in members), Decimal("0")) / denominator,
                sum((item.absolute_error_fahrenheit for item in members), Decimal("0"))
                / denominator,
                sum((item.squared_error_fahrenheit for item in members), Decimal("0"))
                / denominator,
            )
        )

    if not complete:
        signed = mae = mse = None
    else:
        denominator = Decimal(len(complete))
        signed = sum((row[0] for row in complete), Decimal("0")) / denominator
        mae = sum((row[1] for row in complete), Decimal("0")) / denominator
        mse = sum((row[2] for row in complete), Decimal("0")) / denominator

    return ClusterEqualPointSummary(
        expected_date_clusters=len(expected_target_dates()),
        complete_date_clusters=len(complete),
        scored_city_days=len(material),
        cluster_equal_signed_error_fahrenheit=signed,
        cluster_equal_mae_fahrenheit=mae,
        cluster_equal_mse_fahrenheit2=mse,
    )
