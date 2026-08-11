from decimal import Decimal

import pytest

from services.core.config import Settings
from services.core.money import decimal_from_wire
from services.risk_engine.policy import RiskRequest, authorize_new_risk


def test_runtime_defaults_fail_safe() -> None:
    settings = Settings()
    assert settings.production_write_enabled is False
    assert settings.autonomous_trading_enabled is False
    assert settings.kalshi_subaccount == 0


def test_general_service_cannot_enable_production_write() -> None:
    with pytest.raises(ValueError, match="isolated signer"):
        Settings(production_write_enabled=True)


def test_wire_money_rejects_float_and_non_finite() -> None:
    assert decimal_from_wire("0.0100") == Decimal("0.0100")
    with pytest.raises(ValueError, match="finite"):
        decimal_from_wire("NaN")


def test_risk_fails_closed_by_default() -> None:
    decision = authorize_new_risk(
        RiskRequest(
            incremental_market_loss=Decimal("1"),
            current_market_risk=Decimal("0"),
            current_event_risk=Decimal("0"),
            current_open_risk=Decimal("0"),
        )
    )
    assert not decision.authorized
    assert decision.reason == "global halt is active"


def test_risk_authorizes_small_reconciled_fresh_request() -> None:
    decision = authorize_new_risk(
        RiskRequest(
            incremental_market_loss=Decimal("1"),
            current_market_risk=Decimal("0"),
            current_event_risk=Decimal("0"),
            current_open_risk=Decimal("0"),
            data_fresh=True,
            reconciled=True,
            globally_halted=False,
        )
    )
    assert decision.authorized
