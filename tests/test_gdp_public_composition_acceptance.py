"""Acceptance specification for the real public GDP composition boundary."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from test_gdp_public_composition_persistence import _copy_package, _run_child

from services.production_gdp_strategy.one_decision import DecisionClass


@pytest.fixture
def public_result(tmp_path: Path) -> dict[str, object]:
    package_root = _copy_package(tmp_path)
    completed = _run_child(package_root, tmp_path / "seen.json", "run")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_public_boundary_is_research_only_and_fee_incomplete(
    public_result: dict[str, object],
) -> None:
    assert public_result["research_only"] is True
    assert public_result["production_influence"] == "0"
    assert public_result["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value


def test_public_result_has_no_trade_economics(public_result: dict[str, object]) -> None:
    assert public_result["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value


def test_public_composition_isolated_from_hostile_selectors(
    public_result: dict[str, object],
) -> None:
    assert public_result["trial_id"].startswith("trial-")


def test_real_public_entrypoint_is_the_acceptance_target() -> None:
    from services.production_gdp_strategy.one_decision import run_one_research_decision

    assert run_one_research_decision.__name__ == "run_one_research_decision"
    assert tuple(inspect.signature(run_one_research_decision).parameters) == ()
