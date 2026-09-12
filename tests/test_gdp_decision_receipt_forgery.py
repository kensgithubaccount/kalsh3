"""Adversarial coverage for the DecisionReceipt issuer/restoration boundary.

Blocker 3: no module-visible capability, generic helper, or registry can
bless an unissued DecisionReceipt, and restoration is authoritative only with
genuine issuer MAC authentication -- never from a caller-authored,
self-consistent mapping alone.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.production_gdp_strategy import one_decision as od


def _live_receipt() -> od.DecisionReceipt:
    sample = od._ClockSample(datetime.now(UTC), 1)
    return od._incomplete_receipt(sample)


def _copied_fields(receipt: od.DecisionReceipt) -> od.DecisionReceipt:
    forged = object.__new__(od.DecisionReceipt)
    for field in od.fields(od.DecisionReceipt):
        object.__setattr__(forged, field.name, getattr(receipt, field.name))
    return forged


def test_direct_constructor_is_rejected() -> None:
    with pytest.raises(od.DecisionError):
        od.DecisionReceipt(values={}, bundle=None, _capability=od._ISSUER)
    with pytest.raises(od.DecisionError):
        od.DecisionReceipt()


def test_object_new_plus_copied_fields_remains_unissued() -> None:
    forged = _copied_fields(_live_receipt())
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)


def test_self_computed_payload_hash_is_insufficient() -> None:
    receipt = _live_receipt()
    forged = _copied_fields(receipt)
    payload_values = {
        field.name: getattr(receipt, field.name)
        for field in od.fields(od.DecisionReceipt)
        if field.name not in {"payload_hash", "bundle"}
    }
    # The attacker has full access to the pure hash function and every
    # plaintext field, so they can recompute the exact same payload_hash the
    # real issuer would -- and it must still not help.
    recomputed = od.stable_hash(tuple(sorted((k, str(v)) for k, v in payload_values.items())))
    assert recomputed == receipt.payload_hash
    object.__setattr__(forged, "payload_hash", recomputed)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)


def test_former_issuer_capability_cannot_bless_a_receipt() -> None:
    # `_ISSUER` still exists (the unrelated evidence classes use it) but has
    # zero effect on DecisionReceipt now: the constructor rejects all args.
    with pytest.raises(od.DecisionError):
        od.DecisionReceipt(
            values={"classification": od.DecisionClass.TRADE_YES},
            bundle=None,
            _capability=od._ISSUER,
        )


def test_direct_registration_helper_cannot_bless_a_forged_receipt() -> None:
    receipt = _live_receipt()
    forged = _copied_fields(receipt)
    object.__setattr__(forged, "classification", od.DecisionClass.TRADE_YES)
    # `_register_issued`/`_ISSUED` are the shared evidence-class registry, not
    # the DecisionReceipt issuer's closure-private one: writing to them has no
    # effect on `validate_decision_receipt`.
    od._register_issued(forged, forged.payload_hash)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)


def test_direct_restore_helper_invocation_without_real_key_is_rejected() -> None:
    values = {
        "classification": od.DecisionClass.EVIDENCE_INCOMPLETE,
        "research_only": True,
        "production_influence": Decimal("0"),
    }
    with pytest.raises(od.DecisionError):
        od._restore_decision_from_authenticated_record(
            values=values,
            record_without_mac={"trial_id": "attacker-chosen"},
            claimed_mac=hashlib.sha256(b"guess").hexdigest(),
            signing_key=b"\x00" * 32,
            canonicalize=lambda value: repr(value).encode(),
        )


def test_caller_created_authenticated_looking_mapping_is_rejected() -> None:
    """A caller-authored, self-consistent JSON+hash without issuer
    authentication must remain non-authoritative, even if it "looks" signed
    with a plausible (but not the archive's real) key."""
    record_without_mac = {"trial_id": "t", "underlying_event_id": "e", "payload_hash": "deadbeef"}

    def canonicalize(value: object) -> bytes:
        return repr(value).encode()

    forged_mac = hmac.new(
        b"\x02" * 32, canonicalize(record_without_mac), hashlib.sha256
    ).hexdigest()
    with pytest.raises(od.DecisionError):
        od._restore_decision_from_authenticated_record(
            values={
                "classification": od.DecisionClass.EVIDENCE_INCOMPLETE,
                "research_only": True,
                "production_influence": Decimal("0"),
            },
            record_without_mac=record_without_mac,
            claimed_mac=forged_mac,
            signing_key=b"\x01" * 32,  # attacker's guessed key != the real one above
            canonicalize=canonicalize,
        )


@pytest.mark.parametrize(
    "poison",
    [
        {"classification": od.DecisionClass.TRADE_YES},
        {"research_only": False},
        {"production_influence": Decimal("1")},
    ],
)
def test_restore_rejects_non_incomplete_or_non_research_only_values_even_with_valid_mac(
    poison: dict[str, object],
) -> None:
    """Changed classification / research_only / production_influence must fail
    closed even when signed with the genuine key -- the restoration
    checkpoint invariants are enforced independently of MAC authenticity."""
    key = b"\x03" * 32

    def canonicalize(value: object) -> bytes:
        return repr(value).encode()

    values: dict[str, object] = {
        "classification": od.DecisionClass.EVIDENCE_INCOMPLETE,
        "research_only": True,
        "production_influence": Decimal("0"),
    }
    values.update(poison)
    record_without_mac = {"trial_id": "t"}
    valid_mac = hmac.new(key, canonicalize(record_without_mac), hashlib.sha256).hexdigest()
    with pytest.raises(od.DecisionError):
        od._restore_decision_from_authenticated_record(
            values=values,
            record_without_mac=record_without_mac,
            claimed_mac=valid_mac,
            signing_key=key,
            canonicalize=canonicalize,
        )


def test_restore_with_genuinely_valid_mac_succeeds_and_validates() -> None:
    """Positive control: the MAC gate is real (not merely always rejecting)."""
    receipt = _live_receipt()
    values = {
        field.name: getattr(receipt, field.name)
        for field in od.fields(od.DecisionReceipt)
        if field.name not in {"payload_hash", "bundle"}
    }
    key = b"\x04" * 32

    def canonicalize(value: object) -> bytes:
        return repr(value).encode()

    record_without_mac = {"trial_id": receipt.trial_id}
    valid_mac = hmac.new(key, canonicalize(record_without_mac), hashlib.sha256).hexdigest()
    restored = od._restore_decision_from_authenticated_record(
        values=values,
        record_without_mac=record_without_mac,
        claimed_mac=valid_mac,
        signing_key=key,
        canonicalize=canonicalize,
    )
    od.validate_decision_receipt(restored)
    assert restored.payload_hash == receipt.payload_hash
    assert restored is not receipt


def test_no_module_visible_generic_decision_receipt_blessing_names() -> None:
    assert not hasattr(od, "_restore_authenticated_decision")
    assert not hasattr(od, "_bless_decision_receipt")


def test_module_visible_issuer_and_registry_remain_for_evidence_only() -> None:
    # Retained only for the unrelated, fixture-only evidence classes and
    # outcome.py's disposable settlement observation. Writing to them cannot
    # bless a DecisionReceipt (covered above).
    assert hasattr(od, "_ISSUER")
    assert hasattr(od, "_register_issued")
