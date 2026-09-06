from __future__ import annotations

from scripts.build_e01_acceptance_receipt import _hash, build_receipt


def test_e01_receipt_hash_is_deterministic_and_excludes_only_hash_field() -> None:
    kwargs = {
        "canonical_sha": "a" * 40,
        "canonical_tree": "b" * 40,
        "acceptance_sha": "c" * 40,
        "acceptance_tree": "d" * 40,
        "environment_type": "LOCAL_EPHEMERAL_NO_POSTGRES",
        "api_schema_identity": {"sha256": "e" * 64},
        "authenticated_read": {"status": "BLOCKED_BY_CREDENTIAL"},
        "account_reconciliation": {"status": "FIXTURE_PASS_LIVE_READ_BLOCKED"},
        "postgres_acceptance": {"status": "BLOCKED_NO_NONPRODUCTION_DATABASE"},
        "restart_recovery": {"status": "FIXTURE_PASS_POSTGRES_BLOCKED"},
        "fee_policy": {"status": "CURRENT_FEE_POLICY_COMPATIBLE"},
    }
    first = build_receipt(**kwargs)
    second = build_receipt(**kwargs)
    assert first == second
    unsigned = {key: value for key, value in first.items() if key != "receipt_sha256"}
    assert first["receipt_sha256"] == _hash(unsigned)


def test_e01_receipt_records_zero_production_influence() -> None:
    receipt = build_receipt(
        canonical_sha="a" * 40,
        canonical_tree="b" * 40,
        acceptance_sha="c" * 40,
        acceptance_tree="d" * 40,
        environment_type="LOCAL_EPHEMERAL_NO_POSTGRES",
        api_schema_identity={},
        authenticated_read={},
        account_reconciliation={},
        postgres_acceptance={},
        restart_recovery={},
        fee_policy={},
    )
    assert receipt["safety"] == {
        "live_mutation_count": 0,
        "live_orders_placed": 0,
        "write_credentials_installed": False,
        "production_boundary_armed": False,
        "production_influence": 0,
    }
