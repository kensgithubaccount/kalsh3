"""Regression for EvaluationPlan substitution before or after GDP bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_gdp_public_composition_persistence import _copy_package

_ATTACK = r"""
import json
import socket
import sys
from pathlib import Path

def no_network(*args, **kwargs):
    raise AssertionError("ledger identity tests must not acquire evidence")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network

import services.forward_reality.trial_ledger as tl

genuine_plan_type = tl.EvaluationPlan

class FakeEvaluationPlan:
    def __init__(self, value):
        self.value = value
        self.identity = "forged-evaluation-identity"

if sys.argv[1] == "after":
    from services.production_gdp_strategy import gdp_persistence as gp

tl.EvaluationPlan = FakeEvaluationPlan
from services.production_gdp_strategy import gdp_persistence as gp

ledger, archive = gp.open_default_research_storage()
root = Path.cwd() / ".kalsh3-gdp-research"

def snapshot():
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

before = snapshot()
try:
    gp.register_gdp_attempt(ledger)
except gp.GDPPersistenceError as exc:
    assert "evaluation plan must be an EvaluationPlan" in str(exc), str(exc)
else:
    raise AssertionError("forged plan obtained genuine journal authority")
assert snapshot() == before, "rejection modified durable ledger state"

# A rejected attack must leave the canonical ledger usable.
tl.EvaluationPlan = genuine_plan_type
trial = gp.register_gdp_attempt(ledger)
assert type(trial.definition.evaluation_plan) is genuine_plan_type
print(json.dumps({
    "trial_id": trial.trial_id,
    "plan_id": trial.definition.evaluation_plan.identity,
}))
"""

_REOPEN = r"""
import json
import socket
import sys

def no_network(*args, **kwargs):
    raise AssertionError("reopen must not acquire evidence")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network

from services.production_gdp_strategy import gdp_persistence as gp
ledger, archive = gp.open_default_research_storage()
expected = json.loads(sys.argv[1])
trial = ledger.get(expected["trial_id"])
assert trial.definition.evaluation_plan.identity == expected["plan_id"]
assert ledger.unique_underlying_event_count() == 1
print("clean reopen passed")
"""


@pytest.mark.parametrize("import_order", ["before", "after"])
def test_fake_plan_cannot_poison_canonical_gdp_ledger(tmp_path: Path, import_order: str) -> None:
    package_root = _copy_package(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    attack = subprocess.run(
        [sys.executable, "-c", _ATTACK, import_order],
        cwd=package_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert attack.returncode == 0, attack.stderr
    reopen = subprocess.run(
        [sys.executable, "-c", _REOPEN, attack.stdout],
        cwd=package_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert reopen.returncode == 0, reopen.stderr


def test_public_plan_rebinding_cannot_replace_replay_constructor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.forward_reality.trial_ledger as tl

    genuine_plan_type = tl.EvaluationPlan
    plan = genuine_plan_type({"split": "prospective", "horizon": 1})
    path = tmp_path / "ledger.sqlite"
    ledger = tl.TrialLedger(path)

    class FakeEvaluationPlan:
        def __init__(self, value: object) -> None:
            raise AssertionError("ledger replay invoked the rebound public constructor")

    monkeypatch.setattr(tl, "EvaluationPlan", FakeEvaluationPlan)
    trial = ledger.register(
        candidate_family="D1-G2",
        model_identity="identity-regression",
        feature_specification_identity="features-v1",
        evaluation_plan=plan,
        underlying_event_id="fixture-event",
        reason="regression test",
    )
    restored = tl.TrialLedger(path).get(trial.trial_id)
    assert type(restored.definition.evaluation_plan) is genuine_plan_type
    assert restored.definition.evaluation_plan.identity == plan.identity
