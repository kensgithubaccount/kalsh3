"""Pure offline S01 aggregate accounting. No source/experiment authority or I/O.

Real receipt approvals are deliberately empty. Hash/schema checks alone do not
authenticate an invoice, quote, complete cohort coverage or provider entitlement.
Synthetic rehearsal is a separate entrypoint with explicitly synthetic results.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from functools import wraps
from typing import Final, Literal, cast

D = Decimal
BUDGET_USD: Final = D("53.00")
CITY_DAY_LIMIT_USD: Final = D("0.05")
_MAX_LINE_USD: Final = D("1000000000000")
CANONICAL_CATEGORIES: Final = (
    "license_access_subscription_total",
    "data_get_per_1000",
    "metadata_effective_per_1000",
    "source_storage_retrieval_per_gb",
    "source_to_processing_per_gb",
    "processing_to_evidence_per_gb",
    "raw_storage_per_gb_month",
    "journal_index_storage_per_gb_month",
    "storage_write_per_1000",
    "evidence_replay_read_per_gb",
    "replication_transfer_per_gb",
    "cpu_core_hour",
    "memory_gib_hour",
    "scratch_gb_hour",
    "host_day",
    "monitoring_immutability_kms_total",
    "market_evidence_capture_retention_total",
    "labor_hour",
    "local_power_kwh",
    "fixed_shared_capital_financing_total",
    "applicable_taxes_total",
    "other_unitemized_incremental_total",
)
CATEGORY_SET: Final = frozenset(CANONICAL_CATEGORIES)
# Future additions require separately reviewed evidence and a new code revision.
# No API parameter, environment setting or caller-supplied flag can add approvals.
_APPROVED_REAL_RECEIPT_SHA256: Final[frozenset[str]] = frozenset()
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_AMOUNT = re.compile(r"[0-9]{1,13}(?:\.[0-9]{1,12})?\Z")
_RECEIPT_KEYS: Final = frozenset(
    (
        "schema",
        "domain",
        "receipt_id",
        "category",
        "currency",
        "amount_usd",
        "coverage",
        "cohort_start",
        "cohort_end",
        "city_days",
        "source_document_sha256",
        "includes_failure_abstention_expenses",
    )
)
Status = Literal["OVER_BUDGET", "INCONCLUSIVE", "WITHIN_BUDGET"]


class AccountingInputError(ValueError):
    """Malformed or incomplete accounting input; never an admission result."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise AccountingInputError(reason)


def exact[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        with localcontext(Context(prec=50)):
            return function(*args, **kwargs)

    return run


def money(value: Decimal, maximum: Decimal = _MAX_LINE_USD) -> Decimal:
    require(type(value) is D and value.is_finite(), "FINITE_DECIMAL_REQUIRED")
    require(D(0) <= value <= maximum, "NONNEGATIVE_BOUNDED_EXPENSE_REQUIRED")
    require(-12 <= cast(int, value.as_tuple().exponent) <= 12, "EXPENSE_PRECISION_UNSUPPORTED")
    return value


def digest_id(value: str) -> str:
    require(type(value) is str and _HEX.fullmatch(value) is not None, "SHA256_REQUIRED")
    return value


def amount_text(value: object) -> Decimal:
    require(
        type(value) is str and _AMOUNT.fullmatch(value) is not None, "USD_DECIMAL_TEXT_REQUIRED"
    )
    return money(D(cast(str, value)))


@dataclass(frozen=True, slots=True)
class ExpenseLine:
    category: str
    amount_usd: Decimal | None
    receipt_sha256: str | None

    def __post_init__(self) -> None:
        require(type(self.category) is str and self.category in CATEGORY_SET, "UNEXPECTED_CATEGORY")
        if self.amount_usd is not None:
            money(self.amount_usd)
        if self.receipt_sha256 is not None:
            digest_id(self.receipt_sha256)
            require(self.amount_usd is not None, "RECEIPT_WITHOUT_STATED_AMOUNT")

    @classmethod
    def from_record(cls, record: dict[str, object]) -> ExpenseLine:
        require(
            type(record) is dict and set(record) == {"category", "amount_usd", "receipt_sha256"},
            "EXACT_EXPENSE_FIELDS_REQUIRED",
        )
        category, amount, receipt = (
            record[k] for k in ("category", "amount_usd", "receipt_sha256")
        )
        require(type(category) is str, "CATEGORY_TEXT_REQUIRED")
        require(receipt is None or type(receipt) is str, "RECEIPT_SHA256_TEXT_REQUIRED")
        return cls(
            cast(str, category),
            None if amount is None else amount_text(amount),
            cast(str | None, receipt),
        )


@dataclass(frozen=True, slots=True)
class BudgetAssessment:
    status: Status
    verified_subtotal_usd: Decimal
    total_cost_usd: Decimal | None
    verified_categories: tuple[str, ...]
    complete_categories: tuple[str, ...]
    unresolved_categories: tuple[str, ...]
    synthetic_only: bool
    live_authorized: Literal[False] = False
    scientific_scoring_authorized: Literal[False] = False
    trading_authority: Literal["NONE"] = "NONE"
    allocation_admission: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"

    def __post_init__(self) -> None:
        money(self.verified_subtotal_usd, D("22000000000000"))
        require(type(self.synthetic_only) is bool, "EXPLICIT_RESEARCH_DOMAIN_REQUIRED")
        require(
            self.live_authorized is False
            and self.scientific_scoring_authorized is False
            and self.trading_authority == "NONE"
            and self.allocation_admission == "NOT_EVALUATED",
            "ACCOUNTING_CONFERS_NO_AUTHORITY",
        )
        require(
            set(self.complete_categories) <= set(self.verified_categories) <= CATEGORY_SET,
            "RESULT_CATEGORY_INTEGRITY",
        )
        require(
            set(self.unresolved_categories) == CATEGORY_SET - set(self.complete_categories),
            "RESULT_UNRESOLVED_INTEGRITY",
        )
        if self.total_cost_usd is not None:
            money(self.total_cost_usd, D("22000000000000"))
            require(
                set(self.complete_categories) == CATEGORY_SET
                and self.total_cost_usd == self.verified_subtotal_usd,
                "TOTAL_REQUIRES_COMPLETE_PROVENANCE",
            )
        require(
            self.status
            == (
                "OVER_BUDGET"
                if self.verified_subtotal_usd > BUDGET_USD
                else "INCONCLUSIVE"
                if self.total_cost_usd is None
                else "WITHIN_BUDGET"
            ),
            "RESULT_BUDGET_PRECEDENCE",
        )


@dataclass(frozen=True, slots=True)
class _Receipt:
    sha256: str
    receipt_id: str
    category: str
    amount: Decimal
    complete: bool
    synthetic: bool


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    require(len(pairs) == len({k for k, _ in pairs}), "DUPLICATE_JSON_FIELD")
    return dict(pairs)


def _receipt(raw: bytes) -> _Receipt:
    require(type(raw) is bytes and 0 < len(raw) <= 8192, "BOUNDED_RAW_RECEIPT_REQUIRED")
    try:
        record = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AccountingInputError("INVALID_RECEIPT_JSON") from exc
    require(type(record) is dict and set(record) == _RECEIPT_KEYS, "EXACT_RECEIPT_FIELDS_REQUIRED")
    require(record["schema"] == "s01-expense-receipt-v1", "RECEIPT_SCHEMA")
    require(
        record["domain"] in ("REVIEWED_RESEARCH_EXPENSE", "SYNTHETIC_FIXTURE"), "RECEIPT_DOMAIN"
    )
    require(
        type(record["receipt_id"]) is str and 0 < len(record["receipt_id"]) <= 128, "RECEIPT_ID"
    )
    synthetic = record["domain"] == "SYNTHETIC_FIXTURE"
    require(record["receipt_id"].startswith("SIM-") == synthetic, "RECEIPT_NAMESPACE")
    require(
        type(record["category"]) is str and record["category"] in CATEGORY_SET, "RECEIPT_CATEGORY"
    )
    require(
        record["currency"] == "USD"
        and record["cohort_start"] == "2027-01-01"
        and record["cohort_end"] == "2027-07-31"
        and type(record["city_days"]) is int
        and record["city_days"] == 1060,
        "RECEIPT_CURRENCY_OR_COHORT",
    )
    require(record["coverage"] in ("COMPLETE", "LOWER_BOUND"), "RECEIPT_COVERAGE")
    require(
        record["includes_failure_abstention_expenses"] is True, "FAILURES_AND_ABSTENTIONS_REQUIRED"
    )
    digest_id(record["source_document_sha256"])
    return _Receipt(
        hashlib.sha256(raw).hexdigest(),
        record["receipt_id"],
        record["category"],
        amount_text(record["amount_usd"]),
        record["coverage"] == "COMPLETE",
        synthetic,
    )


@exact
def _assess(
    lines: tuple[ExpenseLine, ...], receipts: tuple[bytes, ...], *, synthetic: bool
) -> BudgetAssessment:
    require(
        type(lines) is tuple and all(type(line) is ExpenseLine for line in lines),
        "EXPENSE_TUPLE_REQUIRED",
    )
    categories = tuple(line.category for line in lines)
    require(len(categories) == len(set(categories)), "DUPLICATE_CATEGORY")
    require(
        len(categories) == 22 and set(categories) == CATEGORY_SET, "EXACT_22_CATEGORIES_REQUIRED"
    )
    require(type(receipts) is tuple and len(receipts) <= 22, "BOUNDED_RECEIPT_TUPLE_REQUIRED")
    parsed = tuple(_receipt(raw) for raw in receipts)
    require(
        len(parsed) == len({r.sha256 for r in parsed}) == len({r.receipt_id for r in parsed}),
        "DUPLICATE_RECEIPT",
    )
    referenced = {line.receipt_sha256 for line in lines if line.receipt_sha256 is not None}
    require(all(r.sha256 in referenced for r in parsed), "UNREFERENCED_RECEIPT")
    by_digest = {r.sha256: r for r in parsed}
    verified: dict[str, Decimal] = {}
    complete: set[str] = set()
    for line in lines:
        proof = by_digest.get(line.receipt_sha256 or "")
        if proof is None:
            continue
        require(
            proof.category == line.category and proof.amount == line.amount_usd,
            "RECEIPT_AMOUNT_OR_CATEGORY_MISMATCH",
        )
        accepted = (
            proof.synthetic
            if synthetic
            else not proof.synthetic and proof.sha256 in _APPROVED_REAL_RECEIPT_SHA256
        )
        if not accepted:
            continue
        verified[line.category] = proof.amount
        if proof.complete:
            complete.add(line.category)
    known = sum(verified.values(), D(0))
    total = known if complete == CATEGORY_SET else None
    status: Status = (
        "OVER_BUDGET"
        if known > BUDGET_USD
        else "INCONCLUSIVE"
        if total is None
        else "WITHIN_BUDGET"
    )
    return BudgetAssessment(
        status,
        known,
        total,
        tuple(c for c in CANONICAL_CATEGORIES if c in verified),
        tuple(c for c in CANONICAL_CATEGORIES if c in complete),
        tuple(c for c in CANONICAL_CATEGORIES if c not in complete),
        synthetic,
    )


def assess_budget(
    lines: tuple[ExpenseLine, ...], receipts: tuple[bytes, ...] = ()
) -> BudgetAssessment:
    """Real research budget: receipt bytes AND separately reviewed hash approvals required."""
    return _assess(lines, receipts, synthetic=False)


def assess_synthetic_budget(
    lines: tuple[ExpenseLine, ...], receipts: tuple[bytes, ...] = ()
) -> BudgetAssessment:
    """SIM-only algebra rehearsal; never authenticates real expenses or source feasibility."""
    return _assess(lines, receipts, synthetic=True)


def total_cost(lines: tuple[ExpenseLine, ...], receipts: tuple[bytes, ...] = ()) -> Decimal | None:
    """Null until all22 expense totals have complete accepted real provenance."""
    return assess_budget(lines, receipts).total_cost_usd


def cost_admission(lines: tuple[ExpenseLine, ...], receipts: tuple[bytes, ...] = ()) -> Status:
    """Aggregate53USD classification only; does not validate city-day allocations."""
    return assess_budget(lines, receipts).status
