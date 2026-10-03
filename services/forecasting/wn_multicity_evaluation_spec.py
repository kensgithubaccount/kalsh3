"""Outcome-blind freeze for WN multicity prospective-block evaluation.

This module defines only the frozen cohort identity, dependence unit, and
operational inventory classification for the 2026-10-03..2026-10-14
multicity block. It has no network, outcome acquisition, model fitting,
market scoring, P&L, fee, execution, or production-authority capability.

The five city-days on one target date are correlated diagnostics, not five
independent experiments. The primary independence unit is the target-date
cluster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Iterable

FROZEN_PROTOCOL_SHA256 = (
    "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
)
PRE_FIRST_EVENT_AMENDMENT_SHA256 = (
    "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
)
REVIEWED_IMAGE_DIGEST = (
    "sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08"
)

PROSPECTIVE_START = date(2026, 10, 3)
PROSPECTIVE_END = date(2026, 10, 14)
EXPECTED_CITY_IDS = ("boston", "miami", "denver", "los_angeles", "seattle")
OBSERVATIONAL_UNIT = "CITY_DAY"
PRIMARY_INDEPENDENCE_UNIT = "TARGET_DATE_CLUSTER"
CITY_DAYS_INDEPENDENT = False
RESEARCH_ONLY = True
PRODUCTION_INFLUENCE = "0"

_CITY_DAY_RE = re.compile(
    r"(?:^|/)city_days/(?P<city>[a-z_]+)/(?P<day>\d{8})/(?P<leaf>.+)$"
)


class MulticityEvaluationSpecError(ValueError):
    """Raised when frozen cohort or inventory invariants are violated."""


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
        try:
            target_date = date.fromisoformat(
                f"{match.group('day')[:4]}-{match.group('day')[4:6]}-{match.group('day')[6:]}"
            )
        except ValueError as exc:
            raise MulticityEvaluationSpecError("malformed city-day date") from exc
        key = CityDayKey(match.group("city"), target_date)
        if key not in expected_set:
            unexpected.append(path)
            continue
        grouped[key].append(path)

    states = tuple(
        (key, classify_city_day_objects(grouped[key]))
        for key in expected
    )
    counts = {state: 0 for state in CaptureState}
    for _key, state in states:
        counts[state] += 1

    complete_clusters = 0
    for target_date in expected_target_dates(through=as_of):
        cluster = [
            state
            for key, state in states
            if key.target_date == target_date
        ]
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
