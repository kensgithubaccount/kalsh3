"""M27R public-evidence adapter tests: deterministic and network-free."""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.market_universe.m27e_public_acceptance import SCHEMA as M27E_SCHEMA
from services.market_universe.public_read import HOST
from services.supervised_canary.m27r_public_adapter import (
    GetOnlyPublicEvidenceProvider,
    M27RPublicAdapterError,
    _record_number_for_route,
)
from tests.test_m27n2_candidate_packet import _extraction_2026

NOW = datetime(2026, 8, 20, 3, 10, tzinfo=UTC)
MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "services"
    / "supervised_canary"
    / "m27r_public_adapter.py"
)


def _raw_grib():
    return parse_wgrib2_max_t_evidence(
        _extraction_2026(),
        raw_grib_sha256="m27r-test-raw",
        extraction_sha256="m27r-test-extraction",
    )


def _provider(tmp_path: Path, **changes: Any) -> GetOnlyPublicEvidenceProvider:
    values: dict[str, Any] = {
        "raw_grib_evidence": _raw_grib(),
        "population_artifact_payload": {},
        "population_training_start": date(2024, 1, 1),
        "population_training_end": date(2025, 6, 30),
        "requested_quantity": Decimal(1),
        "output_dir": tmp_path,
    }
    values.update(changes)
    return GetOnlyPublicEvidenceProvider(**values)


def test_contract_date_binds_to_exact_grib_record() -> None:
    raw = _raw_grib()

    assert (
        _record_number_for_route(
            raw_grib=raw,
            local_date=date(2026, 8, 20),
            timezone="America/Chicago",
        )
        == 1
    )
    assert (
        _record_number_for_route(
            raw_grib=raw,
            local_date=date(2026, 8, 21),
            timezone="America/Chicago",
        )
        == 2
    )
    assert (
        _record_number_for_route(
            raw_grib=raw,
            local_date=date(2026, 8, 22),
            timezone="America/Chicago",
        )
        == 3
    )


def test_contract_date_without_exact_grib_record_fails_closed() -> None:
    with pytest.raises(M27RPublicAdapterError, match="does not bind to exactly one GRIB record"):
        _record_number_for_route(
            raw_grib=_raw_grib(),
            local_date=date(2026, 8, 23),
            timezone="America/Chicago",
        )


def test_non_one_contract_request_rejected_before_public_read(tmp_path: Path) -> None:
    calls = 0

    def forbidden_public_read(**_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("public read must not run")

    provider = _provider(
        tmp_path,
        requested_quantity=Decimal(2),
        public_acceptance_acquirer=forbidden_public_read,
    )

    with pytest.raises(M27RPublicAdapterError, match="exactly one contract"):
        provider.collect_public_evidence(now=NOW)

    assert calls == 0


def test_empty_complete_scope_persists_m27e_and_returns_no_candidates(tmp_path: Path) -> None:
    public: dict[str, object] = {
        "schema": M27E_SCHEMA,
        "host": "https://" + HOST,
        "started_at": NOW.isoformat(),
        "exchange_status": {"classification": "SUCCESS", "payload": {}},
        "series": {
            "classification": "SUCCESS",
            "payload": {"series": {"ticker": "KXHIGHCHI"}},
        },
        "markets": {
            "classification": "SUCCESS",
            "pages": [
                {
                    "classification": "SUCCESS",
                    "payload": {"markets": [], "cursor": ""},
                }
            ],
            "pagination_complete": True,
            "market_count": 0,
            "total_returned": 0,
        },
    }

    provider = _provider(
        tmp_path,
        public_acceptance_acquirer=lambda **_: public,
    )
    evidence = provider.collect_public_evidence(now=NOW)

    assert evidence.markets == ()
    assert evidence.candidate_inputs == ()
    persisted = json.loads(evidence.public_evidence_path.read_text())
    assert persisted == public
    assert persisted["schema"] == M27E_SCHEMA


def test_successful_snapshot_without_body_hash_is_rejected(tmp_path: Path) -> None:
    success_without_hash = SimpleNamespace(succeeded=True, body_sha256=None)
    success_with_hash = SimpleNamespace(succeeded=True, body_sha256="present")
    provider = _provider(
        tmp_path,
        market_snapshot_acquirer=cast(Any, lambda *_args, **_kwargs: success_without_hash),
        event_snapshot_acquirer=cast(Any, lambda *_args, **_kwargs: success_with_hash),
        orderbook_snapshot_acquirer=cast(Any, lambda *_args, **_kwargs: success_with_hash),
    )

    with pytest.raises(M27RPublicAdapterError, match="missing retained body hash"):
        provider._build_market_slice(
            now=NOW,
            market_ticker="KXHIGHCHI-TEST",
            event_ticker="KXHIGHCHI-EVENT",
            series_raw={},
        )


def test_naive_clock_is_rejected_before_public_read(tmp_path: Path) -> None:
    calls = 0

    def forbidden_public_read(**_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("public read must not run")

    provider = _provider(tmp_path, public_acceptance_acquirer=forbidden_public_read)

    with pytest.raises(M27RPublicAdapterError, match="timezone-aware"):
        provider.collect_public_evidence(now=datetime(2026, 8, 20, 3, 10))

    assert calls == 0


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_public_adapter_has_no_account_credential_or_mutation_capability() -> None:
    source = MODULE_PATH.read_text()
    imported = _imported_modules(ast.parse(source))
    forbidden_prefixes = (
        "services.kalshi_account_gateway",
        "services.production_execution",
        "services.risk_engine.authorization",
        "subprocess",
        "requests",
        "socket",
        "ssl",
    )
    assert not any(
        module.startswith(prefix) for module in imported for prefix in forbidden_prefixes
    )

    forbidden_tokens = (
        "ProtectedWriteCredentialStore",
        "SignAndSendBoundary",
        "m27o_operator",
        "m27o_live_canary",
        '"POST"',
        "'POST'",
        '"PUT"',
        "'PUT'",
        '"PATCH"',
        "'PATCH'",
        '"DELETE"',
        "'DELETE'",
    )
    for token in forbidden_tokens:
        assert token not in source, token
