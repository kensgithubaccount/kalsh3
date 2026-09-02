from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.contract_intelligence.documents import ContractDocumentConnector, DocumentError
from services.contract_intelligence.registry import (
    ContractRegistry,
    RelationshipStatus,
    validate_bins,
)
from services.contract_intelligence.settlement import (
    DeterminationState,
    ExchangeDetermination,
    ReconciliationStatus,
    SettlementQueue,
    SettlementRecord,
    SourceObservation,
)
from services.contract_intelligence.specification import (
    Comparator,
    ContractSpecificationParser,
    IssueType,
    PayoutModel,
    SemanticsInputBundle,
    SemanticStatus,
    normalize_timezone,
    parse_comparison,
    validate_llm_proposal,
)
from services.market_universe.domain import material_hashes

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def bundle(**market_changes: Any) -> SemanticsInputBundle:
    market = {
        "ticker": "M",
        "event_ticker": "E",
        "title": "Will the final temperature be at least 90 F?",
        "yes_sub_title": "Final temperature is 90 F or higher",
        "no_sub_title": "Final temperature is below 90 F",
        "rules_primary": "YES if the final NWS report at station KNYC is at least 90 F.",
        "rules_secondary": (
            "Use the final daily climate report, rounded to the nearest whole degree."
        ),
        "station_code": "KNYC",
        "floor_strike": "90",
        "timezone": "America/New_York",
        "expiration_time": "2026-08-11T23:59:00-04:00",
        "expected_expiration_time": "2026-08-11T23:59:00-04:00",
        "occurrence_datetime": "2026-08-11T00:00:00-04:00",
        "rounding_rules": "nearest whole degree",
        "revision_rules": "final report",
        "correction_rules": "published corrections control",
        "early_close_condition": "none",
        "rules_version_id": "r1",
        "metadata_version_id": "m1",
        "measured_event_or_value": "daily maximum temperature",
        "geographic_scope": "KNYC station",
        "threshold_unit": "F",
        "settlement_value_dollars": None,
        "is_provisional": False,
    }
    market.update(market_changes)
    event = {
        "event_ticker": "E",
        "series_ticker": "S",
        "title": "NYC daily high",
        "category": "Weather",
        "timezone": "America/New_York",
        "settlement_sources": [{"name": "NWS", "url": "https://weather.gov"}],
        "mutually_exclusive": False,
    }
    series = {
        "ticker": "S",
        "title": "Daily weather",
        "category": "Weather",
        "frequency": "daily",
        "settlement_sources": [{"name": "NWS", "url": "https://weather.gov"}],
        "contract_url": "https://kalshi.com/contracts/weather",
        "contract_terms_url": "https://kalshi.com/terms/weather",
        "additional_prohibitions": [],
        "product_metadata": {},
    }
    return SemanticsInputBundle.build(market, event, series)


def test_valid_weather_spec_provenance_hash_and_nonmaterial_price_stability() -> None:
    parser = ContractSpecificationParser()
    one = parser.parse(bundle(), NOW)
    two = parser.parse(bundle(yes_bid_dollars="0.4"), NOW)
    assert (
        one.semantic_status == SemanticStatus.VALID
        and one.payout_model == PayoutModel.SIMPLE_BINARY
        and one.comparator == Comparator.GTE
        and one.threshold_value == Decimal("90")
    )
    assert one.semantic_hash == two.semantic_hash and one.source_input_hash != two.source_input_hash
    assert (one.market_rules_hash, one.market_metadata_hash) == material_hashes(
        bundle().market.fields
    )
    assert (two.market_rules_hash, two.market_metadata_hash) == material_hashes(
        bundle(yes_bid_dollars="0.4").market.fields
    )
    assert {p.field_name for p in one.provenance} >= {
        "yes_proposition",
        "no_proposition",
        "comparator",
        "settlement_authority",
        "deadline",
        "timezone",
    }


def test_comparison_language_corpus() -> None:
    cases = [
        ("above 5", Comparator.GT, "5"),
        ("greater than 5", Comparator.GT, "5"),
        ("more than 5", Comparator.GT, "5"),
        ("at least 5", Comparator.GTE, "5"),
        ("under 5", Comparator.LT, "5"),
        ("below 5", Comparator.LT, "5"),
        ("fewer than 5", Comparator.LT, "5"),
        ("at most 5", Comparator.LTE, "5"),
        ("no more than 5", Comparator.LTE, "5"),
        ("5 or less", Comparator.LTE, "5"),
        ("exactly 5", Comparator.EQ, "5"),
    ]
    for text, expected, value in cases:
        comparator, threshold, _, _, _ = parse_comparison(text)
        assert comparator == expected and threshold == Decimal(value)
    comparator, _, lower, upper, inclusive = parse_comparison("between 5 and 10")
    assert (comparator, lower, upper, inclusive) == (
        Comparator.BETWEEN,
        Decimal(5),
        Decimal(10),
        "inclusive",
    )
    assert parse_comparison("roughly five")[0] == Comparator.NONE


@pytest.mark.parametrize(
    ("text", "expected", "value"),
    [
        ("more than 0.2", Comparator.GT, "0.2"),
        ("greater than 0.2", Comparator.GT, "0.2"),
        ("no more than 0.2", Comparator.LTE, "0.2"),
        ("not more than 0.2", Comparator.LTE, "0.2"),
        ("not greater than 0.2", Comparator.LTE, "0.2"),
        ("less than 0.2", Comparator.LT, "0.2"),
        ("not less than 0.2", Comparator.GTE, "0.2"),
        ("not below 0.2", Comparator.GTE, "0.2"),
    ],
)
def test_polarity_aware_comparison_phrases(text: str, expected: Comparator, value: str) -> None:
    comparator, threshold, _, _, _ = parse_comparison(text)
    assert comparator == expected and threshold == Decimal(value)


@pytest.mark.parametrize(
    "text",
    [
        "not exactly 0.2",
        "not not more than 0.2",
        "more than 0.2 and less than 0.1",
        "more than 0.2 and no more than 0.2",
        "exactly 0.2 or more than 0.3",
        "between 0.1 and 0.3 but below 0.2",
        "If not more than 0.2, the market resolves to No",
        "more than 0.2, but not more than 0.3",
        "not more than 0.2 in Junebug",
        "more than ||",
        "more than NaN",
    ],
)
def test_negated_contradictory_and_malformed_comparisons_are_unsupported(text: str) -> None:
    assert parse_comparison(text)[0] == Comparator.NONE


@pytest.mark.parametrize(
    "text",
    [
        "  NOT   MORE   THAN  0.2  ",
        "not-more-than 0.2",
        "not\tless\tthan 0.2",
    ],
)
def test_punctuation_and_whitespace_do_not_hide_polarity(text: str) -> None:
    comparator = parse_comparison(text)[0]
    assert comparator in {Comparator.NONE, Comparator.LTE, Comparator.GTE}
    assert comparator != Comparator.GT and comparator != Comparator.LT


def test_missing_timezone_station_source_and_conflicts_fail_closed() -> None:
    parser = ContractSpecificationParser()
    base = bundle(timezone=None)
    missing_tz = parser.parse(
        SemanticsInputBundle.build(
            base.market.fields, base.event.fields | {"timezone": None}, base.series.fields
        ),
        NOW,
    )
    assert (
        missing_tz.semantic_status == SemanticStatus.AMBIGUOUS
        and IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in missing_tz.issues}
    )
    missing_station = parser.parse(bundle(station_code=None), NOW)
    assert IssueType.WEATHER_STATION_MISSING in {x.issue_type for x in missing_station.issues}
    raw = bundle()
    raw = SemanticsInputBundle.build(
        raw.market.fields,
        raw.event.fields | {"settlement_sources": []},
        raw.series.fields | {"settlement_sources": []},
    )
    assert parser.parse(raw, NOW).semantic_status != SemanticStatus.VALID
    conflict_bundle = bundle()
    conflict = SemanticsInputBundle.build(
        conflict_bundle.market.fields,
        conflict_bundle.event.fields | {"settlement_sources": [{"name": "NOAA"}]},
        conflict_bundle.series.fields,
    )
    spec = parser.parse(conflict, NOW)
    assert (
        spec.semantic_status == SemanticStatus.CONFLICTING
        and spec.source_precedence_status == "CONFLICT"
    )


def test_threshold_title_strike_and_scalar_mve_provisional() -> None:
    parser = ContractSpecificationParser()
    conflict = parser.parse(bundle(floor_strike="91"), NOW)
    assert IssueType.THRESHOLD_CONFLICT in {x.issue_type for x in conflict.issues}
    scalar = parser.parse(bundle(settlement_value_dollars="0.375"), NOW)
    assert scalar.payout_model == PayoutModel.SCALAR_OR_PARTIAL and not scalar.strategy_supported
    mve = parser.parse(bundle(mve_collection_ticker="MVE"), NOW)
    assert mve.payout_model == PayoutModel.MULTIVARIATE and IssueType.MVE_UNSUPPORTED in {
        x.issue_type for x in mve.issues
    }
    provisional = parser.parse(bundle(is_provisional=True), NOW)
    assert provisional.semantic_hash


def test_timezones_dst_midnight_and_date_without_timezone() -> None:
    assert (
        normalize_timezone("ET") == "America/New_York"
        and normalize_timezone("UTC") == "UTC"
        and normalize_timezone("Mars/Nowhere") is None
    )
    winter = ContractSpecificationParser().parse(
        bundle(expiration_time="2026-01-15T17:00:00-05:00"), NOW
    )
    summer = ContractSpecificationParser().parse(
        bundle(expiration_time="2026-07-15T17:00:00-04:00"), NOW
    )
    assert (
        winter.deadline
        and summer.deadline
        and winter.deadline.utcoffset() == timedelta(0)
        and summer.deadline.utcoffset() == timedelta(0)
    )
    base = bundle(expiration_time="2026-08-11", timezone=None)
    ambiguous = ContractSpecificationParser().parse(
        SemanticsInputBundle.build(
            base.market.fields, base.event.fields | {"timezone": None}, base.series.fields
        ),
        NOW,
    )
    assert ambiguous.semantic_status == SemanticStatus.AMBIGUOUS


def test_registry_immutable_idempotent_stale_and_material_changes() -> None:
    parser = ContractSpecificationParser()
    registry = ContractRegistry()
    one = registry.add(parser.parse(bundle(), NOW))
    same = registry.add(parser.parse(bundle(), NOW))
    assert same.contract_spec_id == one.contract_spec_id and len(registry.versions["M"]) == 1
    changed_bundle = bundle(
        rules_primary="YES if final NWS KNYC report is greater than 91 F.", floor_strike="91"
    )
    changed = registry.add(parser.parse(changed_bundle, NOW))
    assert (
        changed.supersedes_spec_id == one.contract_spec_id
        and registry.versions["M"][0].semantic_status == SemanticStatus.STALE
        and changed.semantic_hash != one.semantic_hash
    )
    registry.invalidate("M", "contract terms changed", NOW)
    assert registry.current("M").semantic_status == SemanticStatus.INVALIDATED  # type: ignore[union-attr]


def test_llm_confidence_cannot_override_blocker() -> None:
    spec = ContractSpecificationParser().parse(
        bundle(settlement_value_dollars="0.125"), NOW, llm_confidence=Decimal("0.99")
    )
    assert validate_llm_proposal(spec).semantic_status == SemanticStatus.UNSUPPORTED


def test_document_allowlist_hash_change_tamper_and_bounds() -> None:
    class Transport:
        value = b"terms v1"

        def get(
            self, url: str, *, timeout_seconds: float, max_bytes: int, follow_redirects: bool
        ) -> tuple[str, bytes]:
            assert not follow_redirects
            return "text/plain", self.value

    transport = Transport()
    connector = ContractDocumentConnector(transport)
    one = connector.retrieve("https://kalshi.com/terms", NOW)
    transport.value = b"terms v2"
    two = connector.retrieve("https://kalshi.com/terms", NOW)
    assert one.content_hash != two.content_hash
    for url in (
        "http://kalshi.com/terms",
        "https://evil.example/terms",
        "https://user:pass@kalshi.com/terms",
    ):
        with pytest.raises(DocumentError):
            connector.retrieve(url, NOW)
    transport.value = b"x" * 20
    with pytest.raises(DocumentError):
        ContractDocumentConnector(transport, max_bytes=10).retrieve("https://kalshi.com/terms", NOW)


def test_settlement_observation_determination_amendment_final_label() -> None:
    observation = SourceObservation("O", "M", "NWS", Decimal("91"), NOW, NOW, "hash", False, None)
    determined = ExchangeDetermination(
        "D1", "M", DeterminationState.DETERMINED, "yes", Decimal("1"), NOW, NOW, "raw1", None
    )
    amended = ExchangeDetermination(
        "D2",
        "M",
        DeterminationState.AMENDED,
        "no",
        Decimal("0"),
        NOW,
        NOW,
        "raw2",
        determined.determination_id,
    )
    assert amended.supersedes_determination_id == "D1" and observation.value == 91
    provisional = SettlementRecord(
        "M",
        "r1",
        "spec",
        "yes",
        Decimal(1),
        NOW,
        None,
        "record",
        observation.observation_id,
        ReconciliationStatus.MATCHED,
    )
    final = replace(provisional, finalized_at=NOW)
    assert not provisional.eligible_training_label and final.eligible_training_label


def test_settlement_queue_not_alphabetical_and_positions_prioritized() -> None:
    queue = SettlementQueue()
    queue.add(
        "ZZZ",
        state="determined",
        has_open_position=True,
        expected_at=NOW,
        now=NOW,
        lifecycle_determined=True,
        stale=False,
    )
    queue.add(
        "AAA",
        state="closed",
        has_open_position=False,
        expected_at=NOW - timedelta(days=1),
        now=NOW,
        lifecycle_determined=False,
        stale=True,
    )
    assert queue.pop(2)[0].market_ticker == "ZZZ"


def test_sibling_bins_complete_gap_overlap() -> None:
    parser = ContractSpecificationParser()
    under = parser.parse(
        bundle(ticker="LOW", title="under 3", rules_primary="under 3", floor_strike=None), NOW
    )
    middle = parser.parse(
        bundle(
            ticker="MID",
            title="between 3 and 5",
            rules_primary="between 3 and 5",
            floor_strike=None,
        ),
        NOW,
    )
    high = parser.parse(
        bundle(ticker="HIGH", title="at least 5", rules_primary="at least 5", floor_strike="5"), NOW
    )
    assert validate_bins("E", [under, middle, high]).status == RelationshipStatus.COMPLETE
    gap = replace(middle, lower_bound=Decimal(4))
    assert validate_bins("E", [under, gap, high]).status in {
        RelationshipStatus.GAP,
        RelationshipStatus.OVERLAP,
    }


def test_price_only_input_does_not_version_but_terms_change_does() -> None:
    from services.contract_intelligence.specification import InputLayer, SourceLayer

    parser, registry = ContractSpecificationParser(), ContractRegistry()
    original = registry.add(parser.parse(bundle(), NOW))
    price_only = registry.add(parser.parse(bundle(yes_bid_dollars="0.12"), NOW))
    assert price_only.contract_spec_id == original.contract_spec_id
    base = bundle()
    terms_v1 = InputLayer(SourceLayer.CONTRACT_TERMS, {"content": "terms one"}, "terms-hash-1")
    terms_v2 = InputLayer(SourceLayer.CONTRACT_TERMS, {"content": "terms two"}, "terms-hash-2")
    one = parser.parse(SemanticsInputBundle(base.market, base.event, base.series, (terms_v1,)), NOW)
    two = parser.parse(SemanticsInputBundle(base.market, base.event, base.series, (terms_v2,)), NOW)
    assert one.semantic_hash != two.semantic_hash


def test_required_semantic_fixture_corpus_is_complete() -> None:
    import json
    from pathlib import Path

    fixtures = json.loads(Path("tests/fixtures/m4_semantics.json").read_text())
    assert len(fixtures) == 35
    assert len({item["id"] for item in fixtures}) == 35
    assert {"weather", "macro", "sports", "structural"} == {item["category"] for item in fixtures}
    assert sum(bool(item["expected_fail_closed"]) for item in fixtures) >= 9
