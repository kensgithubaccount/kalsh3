"""M27B.2 architecture guard: the new structural-measurement modules must never import execution,
signer, risk, or credential authority -- research-only, read-only, production_influence=0 by
construction, not merely by review. Mirrors the AST-scanning approach in
``tests/test_cpi_settlement_architecture.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

M27B2_MODULES = (
    Path("services/opportunity_engine/structural_measurement.py"),
    Path("services/opportunity_engine/structural_measurement_store.py"),
    Path("services/opportunity_engine/structural_measurement_runner.py"),
)

# Each of these packages carries live order/execution, risk-authorization, or authenticated
# account/credential authority somewhere in this repository (see their module docstrings:
# "M15 production execution boundary", "M11 historical fill simulation", "Demo, mock, and paper
# execution boundary", "Deterministic risk authorization boundary", "Read-only Kalshi account
# gateway" -- authenticated, unlike the unauthenticated services.market_universe.public_read this
# module uses -- and "M17 bounded-autonomy evidence architecture"). None of them may ever be
# imported, directly or transitively-aliased, by a module that is supposed to remain
# unauthenticated, read-only, and research-only.
FORBIDDEN_PACKAGES = (
    "services.production_execution",
    "services.execution_simulation",
    "services.demo_execution",
    "services.risk_engine",
    "services.kalshi_account_gateway",
    "services.bounded_autonomy",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_m27b2_modules_import_no_execution_signer_risk_or_credential_authority() -> None:
    violations: list[str] = []
    for path in M27B2_MODULES:
        for module in _imported_modules(path):
            if any(
                module == forbidden or module.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_PACKAGES
            ):
                violations.append(f"{path}: forbidden import {module!r}")
    assert violations == []


def test_m27b2_modules_exist_and_are_scanned() -> None:
    # A guard that silently scans zero files proves nothing -- fail closed if the module set
    # this test is supposed to police ever goes missing or gets renamed without updating this list.
    assert all(path.is_file() for path in M27B2_MODULES)


def test_guard_detects_a_forbidden_import(tmp_path: Path) -> None:
    attacker = tmp_path / "attacker.py"
    attacker.write_text("from services.production_execution.store import anything\n")
    modules = _imported_modules(attacker)
    assert any(
        module == "services.production_execution.store"
        or module.startswith("services.production_execution.")
        for module in modules
    )


def test_guard_detects_a_plain_import_form(tmp_path: Path) -> None:
    attacker = tmp_path / "attacker2.py"
    attacker.write_text("import services.risk_engine.authorization\n")
    modules = _imported_modules(attacker)
    assert any(module.startswith("services.risk_engine") for module in modules)
