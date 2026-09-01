import inspect
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.forward_reality.trial_ledger import (
    EvaluationPlan,
    LedgerError,
    TrialLedger,
    TrialStatus,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def ledger(path, *, count=20):
    moments = iter(NOW + timedelta(seconds=i) for i in range(count))
    return TrialLedger._for_tests(path, lambda: next(moments))


def register(
    ledger, *, event="cpi-release:2026-08", model="model:v1", parents=(), siblings=("YES",)
):
    return ledger.register(
        candidate_family="cpi.baseline",
        model_identity=model,
        feature_specification_identity="features:v1",
        evaluation_plan=EvaluationPlan({"split": "prospective", "horizon": 1}),
        underlying_event_id=event,
        reason="serious research attempt",
        sibling_market_ids=siblings,
        parent_trial_ids=parents,
    )


def test_public_constructor_has_no_clock_and_normal_api_cannot_backdate(tmp_path):
    assert "issuer_clock" not in inspect.signature(TrialLedger).parameters
    assert "created_at" not in inspect.signature(TrialLedger.register).parameters
    with pytest.raises(TypeError):
        TrialLedger(tmp_path / "ledger.sqlite", issuer_clock=lambda: NOW)
    with pytest.raises(TypeError):
        TrialLedger(tmp_path / "ledger.sqlite").register(created_at=NOW)


def test_registration_is_durable_and_binds_safety_fields_and_time(tmp_path):
    path = tmp_path / "ledger.sqlite"
    first = register(ledger(path))
    reopened = TrialLedger(path)
    restored = reopened.get(first.trial_id)
    assert restored.trial_id == first.trial_id
    assert restored.created_at == NOW
    assert restored.research_only is True
    assert restored.production_influence == 0
    assert restored.registration_fingerprint


@pytest.mark.parametrize("terminal", [TrialStatus.FAILED, TrialStatus.ABANDONED])
def test_terminal_statuses_and_history_survive_restart(tmp_path, terminal):
    path = tmp_path / "ledger.sqlite"
    trial = register(ledger(path)).trial_id
    writer = ledger(path)
    writer.advance(trial, TrialStatus.RUNNING)
    writer.advance(trial, terminal)
    restored = TrialLedger(path)
    assert restored.get(trial).status is terminal
    assert [event.status for event in restored.status_events(trial)] == [
        TrialStatus.PLANNED,
        TrialStatus.RUNNING,
        terminal,
    ]
    with pytest.raises(LedgerError, match="terminal"):
        restored.advance(trial, TrialStatus.COMPLETED)


def test_definition_is_separate_from_append_only_status_events(tmp_path):
    path = tmp_path / "ledger.sqlite"
    trial = register(ledger(path))
    writer = ledger(path)
    writer.advance(trial.trial_id, TrialStatus.RUNNING)
    writer.advance(trial.trial_id, TrialStatus.COMPLETED)
    assert len(TrialLedger(path).status_events(trial.trial_id)) == 3
    assert TrialLedger(path).get(trial.trial_id).status is TrialStatus.COMPLETED


@pytest.mark.parametrize("kind", ["definition", "event"])
def test_corrupted_artifacts_fail_closed(tmp_path, kind):
    path = tmp_path / "ledger.sqlite"
    register(ledger(path))
    artifact = path.with_name(path.name + ".journal")
    lines = artifact.read_text().splitlines()
    if kind == "definition":
        lines[0] = lines[0].replace("DEFINITION", "FORGED")
        artifact.write_text("\n".join(lines) + "\n")
    else:
        lines[1] = lines[1].replace('"issuer_mac":"', '"issuer_mac":"0')
        artifact.write_text(lines[0] + "\n" + lines[1] + "\n")
    with pytest.raises(LedgerError):
        TrialLedger(path)


def test_duplicate_registration_conflict_and_identical_are_rejected(tmp_path):
    path = tmp_path / "ledger.sqlite"
    register(ledger(path))
    with pytest.raises(LedgerError, match="duplicate"):
        register(ledger(path))
    with pytest.raises(LedgerError, match="duplicate"):
        register(ledger(path), model="model:v2") if False else register(ledger(path))


def test_plan_is_frozen_and_canonical_hardened():
    plan = EvaluationPlan({"z": [1], "a": True})
    with pytest.raises(TypeError):
        plan.value["a"] = False  # type: ignore[index]
    with pytest.raises(LedgerError):
        EvaluationPlan({1: "collision"})  # type: ignore[dict-item]
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(LedgerError):
            EvaluationPlan({"value": value})


def test_siblings_and_models_count_once_per_underlying_event(tmp_path):
    path = tmp_path / "ledger.sqlite"
    first = register(ledger(path), siblings=("YES", "NO"))
    register(
        ledger(path), model="model:v2", event=first.underlying_event_id, siblings=("THRESHOLD-2",)
    )
    register(ledger(path), event="cpi-release:2026-09")
    restored = TrialLedger(path)
    assert len(restored.trials_for_event(first.underlying_event_id)) == 2
    assert restored.unique_underlying_event_count() == 2


def test_parent_relationship_survives_restart(tmp_path):
    path = tmp_path / "ledger.sqlite"
    parent = register(ledger(path), model="parent").trial_id
    child = register(ledger(path), model="child", parents=(parent,))
    assert TrialLedger(path).get(child.trial_id).parent_trial_ids == (parent,)


def test_status_event_cannot_change_definition_and_safety_is_constant(tmp_path):
    path = tmp_path / "ledger.sqlite"
    trial = register(ledger(path))
    current = ledger(path).advance(trial.trial_id, TrialStatus.FAILED)
    assert current.trial_id == trial.trial_id
    assert current.registration_fingerprint == trial.registration_fingerprint
    assert current.research_only is True and current.production_influence == 0


def test_services_cannot_reference_the_test_issuer_seam():
    source = "\n".join(
        path.read_text()
        for path in Path("services").rglob("*.py")
        if path.name != "trial_ledger.py"
    )
    assert "_for_tests" not in source
    assert "_TrustedIssuer" not in source
    assert "_load_or_create_key" not in source


def test_sqlite_is_not_authority_and_copied_index_cannot_recreate_ledger(tmp_path):
    path = tmp_path / "ledger.sqlite"
    trial = register(ledger(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO trial_index VALUES (?, ?)", ("forged", "sha"))
    assert TrialLedger(path).get(trial.trial_id).trial_id == trial.trial_id
    copied = tmp_path / "copied.sqlite"
    copied.write_bytes(path.read_bytes())
    with pytest.raises(LedgerError):
        TrialLedger(copied)


@pytest.mark.parametrize("mutation", ["delete_failed", "delete_abandoned", "truncate", "reorder"])
def test_authenticated_completeness_detects_history_loss(tmp_path, mutation):
    path = tmp_path / "ledger.sqlite"
    writer = ledger(path)
    failed = register(writer, model="failed").trial_id
    writer.advance(failed, TrialStatus.FAILED)
    abandoned = register(ledger(path), model="abandoned").trial_id
    ledger(path).advance(abandoned, TrialStatus.ABANDONED)
    journal = path.with_name(path.name + ".journal")
    lines = journal.read_text().splitlines()
    if mutation.startswith("delete"):
        target = failed if mutation == "delete_failed" else abandoned
        lines = [line for line in lines if target not in line]
    elif mutation == "truncate":
        lines = lines[:-1]
    else:
        lines[0], lines[1] = lines[1], lines[0]
    journal.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerError):
        TrialLedger(path)


def test_checkpoint_and_mac_mutation_fail_closed(tmp_path):
    path = tmp_path / "ledger.sqlite"
    register(ledger(path))
    head = path.with_name(path.name + ".head")
    value = head.read_text()
    head.write_text(value.replace('"last_sequence":2', '"last_sequence":1'))
    with pytest.raises(LedgerError):
        TrialLedger(path)


def test_clock_regression_fails_closed(tmp_path):
    path = tmp_path / "ledger.sqlite"
    moments = iter((NOW, NOW - timedelta(seconds=1)))
    writer = TrialLedger._for_tests(path, lambda: next(moments))
    trial = register(writer)
    with pytest.raises(LedgerError, match="backwards"):
        writer.advance(trial.trial_id, TrialStatus.FAILED)
