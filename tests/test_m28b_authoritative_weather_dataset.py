"""Offline, fail-closed tests for M28B authoritative weather settlement labels."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.daily_temperature import SETTLEMENT_LOCATIONS
from services.production_weather_strategy.contracts import TemporalSplit
from services.production_weather_strategy.settlement_dataset import (
    EventPartition,
    HistoricalWeatherDatasetError,
    build_authoritative_weather_dataset,
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
        "status": "settled",
        "result": result,
        "settlement_value_dollars": settlement_value,
        "settlement_ts": settlement_ts,
        "rules_primary": rules_primary,
        "rules_secondary": "The official value is reported by The Weather Company.",
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
    }


def split() -> TemporalSplit:
    return TemporalSplit(
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 1, 1, tzinfo=UTC),
        validation_start=datetime(2025, 1, 1, tzinfo=UTC),
        validation_end=datetime(2026, 1, 1, tzinfo=UTC),
        test_start=datetime(2026, 1, 1, tzinfo=UTC),
        test_end=datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_finalized_kalshi_result_becomes_exact_binary_label() -> None:
    parsed = parse_resolved_temperature_market(row())
    assert parsed is not None
    assert parsed.realized_yes == 1
    assert parsed.result == "yes"
    assert parsed.settlement_value_dollars == Decimal("1.0000")
    assert parsed.station_id == "CLIAUS"
    assert parsed.measurement == "DAILY_MAX"
    assert parsed.comparator == "RANGE"
    assert parsed.lower == Decimal("100")
    assert parsed.upper == Decimal("101")
    assert parsed.content_hash == parsed.contract_id


def test_non_temperature_market_is_skipped_but_temperature_lookalike_fails_closed() -> None:
    unrelated = row(rule="Will the Fed cut rates?", result="no")
    assert parse_resolved_temperature_market(unrelated) is None

    malformed = row(
        rule=(
            "If the maximum temperature recorded at Austin(CLIAUS) for Jun 15, 2024, "
            "is between 100-101° celsius according to The Weather Company, then the "
            "market resolves to Yes."
        )
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="unsupported exact rule"):
        parse_resolved_temperature_market(malformed)


@pytest.mark.parametrize(
    ("strike_type", "floor", "cap", "result", "comparator"),
    [
        ("between", 100, 101, "yes", "RANGE"),
        ("greater", 100, None, "no", "GT"),
        ("less", None, 70, "yes", "LT"),
    ],
)
def test_supported_predicates_are_rule_and_metadata_bound(
    strike_type: str,
    floor: object,
    cap: object,
    result: str,
    comparator: str,
) -> None:
    parsed = parse_resolved_temperature_market(
        row(strike_type=strike_type, floor=floor, cap=cap, result=result)
    )
    assert parsed is not None and parsed.comparator == comparator


def test_binary_result_and_settlement_value_must_agree() -> None:
    with pytest.raises(HistoricalWeatherDatasetError, match="settlement value conflicts"):
        parse_resolved_temperature_market(row(result="yes", settlement_value="0.0000"))
    with pytest.raises(HistoricalWeatherDatasetError, match="result is missing"):
        parse_resolved_temperature_market(row(result=""))


def test_rule_strike_metadata_mismatch_fails_closed() -> None:
    rule_text = (
        "If the maximum temperature recorded at Austin(CLIAUS) for Jun 15, 2024, is "
        "between 100-101° fahrenheit according to The Weather Company, then the market "
        "resolves to Yes."
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="strike values conflict"):
        parse_resolved_temperature_market(row(floor=99, cap=101, rule=rule_text))


def test_all_reviewed_settlement_locations_are_supported_without_city_specific_core_code() -> None:
    parsed_locations: set[str] = set()
    for index, settlement in enumerate(SETTLEMENT_LOCATIONS.values(), start=1):
        event = f"KXWEATHER{index}-24JUN15"
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
        parsed_locations.add(parsed.station_id)
    assert parsed_locations == set(SETTLEMENT_LOCATIONS)
    assert len(parsed_locations) == 20


def test_sibling_contracts_are_one_event_and_have_a_jointly_feasible_outcome() -> None:
    event = "KXHIGHAUS-24JUN15"
    rows = [
        row(
            event=event,
            ticker=f"{event}-T95",
            strike_type="less",
            floor=None,
            cap=95,
            result="no",
        ),
        row(
            event=event,
            ticker=f"{event}-B100.5",
            floor=100,
            cap=101,
            result="yes",
        ),
        row(
            event=event,
            ticker=f"{event}-T105",
            strike_type="greater",
            floor=105,
            cap=None,
            result="no",
        ),
    ]
    dataset = build_authoritative_weather_dataset(rows)
    assert dataset.event_count == 1
    assert dataset.contract_count == 3
    assert dataset.unique_event_count == 1
    witness = dataset.events[0].feasible_witness_deg_f
    assert Decimal("100") <= witness <= Decimal("101")


def test_mutually_contradictory_sibling_settlements_are_rejected() -> None:
    event = "KXHIGHAUS-24JUN15"
    rows = [
        row(
            event=event,
            ticker=f"{event}-GT70",
            strike_type="greater",
            floor=70,
            cap=None,
            result="yes",
        ),
        row(
            event=event,
            ticker=f"{event}-LT70",
            strike_type="less",
            floor=None,
            cap=70,
            result="yes",
        ),
    ]
    with pytest.raises(HistoricalWeatherDatasetError, match="mutually contradictory"):
        build_authoritative_weather_dataset(rows)


def test_temporal_split_is_event_level_so_siblings_cannot_leak_between_partitions() -> None:
    train_event = "KXHIGHAUS-24JUN15"
    validation_event = "KXHIGHCHI-25JUN15"
    test_event = "KXHIGHNY-26JUN15"
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
            event=validation_event,
            ticker=f"{validation_event}-B80.5",
            location="Chicago",
            identifier="CLIMDW",
            date_text="Jun 15, 2025",
            floor=80,
            cap=81,
            settlement_ts="2025-06-16T02:00:00Z",
        ),
        row(
            event=test_event,
            ticker=f"{test_event}-B78.5",
            location="New York City",
            identifier="CLINYC",
            date_text="Jun 15, 2026",
            floor=78,
            cap=79,
            settlement_ts="2026-06-16T02:00:00Z",
        ),
    ]
    dataset = build_authoritative_weather_dataset(rows, temporal_split=split())
    assert dataset.event_count == 3
    assert dataset.contract_count == 4
    assert len(dataset.train_event_ids) == 1
    assert len(dataset.validation_event_ids) == 1
    assert len(dataset.test_event_ids) == 1
    assert dataset.split_for_event(dataset.train_event_ids[0]) is EventPartition.TRAIN
    assert dataset.split_for_event(dataset.validation_event_ids[0]) is EventPartition.VALIDATION
    assert dataset.split_for_event(dataset.test_event_ids[0]) is EventPartition.TEST

    event_by_ticker = {event.event_ticker: event for event in dataset.events}
    train = event_by_ticker[train_event]
    assert len(train.market_tickers) == 2
    assert train.event_id in dataset.train_event_ids
    assert train.event_id not in dataset.validation_event_ids
    assert train.event_id not in dataset.test_event_ids


def test_dataset_identity_is_deterministic_and_order_independent() -> None:
    rows = [
        row(),
        row(
            event="KXHIGHCHI-24JUN15",
            ticker="KXHIGHCHI-24JUN15-B80.5",
            location="Chicago",
            identifier="CLIMDW",
            floor=80,
            cap=81,
        ),
    ]
    first = build_authoritative_weather_dataset(rows)
    second = build_authoritative_weather_dataset(list(reversed(rows)))
    assert first.dataset_id == second.dataset_id
    assert first.content_hash == second.content_hash


def test_duplicate_market_ticker_and_out_of_window_event_fail_closed() -> None:
    duplicate = row()
    with pytest.raises(HistoricalWeatherDatasetError, match="duplicate"):
        build_authoritative_weather_dataset([duplicate, duplicate])

    outside = row(
        event="KXHIGHAUS-23JUN15",
        ticker="KXHIGHAUS-23JUN15-B100.5",
        date_text="Jun 15, 2023",
        settlement_ts="2023-06-16T02:00:00Z",
    )
    with pytest.raises(HistoricalWeatherDatasetError, match="outside declared"):
        build_authoritative_weather_dataset([outside], temporal_split=split())


def test_core_dataset_builder_has_no_network_credential_execution_or_mutation_boundary() -> None:
    source = Path("services/production_weather_strategy/settlement_dataset.py").read_text()
    forbidden = (
        "urllib",
        "requests",
        "httpx",
        "services.production_execution",
        "services.kalshi_account_gateway",
        "services.risk_engine.authorization",
        "services.supervised_canary",
        "AuthorizationStore",
        "submit_order",
        "private_key",
    )
    assert all(term not in source for term in forbidden)
