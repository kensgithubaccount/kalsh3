from copy import copy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.market_universe import router as router_module
from services.market_universe.lifecycle import LifecycleState, ProductType
from services.market_universe.router import (
    FamilyCoverageManifest,
    MarketUniverseRouter,
    UniverseCensusError,
    UniverseCensusManifest,
    UniverseCensusResult,
)

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def series(category: str = "Economics") -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": category,
        "frequency": "daily",
        "settlement_sources": [{"name": "Official Source", "url": "https://example.invalid"}],
    }


def event(category: str = "Economics") -> dict[str, object]:
    return {
        "event_ticker": "KXEVENT",
        "series_ticker": "KXSERIES",
        "title": "Test event",
        "category": category,
    }


def market(ticker: str = "KXEVENT-10", **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": "KXEVENT",
        "title": "Test threshold",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "The market resolves Yes if the official value is at least 10.",
        "price_level_structure": "standard",
        "timezone": "UTC",
        "expiration_time": "2026-08-26T20:00:00Z",
        "volume_fp": "12.00",
        "open_interest_fp": "3.00",
    }
    row.update(changes)
    return row


def census(markets: list[dict[str, object]], **changes: object):
    values: dict[str, object] = {
        "market_rows": markets,
        "event_rows": [event()],
        "series_rows": [series()],
        "source_authority": "captured-public-kalshi",
        "request_locator": "fixture://whole-exchange",
        "response_sha256": "a" * 64,
        "captured_at": CAPTURED_AT,
    }
    values.update(changes)
    return MarketUniverseRouter().census(**values)  # type: ignore[arg-type]


def test_valid_contract_is_deterministically_understood() -> None:
    result = census([market()])
    assert result.manifest.accounted_market_count == 1
    assert result.manifest.state_counts == (("SEMANTICALLY_UNDERSTOOD", 1),)
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert result.records[0].product_type is ProductType.BINARY_EVENT


def test_malformed_market_is_quarantined_not_dropped() -> None:
    malformed = market()
    malformed.pop("ticker")
    result = census([malformed])
    assert result.manifest.input_market_count == 1
    assert result.manifest.accounted_market_count == 1
    assert result.records == ()
    assert len(result.quarantines) == 1
    assert result.quarantines[0].reason == "MARKET_PARSE_FAILURE"


def test_valid_market_and_malformed_sibling_are_both_accounted() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    result = census([market(), malformed])
    assert result.manifest.input_market_count == 2
    assert result.manifest.accounted_market_count == 2
    assert len(result.records) == 1
    assert len(result.quarantines) == 1


def test_duplicate_market_identity_fails_closed() -> None:
    with pytest.raises(UniverseCensusError, match="duplicate market identity"):
        census([market(), market()])


def test_missing_parent_remains_discovered() -> None:
    result = census([market()], event_rows=[])
    record = result.records[0]
    assert record.state is LifecycleState.DISCOVERED
    assert "MISSING_EVENT" in record.semantic_blockers


def test_unknown_product_remains_visible_and_discovered() -> None:
    result = census([market(market_type="mystery")])
    record = result.records[0]
    assert record.product_type is ProductType.UNKNOWN
    assert record.state is LifecycleState.DISCOVERED
    assert "UNKNOWN_PRODUCT" in record.unsupported_reasons


def test_non_event_product_is_hard_gated() -> None:
    result = census([market(product_type="perpetual_future")])
    record = result.records[0]
    assert record.product_type is ProductType.NON_EVENT
    assert record.state is LifecycleState.DISCOVERED
    assert "NON_EVENT_PRODUCT_OUT_OF_DOMAIN" in record.unsupported_reasons


def test_scalar_and_mve_are_not_coerced_to_binary() -> None:
    scalar = census([market(settlement_value_dollars="0.50")]).records[0]
    mve = census([market(mve_collection_ticker="MVE-1")]).records[0]
    assert scalar.product_type is ProductType.SCALAR_OR_PARTIAL
    assert scalar.state is LifecycleState.DISCOVERED
    assert mve.product_type is ProductType.MULTIVARIATE_EVENT
    assert mve.state is LifecycleState.DISCOVERED


def test_unsupported_semantic_grammar_remains_discovered() -> None:
    result = census([market(rules_primary="The official source will decide the outcome.")])
    record = result.records[0]
    assert record.state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in record.semantic_blockers


def test_dataclasses_replace_cannot_promote_router_issued_discovered_record() -> None:
    record = census([market(rules_primary="The official source will decide the outcome.")]).records[
        0
    ]
    assert record.state is LifecycleState.DISCOVERED
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        replace(
            record,
            state=LifecycleState.SEMANTICALLY_UNDERSTOOD,
            semantic_status="VALID",
            semantic_proof_ids=("invented-proof",),
            semantic_blockers=(),
            unsupported_reasons=(),
        )


def test_dataclasses_replace_cannot_inject_supersession_identity() -> None:
    record = census([market()]).records[0]
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        replace(record, supersedes_record_id="invented-prior-record")


def test_unknown_category_is_not_a_discovery_blocker() -> None:
    result = census([market()], event_rows=[event("Unmapped")], series_rows=[series("Unmapped")])
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert result.records[0].advisory_family == "other/unknown"


def test_input_iteration_order_does_not_change_manifest_identity() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    left = census(markets)
    right = census(list(reversed(markets)))
    assert left.manifest.manifest_id == right.manifest.manifest_id
    assert [record.lifecycle_record_id for record in left.records] == [
        record.lifecycle_record_id for record in right.records
    ]


def quote_fields() -> dict[str, object]:
    return {
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "yes_bid_size_fp": "8.00",
        "yes_ask_size_fp": "9.00",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "volume_24h_fp": "7.00",
        "liquidity_dollars": "25.00",
    }


def test_m27b_route_is_composed_but_never_confers_semantic_authority() -> None:
    row = market(
        rules_primary="The official source will decide the outcome.",
        strike_type="greater",
        floor_strike="10",
    )
    record = census([row]).records[0]
    assert record.specialist_route_id is not None
    assert record.specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    assert record.state is LifecycleState.DISCOVERED


def test_family_coverage_is_descriptive_and_contains_no_readiness_score() -> None:
    row = market(**quote_fields())
    result = census([row])
    descriptor = result.coverage_descriptors[0]
    assert descriptor.lifecycle_record_id == result.records[0].lifecycle_record_id
    assert descriptor.volume_24h == 7
    assert descriptor.liquidity == 25
    assert descriptor.yes_bid == Decimal("0.40")
    assert result.coverage_manifest.category_counts == (("Economics", 1),)
    assert result.coverage_manifest.series_counts == (("KXSERIES", 1),)
    assert result.coverage_manifest.recurrence_counts == (("daily", 1),)
    assert result.coverage_manifest.product_counts == (("BINARY_EVENT", 1),)
    assert not hasattr(result.coverage_manifest, "research_readiness_score")
    assert not hasattr(descriptor, "historical_depth")
    assert not hasattr(descriptor, "executable_capacity")
    assert not hasattr(descriptor, "slippage")


def test_invalid_broad_descriptor_is_visible_but_not_a_semantic_blocker() -> None:
    fields = quote_fields()
    fields["yes_bid_dollars"] = "not-a-number"
    row = market(**fields)
    result = census([row])
    record = result.records[0]
    descriptor = result.coverage_descriptors[0]
    assert record.state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert "INVALID_DISCOVERY_QUOTE_INPUT" in record.specialist_route_reasons
    assert "INVALID_DESCRIPTOR_YES_BID_DOLLARS" in descriptor.descriptor_issues


def _previous(result):
    record = result.records[0]
    return {record.market_ticker: record}


def _tampered_previous(record, **changes: object):
    tampered = copy(record)
    for name, value in changes.items():
        object.__setattr__(tampered, name, value)
    return {record.market_ticker: tampered}


def test_arbitrary_previous_object_is_rejected_before_supersession() -> None:
    with pytest.raises(UniverseCensusError, match="must be a MarketLifecycleRecord"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records={"KXEVENT-10": object()},
        )


def test_attribute_compatible_previous_object_cannot_inject_supersession_id() -> None:
    first = census([market()])
    prior = first.records[0]

    class PlausiblePrevious:
        market_ticker = prior.market_ticker
        state = prior.state
        semantic_material_hash = prior.semantic_material_hash
        lifecycle_record_id = "caller-invented-prior-id"

    with pytest.raises(UniverseCensusError, match="must be a MarketLifecycleRecord"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records={prior.market_ticker: PlausiblePrevious()},
        )


def test_tampered_previous_lifecycle_record_id_is_rejected() -> None:
    first = census([market()])
    prior = first.records[0]
    with pytest.raises(UniverseCensusError, match="canonical identity is invalid"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records=_tampered_previous(prior, lifecycle_record_id="caller-invented"),
        )


def test_tampered_previous_content_hash_is_rejected() -> None:
    first = census([market()])
    prior = first.records[0]
    with pytest.raises(UniverseCensusError, match="canonical identity is invalid"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records=_tampered_previous(prior, content_hash="caller-invented"),
        )


def test_tampered_previous_semantic_material_hash_is_rejected() -> None:
    first = census([market()])
    prior = first.records[0]
    with pytest.raises(UniverseCensusError, match="canonical identity is invalid"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records=_tampered_previous(
                prior, semantic_material_hash="caller-invented-material"
            ),
        )


def test_tampered_previous_state_is_rejected() -> None:
    first = census([market()])
    prior = first.records[0]
    with pytest.raises(UniverseCensusError, match="canonical identity is invalid"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records=_tampered_previous(prior, state=LifecycleState.DISCOVERED),
        )


def test_tampered_previous_semantic_proof_ids_are_rejected() -> None:
    first = census([market()])
    prior = first.records[0]
    with pytest.raises(UniverseCensusError, match="canonical identity is invalid"):
        census(
            [market(rules_primary="The official value is at least 11.")],
            previous_records=_tampered_previous(
                prior, semantic_proof_ids=("caller-invented-proof",)
            ),
        )


@pytest.mark.parametrize(
    ("market_changes", "event_rows", "series_rows"),
    [
        ({"rules_primary": "The official value is at least 11."}, [event()], [series()]),
        (
            {},
            [event()],
            [
                {
                    **series(),
                    "settlement_sources": [
                        {"name": "Different Official Source", "url": "https://other.invalid"}
                    ],
                }
            ],
        ),
        ({"settlement_value_dollars": "0.50"}, [event()], [series()]),
        ({"expiration_time": "2026-08-27T20:00:00Z"}, [event()], [series()]),
        (
            {"event_ticker": "KXEVENT2"},
            [
                {
                    **event(),
                    "event_ticker": "KXEVENT2",
                }
            ],
            [series()],
        ),
    ],
)
def test_material_semantic_change_supersedes_prior_record(
    market_changes: dict[str, object],
    event_rows: list[dict[str, object]],
    series_rows: list[dict[str, object]],
) -> None:
    first = census([market()])
    second = census(
        [market(**market_changes)],
        event_rows=event_rows,
        series_rows=series_rows,
        response_sha256="b" * 64,
        previous_records=_previous(first),
    )
    assert second.records[0].supersedes_record_id == first.records[0].lifecycle_record_id
    assert first.records[0].supersedes_record_id is None


def test_price_only_change_does_not_supersede_semantic_proof() -> None:
    first = census([market(**quote_fields())])
    changed = quote_fields()
    changed["yes_bid_dollars"] = "0.41"
    changed["no_ask_dollars"] = "0.59"
    second = census(
        [market(**changed)],
        response_sha256="b" * 64,
        previous_records=_previous(first),
    )
    assert second.records[0].supersedes_record_id is None
    assert second.records[0].semantic_material_hash == first.records[0].semantic_material_hash
    assert second.records[0].lifecycle_record_id != first.records[0].lifecycle_record_id


def test_unchanged_replay_is_identical_even_when_prior_record_is_supplied() -> None:
    first = census([market()])
    second = census([market()], previous_records=_previous(first))
    assert second.records[0].lifecycle_record_id == first.records[0].lifecycle_record_id
    assert second.manifest.manifest_id == first.manifest.manifest_id
    assert second.coverage_manifest.manifest_id == first.coverage_manifest.manifest_id


def test_title_keywords_alone_cannot_promote_unsupported_rules() -> None:
    first = census([market(rules_primary="The official source decides the outcome.")])
    second = census(
        [
            market(
                title="At least 10 according to all the important keywords",
                rules_primary="The official source decides the outcome.",
            )
        ]
    )
    assert first.records[0].state is LifecycleState.DISCOVERED
    assert second.records[0].state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in second.records[0].semantic_blockers


def test_primary_comparison_is_not_invalidated_by_secondary_boilerplate() -> None:
    result = census(
        [
            market(
                title="Will the official value be at least 10?",
                rules_secondary=(
                    "The market closes after the scheduled release. "
                    "The official agency publishes the final value."
                ),
            )
        ]
    )
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert "RULES_COMPARATOR_UNPROVEN" not in result.records[0].semantic_blockers


def test_refused_primary_cannot_fall_back_to_title() -> None:
    result = census(
        [
            market(
                title="Will the official value be at least 10?",
                rules_primary="The NO side prevails if the official value is greater than 10.",
            )
        ]
    )
    assert result.records[0].state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in result.records[0].semantic_blockers


def test_secondary_conflicting_comparison_fails_closed() -> None:
    result = census(
        [
            market(
                rules_secondary="The official value is less than 10 units.",
            )
        ]
    )
    assert result.records[0].state is LifecycleState.DISCOVERED
    assert "RULES_COMPARATOR_UNPROVEN" in result.records[0].semantic_blockers


def test_exchange_category_alone_cannot_promote_weather_without_station() -> None:
    rules = "The temperature is at least 10."
    first = census(
        [market(rules_primary=rules)],
        event_rows=[event("Weather")],
        series_rows=[series("Weather")],
    )
    second = census(
        [market(rules_primary=rules)],
        event_rows=[event("Economics")],
        series_rows=[series("Economics")],
    )
    assert first.records[0].state is LifecycleState.DISCOVERED
    assert second.records[0].state is LifecycleState.DISCOVERED
    assert "WEATHER_STATION_MISSING" in second.records[0].semantic_blockers


def test_derived_family_classifier_cannot_promote_unsupported_market(monkeypatch) -> None:
    from services.market_universe.quality import Family

    monkeypatch.setattr(router_module, "classify", lambda _category, _title: Family.MACRO)
    result = census([market(rules_primary="The official source decides the outcome.")])
    assert result.records[0].advisory_family == "macro"
    assert result.records[0].state is LifecycleState.DISCOVERED


def test_closed_market_is_accounted_and_m27b_status_is_only_specialist_evidence() -> None:
    result = census([market(status="closed")])
    assert result.manifest.accounted_market_count == 1
    assert result.records[0].state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    assert result.records[0].specialist_route_state == "ROUTE_ONLY"
    assert "NON_ACTIVE_MARKET" in result.records[0].specialist_route_reasons


def test_understood_and_unsupported_siblings_both_remain_visible() -> None:
    result = census([market("KXEVENT-10"), market("KXEVENT-MVE", mve_collection_ticker="MVE")])
    assert result.manifest.accounted_market_count == 2
    assert {record.state for record in result.records} == {
        LifecycleState.DISCOVERED,
        LifecycleState.SEMANTICALLY_UNDERSTOOD,
    }


def test_malformed_parent_is_explicit_market_blocker_not_a_silent_drop() -> None:
    bad_event = event()
    bad_event.pop("title")
    result = census([market()], event_rows=[bad_event])
    assert result.manifest.accounted_market_count == 1
    assert result.records[0].state is LifecycleState.DISCOVERED
    assert "INVALID_EVENT_PARENT" in result.records[0].semantic_blockers


def test_duplicate_parent_identity_fails_closed() -> None:
    with pytest.raises(UniverseCensusError, match="duplicate parent identity"):
        census([market()], event_rows=[event(), event()])


def test_caller_cannot_directly_mint_canonical_census_manifest() -> None:
    result = census([market()])
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        UniverseCensusManifest(
            capture=result.capture,
            input_market_count=1,
            records=result.records,
            quarantines=result.quarantines,
        )


def test_caller_cannot_claim_input_count_with_hand_assembled_census_receipts() -> None:
    result = census([market()])
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        UniverseCensusManifest(
            capture=result.capture,
            input_market_count=999,
            records=result.records,
            quarantines=(),
        )


def test_caller_cannot_directly_mint_canonical_coverage_manifest_or_result() -> None:
    result = census([market()])
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        FamilyCoverageManifest(result.manifest, result.coverage_descriptors)
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        UniverseCensusResult(
            result.capture,
            result.records,
            result.quarantines,
            result.manifest,
            result.coverage_descriptors,
            result.coverage_manifest,
        )


def _internal_result_issue(result, **changes: object):
    values: dict[str, object] = {
        "capture": result.capture,
        "records": result.records,
        "quarantines": result.quarantines,
        "manifest": result.manifest,
        "coverage_descriptors": result.coverage_descriptors,
        "coverage_manifest": result.coverage_manifest,
    }
    values.update(changes)
    return UniverseCensusResult._issue(
        capability=router_module._ROUTER_ISSUANCE_CAPABILITY,  # type: ignore[attr-defined]
        **values,  # type: ignore[arg-type]
    )


def _internal_coverage_issue(result, descriptors):
    return FamilyCoverageManifest._issue(
        capability=router_module._ROUTER_ISSUANCE_CAPABILITY,  # type: ignore[attr-defined]
        census_manifest=result.manifest,
        records=result.records,
        descriptors=descriptors,
    )


def test_omitted_coverage_descriptor_fails_canonical_result_issuance() -> None:
    result = census([market("KXEVENT-10"), market("KXEVENT-11")])
    with pytest.raises(UniverseCensusError, match="descriptor-ID mismatch"):
        _internal_result_issue(result, coverage_descriptors=result.coverage_descriptors[:-1])
    with pytest.raises(UniverseCensusError, match="do not exactly match lifecycle records"):
        _internal_coverage_issue(result, result.coverage_descriptors[:-1])


def test_extra_unrelated_coverage_descriptor_fails_canonical_result_issuance() -> None:
    result = census([market("KXEVENT-10")])
    unrelated = census([market("KXEVENT-99")]).coverage_descriptors[0]
    with pytest.raises(UniverseCensusError, match="descriptor-ID mismatch"):
        _internal_result_issue(
            result,
            coverage_descriptors=(*result.coverage_descriptors, unrelated),
        )


def test_descriptor_set_from_another_census_fails_lifecycle_binding() -> None:
    first = census([market()], response_sha256="a" * 64)
    second = census([market()], response_sha256="b" * 64)
    assert first.records[0].market_ticker == second.records[0].market_ticker
    assert first.records[0].lifecycle_record_id != second.records[0].lifecycle_record_id
    with pytest.raises(UniverseCensusError, match="do not exactly match lifecycle records"):
        _internal_coverage_issue(first, second.coverage_descriptors)


def test_records_from_another_capture_fail_canonical_manifest_issuance() -> None:
    first = census([market()], response_sha256="a" * 64)
    second = census([market()], response_sha256="b" * 64)
    with pytest.raises(UniverseCensusError, match="lifecycle capture identity mismatch"):
        UniverseCensusManifest._issue(
            capability=router_module._ROUTER_ISSUANCE_CAPABILITY,  # type: ignore[attr-defined]
            capture=first.capture,
            input_market_count=1,
            records=second.records,
            quarantines=(),
        )


def test_quarantines_from_another_capture_fail_canonical_manifest_issuance() -> None:
    malformed = market()
    malformed.pop("ticker")
    first = census([malformed], response_sha256="a" * 64)
    second = census([malformed], response_sha256="b" * 64)
    with pytest.raises(UniverseCensusError, match="quarantine capture identity mismatch"):
        UniverseCensusManifest._issue(
            capability=router_module._ROUTER_ISSUANCE_CAPABILITY,  # type: ignore[attr-defined]
            capture=first.capture,
            input_market_count=1,
            records=(),
            quarantines=second.quarantines,
        )


def test_manifest_result_lifecycle_id_mismatch_fails() -> None:
    first = census([market("KXEVENT-10")])
    other = census([market("KXEVENT-11")])
    with pytest.raises(UniverseCensusError, match="manifest/result lifecycle-ID mismatch"):
        _internal_result_issue(first, manifest=other.manifest)


def test_manifest_result_quarantine_id_mismatch_fails() -> None:
    first_row = market("KXEVENT-BAD-1")
    first_row.pop("ticker")
    second_row = market("KXEVENT-BAD-2")
    second_row.pop("ticker")
    second_row["title"] = "different malformed input"
    first = census([first_row])
    other = census([second_row])
    assert first.manifest.lifecycle_record_ids == other.manifest.lifecycle_record_ids == ()
    with pytest.raises(UniverseCensusError, match="manifest/result quarantine-ID mismatch"):
        _internal_result_issue(first, manifest=other.manifest)


def test_router_issued_mixed_understood_discovered_and_quarantine_census_succeeds() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("ticker")
    result = census(
        [
            market("KXEVENT-10"),
            market("KXEVENT-UNKNOWN", market_type="mystery"),
            malformed,
        ]
    )
    assert result.manifest.input_market_count == 3
    assert result.manifest.accounted_market_count == 3
    assert len(result.records) == 2
    assert len(result.quarantines) == 1
    assert {record.state for record in result.records} == {
        LifecycleState.DISCOVERED,
        LifecycleState.SEMANTICALLY_UNDERSTOOD,
    }
    assert result.manifest.lifecycle_record_ids == tuple(
        sorted(record.lifecycle_record_id for record in result.records)
    )
    assert result.manifest.quarantine_ids == tuple(
        sorted(item.quarantine_id for item in result.quarantines)
    )
    assert result.coverage_manifest.descriptor_ids == tuple(
        sorted(item.descriptor_id for item in result.coverage_descriptors)
    )


def test_all_ku_a1_outputs_are_research_only_with_zero_influence() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    result = census([market(**quote_fields()), malformed])
    outputs = (
        result.capture,
        result.manifest,
        result.coverage_manifest,
        *result.records,
        *result.quarantines,
        *result.coverage_descriptors,
    )
    assert outputs
    assert all(item.research_only is True for item in outputs)
    assert all(item.production_influence == Decimal("0") for item in outputs)


def test_new_package_cannot_name_later_lifecycle_authority_or_network_clients() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / relative).read_text()
        for relative in (
            "services/market_universe/lifecycle.py",
            "services/market_universe/router.py",
        )
    )
    forbidden_states = (
        "RESEARCHABLE",
        "MODELABLE",
        "ECONOMICALLY_EVALUABLE",
        "SHADOW_ELIGIBLE",
        "TRADE_CANDIDATE",
    )
    forbidden_network = ("requests", "httpx", "websocket", "websockets", "urllib.request")
    assert all(token not in source for token in forbidden_states)
    assert all(token not in source for token in forbidden_network)


def test_router_dependency_graph_cannot_reach_authority_or_execution_packages() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    service_root = root / "services"
    forbidden_prefixes = (
        "services.agent_control_center",
        "services.bounded_autonomy",
        "services.demo_execution",
        "services.execution_simulation",
        "services.kalshi_account_gateway",
        "services.production_execution",
        "services.production_weather_strategy",
        "services.risk_engine",
        "services.supervised_canary",
    )

    def module_path(module: str) -> Path | None:
        candidate = root / (module.replace(".", "/") + ".py")
        if candidate.is_file():
            return candidate
        package = root / module.replace(".", "/") / "__init__.py"
        return package if package.is_file() else None

    def absolute_module(current: str, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        package = current.split(".")[:-1]
        keep = len(package) - (node.level - 1)
        if keep < 1:
            return None
        prefix = package[:keep]
        if node.module:
            prefix.extend(node.module.split("."))
        return ".".join(prefix)

    pending = ["services.market_universe.lifecycle", "services.market_universe.router"]
    visited: set[str] = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        assert not module.startswith(forbidden_prefixes), module
        forbidden_fragments = (".credential", ".portfolio", ".signer", ".order_submission")
        assert not any(fragment in module for fragment in forbidden_fragments), module
        path = module_path(module)
        if path is None or service_root not in path.parents:
            continue
        tree = ast.parse(path.read_text())
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(
                    alias.name for alias in node.names if alias.name.startswith("services.")
                )
            elif isinstance(node, ast.ImportFrom):
                imported = absolute_module(module, node)
                if imported and imported.startswith("services."):
                    imports.add(imported)
                    for alias in node.names:
                        candidate = f"{imported}.{alias.name}"
                        if module_path(candidate) is not None:
                            imports.add(candidate)
        pending.extend(sorted(imports - visited))


def test_coverage_manifest_replays_deterministically_under_input_permutation() -> None:
    markets = [market("KXEVENT-10", **quote_fields()), market("KXEVENT-11")]
    left = census(markets)
    right = census(list(reversed(markets)))
    assert left.coverage_manifest.manifest_id == right.coverage_manifest.manifest_id
    assert [item.descriptor_id for item in left.coverage_descriptors] == [
        item.descriptor_id for item in right.coverage_descriptors
    ]
