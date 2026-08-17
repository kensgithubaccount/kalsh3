from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_prospective import (
    BLACKOUT_END,
    BLACKOUT_START,
    FROZEN_MODEL_IDENTITIES,
    FROZEN_MODEL_TRAINING_END,
    PROSPECTIVE_END,
    PROSPECTIVE_PROTOCOL,
    PROSPECTIVE_START,
    PROTOCOL_IDENTITY,
    validate_prospective_forecast_evidence,
)


def evidence(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "evidence_identity": "forecast-evidence-1",
        "source": "CLIMDW",
        "station": "KMDW",
        "ghcnd_station": "USW00014819",
        "measurement": "DAILY_MAX",
        "family": "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z",
        "target_date": date(2026, 9, 1),
        "exact_midpoint_seconds": 54_000,
        "model_identity": FROZEN_MODEL_IDENTITIES[54_000],
        "forecast_reference_time": "2026-08-31T03:00:00+00:00",
        "interval_start": "2026-09-01T12:00:00+00:00",
        "interval_end": "2026-09-02T00:00:00+00:00",
        "midpoint": "2026-09-01T18:00:00+00:00",
        "collection_timestamp": "2026-08-31T04:00:00+00:00",
        "raw_grib_sha256": "raw-sha256",
        "extraction_sha256": "extraction-sha256",
        "extraction_policy_version": "m27c-wgrib2-maxt-extraction-v1",
        "wgrib2_version": "3.8.0",
        "central_kelvin": Decimal("300"),
        "central_deg_f": Decimal("80.33"),
        "quality_status": "ACCEPTED",
        "missing_source": False,
        "research_only": True,
        "production_influence": Decimal("0"),
    }
    value.update(changes)
    return value


def test_valid_forecast_only_evidence_passes() -> None:
    validate_prospective_forecast_evidence(evidence())
    assert PROSPECTIVE_PROTOCOL.protocol_identity == PROTOCOL_IDENTITY
    assert PROSPECTIVE_PROTOCOL.training_end == FROZEN_MODEL_TRAINING_END


@pytest.mark.parametrize(
    "field",
    ("observed_deg_f", "residual_deg_f", "evaluation_metrics", "market_data"),
)
def test_outcome_residual_evaluation_and_market_fields_are_rejected(field: str) -> None:
    with pytest.raises(ForecastError, match="prohibited"):
        validate_prospective_forecast_evidence(evidence(**{field: Decimal("1")}))


@pytest.mark.parametrize("target", (date(2026, 8, 31), date(2027, 4, 1)))
def test_target_outside_period_is_rejected(target: date) -> None:
    with pytest.raises(ForecastError, match="outside"):
        validate_prospective_forecast_evidence(evidence(target_date=target))


def test_august_operations_blackout_is_explicit() -> None:
    assert (date(2026, 8, 1), date(2026, 8, 31)) == (BLACKOUT_START, BLACKOUT_END)
    with pytest.raises(ForecastError):
        validate_prospective_forecast_evidence(evidence(target_date=BLACKOUT_START))


@pytest.mark.parametrize(
    "field,value",
    (
        ("source", "OTHER"),
        ("station", "KORD"),
        ("measurement", "DAILY_MIN"),
        ("family", "LEGACY_CHICAGO_MAXT_5KM_YGFZ98"),
        ("exact_midpoint_seconds", 54_001),
        ("model_identity", "wrong-model"),
    ),
)
def test_frozen_source_semantics_and_identity_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ForecastError):
        validate_prospective_forecast_evidence(evidence(**{field: value}))


def test_zero_influence_is_required() -> None:
    with pytest.raises(ForecastError, match="zero influence"):
        validate_prospective_forecast_evidence(evidence(production_influence=Decimal("0.01")))


def test_training_boundary_excludes_2026_and_2027_residuals() -> None:
    assert PROSPECTIVE_START > FROZEN_MODEL_TRAINING_END
    assert PROSPECTIVE_END > FROZEN_MODEL_TRAINING_END
    source = Path("services/forecasting/weather_prospective.py").read_text()
    assert "weather_calibration" not in source
    assert "Ghcnd" not in source


def test_prospective_path_has_no_ghcn_outcome_or_market_execution_imports() -> None:
    source = Path("services/forecasting/weather_prospective.py").read_text()
    assert "weather_calibration" not in source
    assert "market_universe" in source  # only stable hashing is permitted
    assert "production_execution" not in source
    assert "market_data" in source  # prohibited field name, not a dependency
