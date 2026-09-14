# GDP evidence feasibility — PARKED

## September 14, 2026 disposition

The user-supplied canonical oversight assessment completed this documentary
milestone as **PARKED**. It did not acquire canonical decision-time evidence.
Its load-bearing gaps under the frozen exact-fee semantics are authoritative
`fee_waiver_expiration_time` meaning/applicability and participant-category plus
fill/rounding-accumulator context. These are scoped unresolved dependencies, not
a claim that all possible fee models or public sources are unavailable.

Reopening requires first-party waiver authority (or direct authority for the
selected market's effective fee), a reviewed participant/fill treatment, and
canonical provenance for selected event/series state, applicable fee changes and
contemporaneous schedule. An alternative conservative fee model would require
its own explicit scientific-method change; it is not silently substituted here.
Only then consider the narrow fee composition and current-PDF wording adapter.

GDP remains `EVIDENCE_INCOMPLETE`; PR #157's identity repair remains closed.
The next executable engineering task is M9-E1 event promotion authority, recorded
in `M9_E1_EVENT_PROMOTION_AUTHORITY.md`. The source leads and original bounded
assessment instructions below are retained as history, not an active request
for another GDP review.

## Starting state

PR #157 is merged as `524d04092cb23364073db21185ffa4dc91cc35b8`.
The reported integration findings are closed by the supplied independent
reviews; the final repair's exact-head CI passed. Post-merge verification is
tracked at [run 34866085527](https://github.com/kensgithubaccount/kalsh3/actions/runs/34866085527).

This milestone asks whether the missing external facts can support the smallest
justified GDP adapter change. It does not repeat the completed identity repair.
The current research decision remains `EVIDENCE_INCOMPLETE`.

## First-party discovery completed September 14, 2026

These are documentation leads and unresolved questions, not acquired canonical
decision evidence. Public documentation was read; no project acquisition
entrypoint, account endpoint, launcher, or live experiment was run. Documentation
schemas and examples do not establish actual KXGDP values for a decision time.

| Fact | First-party source and what it establishes | Work still required |
| --- | --- | --- |
| General formula and series schedule | The [fee PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf) identifies a July 7, 2026 effective date, general maker/taker formulas, and a KXGDP entry. | Preserve the actual version and validate its applicability. The fee parser's assumed document wording/layout must be checked against actual bytes. This is adapter feasibility work, not an integration blocker. |
| Event overrides | [Get Event Fee Changes](https://docs.kalshi.com/api-reference/events/get-event-fee-changes) documents event overrides over parent-series fees, null/null clearing, a scheduled timestamp, and cursor pagination. | Assess a narrowly scoped public-read adapter for the selected event, complete page traversal, effective intervals, and explicit clearing. Establish coverage before interpreting absence. Do not invent a show_historical parameter for this endpoint. |
| Series changes and timing | [Get Series Fee Changes](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes) documents series filtering, show_historical, fee type/multiplier, and scheduled timestamps. | Reconcile initial series state and transitions with event overrides at the exact decision time. A retrieved empty array alone does not establish exhaustive authority. |
| Market waiver and contract fields | [Get Event](https://docs.kalshi.com/api-reference/events/get-event) shows nested market rules, timing fields, and fee_waiver_expiration_time. [Get Series](https://docs.kalshi.com/api-reference/market/get-series) exposes contract document URLs, settlement sources, and series fee fields. | Inspect the selected event and market under existing public-read permissions. Determine waiver semantics and applicable precedence. Establish observation quarter, publication time, settlement cutoff, source, and revision policy from contract rules; never infer them from the ticker. |
| Precision and fills | [Fee Rounding](https://docs.kalshi.com/getting_started/fee_rounding) documents direct-member balance precision of $0.0001, non-direct precision of $0.01, model-fee rounding to $0.000001, and an order-level rounding accumulator. | Bind the researched member category and fill assumptions without accessing private accounts. Account for rounding and rebates; do not assume cent rounding is universal or infer member category from the user interface. If it cannot be bound, retain separate scenarios or mark the exact fee unproven. |

The fee schedule, rounding documentation, and their effective scope need to be
reconciled. Finding these sources does not establish historical applicability,
actual override values, the selected member category, or contract semantics.

## Bounded deliverable and completion rule

Produce one evidence matrix with the exact source URL and clause/field, retained
source provenance where supported, applicability interval, proven fact,
unresolved dependency, and disposition. Distinguish documentary feasibility
from canonical evidence acquisition and from implementation acceptance.

Return exactly one overall disposition:

- **IMPLEMENTABLE:** the required facts are demonstrably obtainable and
  reconcilable for the bounded experiment. Specify the smallest adapter change,
  source scope, exact fee/rounding cases, and source-to-decision-to-outcome
  acceptance criteria. Do not claim the new adapter is already accepted.
- **PARKED:** a named necessary fact is unavailable or contradictory within the
  permitted scope. List the exact evidence that would reopen the experiment and
  select the next eligible project task from the authoritative milestone plan.
  Repeated generic repair reviews are not a remedy for missing source facts.

Completion is an evidence disposition, not a profitable signal. No COMPLETE
issuance, production influence, scientific-clock activation, account action,
order, or capital deployment is authorized by this document.

## Oversight continuation

Use the current main commit and verify its ancestry to the accepted merge.
Carry forward the reported closed integration findings. Read this brief and
finish the evidence matrix yourself under the existing public-read scope,
recording a bounded proposal or explicit parking decision. Keep one consolidated
list of remaining evidence requirements. Do not generate another oversight
handoff in place of the assessment.
