import pytest

from services.production_execution.boundary import ProductionExecutionBoundary


def test_m15_begins_disarmed_without_credential_or_signer() -> None:
    boundary = ProductionExecutionBoundary()
    assert not boundary.credential.installed
    assert not hasattr(boundary, "signer")
    with pytest.raises(PermissionError, match="DISARMED"):
        boundary.preflight()


def test_initial_boundary_has_no_injectable_signer_slot() -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        ProductionExecutionBoundary(signer=object())  # type: ignore[call-arg]
