# S01 accounting helper repair V1 — offline research only

Parent Issue #234. Baseline `3cd2695592e10132ed54773ccb9d0fbfbcbe635f`.
This is an additive research helper, test suite and specification. It does not
replace the sealed S01 module or change PR #267, October evidence, source
feasibility conclusions, model code, acquisition paths or operating budgets.

The original sealed S01 manifest remains
`cf6f7948d11512c2c2285e26c9f1fd59e44180832fd55e71e9b57e2a8012690a`.
Independent review recorded that its `total_cost` accepted incomplete category
sets and its status helper checked unknowns before an already known breach.
Both original failures are reproduced separately without changing those files.

## Input contract and unchanged budget

`services/forecasting/wn_s01_accounting_v1.py` declares exactly the original
22 category identifiers. Every assessment requires all22 exactly once.
Missing, duplicate, unexpected or differently spelled identifiers are rejected
before arithmetic. `ExpenseLine.from_record` accepts only category,
amount_usd and receipt_sha256; unknown fields cannot supply a verified flag,
change the budget or grant authority. Monetary text is bounded finite
nonnegative USD Decimal, with up to12 fractional digits and no float coercion.
The per-line bound is $1e12;22 lines can sum to $22e12 without losing a breach.
Unsupported numeric representations are rejected, never rounded into budget.

The aggregate limit remains **$53.00**. The separate **$0.05 per city-day**
limit and all1,060 allocation rows remain part of the unchanged original
protocol. This module does not validate those allocations. A result says
`allocation_admission=NOT_EVALUATED` and cannot close that separate gate.

For structurally valid complete category sets:

1. Sum the amounts supported by accepted provenance. All expenses are
   nonnegative, so accepted incurred lower bounds can establish a breach.
   If that sum is strictly greater than53, return **OVER_BUDGET** first,
   even if other required amounts or complete coverage remain unknown.
2. Otherwise, if any required category lacks a fully priced complete
   accepted receipt, return **INCONCLUSIVE**, with `total_cost_usd=None`.
   A verified subtotal is a lower bound, not an imputation of zero to other costs.
3. Return **WITHIN_BUDGET** only if every canonical category is complete,
   fully priced and supported, and the exact total is at most53.

Exactly53 with unknowns is INCONCLUSIVE; exactly53 with all complete accepted
provenance is WITHIN_BUDGET. An arbitrarily small supported excess is
OVER_BUDGET. Fixed Decimal context makes these boundaries independent of the
caller's rounding context. Input order does not change the result.

`total_cost` validates the same canonical contract and remains null until
complete accepted real provenance exists. `cost_admission` returns the same
three-way aggregate classification. A lower-bound-only receipt contributes to
breach detection but cannot complete a category's total.

## Acceptable provenance and current absence of approvals

The real `assess_budget` entrypoint requires retained receipt bytes, their
matching SHA-256 and a separately reviewed hash approval. The shipped real
approval set is **empty**. There is no caller-controlled approval flag,
registry argument, environment override, network lookup or file loader.
A matching digest or well-formed invoice-shaped JSON alone proves no authority.
All currently retained S01 costs therefore remain unknown, with null total and
INCONCLUSIVE accounting; source feasibility remains NO-GO.

Receipt schema `s01-expense-receipt-v1` binds category, exact amount, USD,
January1–July31 2027 cohort,1,060 city-days, COMPLETE versus LOWER_BOUND coverage,
unique receipt ID, supporting-document SHA-256 and inclusion of failed/ABSTAIN
expenses. Duplicate/unknown JSON keys, wrong category/amount/cohort/currency,
false failure-cost inclusion, duplicate receipt identities, orphan receipts,
malformed/oversized payloads and unsupported values are rejected. Missing raw
bytes or absent approval leave the category unresolved.

Before any future real approval is added in a new reviewed revision, an
independent reviewer must inspect the retained source-document bytes and their
digest, publisher/custody, amount derivation and applicable dates; confirm the
invoice or conservative bound covers the declared category without double
counting; and verify taxes, failures, ABSTAIN/idle days and recurring expense
scope. A quote or subtotal without justified complete coverage is not a
complete-category approval. This task creates no such approved evidence.

`assess_synthetic_budget` is a separate explicit rehearsal entrypoint. It
accepts only SIM-namespace synthetic receipts and marks every result synthetic.
It cannot authenticate real costs. Tests also inject an in-memory unit-test
approval set to exercise the real entrypoint's acceptance/denial branches;
those values are fictional and no approval is written into the shipped code.

Every result has `live_authorized=False`,
`scientific_scoring_authorized=False`, `trading_authority=NONE`. WITHIN_BUDGET
classifies aggregate accounting only; it proves neither source access nor
per-row admissibility, funding, scientific validity, executability or profitability.

## Verification and preservation

The focused tests cover every missing and duplicate category, unexpected
identifiers and fields, negative/nonfinite/unsupported values, unpriced costs,
known sums exceeding53 with unknowns, exact53 and fractional boundaries,
lower-bound versus complete receipts, forged/unapproved provenance, tampered
or missing bytes, failed-day coverage, hostile Decimal contexts, input order,
large supported sums, synthetic/real separation and immutable authority flags.
All fixtures are synthetic; there are no source requests or real scores.

Run with network denied:

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' /Users/ksyme/miniforge3/envs/kalsh3/bin/python -B -m pytest -p no:cacheprovider tests/test_wn_s01_accounting_v1.py -q
```

Exact-head commands, identities, file hashes, regression outcomes, original
seal checks and a fresh detached independent review are retained in the separate
candidate handoff. No merge is recommended before that independent PASS.

**LIVE NOT AUTHORIZED. SCIENTIFIC SCORING NOT AUTHORIZED. TRADING AUTHORITY NONE.**
