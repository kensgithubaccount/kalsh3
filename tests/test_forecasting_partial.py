from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.domain import (
    CalibrationBin,
    ForecastError,
    FrozenForecast,
    MarketBaseline,
    ModelFamily,
)


def test_weather_and_scheduled_release_forecasts_are_frozen_against_executable_baseline() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    baseline = MarketBaseline(now, Decimal(".44"), Decimal(".46"), Decimal(".53"), Decimal(".55"))
    forecast = FrozenForecast(
        "f",
        "WX",
        ModelFamily.WEATHER,
        now,
        now + timedelta(days=1),
        Decimal(".50"),
        Decimal(".40"),
        Decimal(".60"),
        "weather-v1",
        "vintage",
        ("validated-evidence",),
        baseline,
    )
    assert forecast.comparison_to_executable_ask == Decimal(".04")
    assert forecast.production_influence == 0
    assert ModelFamily.SCHEDULED_ECONOMIC_RELEASE


def test_uncertainty_and_zero_influence_fail_closed() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    baseline = MarketBaseline(now, Decimal(".4"), Decimal(".5"), Decimal(".5"), Decimal(".6"))
    with pytest.raises(ForecastError):
        FrozenForecast(
            "f",
            "M",
            ModelFamily.WEATHER,
            now,
            now,
            Decimal(".8"),
            Decimal(".9"),
            Decimal("1"),
            "v",
            "h",
            (),
            baseline,
        )
    with pytest.raises(ForecastError, match="zero production"):
        FrozenForecast(
            "f",
            "M",
            ModelFamily.WEATHER,
            now,
            now,
            Decimal(".5"),
            Decimal(".4"),
            Decimal(".6"),
            "v",
            "h",
            (),
            baseline,
            Decimal(".1"),
        )
    assert CalibrationBin(Decimal(".4"), Decimal(".5"), 0, Decimal(".45"), None)


def test_forecasting_has_no_signer_risk_or_execution_path() -> None:
    code = "\n".join(path.read_text() for path in Path("services/forecasting").glob("*.py"))
    for forbidden in ("risk_engine", "RequestSigner", "kalshi_account_gateway", "submit_order"):
        assert forbidden not in code
