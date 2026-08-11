import pytest

from services.production_execution.boundary import ProductionExecutionBoundary


def test_m15_begins_disarmed_without_credential_or_signer() -> None:
    boundary = ProductionExecutionBoundary()
    assert not boundary.production_write_credential_present
    assert boundary.signer is None
    with pytest.raises(PermissionError, match="DISARMED"):
        boundary.preflight()


def test_initial_boundary_rejects_injected_signer() -> None:
    class FakeSigner:
        def sign_bound_request(self, request_hash: str, one_time_capability: bytes) -> bytes:
            del request_hash, one_time_capability
            return b"signature"

    with pytest.raises(ValueError, match="accepts no production"):
        ProductionExecutionBoundary(signer=FakeSigner()).preflight()
