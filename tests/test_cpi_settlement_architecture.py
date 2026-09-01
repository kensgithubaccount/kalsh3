from __future__ import annotations

import ast
from pathlib import Path

OWNER = Path("services/forecasting/cpi_settlement_reconciliation.py")
PRIVATE_NAMES = frozenset(
    {
        "_ACQUISITION_CAPABILITY",
        "_PublicHTTPResponse",
        "_issue_reviewed_public_get",
        "_read_complete_response",
        "_ISSUED_KALSHI_ACQUISITION_FINGERPRINTS",
    }
)


def _violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        if path == OWNER:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in PRIVATE_NAMES:
                        violations.append(f"{path}:{node.lineno}:{imported.name}")
            if isinstance(node, ast.Name) and node.id in PRIVATE_NAMES:
                violations.append(f"{path}:{node.lineno}:{node.id}")
            if isinstance(node, ast.Attribute) and node.attr in PRIVATE_NAMES:
                violations.append(f"{path}:{node.lineno}:{node.attr}")
    return violations


def test_private_kalshi_acquisition_symbols_have_one_production_owner() -> None:
    assert _violations(Path("services")) == []


def test_guard_detects_an_unauthorized_services_module(tmp_path: Path) -> None:
    unauthorized = tmp_path / "services" / "execution" / "attacker.py"
    unauthorized.parent.mkdir(parents=True)
    unauthorized.write_text(
        "from services.forecasting.cpi_settlement_reconciliation import "
        "_issue_reviewed_public_get\n"
        "_issue_reviewed_public_get(None, None)\n"
    )
    assert _violations(tmp_path / "services")


def test_guard_detects_an_aliased_unauthorized_import(tmp_path: Path) -> None:
    unauthorized = tmp_path / "services" / "execution" / "aliased_attacker.py"
    unauthorized.parent.mkdir(parents=True)
    unauthorized.write_text(
        "from services.forecasting.cpi_settlement_reconciliation import (\n"
        "    _PublicHTTPResponse as Response,\n"
        "    _issue_reviewed_public_get as issue,\n"
        ")\n"
        "del Response, issue\n"
    )
    assert _violations(tmp_path / "services")
