"""CPI-E1-P7R regressions: identity-safe issuance registry.

These tests exist because the prior ``dict[int, str]`` registry keyed by bare
``id()`` could let a reconstructed/forged ``KalshiHistoricalAcquisitionEvidence``
inherit a stale, unrelated object's issuance authority once CPython reused its
memory address. The registry now stores the *object itself* in a
``WeakValueDictionary`` keyed by ``id()``, and the accept/reject decision is an
``is`` identity check against the retrieved value -- never a value-equality or
bare-address comparison. See docs/reviews/CPI_E1_P7R_ISSUANCE_IDENTITY_REPAIR.md.
"""

from __future__ import annotations

import copy
import gc

import pytest

import services.forecasting.cpi_settlement_reconciliation as reconciliation
from services.forecasting.cpi_settlement_reconciliation import (
    CPISettlementReconciliationError,
    KalshiHistoricalAcquisitionEvidence,
    load_frozen_kalshi_acquisition,
    validate_kalshi_acquisition,
)


def test_an_issued_object_validates() -> None:
    """Requirement 1: a genuinely issued object still validates."""
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    validate_kalshi_acquisition(acquisition)  # must not raise


def test_copy_copy_of_issued_object_does_not_validate() -> None:
    """Requirement 2 / 4: a value-equal shallow copy is not the issued object."""
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    reconstructed = copy.copy(acquisition)
    assert reconstructed is not acquisition
    assert reconstructed == acquisition  # value-equal, by construction
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(reconstructed)


def test_object_new_reconstruction_does_not_validate() -> None:
    """Requirement 3: bypassing __init__ entirely never touches the registry."""
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    forged = object.__new__(KalshiHistoricalAcquisitionEvidence)
    for name in KalshiHistoricalAcquisitionEvidence.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(acquisition, name))
    assert forged == acquisition
    assert forged is not acquisition
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(forged)


def test_identical_value_equal_reconstructed_object_does_not_validate() -> None:
    """Requirement 4: exact field-for-field equality is not sufficient authority."""
    acquisition = load_frozen_kalshi_acquisition("market-dec")
    forged = object.__new__(KalshiHistoricalAcquisitionEvidence)
    for name in KalshiHistoricalAcquisitionEvidence.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(acquisition, name))
    assert forged == acquisition
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(forged)


def test_mutation_with_recomputed_public_fingerprint_does_not_validate() -> None:
    """Requirement 4: a caller who recomputes a coherent fingerprint still fails,
    because the object was never the exact instance the registry issued."""
    acquisition = load_frozen_kalshi_acquisition("market-jan")
    forged = object.__new__(KalshiHistoricalAcquisitionEvidence)
    for name in KalshiHistoricalAcquisitionEvidence.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(acquisition, name))
    mutated_body = acquisition.raw_response + b" "
    object.__setattr__(forged, "raw_response", mutated_body)
    object.__setattr__(forged, "byte_count", len(mutated_body))
    recomputed = reconciliation._kalshi_acquisition_fingerprint(forged)
    object.__setattr__(forged, "issuance_fingerprint", recomputed)
    object.__setattr__(forged, "evidence_id", recomputed)
    with pytest.raises(CPISettlementReconciliationError):
        validate_kalshi_acquisition(forged)


def test_dead_registry_entry_is_pruned_and_cannot_bless_a_new_object() -> None:
    """Requirement 5 / 6: once the original object is collected, its registry
    entry is gone -- there is nothing left for a reused address to inherit."""
    acquisition = load_frozen_kalshi_acquisition("market-jul")
    issued_id = id(acquisition)
    assert reconciliation._ISSUED_KALSHI_ACQUISITION_EVIDENCE.get(issued_id) is acquisition
    del acquisition
    gc.collect()
    assert issued_id not in reconciliation._ISSUED_KALSHI_ACQUISITION_EVIDENCE
    assert len(reconciliation._ISSUED_KALSHI_ACQUISITION_EVIDENCE) == 0


def test_repeated_construct_discard_cycles_never_falsely_validate() -> None:
    """Requirement 5 / 6: heavy construct/discard churn -- the exact pattern
    that exposed the original bug under M27B.2's larger test suite -- must
    never let a reconstructed object validate, and the registry must never
    accumulate unbounded stale authority."""
    for _ in range(2000):
        acquisition = load_frozen_kalshi_acquisition("market-jul")
        reconstructed = copy.copy(acquisition)
        with pytest.raises(CPISettlementReconciliationError):
            validate_kalshi_acquisition(reconstructed)
        del acquisition, reconstructed
    gc.collect()
    assert len(reconciliation._ISSUED_KALSHI_ACQUISITION_EVIDENCE) == 0


def test_legitimate_fixture_reload_still_validates_for_every_fixture() -> None:
    """Requirement 7: frozen reviewed fixture reload is unaffected."""
    for fixture_id in (
        "market-jul",
        "market-dec",
        "market-jan",
        "event-jul",
        "event-dec",
        "event-jan",
        "series",
        "contract-terms",
    ):
        validate_kalshi_acquisition(load_frozen_kalshi_acquisition(fixture_id))  # must not raise


@pytest.mark.parametrize(
    "fixture_id,expected_result,expected_value",
    [
        ("market-jul", "YES", 1),
        ("market-dec", "YES", 1),
        ("market-jan", "YES", 1),
    ],
)
def test_p7_matched_settlement_labels_are_unchanged(
    fixture_id: str, expected_result: str, expected_value: int
) -> None:
    """Requirement 9: the three P7 MATCHED historical labels are untouched by
    the issuance-identity repair -- this locks in the exact exchange-final
    result/value pinned in docs/reviews/CPI_E1_P7_SETTLEMENT_RECONCILIATION.md."""
    from services.forecasting.cpi_settlement_reconciliation import KalshiFinalizedEvidence

    acquisition = load_frozen_kalshi_acquisition(fixture_id)
    exchange = KalshiFinalizedEvidence.from_acquisition(acquisition)
    assert exchange.determination.result == expected_result
    assert exchange.determination.settlement_value_dollars == expected_value
