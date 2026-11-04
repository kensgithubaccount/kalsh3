"""Explicit SIM fixtures only. No October forecasts/outcomes, source I/O or orders."""

from __future__ import annotations

import ast
import csv
import json
import socket
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

import pytest

from services.forecasting import wn_future_design_rehearsal as rehearsal

D = Decimal
FIXTURE = "SIM-DESIGN-ALGEBRA"
AT = datetime(2027, 5, 1, 9, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("NETWORK_DENIED_SYNTHETIC_DESIGN_REHEARSAL")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def members() -> rehearsal.ToyJointMembers:
    return rehearsal.ToyJointMembers(
        FIXTURE, tuple(rehearsal.ToyTrajectory(i, (D(59 + (i % 6) * 9),) * 24) for i in range(64))
    )


def ladder() -> rehearsal.ToyLadder:
    return rehearsal.ToyLadder(FIXTURE, ((68, 76), (77, 85), (86, 94), (95, 103)))


def fee(**kwargs: object) -> rehearsal.ToyFee:
    return replace(
        rehearsal.ToyFee(FIXTURE, D("0.04"), AT - timedelta(days=1), AT + timedelta(days=1)),
        **kwargs,
    )


def quote(**kwargs: object) -> rehearsal.ToyQuote:
    return replace(
        rehearsal.ToyQuote(
            FIXTURE,
            "YES",
            D("0.20"),
            D(1),
            AT - timedelta(milliseconds=500),
            AT,
            AT + timedelta(hours=8),
        ),
        **kwargs,
    )


def test_complete_draft_calendar_and_machine_declaration() -> None:
    slots = rehearsal.draft_slots()
    assert len(slots) == 1060 and len({s.cluster for s in slots}) == 212
    assert len({(s.target_date, s.city) for s in slots}) == 1060
    assert Counter(s.role for s in slots) == {
        "TRAIN": 295,
        "EMBARGO_TRAIN_FREEZE": 70,
        "CALIBRATION": 155,
        "EMBARGO_CALIBRATION_FREEZE": 80,
        "UNTOUCHED_HOLDOUT": 460,
    }
    plan = json.loads(
        (ROOT / "experiments/wn_future_probability_protocol_v1/draft_protocol.json").read_bytes()
    )
    assert plan["experiment_id"] == rehearsal.EXPERIMENT_ID
    assert plan["status"] == "DRAFT_NOT_REGISTERED_NOT_FROZEN"
    assert plan["cohort"]["city_days"] == len(slots)
    assert plan["cohort"]["contract_slots"] == 1060 * 6
    assert plan["cohort"]["side_alternative_slots"] == 1060 * 12
    assert tuple(c["city_id"] for c in plan["cities"]) == rehearsal.CITIES
    assert {c["station_icao"] for c in plan["cities"]} == {"KBOS", "KMIA", "KDEN", "KLAX", "KSEA"}
    assert plan["authority"]["registered"] is False and plan["authority"]["frozen"] is False
    assert not plan["authority"]["scientific_scoring_authorized"]
    assert all(item["status"] == "OPEN_NO_AUTHORITY" for item in plan["checklist"])
    for partition in plan["partitions"]:
        begin, end = (
            date.fromisoformat(partition["start"]),
            date.fromisoformat(partition["end_inclusive"]),
        )
        assert (end - begin).days + 1 == partition["date_clusters"]
        assert partition["city_days"] == partition["date_clusters"] * 5
        assert all(
            rehearsal.role(begin + timedelta(days=i)) == partition["role"]
            for i in range(partition["date_clusters"])
        )
    assert plan["decision_times"]["decision"] == "D 09:00:00Z"


@pytest.mark.parametrize("as_of", [date(2026, 12, 15), date(2027, 1, 1), date(2028, 1, 1)])
def test_missed_registration_requires_new_cohort(as_of: date) -> None:
    with pytest.raises(rehearsal.DesignError):
        rehearsal.draft_slots(as_of)


def test_source_matrix_explicitly_keeps_thirteen_gaps() -> None:
    with (
        ROOT / "experiments/wn_future_probability_protocol_v1/unresolved_sources.csv"
    ).open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 13 and len({r["id"] for r in rows}) == 13
    assert all(r["not_established"] and r["required_before_activation"] for r in rows)
    assert any(r["classification"] == "CONFLICT_UNRESOLVED" for r in rows)
    assert not any(r["classification"] == "ADMISSIBLE" for r in rows)


def test_all_missing_failed_and_embargo_slots_preserved() -> None:
    states = (
        (date(2027, 1, 1), "boston", "FAILED_ACQUISITION"),
        (date(2027, 1, 1), "miami", "ABSTAIN"),
        (date(2027, 3, 1), "seattle", "FAILED_ACQUISITION"),
    )
    result = rehearsal.preserve_draft_denominator(states)
    assert len(result) == 1060 and len({r[0] for r in result}) == 212
    assert (date(2027, 3, 1), "seattle", "FAILED_ACQUISITION") in result
    assert Counter(r[2] for r in result) == {
        "FAILED_ACQUISITION": 2,
        "ABSTAIN": 1,
        "EMBARGO": 149,
        "MISSING": 908,
    }


@pytest.mark.parametrize(
    "states",
    [
        ((date(2026, 10, 3), "boston", "SYNTHETIC_ONLY"),),
        ((date(2027, 1, 1), "wrong", "ABSTAIN"),),
        ((date(2027, 1, 1), "boston", "LIVE_ADMISSIBLE"),),
        ((date(2027, 1, 1), "boston", "ABSTAIN"), (date(2027, 1, 1), "boston", "MISSING")),
    ],
)
def test_no_retroactive_extra_or_duplicate_ledger_entries(
    states: tuple[tuple[date, str, str], ...],
) -> None:
    with pytest.raises(rehearsal.DesignError):
        rehearsal.preserve_draft_denominator(states)


def test_raw_member_algebra_exact_probability_simplex() -> None:
    result = rehearsal.raw_member_probabilities(members(), ladder())
    assert result.probabilities == tuple(D(n) / 64 for n in (11, 11, 11, 11, 10, 10))
    assert sum(result.probabilities) == 1 and not result.authority.registered
    assert result.authority.synthetic_only and not result.authority.scientific_scoring_authorized


@pytest.mark.parametrize("mode", ["missing", "duplicate", "p50", "marginals", "wrong_fixture"])
def test_incomplete_or_nonjoint_information_cannot_be_probabilities(mode: str) -> None:
    source = members()
    with pytest.raises(rehearsal.DesignError):
        if mode == "missing":
            replace(source, rows=source.rows[:-1])
        elif mode == "duplicate":
            replace(source, rows=(*source.rows[:-1], source.rows[0]))
        elif mode in ("p50", "marginals"):
            replace(source, source_kind=mode)
        else:
            rehearsal.raw_member_probabilities(source, replace(ladder(), fixture="SIM-OTHER"))


def test_identical_hourly_marginals_do_not_identify_daily_maximum_distribution() -> None:
    aligned = ((D(0), D(0)), (D(1), D(1)))
    crossed = ((D(0), D(1)), (D(1), D(0)))
    assert all(sorted(r[h] for r in aligned) == sorted(r[h] for r in crossed) for h in (0, 1))
    assert sorted(max(row) for row in aligned) != sorted(max(row) for row in crossed)


@pytest.mark.parametrize(
    "value,index",
    [
        (D(67), 0),
        (D(68), 1),
        (D(76), 1),
        (D(77), 2),
        (D(85), 2),
        (D(86), 3),
        (D(94), 3),
        (D(95), 4),
        (D(103), 4),
        (D(104), 5),
    ],
)
def test_strict_tails_and_inclusive_integer_ranges(value: Decimal, index: int) -> None:
    assert ladder().bin(value) == index


@pytest.mark.parametrize(
    "ranges", [((68, 76), (78, 85), (86, 94), (95, 103)), ((68, 76), (76, 85), (86, 94), (95, 103))]
)
def test_gaps_and_overlaps_do_not_get_renormalized(ranges: tuple[tuple[int, int], ...]) -> None:
    with pytest.raises(rehearsal.DesignError):
        rehearsal.ToyLadder(FIXTURE, ranges)


def test_kelvin_precision_retained_and_fractional_domain_rejected() -> None:
    assert rehearsal.kelvin_to_fahrenheit(D("273.15")) == 32
    assert rehearsal.kelvin_to_fahrenheit(D("273.16")) == D("32.018")
    with pytest.raises(rehearsal.DesignError, match="UNPROVEN_PRECISION"):
        ladder().bin(D("76.5"))
    source = members()
    row = replace(source.rows[0], hourly_fahrenheit=(D("76.5"),) * 24)
    with pytest.raises(rehearsal.DesignError):
        rehearsal.raw_member_probabilities(replace(source, rows=(row, *source.rows[1:])), ladder())


def test_proper_score_arithmetic_is_only_toy_and_zero_probability_loss_is_infinite() -> None:
    probabilities = rehearsal.ToyProbability(FIXTURE, (D(1), D(0), D(0), D(0), D(0), D(0)))
    perfect = rehearsal.score_toy(probabilities, FIXTURE, 0)
    impossible = rehearsal.score_toy(probabilities, FIXTURE, 1)
    assert perfect.brier == perfect.logarithmic_loss == 0
    assert impossible.brier == 2 and impossible.logarithmic_loss == D("Infinity")
    assert not perfect.authority.scientific_scoring_authorized
    with pytest.raises(rehearsal.DesignError):
        rehearsal.score_toy(probabilities, "REAL-OUTCOME", 0)


@pytest.mark.parametrize(
    "bad", [D("NaN"), D("Infinity"), D("-Infinity"), D("-0.1"), D("1.1"), D("1e-9999"), 0.5, True]
)
def test_invalid_probability_inputs_fail_closed(bad: Decimal) -> None:
    with pytest.raises(rehearsal.DesignError):
        rehearsal.ToyProbability(FIXTURE, (bad, D(0), D(0), D(0), D(0), D(0)))


def test_hypothetical_fee_cent_ceiling_and_effective_window() -> None:
    assert fee().cost(D("0.50"), AT) == D("0.01")
    assert fee(coefficient=D("0.123")).cost(D("0.50"), AT) == D("0.04")
    assert fee().cost(D("0.01"), AT) == D("0.01")
    with pytest.raises(rehearsal.DesignError):
        fee().cost(D("0.501"), AT)
    with pytest.raises(rehearsal.DesignError):
        fee(effective_end=AT).cost(D("0.50"), AT)


def test_numeric_results_independent_of_callers_decimal_context() -> None:
    expected = rehearsal.after_cost_toy(quote(), fee(), (D("0.85"), D("0.95")), (D("0.8"), D(1)))
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_FLOOR
        actual = rehearsal.after_cost_toy(quote(), fee(), (D("0.85"), D("0.95")), (D("0.8"), D(1)))
        assert actual == expected
        assert fee(coefficient=D("0.123")).cost(D("0.50"), AT) == D("0.04")


@pytest.mark.parametrize(
    "side,p", [("YES", (D("0.85"), D("0.95"))), ("NO", (D("0.05"), D("0.15")))]
)
def test_synthetic_positive_inequality_still_conveys_no_authority(
    side: str, p: tuple[Decimal, Decimal]
) -> None:
    result = rehearsal.after_cost_toy(quote(side=side), fee(), p, (D("0.8"), D(1)))
    assert result.decision == "SYNTHETIC_CANDIDATE_ONLY"
    assert len(result.scenario_lower_bounds) == 3 and min(result.scenario_lower_bounds) >= D("0.02")
    assert not result.authority.registered and not result.authority.live_authorized
    assert result.authority.trading_authority == "NONE"


@pytest.mark.parametrize(
    "changes",
    [
        {"available_units": D("0.5")},
        {"sequence_valid": False},
        {"latency_depth_verified": False},
        {"partial_execution": True},
        {"order_type": "IOC"},
        {"requested_units": D(2)},
        {"acquired_at": AT + timedelta(microseconds=1)},
        {"acquired_at": AT - timedelta(seconds=1, microseconds=1)},
        {"close_at": AT},
        {"close_at": AT + timedelta(minutes=14)},
        {"market_status": "FINALIZED"},
        {"rules_and_source_consistent": False},
        {"siblings_complete": False},
    ],
)
def test_nonexecutable_or_unproven_condition_always_abstains(changes: dict[str, object]) -> None:
    result = rehearsal.after_cost_toy(quote(**changes), fee(), (D("0.99"), D("0.99")), (D(1), D(1)))
    assert result.decision == "ABSTAIN"


@pytest.mark.parametrize(
    "kind",
    [
        "fee",
        "expired_fee",
        "fill",
        "all_fill_uncertainty",
        "no_fill",
        "model",
        "expensive",
        "no_advantage",
        "fee_cap",
    ],
)
def test_unknowns_costs_or_no_advantage_cannot_be_candidates(kind: str) -> None:
    f = None if kind == "fee" else fee(effective_end=AT) if kind == "expired_fee" else fee()
    q = (
        None
        if kind == "fill"
        else (D(0), D(1))
        if kind == "all_fill_uncertainty"
        else (D(0), D(0))
        if kind == "no_fill"
        else (D(1), D(1))
    )
    p = (
        None
        if kind == "model"
        else (D("0.3"), D("0.4"))
        if kind == "no_advantage"
        else (D("0.9"), D("0.95"))
    )
    price = D("0.98") if kind == "expensive" else D("0.20")
    if kind == "fee_cap":
        f = fee(coefficient=D(1))
    assert rehearsal.after_cost_toy(quote(ask=price), f, p, q).decision == "ABSTAIN"


def test_fill_outcome_dependence_not_silently_assumed_independent() -> None:
    result = rehearsal.after_cost_toy(
        quote(ask=D("0.80")), fee(), (D("0.99"), D("0.99")), (D("0.01"), D("0.01"))
    )
    assert result.decision == "ABSTAIN"  # The small fill fraction can select only losing outcomes.
    p, cost = D("0.75"), D("0.20")
    qlo, qhi = D("0.1"), D("0.9")
    kink = 1 - p
    assert max(D(0), p + kink - 1) - kink * cost < min(
        max(D(0), p + q - 1) - q * cost for q in (qlo, qhi)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("registered", True),
        ("synthetic_only", False),
        ("live_authorized", True),
        ("scientific_scoring_authorized", True),
        ("trading_authority", "PAPER"),
    ],
)
def test_all_result_stamps_cannot_be_promoted(field: str, value: object) -> None:
    with pytest.raises(rehearsal.DesignError):
        replace(rehearsal.NoAuthority(), **{field: value})
    probability = rehearsal.raw_member_probabilities(members(), ladder())
    with pytest.raises(rehearsal.DesignError):
        replace(probability, authority={"registered": True})


def test_no_network_loader_fit_cli_old_scorer_or_order_client() -> None:
    source = Path(rehearsal.__file__ or "").read_text()
    forbidden = {
        "socket",
        "requests",
        "httpx",
        "urllib",
        "http",
        "subprocess",
        "pathlib",
        "json",
        "numpy",
        "scipy",
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert not {name.name.split(".")[0] for name in node.names} & forbidden
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden
    assert "__main__" not in source and "wn_multicity_evaluation_spec" not in source
