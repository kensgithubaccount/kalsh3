from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.wn_a1_current_daily_high_authority import POLICY_IDENTITY
from services.forecasting.wn_a2_settlement_reconciliation import (
    ReconciliationError,
    ReconciliationState,
    _check_market,
    _reconcile,
)
from services.forecasting.wn_a2_temperature_bucket_semantics import (
    BucketSemanticsError,
    contains_finalized_temperature,
)
from tests.test_wn_a2_settlement_reconciliation import NOW, _ready


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("67.999999", False),
        ("68", True),
        ("68.52453125", True),
        ("69", True),
        ("69.000001", False),
        ("69.04", False),
        ("67.999", False),
    ],
)
def test_range_includes_both_structured_bounds_without_rounding(value, expected) -> None:
    assert (
        contains_finalized_temperature(
            comparator="RANGE", lower=Decimal(68), upper=Decimal(69), value=Decimal(value)
        )
        is expected
    )


@pytest.mark.parametrize(
    ("comparator", "threshold", "value", "expected"),
    [
        ("LT", "66", "65.999999", True),
        ("LT", "66", "66", False),
        ("LT", "66", "66.000001", False),
        ("LT", "66", "65", True),  # Display says 65 or below, not the API threshold.
        ("LT", "66", "65.5", True),
        ("GT", "73", "72.999999", False),
        ("GT", "73", "73", False),
        ("GT", "73", "73.000001", True),
        ("GT", "73", "74", True),  # Display says 74 or above, API rule says >73.
        ("GT", "73", "73.5", True),
    ],
)
def test_tails_use_strict_structured_thresholds_not_display_labels(
    comparator, threshold, value, expected
) -> None:
    assert (
        contains_finalized_temperature(
            comparator=comparator, lower=Decimal(threshold), upper=None, value=Decimal(value)
        )
        is expected
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_temperatures_fail_closed(value) -> None:
    with pytest.raises(BucketSemanticsError, match="finite"):
        contains_finalized_temperature(
            comparator="RANGE", lower=Decimal(68), upper=Decimal(69), value=Decimal(value)
        )


@pytest.mark.parametrize(
    ("comparator", "lower", "upper"),
    [
        ("RANGE", "68", None),
        ("RANGE", "69", "68"),
        ("RANGE", "68", "68"),
        ("RANGE", "NaN", "69"),
        ("RANGE", "68", "Infinity"),
        ("EXACT", "68", None),
        ("GT", "73", "74"),
        ("LT", "66", "67"),
    ],
)
def test_ambiguous_bound_shapes_and_unsupported_rounding_fail_closed(
    comparator, lower, upper
) -> None:
    with pytest.raises(BucketSemanticsError):
        contains_finalized_temperature(
            comparator=comparator,
            lower=Decimal(lower),
            upper=None if upper is None else Decimal(upper),
            value=Decimal(69),
        )


def test_gap_is_not_filled_by_integer_or_nearest_bucket_transformation() -> None:
    for low, high in ((68, 69), (70, 71)):
        assert not contains_finalized_temperature(
            comparator="RANGE", lower=Decimal(low), upper=Decimal(high), value=Decimal("69.5")
        )
    assert contains_finalized_temperature(
        comparator="RANGE",
        lower=Decimal("68.1"),
        upper=Decimal("68.2"),
        value=Decimal("68.15"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expiration_value", None),
        ("expiration_value", ""),
        ("expiration_value", "NaN"),
        ("expiration_value", True),
        ("expiration_value", 72.0),
        ("settlement_value_dollars", "NaN"),
        ("settlement_value_dollars", "1.0000"),
        ("settlement_ts", "2026-09-20T10:00:00"),
        ("settlement_ts", "malformed"),
    ],
)
def test_invalid_exact_authority_payout_and_chronology_do_not_settle(tmp_path, field, value):
    attempts, outcomes, attempt, envelope, _ = _ready(tmp_path)
    raw = {**envelope["payload"]["market"], field: value}
    if value is None:
        raw.pop(field)
    body = json.dumps({"market": raw}).encode()
    envelope.update(
        payload={"market": raw},
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body_b64=base64.b64encode(body).decode(),
    )
    result = _reconcile(
        attempt.attempt_id,
        attempts=attempts,
        outcomes=outcomes,
        acquire=lambda _: (envelope, body, "path"),
        observed_clock=lambda: NOW,
    )
    assert result.state is ReconciliationState.EVIDENCE_INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("floor_strike", 88),
        ("cap_strike", 88),
        ("strike_type", "less"),
        ("rules_primary", "If Chicago (CLIMDW) exactly 72 according to The Weather Company."),
        ("rules_primary", "If Chicago (CLIMDW) rounded up according to The Weather Company."),
        ("rules_secondary", "The upper endpoint is excluded."),
        ("rules_secondary", "Round the finalized temperature to the nearest integer."),
        ("rules_secondary", "Apply half-up rounding to one decimal before settlement."),
        ("rules_secondary", 123),
        ("rules_secondary", {}),
    ],
)
def test_changed_structured_strikes_or_rule_transform_fail_closed(tmp_path, field, value):
    _, _, attempt, envelope, _ = _ready(tmp_path)
    raw = {**envelope["payload"]["market"], field: value}
    with pytest.raises(ReconciliationError, match="authority invalid"):
        _check_market(attempt, raw, NOW, "path", json.dumps({"market": raw}).encode())


FIXTURE = Path(__file__).parent / "fixtures/wn_a2_e1/contract_family.json"
FAMILY = json.loads(FIXTURE.read_text())


def family_markets():
    """Build minimal market responses from explicitly labeled raw-data derivatives."""
    rows = []
    for event in FAMILY["events"]:
        specs = [("LT", event["lower_tail_cap"], None)]
        specs += [("RANGE", low, high) for low, high in event["ranges"]]
        specs += [("GT", event["upper_tail_floor"], None)]
        for comparator, lower, upper in specs:
            suffix = f"B{Decimal(lower) + Decimal('0.5')}" if upper is not None else f"T{lower}"
            ticker = event["event_ticker"] + "-" + suffix
            clause = (
                f"between {lower}-{upper}"
                if comparator == "RANGE"
                else f"less than {lower}"
                if comparator == "LT"
                else f"greater than {lower}"
            )
            target = datetime.fromisoformat(event["target_date"])
            result = "yes" if ticker == event["winning_ticker"] else "no"
            raw = {
                "ticker": ticker,
                "event_ticker": event["event_ticker"],
                "market_type": "binary",
                "status": "finalized",
                "price_level_structure": "linear_cent",
                "result": result,
                "settlement_value_dollars": "1.0000" if result == "yes" else "0.0000",
                "expiration_value": event["actual_f"],
                "settlement_ts": event["settlement_ts"],
                "strike_type": {"LT": "less", "GT": "greater", "RANGE": "between"}[comparator],
                "floor_strike": None if comparator == "LT" else lower,
                "cap_strike": lower if comparator == "LT" else upper,
                "rules_primary": (
                    "If the maximum temperature recorded at Chicago (CLIMDW) for "
                    f"{target.strftime('%b')} {target.day}, {target.year}, is {clause}° fahrenheit "
                    "according to The Weather Company, then the market resolves to Yes."
                ),
                "rules_secondary": FAMILY["reviewed_standard_secondary_rules"],
                "volume_fp": "0",
                "open_interest_fp": "0",
            }
            rows.append((event, comparator, lower, upper, raw))
    return rows


@pytest.mark.parametrize(
    ("event", "comparator", "lower", "upper", "raw"),
    family_markets(),
    ids=[row[4]["ticker"] for row in family_markets()],
)
def test_all_30_finalized_contract_family_markets_match_official_outcomes(
    tmp_path, event, comparator, lower, upper, raw
) -> None:
    _, _, original, _, _ = _ready(tmp_path)
    attempt = replace(
        original,
        market_ticker=raw["ticker"],
        event_ticker=event["event_ticker"],
        target_local_date=event["target_date"],
        contract_policy_identity=POLICY_IDENTITY,
        contract_comparator=comparator,
        contract_lower=Decimal(lower),
        contract_upper=None if upper is None else Decimal(upper),
        evaluated_at=datetime.fromisoformat(event["target_date"]).replace(tzinfo=UTC),
    )
    body = json.dumps({"market": raw}).encode()
    evidence = _check_market(
        attempt,
        raw,
        datetime(2026, 9, 27, tzinfo=UTC),
        "/trade-api/v2/markets/" + raw["ticker"],
        body,
    )
    assert evidence.result == raw["result"]
    assert evidence.settlement_value_dollars == Decimal(event["actual_f"])
    if raw["ticker"] == "KXHIGHCHI-26SEP24-B68.5":
        assert evidence.result == "yes" and evidence.settlement_value_dollars == Decimal("69.00")
