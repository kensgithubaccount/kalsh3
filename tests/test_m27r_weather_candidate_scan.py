"""Offline tests for the M27R one-command public weather candidate scan."""

from __future__ import annotations

import ast
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.run_m27r_weather_candidate_scan import (
    M27RScanError,
    _active_market_rows,
    _create_run_dir,
    _window_open,
    _write_json_create_only,
)


def test_window_is_only_open_during_reviewed_03z_interval() -> None:
    assert _window_open(datetime(2026, 8, 23, 3, 0, tzinfo=UTC)) is True
    assert _window_open(datetime(2026, 8, 23, 3, 25, tzinfo=UTC)) is True
    assert _window_open(datetime(2026, 8, 23, 2, 59, tzinfo=UTC)) is False
    assert _window_open(datetime(2026, 8, 23, 3, 26, tzinfo=UTC)) is False


def test_active_market_rows_filters_and_sorts_fixed_series() -> None:
    evidence: dict[str, object] = {
        "pages": [
            {
                "payload": {
                    "markets": [
                        {
                            "ticker": "KXHIGHCHI-26AUG23-T81",
                            "event_ticker": "KXHIGHCHI-26AUG23",
                            "status": "active",
                        },
                        {
                            "ticker": "KXHIGHCHI-26AUG22-T85",
                            "event_ticker": "KXHIGHCHI-26AUG22",
                            "status": "closed",
                        },
                        {
                            "ticker": "KXHIGHCHI-26AUG23-B76.5",
                            "event_ticker": "KXHIGHCHI-26AUG23",
                            "status": "active",
                        },
                    ]
                }
            }
        ]
    }

    rows = _active_market_rows(evidence)
    assert [row["ticker"] for row in rows] == [
        "KXHIGHCHI-26AUG23-B76.5",
        "KXHIGHCHI-26AUG23-T81",
    ]


def test_active_market_rows_rejects_active_market_outside_fixed_series() -> None:
    evidence: dict[str, object] = {
        "pages": [
            {
                "payload": {
                    "markets": [
                        {
                            "ticker": "KXHIGHNY-26AUG23-T81",
                            "event_ticker": "KXHIGHNY-26AUG23",
                            "status": "active",
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(M27RScanError, match="outside the fixed series"):
        _active_market_rows(evidence)


def test_run_directory_and_json_are_private_and_create_only(tmp_path: Path) -> None:
    run_dir = _create_run_dir(tmp_path / "evidence", datetime(2026, 8, 23, 3, 5, 6, tzinfo=UTC))
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700

    artifact = run_dir / "summary.json"
    _write_json_create_only(artifact, {"classification": "SUCCESS"})
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        _write_json_create_only(artifact, {"classification": "DIFFERENT"})


def test_script_import_graph_has_no_credentials_risk_authorization_or_execution() -> None:
    source = Path("scripts/run_m27r_weather_candidate_scan.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = (
        "services.kalshi_account_gateway.auth",
        "services.kalshi_account_gateway.production_read_credentials",
        "services.production_execution",
        "services.risk_engine.authorization",
        "services.supervised_canary.live_read_acceptance",
    )
    assert not any(
        module.startswith(prefix) for module in imported for prefix in forbidden_prefixes
    )


def test_script_contains_no_mutating_exchange_method_literals() -> None:
    source = Path("scripts/run_m27r_weather_candidate_scan.py").read_text()
    for token in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{token}"' not in source
        assert f"'{token}'" not in source
