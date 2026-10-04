from __future__ import annotations

from datetime import date

import pytest

from services.forecasting.wn_multicity_evaluation_spec import (
    CITY_DAYS_INDEPENDENT,
    EXPECTED_CITY_IDS,
    FROZEN_PROTOCOL_SHA256,
    PRE_FIRST_EVENT_AMENDMENT_SHA256,
    PRIMARY_INDEPENDENCE_UNIT,
    PROSPECTIVE_END,
    PROSPECTIVE_START,
    REVIEWED_IMAGE_DIGEST,
    CaptureState,
    MulticityEvaluationSpecError,
    audit_inventory,
    classify_city_day_objects,
    expected_city_days,
    expected_target_dates,
)


def test_frozen_identity_constants_match_reviewed_experiment() -> None:
    assert FROZEN_PROTOCOL_SHA256 == (
        "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
    )
    assert PRE_FIRST_EVENT_AMENDMENT_SHA256 == (
        "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
    )
    assert REVIEWED_IMAGE_DIGEST == (
        "sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08"
    )


def test_exact_frozen_roster_is_twelve_dates_and_sixty_city_days() -> None:
    dates = expected_target_dates()
    keys = expected_city_days()
    assert dates[0] == PROSPECTIVE_START
    assert dates[-1] == PROSPECTIVE_END
    assert len(dates) == 12
    assert len(keys) == 60
    assert len(EXPECTED_CITY_IDS) == 5


def test_primary_independence_is_date_cluster_not_city_day() -> None:
    assert CITY_DAYS_INDEPENDENT is False
    assert PRIMARY_INDEPENDENCE_UNIT == "TARGET_DATE_CLUSTER"


@pytest.mark.parametrize(
    ("objects", "expected"),
    [
        (["x/packet.json"], CaptureState.CAPTURED),
        (["x/failure.json"], CaptureState.FAILED),
        (["x/start.json"], CaptureState.MISSING),
        (["x/packet.json", "x/failure.json"], CaptureState.CONFLICT),
    ],
)
def test_city_day_structural_classification(objects: list[str], expected: CaptureState) -> None:
    assert classify_city_day_objects(objects) is expected


def test_duplicate_object_path_fails_closed() -> None:
    with pytest.raises(MulticityEvaluationSpecError, match="duplicate object path"):
        classify_city_day_objects(["x/packet.json", "x/packet.json"])


def test_october_3_full_capture_is_one_complete_independence_cluster() -> None:
    paths = [
        f"gs://bucket/root/city_days/{city}/20261003/packet.json" for city in EXPECTED_CITY_IDS
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert report.expected_city_days == 5
    assert report.captured_city_days == 5
    assert report.complete_date_clusters == 1
    assert report.expected_date_clusters == 1
    assert report.operationally_clean is True


def test_missing_one_city_breaks_cluster_completeness_without_inventing_failure() -> None:
    paths = [
        f"gs://bucket/root/city_days/{city}/20261003/packet.json" for city in EXPECTED_CITY_IDS[:-1]
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert report.captured_city_days == 4
    assert report.missing_city_days == 1
    assert report.failed_city_days == 0
    assert report.complete_date_clusters == 0
    assert report.operationally_clean is False


def test_future_or_unknown_city_path_is_reported_not_silently_adopted() -> None:
    paths = [
        "gs://bucket/root/city_days/chicago/20261003/packet.json",
        "gs://bucket/root/city_days/boston/20261015/packet.json",
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert len(report.unexpected_paths) == 2
    assert report.operationally_clean is False


def test_before_start_has_zero_expected_observations() -> None:
    report = audit_inventory([], as_of=date(2026, 10, 2))
    assert report.expected_city_days == 0
    assert report.expected_date_clusters == 0
    assert report.operationally_clean is True


def _sealed_component_bytes(
    *,
    city_id: str = "boston",
    target: str = "2026-10-03",
    forecast_f: str = "66.8451611328125",
) -> tuple[bytes, bytes, bytes, bytes]:
    common = {
        "city_id": city_id,
        "target_date_utc": target,
        "same_date_cluster": target,
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "development_only": True,
        "protocol_sha256": (
            "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
        ),
        "pre_first_event_amendment_sha256": (
            "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
        ),
    }
    market = {
        "record_type": "WN-MULTICITY-C1-CLOUD-MARKET-v1",
        **common,
    }
    weather = {
        "record_type": "WN-MULTICITY-C1-CLOUD-WEATHER-v1",
        **common,
    }
    market_bytes = json.dumps(market, sort_keys=True, separators=(",", ":")).encode()
    weather_bytes = json.dumps(weather, sort_keys=True, separators=(",", ":")).encode()
    forecast = {
        "record_type": "WN-MULTICITY-C1-CLOUD-FORECAST-RECEIPT-v1",
        **common,
        "forecast_status": "PRE_OUTCOME_FROZEN",
        "outcome_not_consulted": True,
        "no_probability": True,
        "no_trade": True,
        "no_bias_correction": True,
        "no_city_specific_tuning": True,
        "frozen_rule_identity": (
            "daily_max_p50_proxy = max(hourly station_head_temperature_2m_p50) over exactly "
            "the city's 24 authoritative CLI valid hours; no bias correction, city tuning, "
            "probability model, or rounding"
        ),
        "forecast_p50_proxy_fahrenheit": forecast_f,
        "market_sha256": hashlib.sha256(market_bytes).hexdigest(),
        "weather_sha256": hashlib.sha256(weather_bytes).hexdigest(),
        "decision_at_utc": f"{target}T09:00:08Z",
    }
    forecast_bytes = json.dumps(forecast, sort_keys=True, separators=(",", ":")).encode()
    packet = {
        "record_type": "WN-MULTICITY-C1-CLOUD-PACKET-v1",
        **common,
        "no_trade": True,
        "no_alert": True,
        "no_probability_or_edge_calculation": True,
        "weather_development_observation": "VALID_PRE_OUTCOME_SEALED",
        "forecast_p50_proxy_fahrenheit": forecast_f,
        "decision_at_utc": forecast["decision_at_utc"],
        "component_sha256": {
            "forecast.json": hashlib.sha256(forecast_bytes).hexdigest(),
            "market.json": hashlib.sha256(market_bytes).hexdigest(),
            "weather.json": hashlib.sha256(weather_bytes).hexdigest(),
        },
    }
    packet_bytes = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
    return packet_bytes, forecast_bytes, market_bytes, weather_bytes


def test_sealed_packet_schema_is_bound_without_outcome_or_probability() -> None:
    packet, forecast, market, weather = _sealed_component_bytes()
    sealed = validate_sealed_preoutcome_packet(
        packet_bytes=packet,
        forecast_bytes=forecast,
        market_bytes=market,
        weather_bytes=weather,
    )
    assert sealed.city_id == "boston"
    assert sealed.target_date == date(2026, 10, 3)
    assert sealed.forecast_p50_fahrenheit == Decimal("66.8451611328125")


def test_component_byte_change_fails_closed() -> None:
    packet, forecast, market, weather = _sealed_component_bytes()
    with pytest.raises(MulticityEvaluationSpecError, match="component hash mismatch"):
        validate_sealed_preoutcome_packet(
            packet_bytes=packet,
            forecast_bytes=forecast,
            market_bytes=market + b" ",
            weather_bytes=weather,
        )


def test_probability_or_outcome_leakage_flag_fails_closed() -> None:
    packet, forecast, market, weather = _sealed_component_bytes()
    obj = json.loads(forecast)
    obj["no_probability"] = False
    changed = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    packet_obj = json.loads(packet)
    packet_obj["component_sha256"]["forecast.json"] = hashlib.sha256(changed).hexdigest()
    changed_packet = json.dumps(packet_obj, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(MulticityEvaluationSpecError, match="frozen pre-outcome rule"):
        validate_sealed_preoutcome_packet(
            packet_bytes=changed_packet,
            forecast_bytes=changed,
            market_bytes=market,
            weather_bytes=weather,
        )


def test_point_forecast_scoring_is_exact_and_never_rounds() -> None:
    packet, forecast, market, weather = _sealed_component_bytes(forecast_f="66.845")
    sealed = validate_sealed_preoutcome_packet(
        packet_bytes=packet,
        forecast_bytes=forecast,
        market_bytes=market,
        weather_bytes=weather,
    )
    score = score_point_forecast(sealed, finalized_fahrenheit=Decimal("67"))
    assert score.signed_error_fahrenheit == Decimal("-0.155")
    assert score.absolute_error_fahrenheit == Decimal("0.155")
    assert score.squared_error_fahrenheit == Decimal("0.024025")


def test_cluster_equal_summary_requires_all_five_cities_for_primary_cluster() -> None:
    scores = []
    for index, city in enumerate(EXPECTED_CITY_IDS):
        packet, forecast, market, weather = _sealed_component_bytes(
            city_id=city,
            forecast_f=str(70 + index),
        )
        sealed = validate_sealed_preoutcome_packet(
            packet_bytes=packet,
            forecast_bytes=forecast,
            market_bytes=market,
            weather_bytes=weather,
        )
        scores.append(
            score_point_forecast(
                sealed,
                finalized_fahrenheit=Decimal(str(69 + index)),
            )
        )
    full = aggregate_cluster_equal_point_scores(scores)
    assert full.complete_date_clusters == 1
    assert full.scored_city_days == 5
    assert full.cluster_equal_signed_error_fahrenheit == Decimal("1")
    assert full.cluster_equal_mae_fahrenheit == Decimal("1")
    assert full.cluster_equal_mse_fahrenheit2 == Decimal("1")

    partial = aggregate_cluster_equal_point_scores(scores[:-1])
    assert partial.complete_date_clusters == 0
    assert partial.scored_city_days == 4
    assert partial.cluster_equal_mae_fahrenheit is None


def test_non_decimal_settlement_authority_is_rejected() -> None:
    packet, forecast, market, weather = _sealed_component_bytes()
    sealed = validate_sealed_preoutcome_packet(
        packet_bytes=packet,
        forecast_bytes=forecast,
        market_bytes=market,
        weather_bytes=weather,
    )
    with pytest.raises(MulticityEvaluationSpecError, match="finite Decimal"):
        score_point_forecast(sealed, finalized_fahrenheit=67)  # type: ignore[arg-type]
