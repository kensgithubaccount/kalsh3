# M26C — Outcome Linkage, Calibration, and Evidence-Based Agent Evaluation

## Result and architecture audit

M26C is a downstream research-evaluation layer over immutable M26B receipts. The audit found that
`DecisionReceipt` already retains the historical agent/version, conclusion, exact Decimal economics,
evidence IDs, UTC decision time, and Event Edge instrument as `market_ticker:OutcomeSide`. Its receipt
identity binds the immutable `TradeCandidate.candidate_id`. Current registry authority controls only new
receipt writes; historical receipt reads deliberately do not consult the current registry. M26C preserves
that boundary and never modifies receipt history.

Contract Intelligence's `SettlementRecord.eligible_training_label` is the authoritative outcome gate:
`finalized_at is not None` and reconciliation is `MATCHED`. It has no durable settlement store or explicit
revision/supersession identity. M26C therefore snapshots and hashes the complete settlement facts into each
evaluation. It never guesses which conflicting authoritative revision supersedes another and never
overwrites an earlier evaluation. The Learning layer supplies exact Decimal Brier scoring, which M26C
reuses. Its event-level intervals and 50-event convention are deliberately not used because M26B receipts
do not preserve a proven independent event identity.

Existing append-only SQLite evidence conventions use canonical JSON, SHA-256 integrity, WAL, synchronous
commits, deterministic indexes, and update/delete triggers. `EvaluationStore` follows the strongest of
those conventions in a separate `receipt_evaluations` table that may share the dashboard database file but
has no execution dependency.

## Outcome link, target orientation, and settlement eligibility

`EvaluationTarget` is a frozen proof contract containing market ticker, explicit YES/NO `OutcomeSide`,
source kind and ID, source observation time, source content hash, and a target-contract version. Event Edge
requires `source_kind == "trade-candidate"` and the `TradeCandidate` source ID to equal its content hash.
Its target must exactly equal the receipt instrument identity,
and recomputing the M26B receipt ID from the historical agent/version plus target source must reproduce the
receipt ID. Settlement ticker must exactly match the target. String resemblance, price, probability,
decision type, and current registry state are never linkage evidence.

`ReceiptOutcomeLink` binds receipt ID, historical agent/version, receipt instrument, explicit target,
settlement hash/ticker/result/timestamps/reconciliation, provenance hashes, and zero influence. Wrong
ticker or contradictory orientation is rejected. A source binding that cannot reproduce the receipt ID is
retained as `TARGET_UNPROVEN` without a score.

Only settlements satisfying `SettlementRecord.eligible_training_label` and consistent simple-binary
fields can be
scored. Unfinalized records are `OUTCOME_NOT_FINAL`; both `UNRECONCILED` and `MISMATCH` are
`RECONCILIATION_MISMATCH`; non-binary outcomes and contradictions (`YES + 0` or `NO + 1`) are
`UNSUPPORTED`. No determined-only, disputed,
provisional, close-price, news, UI, or guessed label is used. Excluded records remain persisted with their
reason and contain no realized target or outcome metric.

## Temporal leakage policy

The explicit source observation must be no later than the receipt decision time, and the receipt decision
must be strictly earlier than settlement `determined_at`. Finalization remains separately required. A
reverse-time or future-informed record becomes `TEMPORAL_LEAKAGE_RISK`, remains in the evidence ledger, and
cannot contribute to Brier, calibration, counterfactual diagnostics, or performance aggregates. Replay and
evaluation timestamps do not substitute for the historical source/decision chronology.

## Evaluation identity, persistence, and revisions

Evaluation identity is SHA-256 over canonical immutable inputs with the versioned domain/policy separator
`m26c-receipt-outcome-v1`: receipt ID, complete settlement hash, and explicit target. A timestamp never
creates uniqueness. A logical replay is idempotent even when its processing-only `evaluated_at` differs;
the first persisted timestamp remains unchanged as audit history. The logical comparison excludes only
`evaluated_at` and still binds every receipt, settlement, target, eligibility, metric, economics,
production-influence, and policy fact. Changed substantive content under the same identity is rejected,
and the existing row is fully restored and integrity-validated before replay comparison. A separate
settlement-track identity binds stable outcome facts (market, determination
time, rules/semantics, result, and value) while excluding maturation evidence such as finalization,
reconciliation state, exchange-content hash, and reconciled source observation. This permits append-only maturation from unfinalized or
reconciliation-excluded evidence to finalized `MATCHED` evidence on the same track. Every attempt remains
readable. For one receipt and policy version, a second different eligible final evaluation fails closed;
Contract Intelligence cannot yet prove authoritative supersession. Different policy versions form separate
tracks and may evaluate the same receipt and settlement without overwriting historical policy results.

Effective selection is deterministic, not insertion-based: within one receipt/policy it prefers eligible,
then reconciliation-resolved lifecycle progress, then non-final evidence, with finalization time,
evaluation time, and evaluation ID as immutable tie-breakers. Metrics consume one effective attempt per
receipt. Thus one provisional and one final attempt mean one decision, two attempts, and one effective
eligible evaluation.

The append-only SQLite ledger stores canonical JSON and its SHA-256, exact Decimal strings, exact UTC
microseconds, indexed metadata, and database-enforced `production_influence = '0'`. UPDATE and DELETE are
blocked by triggers. Reads re-hash content, restore the frozen object, require byte-for-byte canonical JSON,
and compare indexed metadata. Corruption raises an explicit typed error; the dashboard renders evaluation
history unavailable rather than a fake empty state. Queries cover ID, receipt/policy effective history,
complete agent/version history, recent display history, market family, and aggregate counts. Recent display
limits never supply performance denominators. Historical evaluation reads do not consult current authority.

## Event Edge metrics and Cross-Market decision

For eligible Event Edge receipts, the realized target is oriented to the historical side. M26C uses the
Learning/Forecasting Decimal Brier implementation: `(p-y)^2`. If the receipt preserves a comparable
historical executable probability, it also computes market Brier and `market_brier - model_brier`; positive
means better forecast accuracy than that historical market baseline. This is market-relative forecast
quality, never profit.

When executable price, fee, slippage, side, and binary payout all exist, the optional
`counterfactual_unit_value` is `binary payoff - executable price - fee - slippage` for one hypothetical
contract under the frozen research assumptions. It is available for all decision classes, including
NO_TRADE, and is never called P&L, realized P&L, profit, ROI, or an actual trade result. Missing inputs leave
it `None`. WOULD_TRADE, NO_TRADE, BLOCKED_BY_RISK, and INSUFFICIENT_EVIDENCE are all retained and are not
collapsed into wins/losses.

Cross-Market is deliberately unscoreable in M26C. One Kalshi binary settlement cannot prove a two-venue
discrepancy thesis, both legs, fills, spread, or economics. Its persisted decisions remain visible, while
the Agent surface says performance unavailable. No Cross-Market Brier, spread, arbitrage, leg, or execution
result is fabricated.

## Performance, uncertainty, calibration, and manifest

`AgentPerformance` is recomputed from immutable evaluation history and segmented by historical
`agent_id`/`agent_version`. It explicitly separates total persisted decisions, evaluation attempts,
outcome-linked decisions, effective eligible decisions, current exclusions, awaiting/no-evaluation
decisions, unique markets, and proven independent events. It also exposes decision states and valid average
Brier diagnostics. It does not store an independently mutable score or rank agents.

The receipt does not preserve `TradeCandidate.event_id`. Markets are therefore reported only as unique
markets and are never substituted into `EventContribution.event_id`. Proven unique-event count and the
event-level interval remain unavailable. Zero scoreable evaluations is `NO EVIDENCE`; any positive number
without proven event identity remains `EARLY / INCONCLUSIVE`, including 50 or more market tickers.

Calibration is explicitly descriptive decision-level calibration using versioned equal-width probability
deciles (`m26c-equal-width-deciles-v1`) with exact Decimal mean probability, observed frequency, and count.
One effective evaluation per receipt is counted. The 10-observation label is display-only, not significance
or proof of event independence; observations may remain correlated within markets/events. No smooth curve
is fitted and the forecast calibrator is unchanged.

`EvaluationDatasetManifest` has the immutable, identity-bound semantic marker
`EVALUATION_ATTEMPT_HISTORY`. The authoritative manifest API is store-backed. Its caller supplies only deterministic agent, version,
policy, time-window, and optional family filters; the store loads every matching evaluation itself and
derives all eligible IDs and excluded IDs/reasons. The low-level materializer is private. Filters and the
complete selected universe are content-addressed. `generated_at` is audit metadata and intentionally does
not change identity. Included and excluded entries are evaluation attempts; excluded entries may contain
multiple lifecycle attempts for the same receipt. Manifest entry counts must never be summed or labeled as
decision/performance observations. Performance and calibration consumers use `effective_evaluations()`.
A future one-row-per-decision dataset requires a separate effective-evaluation manifest.

## Exclusion lifecycle

The lifecycle classification follows the actual eligibility triggers. `OUTCOME_NOT_FINAL` may mature when
the settlement is finalized. `RECONCILIATION_MISMATCH` may mature when the same settlement track later has
authoritative `MATCHED` reconciliation. For the same immutable receipt/source/settlement package,
`TARGET_UNPROVEN` is terminal: either the receipt ID cannot be reproduced from the source binding or the
historical target probability is absent. `TEMPORAL_LEAKAGE_RISK` is terminal because source observation,
receipt creation, and settlement determination timestamps are immutable historical facts.

`UNSUPPORTED` is cause-specific but terminal for the same immutable package. A non-Event-Edge receipt lacks
supported outcome semantics; a non-simple-binary settlement is outside this evaluator; and contradictory
YES/0 or NO/1 fields make that settlement package invalid. A genuinely revised settlement package may
produce a distinct attempt, but M26C never reclassifies these immutable inputs in place.

## Dashboard behavior

Event Edge detail now reads only persisted evaluations and shows version, settled links, scoreable count,
unique markets, unavailable independent-event count, evidence state, average Brier values, market-relative improvement, exclusions, and recent
good or bad research decisions with final outcome, historical probability, metrics, optional one-unit
research counterfactual, and eligibility reason. It repeatedly distinguishes research evaluation from
actual trading. Cross-Market states why performance is unavailable. Empty and corrupt histories are
different explicit states.

The Learning page is an evidence laboratory with evidence status, agent evaluation boundaries,
descriptive decision-level calibration buckets/counts, all decision classes, and data-quality exclusions.
It states that event independence is unproven and evidence remains inconclusive. It does not
change strategy thresholds, weights, proposals, research budgets, capital, ranking, autonomy, or
production exposure.

## Tests and safety

Focused tests cover strict settlement eligibility, exact ticker/source/orientation linkage, deterministic
and revision-sensitive identity, temporal leakage, exact YES/NO Decimal Brier and market comparison,
optional counterfactual value, all four decisions, unsupported agents, idempotency/collision behavior,
append-only triggers, DB zero influence, canonical restoration, tamper detection, query ordering,
append-only settlement maturation, policy replay, authoritative-final conflicts, contradictory settlement
fields, true denominators beyond display limits, 50-market inconclusiveness, descriptive calibration, and
store-backed manifest completeness. M26A/M26B, Contract Intelligence, Learning, and dashboard regressions remain
part of validation.

No `production_execution` file or import was added or modified. No M25 credential or Perps boundary was
changed. There is no network call, credential use, scheduler, autostart, live autonomy, allocation, sizing,
order, cancel, strategy mutation, automatic proposal, or profitability claim. Production execution remains
**DISARMED**, trading remains **OFF**, and every link, evaluation, and persisted row enforces
`production_influence == Decimal("0")`.

## Known limitations

M26C provides explicit construction and durable evaluation storage but no collector or automatic settlement
join; a reviewed offline/replay workflow must supply the immutable receipt, settlement, and target proof.
M26B did not persist the whole `TradeCandidate` source package, so Event Edge orientation is provable only
when the caller supplies the candidate identity/content hash and source observation time that reproduce the
receipt binding and exact `ticker:side` instrument. Older receipts lacking that proof fail closed.
Settlement supersession is not established by the current domain, so a contradictory second eligible final
record fails closed and requires review. True independent event count remains unavailable until future
receipts preserve immutable event identity. Cross-Market and all non-Event-Edge agents remain
unscoreable until purpose-built outcome semantics exist.
