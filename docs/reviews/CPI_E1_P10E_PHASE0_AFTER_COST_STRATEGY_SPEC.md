# CPI-E1-P10E Phase 0 — after-cost strategy/economics methodology freeze

## Result

Phase 0 freezes the first legitimate historical economics hypothesis after the
P10D diagnostic. It computes no strategy P&L, return, expectancy, Sharpe,
trade win-rate, fee-adjusted performance, break-even price, or capacity result.
The rule is explicitly **POST-P10D / EXPLORATORY**. It is not preregistered
before predictor testing, independent validation, out-of-sample validation, or
durable proof.

The deterministic artifact is
`docs/reviews/artifacts/cpi-p10e-phase0-after-cost-spec/spec.json`, digest
`9b26a6b96c1b1dbb41125704953a4bc527c77e6010ee17c4fd28dd5a8a23924a`.

## Recovered authority

P10A/P9A machinery establishes the historical displayed crossing convention:
YES uses `yes_ask`; NO uses the positively established `no_ask`, which the
canonical P9A parser derives as `1 - yes_bid` and records as
`DERIVED_COMPLEMENT`. Midpoint, last trade, inferred quotes, later quotes, and
current data are excluded. P9A proves quote evidence only: it does not prove
displayed size, depth, queue position, fills, or capacity.

P9B is the reusable historical fee authority boundary. Its exact-row consumer
`consume_taker_fee_authority()` rejects every current P10D execution row:
exact historical fee coverage is zero. The directly evidenced 0.07 quadratic
formula and next-cent rounding cannot be promoted to continuous exact authority
for this cohort. Therefore Phase 1 is fail-closed:

**BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE**

The reviewed P7/P10A settlement binding supplies the only admissible truth and
canonical simple-binary payoff: one winning contract pays `$1.0000`, a losing
contract pays `$0.0000`. This is an authority binding, not a new settlement
inference.

## Frozen hypothesis and reporting

For each P10D primary-eligible sibling, Reuters is YES iff its point value is
strictly above the contract threshold, otherwise NO. The market is YES iff
`yes_ask > 0.5`, NO iff `< 0.5`, and TIE at exactly `0.5`. Only disagreement
signals trade, with one contract on the Reuters side; agreement and ties are no
trade. Reuters values are never converted to probabilities.

The candidate universe and reason codes are fixed before any economics run.
`NO TRADE BY STRATEGY` and `TRADE SIGNAL BUT EXECUTION EVIDENCE UNAVAILABLE`
remain separate. Unavailable execution evidence is counted, never scored as
zero, and prevents a common denominator when it cannot be resolved without
selective exclusion.

Primary aggregation is one-contract net economics per sibling, arithmetic mean
within event, zero for an event with no disagreement signals, then equal weight
across the four events. Sibling-level totals and means are diagnostics labeled
NON-INDEPENDENT.

## Phase 1 protocol

The artifact freezes the planned sequence: bind a deterministic scorer, run
synthetic tests and structural authority preflight, seal before result exposure,
run exactly once, write a deterministic result, prohibit scorer changes after
exposure, rerun byte-identically, and submit for canonical review. No Phase 1
run occurs in this Phase 0 checkpoint.

`research_only=true`, `production_influence=0`, and trading authority remain
unchanged. The frozen A0.3 checkout remains untouched.

