from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.forecasting.daily_temperature import SETTLEMENT_LOCATIONS
from services.production_weather_strategy.contracts import (
    SettlementLabel,
    SettlementLabelManifest,
    TemporalSplit,
)
from services.production_weather_strategy.settlement_dataset import (
    ACQUISITION_SCHEMA,
    SETTLEMENT_MAPPING_ID,
    AcquisitionBoundMarketRow,
    HistoricalWeatherDatasetError,
    PublicPageEvidence,
    SettlementRegime,
    build_evidence_bound_weather_dataset,
    build_weather_settlement_dataset,
    classify_resolved_temperature_market,
    parse_resolved_temperature_market,
)


def row(
    *,
    event: str = "KXHIGHAUS-24JUN15",
    ticker: str = "KXHIGHAUS-24JUN15-B100.5",
    location: str = "Austin",
    identifier: str = "CLIAUS",
    date_text: str = "Jun 15, 2024",
    measurement: str = "maximum",
    strike_type: str = "between",
    floor: object = 100,
    cap: object = 101,
    result: str = "yes",
    settlement_value: object | None = None,
    settlement_ts: str = "2024-06-16T02:00:00Z",
    status: str = "settled",
    rule: str | None = None,
) -> dict[str, object]:
    if strike_type == "between":
        phrase = f"between {floor}-{cap}"
    elif strike_type == "greater":
        phrase = f"greater than {floor}"
    elif strike_type == "less":
        phrase = f"less than {cap}"
    else:
        phrase = f"equal to {floor}"
    rules_primary = rule or (
        f"If the {measurement} temperature recorded at {location}({identifier}) for "
        f"{date_text}, is {phrase}° fahrenheit according to The Weather Company, then the "
        "market resolves to Yes."
    )
    if settlement_value is None:
        settlement_value = "1.0000" if result == "yes" else "0.0000"
    return {
        "ticker": ticker,
        "event_ticker": event,
        "market_type": "binary",
        "status": status,
        "result": result,
        "settlement_value_dollars": settlement_value,
        "settlement_ts": settlement_ts,
        "rules_primary": rules_primary,
        "rules_secondary": "Official value follows the named rule source.",
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
    }


def page_for(
    value: dict[str, object], *, partition: str = "archive", page: int = 1
) -> PublicPageEvidence:
    if partition == "archive":
        path = "/trade-api/v2/historical/markets?limit=1000&series_ticker=KXHIGHAUS"
    else:
        path = "/trade-api/v2/markets?limit=1000&status=settled&series_ticker=KXHIGHAUS"
    body = json.dumps({"markets": [value], "cursor": ""}, sort_keys=True).encode()
    return PublicPageEvidence.from_response(
        request_path=path,
        response_bytes=body,
        page_number=page,
        scope_series_ticker="KXHIGHAUS",
    )


def bound(
    value: dict[str, object], *, partition: str = "archive", page: int = 1
) -> AcquisitionBoundMarketRow:
    return AcquisitionBoundMarketRow.from_page(
        value, page_for(value, partition=partition, page=page)
    )


def split() -> TemporalSplit:
    return TemporalSplit(
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 1, 1, tzinfo=UTC),
        validation_start=datetime(2025, 1, 1, tzinfo=UTC),
        validation_end=datetime(2026, 1, 1, tzinfo=UTC),
        test_start=datetime(2026, 1, 1, tzinfo=UTC),
        test_end=datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_exact_binary_result_and_settlement_value_agree() -> None:
    parsed = parse_resolved_temperature_market(row())
    assert parsed is not None
    assert parsed.realized_yes == 1
    assert parsed.settlement_value_dollars == Decimal("1.0000")
    with pytest.raises(HistoricalWeatherDatasetError, match="settlement value conflicts"):
        parse_resolved_temperature_market(row(result="yes", settlement_value="0.0000"))


def test_status_must_be_settled() -> None:
    with pytest.raises(HistoricalWeatherDatasetError, match="status is not settled"):
        parse_resolved_temperature_market(row(status="closed"))


def test_rule_and_strike_metadata_must_agree() -> None:
    exact = row()["rules_primary"]
    with pytest.raises(HistoricalWeatherDatasetError, match="strike values conflict"):
        parse_resolved_temperature_market(row(floor=99, rule=str(exact)))


@pytest.mark.parametrize("measurement", ["maximum", "minimum"])
def test_max_and_min_are_supported(measurement: str) -> None:
    parsed = parse_resolved_temperature_market(row(measurement=measurement))
    assert parsed is not None
    assert parsed.measurement == ("DAILY_MAX" if measurement == "maximum" else "DAILY_MIN")


@pytest.mark.parametrize(
    ("strike_type", "floor", "cap", "comparator"),
    [("between", 100, 101, "RANGE"), ("greater", 100, None, "GT"), ("less", None, 70, "LT")],
)
def test_all_historical_predicate_shapes(
    strike_type: str, floor: object, cap: object, comparator: str
) -> None:
    parsed = parse_resolved_temperature_market(row(strike_type=strike_type, floor=floor, cap=cap))
    assert parsed is not None and parsed.comparator == comparator


def test_all_reviewed_current_twc_locations_and_mapping_identity() -> None:
    seen: set[str] = set()
    for index, settlement in enumerate(SETTLEMENT_LOCATIONS.values(), start=1):
        event = f"KXWX{index}-24JUN15"
        parsed = parse_resolved_temperature_market(
            row(
                event=event,
                ticker=f"{event}-B70.5",
                location=settlement.location,
                identifier=settlement.identifier,
                floor=70,
                cap=71,
            )
        )
        assert parsed is not None
        assert parsed.settlement_mapping_id == SETTLEMENT_MAPPING_ID
        seen.add(parsed.station_id)
    assert seen == set(SETTLEMENT_LOCATIONS)


def test_legacy_nws_is_explicitly_non_current_and_cannot_be_labeled() -> None:
    legacy_rule = (
        "If the maximum temperature recorded at Austin(CLIAUS) for Jun 15, 2024, is "
        "between 100-101° fahrenheit according to the National Weather Service's "
        "Climatological Report (Daily), then the market resolves to Yes."
    )
    value = row(rule=legacy_rule)
    classification = classify_resolved_temperature_market(value)
    assert classification.regime is SettlementRegime.LEGACY_NWS
    with pytest.raises(HistoricalWeatherDatasetError, match="no supported"):
        build_evidence_bound_weather_dataset((bound(value),))


def test_malformed_current_twc_like_rule_fails_closed() -> None:
    malformed = row(
        rule=(
            "If the maximum temperature recorded at Austin(CLIAUS) for Jun 15, 2024, is "
            "between 100-101° celsius according to The Weather Company, then the market "
            "resolves to Yes."
        )
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="unsupported exact rule"):
        parse_resolved_temperature_market(malformed)


def test_unrelated_market_is_skipped() -> None:
    unrelated = row(rule="Will the Fed cut rates?")
    semantic = build_weather_settlement_dataset((row(), unrelated))
    assert semantic.skipped_unsupported_count == 1


def test_same_event_ticker_with_conflicting_semantics_fails_closed() -> None:
    first = row()
    second = row(
        ticker="KXHIGHAUS-24JUN15-B90.5",
        location="Chicago",
        identifier="CLIMDW",
        floor=90,
        cap=91,
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="conflicting settlement semantics"):
        build_weather_settlement_dataset((first, second))


def test_event_identity_is_outcome_independent() -> None:
    yes = parse_resolved_temperature_market(row(result="yes"))
    no = parse_resolved_temperature_market(row(result="no"))
    assert yes is not None and no is not None
    assert yes.event_id == no.event_id
    assert yes.contract_id != no.contract_id


def test_contradictory_siblings_fail_closed() -> None:
    event = "KXHIGHAUS-24JUN15"
    yes_gt = row(
        event=event,
        ticker=f"{event}-GT70",
        strike_type="greater",
        floor=70,
        cap=None,
        result="yes",
    )
    yes_lt = row(
        event=event,
        ticker=f"{event}-LT70",
        strike_type="less",
        floor=None,
        cap=70,
        result="yes",
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="mutually contradictory"):
        build_weather_settlement_dataset((yes_gt, yes_lt))


def test_event_level_temporal_split_keeps_siblings_together() -> None:
    train_event = "KXHIGHAUS-24JUN15"
    rows = [
        row(event=train_event, ticker=f"{train_event}-B100.5"),
        row(
            event=train_event,
            ticker=f"{train_event}-GT105",
            strike_type="greater",
            floor=105,
            cap=None,
            result="no",
        ),
        row(
            event="KXHIGHAUS-25JUN15",
            ticker="KXHIGHAUS-25JUN15-B100.5",
            date_text="Jun 15, 2025",
            settlement_ts="2025-06-16T02:00:00Z",
        ),
        row(
            event="KXHIGHAUS-26JUN15",
            ticker="KXHIGHAUS-26JUN15-B100.5",
            date_text="Jun 15, 2026",
            settlement_ts="2026-06-16T02:00:00Z",
        ),
    ]
    dataset = build_weather_settlement_dataset(rows, temporal_split=split())
    train = next(event for event in dataset.events if event.event_ticker == train_event)
    assert train.event_id in dataset.train_event_ids
    assert len(train.market_tickers) == 2
    assert train.event_id not in dataset.validation_event_ids + dataset.test_event_ids


def test_naked_rows_cannot_produce_canonical_labels() -> None:
    semantic = build_weather_settlement_dataset((row(),))
    assert semantic.evidence_bound is False
    assert semantic.settlement_labels is None
    with pytest.raises(HistoricalWeatherDatasetError, match="acquisition-bound"):
        build_evidence_bound_weather_dataset((row(),))  # type: ignore[arg-type]


def test_evidence_bound_path_emits_canonical_m28a_labels_and_manifest() -> None:
    dataset = build_evidence_bound_weather_dataset((bound(row()),))
    assert dataset.evidence_bound is True
    assert isinstance(dataset.settlement_labels, SettlementLabelManifest)
    assert dataset.settlement_labels.settlement_mapping_id == SETTLEMENT_MAPPING_ID
    label = dataset.settlement_labels.labels[0]
    assert isinstance(label, SettlementLabel)
    assert label.event_id == dataset.events[0].event_id
    assert label.resolved_outcome is True
    assert label.settlement_evidence_id == dataset.provenance[0].settlement_evidence_id


def test_changed_outcome_changes_label_and_manifest_but_not_event_identity() -> None:
    yes = build_evidence_bound_weather_dataset((bound(row(result="yes")),))
    no = build_evidence_bound_weather_dataset((bound(row(result="no")),))
    assert yes.events[0].event_id == no.events[0].event_id
    assert yes.settlement_labels is not None and no.settlement_labels is not None
    assert (
        yes.settlement_labels.labels[0].content_hash
        != no.settlement_labels.labels[0].content_hash
    )
    assert yes.settlement_labels.manifest_id != no.settlement_labels.manifest_id


def test_settlement_evidence_id_is_page_and_row_derived() -> None:
    value = row()
    first = bound(value)
    changed_page = PublicPageEvidence.from_response(
        request_path="/trade-api/v2/historical/markets?limit=1000&series_ticker=KXHIGHAUS",
        response_bytes=json.dumps(
            {"markets": [value], "cursor": "different"}, sort_keys=True
        ).encode(),
        page_number=1,
        scope_series_ticker="KXHIGHAUS",
    )
    second = AcquisitionBoundMarketRow.from_page(value, changed_page)
    assert first.settlement_evidence_id != second.settlement_evidence_id
    changed_row = dict(value)
    changed_row["rules_secondary"] = "changed immutable source material"
    third = AcquisitionBoundMarketRow.from_page(changed_row, page_for(changed_row))
    assert first.settlement_evidence_id != third.settlement_evidence_id


def test_page_evidence_requires_fixed_reviewed_series_scope_and_recent_settled_filter() -> None:
    with pytest.raises(HistoricalWeatherDatasetError, match="series scope"):
        PublicPageEvidence.from_response(
            request_path="/trade-api/v2/historical/markets?limit=1000",
            response_bytes=b'{"markets":[],"cursor":""}',
            page_number=1,
            scope_series_ticker="KXHIGHAUS",
        )
    with pytest.raises(HistoricalWeatherDatasetError, match="not settlement-scoped"):
        PublicPageEvidence.from_response(
            request_path="/trade-api/v2/markets?limit=1000&series_ticker=KXHIGHAUS",
            response_bytes=b'{"markets":[],"cursor":""}',
            page_number=1,
            scope_series_ticker="KXHIGHAUS",
        )


def test_authority_and_completeness_claims_are_separate() -> None:
    dataset = build_evidence_bound_weather_dataset((bound(row()),))
    assert dataset.settlement_labels is not None
    assert "NO_TOTAL_COUNT_PROOF" in dataset.coverage_claim
    assert dataset.label_authority
    assert (
        ACQUISITION_SCHEMA in dataset.provenance[0].settlement_evidence_id
        or len(dataset.provenance[0].settlement_evidence_id) == 64
    )


def test_provenance_retains_replay_material() -> None:
    dataset = build_evidence_bound_weather_dataset((bound(row()),))
    record = dataset.provenance[0]
    assert record.request_path.startswith("/trade-api/v2/historical/markets?")
    assert len(record.response_sha256) == 64
    assert len(record.market_row_hash) == 64
    assert record.parser_version
    assert record.settlement_mapping_id == SETTLEMENT_MAPPING_ID
    assert (
        record.label_id
        == dataset.settlement_labels.labels[0].content_hash  # type: ignore[union-attr]
    )


def test_page_evidence_hash_cannot_be_caller_injected() -> None:
    with pytest.raises(HistoricalWeatherDatasetError, match="derived from exact response bytes"):
        PublicPageEvidence(  # type: ignore[call-arg]
            request_path="/trade-api/v2/historical/markets?limit=1000&series_ticker=KXHIGHAUS",
            response_sha256="0" * 64,
            page_number=1,
            scope_series_ticker="KXHIGHAUS",
            market_row_hashes=("1" * 64,),
        )


def test_bound_row_must_exist_in_exact_response_page() -> None:
    value = row()
    other = row(ticker="KXHIGHAUS-24JUN15-B90.5", floor=90, cap=91)
    page = page_for(other)
    with pytest.raises(HistoricalWeatherDatasetError, match="not contained"):
        AcquisitionBoundMarketRow.from_page(value, page)
