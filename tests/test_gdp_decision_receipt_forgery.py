from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.production_gdp_strategy import one_decision as od


def _receipt() -> od.DecisionReceipt:
    return od._incomplete_receipt(od._ClockSample(datetime.now(UTC), 1))


def test_canonical_generic_issuer_and_restore_are_not_module_attributes() -> None:
    assert not callable(getattr(od, "_issue_live_decision", None))
    assert not callable(getattr(od, "_restore_decision_from_authenticated_record", None))
    assert not hasattr(od, "_restore_authenticated_decision")


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
