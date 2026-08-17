from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.daily_temperature import route_daily_temperature
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_coverage import POLICY_VERSION
from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.forecasting.weather_probability import (
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    EmpiricalResidualDistribution,
    PhysicalTemperatureProxyProbability,
    WeatherProbabilityAbstention,
    build_current_weather_forecast_evidence,
    load_weather_residual_population,
    physical_temperature_proxy_probability,
)
from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.market_universe.domain import stable_hash
from tests.test_m27c_daily_temperature_contract_authority import event, market
from tests.test_m27c_weather_calibration_grib import _extraction


def _row(day: date, lead: int, residual: str = "1") -> dict[str, object]:
    reference = datetime.combine(day, datetime.min.time(), UTC).replace(hour=3)
    row: dict[str, object] = {
        "source": "CLIMDW",
        "measurement": "DAILY_MAX",
        "forecast_reference_time": reference.isoformat(),
        "valid_time_coordinate": (reference + timedelta(seconds=lead)).isoformat(),
        "local_target_date": day.isoformat(),
        "lead_to_valid_coordinate_seconds": lead,
        "forecast_kelvin": "300",
        "forecast_deg_f": "70",
        "observed_tenths_c": 217,
        "observed_deg_f": str(Decimal("70") + Decimal(residual)),
        "residual_deg_f": residual,
        "ndfd_descriptor_hash": "descriptor",
        "ndfd_csv_hash": "extraction",
        "ghcnd_snapshot_hash": "outcome",
        "ndfd_evidence_identity": "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z",
        "ghcnd_evidence_identity": "ghcnd",
        "authority_identity": PHYSICAL_WEATHER_SOURCES["CLIMDW"].authority_identity,
        "replay_fidelity": "FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT",
        "research_only": True,
        "production_influence": "0",
    }
    row["residual_id"] = stable_hash(("m27c-residual-v1", row))
    return row


def artifact(*, lead: int = 54_000, count: int = 547) -> dict[str, object]:
    start = date(2024, 1, 1)
    rows = [_row(start + timedelta(days=index), lead, str(index % 5 - 2)) for index in range(count)]
    payload: dict[str, object] = {
        "source": "CLIMDW",
        "measurement": "DAILY_MAX",
        "requested_target_start_date": "2024-01-01",
        "requested_target_end_date": "2026-07-31",
        "authority_identity": PHYSICAL_WEATHER_SOURCES["CLIMDW"].authority_identity,
        "policy_version": POLICY_VERSION,
        "product_family_identity": "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z",
        "status": "COMPLETE",
        "actual_catalog_scan_start_date": "2023-12-29",
        "actual_catalog_scan_end_date": "2026-07-31",
        "aws_discovery_requests": [],
        "archive_catalog_requests": [],
        "successful_descriptors": [],
        "successful_point_csvs": [],
        "rejected_or_ambiguous_datasets": [],
        "missing_dates": [],
        "quality_flagged_outcomes": [],
        "raw_residual_rows": rows,
        "selected_residual_rows": rows,
        "selected_residual_ids": [row["residual_id"] for row in rows],
        "unique_local_target_dates": [row["local_target_date"] for row in rows],
        "counts_by_lead_bucket": {
            "0-24h": len(rows) if lead == 54_000 else 0,
            "24-48h": len(rows) if lead == 140_400 else 0,
            "48-72h": len(rows) if lead == 226_800 else 0,
        },
        "coverage_percent_by_lead_bucket": {},
        "missing_dates_by_lead_bucket": {},
        "lead_seconds": [lead for _ in rows],
        "earliest_selected_target_date": rows[0]["local_target_date"] if rows else None,
        "latest_selected_target_date": rows[-1]["local_target_date"] if rows else None,
        "evidence_identities": [],
        "evidence_hashes": [],
        "acquired_at": "2026-08-17T00:00:00+00:00",
        "production_influence": "0",
        "raw_grib_objects": [],
        "accepted_forecast_records": [],
        "extraction_provenance": [],
        "usable_outcome_count": len(rows),
        "artifact_sha256": None,
    }
    payload["artifact_sha256"] = stable_hash(payload)
    return payload


def population(lead: int = 54_000):
    result = load_weather_residual_population(
        artifact(lead=lead),
        exact_midpoint_seconds=lead,
        training_start=date(2024, 1, 1),
        training_end=date(2025, 6, 30),
    )
    assert not isinstance(result, WeatherProbabilityAbstention)
    return result


@pytest.mark.parametrize("lead", [54_000, 140_400, 226_800])
def test_exact_horizon_population_is_canonical_and_material(lead: int) -> None:
    result = population(lead)
    assert result.identity.exact_midpoint_seconds == lead
    assert result.identity.sample_count == 547
    assert result.production_influence == Decimal("0")
    assert result.residual_ids == tuple(
        sorted(result.residual_ids, key=lambda value: result.residual_ids.index(value))
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(status="PARTIAL"), "incomplete"),
        (
            lambda value: value.update(product_family_identity="LEGACY_CHICAGO_MAXT_5KM_YGFZ98"),
            "family",
        ),
        (lambda value: value.update(measurement="DAILY_MIN"), "lane"),
        (lambda value: value.update(authority_identity="wrong"), "authority"),
    ],
)
def test_artifact_metadata_fails_closed(mutation, message: str) -> None:
    payload = artifact()
    mutation(payload)
    result = load_weather_residual_population(
        payload,
        exact_midpoint_seconds=54_000,
        training_start=date(2024, 1, 1),
        training_end=date(2025, 6, 30),
    )
    assert isinstance(result, WeatherProbabilityAbstention)
    assert message in result.detail


@pytest.mark.parametrize("field,value", [("residual_deg_f", "NaN"), ("forecast_deg_f", "bad")])
def test_malformed_decimal_and_altered_rows_fail_closed(field: str, value: str) -> None:
    payload = artifact()
    payload["selected_residual_rows"][0][field] = value  # type: ignore[index]
    payload["artifact_sha256"] = None
    payload["artifact_sha256"] = stable_hash(payload)
    result = load_weather_residual_population(
        payload,
        exact_midpoint_seconds=54_000,
        training_start=date(2024, 1, 1),
        training_end=date(2025, 6, 30),
    )
    assert isinstance(result, WeatherProbabilityAbstention)


def test_identity_mismatch_duplicate_and_below_floor_abstain() -> None:
    payload = artifact()
    payload["artifact_sha256"] = "altered"
    assert isinstance(
        load_weather_residual_population(
            payload,
            exact_midpoint_seconds=54_000,
            training_start=date(2024, 1, 1),
            training_end=date(2025, 6, 30),
        ),
        WeatherProbabilityAbstention,
    )
    short = artifact(count=364)
    result = load_weather_residual_population(
        short,
        exact_midpoint_seconds=54_000,
        training_start=date(2024, 1, 1),
        training_end=date(2025, 6, 30),
    )
    assert isinstance(result, WeatherProbabilityAbstention)
    assert "V1_OPERATING_SAFETY_FLOOR" in result.detail


def test_empirical_distribution_quantiles_intervals_crps_and_boundaries() -> None:
    distribution = EmpiricalResidualDistribution(
        (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"))
    )
    assert distribution.quantile(Decimal("0")) == Decimal("1")
    assert distribution.quantile(Decimal("0.5")) == Decimal("2")
    assert distribution.quantile(Decimal("1")) == Decimal("4")
    assert distribution.interval(Decimal("0.50")) == (Decimal("1"), Decimal("3"))
    assert distribution.probability_resolution == Decimal("0.25")
    assert EmpiricalResidualDistribution((Decimal("0"), Decimal("2"))).crps(
        Decimal("1")
    ) == Decimal("0.5")


def current(record: int = 1):
    evidence = parse_wgrib2_max_t_evidence(
        _extraction(), raw_grib_sha256="raw", extraction_sha256="extraction"
    )
    return build_current_weather_forecast_evidence(evidence, record_number=record)


def chicago_route(strike_type: str, floor: object, cap: object):
    return route_daily_temperature(
        market(
            measurement="maximum",
            location="Chicago",
            identifier="CLIMDW",
            strike_type=strike_type,
            floor=floor,
            cap=cap,
            date_text="Jun 15, 2024",
        ),
        event(event_ticker="KXHIGHAUS-26AUG15", series_ticker="KXHIGHAUS"),
    )


@pytest.mark.parametrize("record,lead", [(1, 54_000), (2, 140_400), (3, 226_800)])
def test_current_forecast_requires_exact_revalidated_evidence(record: int, lead: int) -> None:
    result = current(record)
    assert result.exact_midpoint_seconds == lead
    forged = replace(
        parse_wgrib2_max_t_evidence(
            _extraction(), raw_grib_sha256="raw", extraction_sha256="extraction"
        ),
        records=(replace(parse_wgrib2_max_t_evidence(_extraction()).records[0], variable="TMP"),),
    )
    with pytest.raises(ForecastError):
        build_current_weather_forecast_evidence(forged, record_number=1)


@pytest.mark.parametrize(
    ("strike_type", "floor", "cap"),
    [("greater", 80, None), ("less", None, 90), ("between", 80, 90)],
)
def test_proxy_exact_predicates_and_truth_boundary(
    strike_type: str, floor: object, cap: object
) -> None:
    result = physical_temperature_proxy_probability(
        route=chicago_route(strike_type, floor, cap), population=population(), current=current()
    )
    assert isinstance(result, PhysicalTemperatureProxyProbability)
    assert result.numerator <= result.denominator == 547
    assert result.probability == Decimal(result.numerator) / Decimal(result.denominator)
    assert result.claim_type == CLAIM_TYPE
    assert result.settlement_mapping_status == SETTLEMENT_MAPPING_STATUS
    assert result.production_influence == Decimal("0")


def test_cross_horizon_and_target_date_mismatch_abstain() -> None:
    result = physical_temperature_proxy_probability(
        route=chicago_route("greater", 80, None), population=population(140_400), current=current(1)
    )
    assert isinstance(result, WeatherProbabilityAbstention)
    wrong_date = replace(current(), local_target_date=date(2024, 6, 16))
    assert isinstance(
        physical_temperature_proxy_probability(
            route=chicago_route("greater", 80, None), population=population(), current=wrong_date
        ),
        WeatherProbabilityAbstention,
    )


def test_module_has_no_downstream_or_io_dependency() -> None:
    import inspect

    import services.forecasting.weather_probability as module

    source = inspect.getsource(module)
    forbidden = (
        "production_execution",
        "risk_engine",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
        "independent_probability",
        "market_anchored_blend",
        "open(",
        "subprocess",
        "httpx",
    )
    assert all(value not in source for value in forbidden)
