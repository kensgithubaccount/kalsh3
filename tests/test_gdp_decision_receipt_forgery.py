from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.forward_reality.trial_ledger import TrialLedger
from services.production_gdp_strategy import one_decision as od
from services.production_gdp_strategy.gdp_persistence import (
    DecisionArchive,
    register_gdp_attempt,
)


def _receipt() -> od.DecisionReceipt:
    issue, _restore, _archive_restore, _validate = od._make_decision_issuer()
    values = {
        "trial_id": "fixture-trial",
        "underlying_event_id": "fixture-event",
        "decision_id": "fixture-decision",
        "policy_version": od.POLICY_VERSION,
        "policy_hash": od._policy_hash(),
        "entry_rule_version": od.ENTRY_RULE_VERSION,
        "entry_rule_hash": od.stable_hash((od.ENTRY_RULE_VERSION, str(od.GATE))),
        "tradability_protocol_version": od.TRADABILITY_PROTOCOL_VERSION,
        "tradability_protocol_hash": od.stable_hash(
            (od.TRADABILITY_PROTOCOL_VERSION, od.MAX_STATUS_BOOK_STATUS_WINDOW_MS)
        ),
        "decision_timestamp": datetime.now(UTC),
        "pipeline_start_timestamp": datetime.now(UTC),
        "pipeline_completion_timestamp": datetime.now(UTC),
        "schedule_id": "UNAVAILABLE",
        "schedule_gate_passed": False,
        "gdpnow_acquisition_id": "UNAVAILABLE",
        "gdpnow_vintage_id": "UNAVAILABLE",
        "market_before_id": "UNAVAILABLE",
        "orderbook_id": "UNAVAILABLE",
        "market_after_id": "UNAVAILABLE",
        "fee_id": "UNAVAILABLE",
        "selected_market_ticker": "UNAVAILABLE",
        "selected_threshold": od.ZERO,
        "signal_side": None,
        "entry_price": None,
        "quantity": od.ONE,
        "depth_at_entry": od.ZERO,
        "entry_fee": None,
        "all_in_debit": None,
        "entry_gate_value": od.GATE,
        "entry_gate_passed": False,
        "classification": od.DecisionClass.EVIDENCE_INCOMPLETE,
        "observation_window_elapsed_ms": 0,
        "whole_window_inside_open_close": False,
        "whole_window_before_cutoff": False,
        "active_before": False,
        "active_after": False,
        "research_only": True,
        "production_influence": od.ZERO,
    }
    return issue(values, None)


def test_canonical_generic_issuer_and_restore_are_not_module_attributes() -> None:
    assert not callable(getattr(od, "_issue_live_decision", None))
    assert not callable(getattr(od, "_restore_decision_from_authenticated_record", None))
    assert not hasattr(od, "_restore_authenticated_decision")
    assert not callable(getattr(od, "_incomplete_receipt", None))
    assert not callable(getattr(od, "_restore_archived_decision", None))


def test_direct_and_copied_receipts_fail_closed() -> None:
    with pytest.raises(od.DecisionError):
        od.DecisionReceipt()
    original = _receipt()
    forged = object.__new__(od.DecisionReceipt)
    for field in od.fields(od.DecisionReceipt):
        object.__setattr__(forged, field.name, getattr(original, field.name))
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)


def test_payload_hash_and_attacker_key_mac_cannot_bless_receipt() -> None:
    original = _receipt()
    forged = object.__new__(od.DecisionReceipt)
    for field in od.fields(od.DecisionReceipt):
        object.__setattr__(forged, field.name, getattr(original, field.name))
    object.__setattr__(forged, "payload_hash", original.payload_hash)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)
    assert not hasattr(od, "_restore_decision_from_authenticated_record")


def test_factory_receipts_are_isolated_from_canonical_validation() -> None:
    issue, _restore, _archive_restore, _isolated_validate = od._make_decision_issuer()
    original = _receipt()
    values = {
        field.name: getattr(original, field.name)
        for field in od.fields(od.DecisionReceipt)
        if field.name not in {"payload_hash", "bundle"}
    }
    receipt = issue(values, None)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(receipt)


def test_attacker_created_archive_cannot_replay_without_ledger_authority(tmp_path) -> None:
    archive = DecisionArchive(tmp_path / "decisions")
    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(object(), archive, "attacker-trial")


def test_attacker_created_ledger_archive_pair_without_public_record_cannot_replay(tmp_path) -> None:
    ledger = TrialLedger(tmp_path / "ledger.sqlite")
    trial = register_gdp_attempt(ledger)
    archive = DecisionArchive(tmp_path / "decisions")
    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(ledger, archive, trial.trial_id)
