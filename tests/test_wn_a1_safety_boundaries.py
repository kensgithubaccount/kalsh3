"""WN-A1 safety-boundary tests: no execution/order capability, zero production influence.

Static source scan (not just runtime behavior) so a forbidden import cannot slip in even
if no test happens to exercise the code path that would use it.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

WN_A1_DIR = Path(__file__).parent.parent / "services" / "forecasting"
WN_A1_MODULES = sorted(WN_A1_DIR.glob("wn_a1_*.py"))

FORBIDDEN_IMPORT_PREFIXES = (
    "services.production_execution",
    "services.demo_execution",
    "services.execution_simulation",
    "services.production_gdp_strategy",
    "services.production_weather_strategy",
    "services.risk_engine",
    "services.supervised_canary",
    "services.bounded_autonomy",
    "services.kalshi_account_gateway",
)
FORBIDDEN_SUBSTRINGS = (
    "place_order",
    "submit_order",
    "create_order",
    "sign_order",
    "signer",
    "private_key",
)


def test_wn_a1_modules_exist() -> None:
    assert len(WN_A1_MODULES) >= 8


@pytest.mark.parametrize("path", WN_A1_MODULES, ids=lambda p: p.name)
def test_no_execution_or_signer_imports(path: Path) -> None:
    text = path.read_text()
    import_lines = [line for line in text.splitlines() if re.match(r"^\s*(import|from)\s", line)]
    for line in import_lines:
        for forbidden in FORBIDDEN_IMPORT_PREFIXES:
            assert forbidden not in line, f"{path.name} imports forbidden module: {line!r}"
    lowered = text.lower()
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in lowered, f"{path.name} references forbidden term: {forbidden!r}"


@pytest.mark.parametrize("path", WN_A1_MODULES, ids=lambda p: p.name)
def test_no_network_write_verbs_beyond_the_reviewed_public_get_transport(path: Path) -> None:
    text = path.read_text()
    # WN-A1 must never construct its own HTTP POST/PUT/DELETE call -- all Kalshi reads go
    # through services.market_universe.public_read's reviewed GET-only transport.
    assert '"POST"' not in text
    assert '"PUT"' not in text
    assert '"DELETE"' not in text


def test_every_wn_a1_dataclass_defaults_zero_production_influence() -> None:
    import importlib
    import inspect
    from dataclasses import MISSING, fields, is_dataclass

    for path in WN_A1_MODULES:
        module_name = "services.forecasting." + path.stem
        module = importlib.import_module(module_name)
        for _name, obj in inspect.getmembers(module):
            if is_dataclass(obj) and inspect.isclass(obj):
                for field in fields(obj):
                    if field.name == "production_influence" and field.default is not MISSING:
                        assert field.default == Decimal(0), (
                            f"{module_name}.{obj.__name__}.production_influence default "
                            f"is not zero: {field.default!r}"
                        )
