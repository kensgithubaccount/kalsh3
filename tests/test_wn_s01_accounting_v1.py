"""Synthetic receipts only; no real expenses, forecast/outcome data or source I/O."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from services.forecasting import wn_s01_accounting_v1 as accounting

D = Decimal
CATEGORIES = accounting.CANONICAL_CATEGORIES


def receipt(
    category: str,
    amount: Decimal,
    *,
    coverage: str = "COMPLETE",
    changes: dict[str, object] | None = None,
) -> bytes:
    fields: dict[str, object] = {
        "schema": "s01-expense-receipt-v1",
        "domain": "SYNTHETIC_FIXTURE",
        "receipt_id": "SIM-" + category,
        "category": category,
        "currency": "USD",
        "amount_usd": str(amount),
        "coverage": coverage,
        "cohort_start": "2027-01-01",
        "cohort_end": "2027-07-31",
        "city_days": 1060,
        "source_document_sha256": "f" * 64,
        "includes_failure_abstention_expenses": True,
    }
    if changes:
        fields.update(changes)
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def unknown() -> tuple[accounting.ExpenseLine, ...]:
    return tuple(accounting.ExpenseLine(c, None, None) for c in CATEGORIES)


def costs(
    amounts: dict[str, Decimal], *, lower_bounds: frozenset[str] = frozenset()
) -> tuple[tuple[accounting.ExpenseLine, ...], tuple[bytes, ...]]:
    lines = []
    proofs = []
    for category in CATEGORIES:
        amount = amounts.get(category)
        proof = (
            None
            if amount is None
            else receipt(
                category, amount, coverage="LOWER_BOUND" if category in lower_bounds else "COMPLETE"
            )
        )
        lines.append(
            accounting.ExpenseLine(
                category, amount, None if proof is None else hashlib.sha256(proof).hexdigest()
            )
        )
        if proof is not None:
            proofs.append(proof)
    return tuple(lines), tuple(proofs)


def complete(total: Decimal) -> tuple[tuple[accounting.ExpenseLine, ...], tuple[bytes, ...]]:
    return costs({c: total if i == 0 else D(0) for i, c in enumerate(CATEGORIES)})


@pytest.mark.parametrize("missing", CATEGORIES)
def test_each_missing_category_rejected_even_if_known_sum_over_budget(missing: str) -> None:
    lines, proofs = complete(D("53.01"))
    kept = tuple(line for line in lines if line.category != missing)
    with pytest.raises(accounting.AccountingInputError, match="EXACT_22"):
        accounting.assess_synthetic_budget(kept, proofs)


@pytest.mark.parametrize("category", CATEGORIES)
def test_each_duplicate_category_rejected(category: str) -> None:
    lines = (*unknown(), accounting.ExpenseLine(category, None, None))
    with pytest.raises(accounting.AccountingInputError, match="DUPLICATE_CATEGORY"):
        accounting.assess_budget(lines)


@pytest.mark.parametrize("category", ["", "unexpected", "CPU_CORE_HOUR", " cpu_core_hour"])
def test_unexpected_or_alternate_category_spelling_rejected(category: str) -> None:
    with pytest.raises(accounting.AccountingInputError, match="UNEXPECTED_CATEGORY"):
        accounting.ExpenseLine(category, None, None)


@pytest.mark.parametrize(
    "amount",
    [D("-0.01"), D("NaN"), D("Infinity"), D("0E+999999"), D("0.0000000000001"), D("1000000000001")],
)
def test_negative_nonfinite_or_unsupported_values_rejected(amount: Decimal) -> None:
    with pytest.raises(accounting.AccountingInputError):
        accounting.ExpenseLine(CATEGORIES[0], amount, None)


@pytest.mark.parametrize(
    "extra", ["verified", "budget_usd", "quantity", "usd_per_unit", "authority"]
)
def test_unknown_input_fields_cannot_supply_authority_or_override_budget(extra: str) -> None:
    record: dict[str, object] = {
        "category": CATEGORIES[0],
        "amount_usd": "0",
        "receipt_sha256": None,
        extra: True,
    }
    with pytest.raises(accounting.AccountingInputError, match="EXACT_EXPENSE_FIELDS"):
        accounting.ExpenseLine.from_record(record)


@pytest.mark.parametrize("amount", [53.0, True, "NaN", "-1", "1e1", "0.0000000000001"])
def test_json_amount_is_strict_bounded_decimal_text(amount: object) -> None:
    with pytest.raises(accounting.AccountingInputError):
        accounting.ExpenseLine.from_record(
            {"category": CATEGORIES[0], "amount_usd": amount, "receipt_sha256": None}
        )


def test_empty_and_non_tuple_budget_rejected() -> None:
    with pytest.raises(accounting.AccountingInputError):
        accounting.assess_budget(())
    with pytest.raises(accounting.AccountingInputError):
        accounting.assess_budget(list(unknown()))  # type: ignore[arg-type]


@pytest.mark.parametrize("total", [D("53.000000000001"), D("53.01"), D(1000)])
def test_verified_breach_precedes_unknown_categories(total: Decimal) -> None:
    lines, proofs = costs({CATEGORIES[0]: total})
    result = accounting.assess_synthetic_budget(lines, proofs)
    assert result.status == "OVER_BUDGET" and result.verified_subtotal_usd == total
    assert result.total_cost_usd is None and len(result.unresolved_categories) == 21


def test_sum_across_categories_not_just_one_line_proves_breach() -> None:
    lines, proofs = costs({CATEGORIES[0]: D(25), CATEGORIES[1]: D("28.01")})
    result = accounting.assess_synthetic_budget(lines, proofs)
    assert result.status == "OVER_BUDGET" and result.verified_subtotal_usd == D("53.01")


@pytest.mark.parametrize("total", [D(0), D(1), D("52.999999999999"), D(53)])
def test_unknown_required_cost_remains_inconclusive_at_or_below_boundary(total: Decimal) -> None:
    lines, proofs = costs({CATEGORIES[0]: total})
    result = accounting.assess_synthetic_budget(lines, proofs)
    assert result.status == "INCONCLUSIVE" and result.total_cost_usd is None


@pytest.mark.parametrize(
    "total,expected",
    [
        (D(0), "WITHIN_BUDGET"),
        (D("52.999999999999"), "WITHIN_BUDGET"),
        (D("53.00"), "WITHIN_BUDGET"),
        (D("53.000000000001"), "OVER_BUDGET"),
    ],
)
def test_complete_supported_synthetic_budgets_exact_boundary(total: Decimal, expected: str) -> None:
    lines, proofs = complete(total)
    result = accounting.assess_synthetic_budget(lines, proofs)
    assert result.status == expected and result.total_cost_usd == total
    assert len(result.complete_categories) == 22 and not result.unresolved_categories
    assert result.synthetic_only and result.allocation_admission == "NOT_EVALUATED"
    assert not result.live_authorized and not result.scientific_scoring_authorized
    assert result.trading_authority == "NONE"


def test_lower_bound_receipt_counts_for_breach_but_not_complete_budget() -> None:
    for total, expected in [(D(53), "INCONCLUSIVE"), (D("53.01"), "OVER_BUDGET")]:
        lines, proofs = costs(
            {c: total if i == 0 else D(0) for i, c in enumerate(CATEGORIES)},
            lower_bounds=frozenset({CATEGORIES[0]}),
        )
        result = accounting.assess_synthetic_budget(lines, proofs)
        assert result.status == expected and result.total_cost_usd is None


def test_all_unknown_s01_total_and_conclusion_cannot_be_promoted() -> None:
    result = accounting.assess_budget(unknown())
    assert result.status == "INCONCLUSIVE" and result.total_cost_usd is None
    assert result.verified_subtotal_usd == 0 and len(result.unresolved_categories) == 22
    assert accounting.total_cost(unknown()) is None
    assert accounting.cost_admission(unknown()) == "INCONCLUSIVE"


def test_numeric_estimates_without_receipt_are_not_verified_spend_or_complete_costs() -> None:
    lines = tuple(accounting.ExpenseLine(c, D(100), None) for c in CATEGORIES)
    result = accounting.assess_budget(lines)
    assert result.status == "INCONCLUSIVE" and not result.verified_categories
    assert result.total_cost_usd is None


def test_synthetic_provenance_cannot_be_used_for_real_budget_admission() -> None:
    lines, proofs = complete(D(0))
    result = accounting.assess_budget(lines, proofs)
    assert result.status == "INCONCLUSIVE" and len(result.unresolved_categories) == 22


def test_fabricated_real_receipts_with_correct_hashes_are_still_unapproved() -> None:
    lines = []
    proofs = []
    for c in CATEGORIES:
        raw = receipt(
            c, D(0), changes={"domain": "REVIEWED_RESEARCH_EXPENSE", "receipt_id": "invoice-" + c}
        )
        lines.append(accounting.ExpenseLine(c, D(0), hashlib.sha256(raw).hexdigest()))
        proofs.append(raw)
    result = accounting.assess_budget(tuple(lines), tuple(proofs))
    assert result.status == "INCONCLUSIVE" and not result.verified_categories
    assert accounting.assess_synthetic_budget(tuple(lines), tuple(proofs)).status == "INCONCLUSIVE"


@pytest.mark.parametrize(
    "changes",
    [
        {"verified": True},
        {"amount_usd": "1"},
        {"category": CATEGORIES[1]},
        {"currency": "EUR"},
        {"cohort_start": "2026-10-03"},
        {"cohort_end": "2027-07-30"},
        {"city_days": True},
        {"city_days": 460},
        {"coverage": "PARTIAL_BUT_FREE"},
        {"includes_failure_abstention_expenses": False},
        {"source_document_sha256": "x" * 64},
        {"domain": "VERIFIED"},
        {"receipt_id": "not-synthetic"},
    ],
)
def test_malformed_or_wrong_scope_receipts_rejected(changes: dict[str, object]) -> None:
    raw = receipt(CATEGORIES[0], D(0), changes=changes)
    lines = list(unknown())
    lines[0] = accounting.ExpenseLine(CATEGORIES[0], D(0), hashlib.sha256(raw).hexdigest())
    with pytest.raises(accounting.AccountingInputError):
        accounting.assess_synthetic_budget(tuple(lines), (raw,))


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"schema":1,"schema":2}', b"x" * 8193])
def test_truncated_duplicate_or_oversized_receipt_json_rejected(raw: bytes) -> None:
    lines = list(unknown())
    lines[0] = accounting.ExpenseLine(CATEGORIES[0], D(0), hashlib.sha256(raw).hexdigest())
    with pytest.raises(accounting.AccountingInputError):
        accounting.assess_synthetic_budget(tuple(lines), (raw,))


def test_missing_raw_bytes_is_unknown_and_tampering_or_orphan_receipts_rejected() -> None:
    lines, proofs = complete(D(53))
    assert accounting.assess_synthetic_budget(lines, proofs[1:]).status == "INCONCLUSIVE"
    changed = proofs[0] + b" "
    with pytest.raises(accounting.AccountingInputError, match="UNREFERENCED"):
        accounting.assess_synthetic_budget(lines, (changed, *proofs[1:]))
    with pytest.raises(accounting.AccountingInputError, match="DUPLICATE_RECEIPT"):
        accounting.assess_synthetic_budget(lines, (proofs[0], proofs[0]))


def test_aggregate_result_is_exact_under_hostile_decimal_context_and_input_order() -> None:
    lines, proofs = complete(D("53.000000000001"))
    expected = accounting.assess_synthetic_budget(lines, proofs)
    with localcontext() as context:
        context.prec = 2
        assert (
            accounting.assess_synthetic_budget(tuple(reversed(lines)), tuple(reversed(proofs)))
            == expected
        )
    assert expected.status == "OVER_BUDGET"


def test_supported_large_nonnegative_cost_sum_does_not_overflow_before_rejection() -> None:
    lines, proofs = costs({c: D("1000000000000") for c in CATEGORIES})
    result = accounting.assess_synthetic_budget(lines, proofs)
    assert result.status == "OVER_BUDGET" and result.total_cost_usd == D("22000000000000")


@pytest.mark.parametrize(
    "field,value",
    [
        ("live_authorized", True),
        ("scientific_scoring_authorized", True),
        ("trading_authority", "PAPER"),
        ("allocation_admission", "WITHIN_BUDGET"),
        ("status", "WITHIN_BUDGET"),
    ],
)
def test_budget_assessment_cannot_confer_authority_or_turn_unknowns_into_within(
    field: str, value: object
) -> None:
    with pytest.raises(accounting.AccountingInputError):
        # Deliberately invalid Literal values: verify runtime rejection.
        replace(accounting.assess_budget(unknown()), **{field: value})  # type: ignore[arg-type]


def test_module_has_no_source_client_file_loader_credentials_scheduler_or_scorer() -> None:
    tree = ast.parse(Path(accounting.__file__ or "").read_text())
    allowed = {
        "__future__",
        "hashlib",
        "json",
        "re",
        "collections",
        "dataclasses",
        "decimal",
        "functools",
        "typing",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(name.name.split(".")[0] in allowed for name in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in allowed
    assert D(53) == accounting.BUDGET_USD and D("0.05") == accounting.CITY_DAY_LIMIT_USD


def test_real_entrypoint_requires_exact_reviewed_hash_approval_not_schema_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Controlled unit-test trust catalogue, not a real invoice or shipped approval.
    lines = []
    proofs = []
    for i, category in enumerate(CATEGORIES):
        amount = D(53) if i == 0 else D(0)
        raw = receipt(
            category,
            amount,
            changes={"domain": "REVIEWED_RESEARCH_EXPENSE", "receipt_id": "unit-only-" + category},
        )
        lines.append(accounting.ExpenseLine(category, amount, hashlib.sha256(raw).hexdigest()))
        proofs.append(raw)
    hashes = frozenset(hashlib.sha256(raw).hexdigest() for raw in proofs)
    monkeypatch.setattr(
        accounting, "_APPROVED_REAL_RECEIPT_SHA256", hashes - {lines[0].receipt_sha256}
    )
    assert accounting.cost_admission(tuple(lines), tuple(proofs)) == "INCONCLUSIVE"
    monkeypatch.setattr(accounting, "_APPROVED_REAL_RECEIPT_SHA256", hashes)
    result = accounting.assess_budget(tuple(lines), tuple(proofs))
    assert result.status == "WITHIN_BUDGET" and result.total_cost_usd == 53
    assert not result.synthetic_only and not result.live_authorized


def test_real_entrypoint_verified_lower_bound_exceeds_budget_before_unknowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = receipt(
        CATEGORIES[0],
        D("53.01"),
        coverage="LOWER_BOUND",
        changes={"domain": "REVIEWED_RESEARCH_EXPENSE", "receipt_id": "unit-only-known-expense"},
    )
    lines = list(unknown())
    digest = hashlib.sha256(raw).hexdigest()
    lines[0] = accounting.ExpenseLine(CATEGORIES[0], D("53.01"), digest)
    monkeypatch.setattr(accounting, "_APPROVED_REAL_RECEIPT_SHA256", frozenset({digest}))
    result = accounting.assess_budget(tuple(lines), (raw,))
    assert result.status == "OVER_BUDGET" and result.total_cost_usd is None
    assert result.verified_subtotal_usd == D("53.01")
