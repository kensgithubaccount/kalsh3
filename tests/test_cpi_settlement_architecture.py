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
    }
)


def test_private_kalshi_acquisition_symbols_have_one_production_owner() -> None:
    root = Path("services/forecasting")
    violations: list[str] = []
    for path in root.rglob("*.py"):
        if path == OWNER:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in PRIVATE_NAMES:
                violations.append(f"{path}:{node.lineno}:{node.id}")
            if isinstance(node, ast.Attribute) and node.attr in PRIVATE_NAMES:
                violations.append(f"{path}:{node.lineno}:{node.attr}")
    assert violations == []
