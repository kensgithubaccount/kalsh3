# M26D Controlled Agent Comparison + Research Competition Review

## Architecture audit

M26A owns the immutable current seven-agent registry and zero-influence research identities. M26B
owns append-only Decision Receipts and source attribution; current authority applies only to new
writes, while historical reads restore without consulting the registry. M26C owns append-only
outcome-evaluation attempt history, authoritative settlement linkage, lifecycle-effective selection,
exact Decimal Brier metrics, and complete-universe evaluation manifests. M26D is downstream of M26C.

The Learning package was audited but is intentionally not connected. `FamilyScore` and
`allocate_budget()` require true `unique_settled_events` and change forecast/source/LLM/backfill
research resources. `paired_event_interval()`, `compare_challenger()`, and `GovernanceProposal`
likewise require genuine shared event identities and stronger evidence. M26C preserves market
identity but explicitly does not prove independent event identity. Market tickers are therefore never
passed to those APIs as event IDs. M26D imports none of tournament, governance, execution, risk,
credentials, network, scheduling, or production-write code.

## Comparison contract

`COMPARISON_CAPABILITIES` is the immutable, versioned semantic authority; registry availability is
not capability. Its sole current entry authorizes Event Edge with M26C policy
`m26c-receipt-outcome-v1`, scoring contract `binary-contract-outcome-brier-v1`, and target
`event-edge-binary-v1`. `ComparisonContender.__post_init__` requires an exact authorized combination,
so direct construction cannot forge Event Edge semantics for Breaking Signals or another agent.
`compare_from_store()` repeats this check for defense in depth and verifies each admitted eligible row's
agent, historical version, policy, target, and Brier availability against the authority. Fabricated
eligible unsupported-agent evidence yields `EVALUATION_UNSUPPORTED` with no cohort or metrics.

`ComparisonContender` is frozen and also binds historical `agent_version`, optional market family, and
production influence exactly `0`. Capability does not consult the current registry version, so Event
Edge 1.0.0 and 1.1.0 remain distinct historical contenders and can be compared when both histories
exist. Logically identical contenders are rejected as `SELF_COMPARISON`, including equal copies.

The only currently supported compatibility contract is
`binary-contract-outcome-brier-v1`: identical evaluation policy, target version, optional family,
binary target orientation, settlement lifecycle, and scoring definition. Eligibility distinguishes
descriptive comparability from policy, target, or outcome-semantic mismatch, unsupported evaluation,
no shared units, duplicate ambiguity, corrupt evidence, and unproven independence. Two probability-
looking values are not automatically comparable.

The versioned comparison unit domain is `m26d-comparison-unit-v1`. Its SHA-256 identity binds market
ticker, explicit `OutcomeSide`, target version, settlement-track identity, and market family. Ticker
alone never proves identical semantics. Exactly one lifecycle-effective eligible evaluation per
contender/unit is usable. Multiple receipts from either contender make the unit
`AMBIGUOUS_DUPLICATE_UNIT`; it is excluded without selecting the best, latest, highest-edge, or most
favorable receipt.

## Complete-universe cohort and persistence decision

`compare_from_store()` accepts contenders, policy-bound time window, family, and audit generation
time—not evaluation IDs. It calls `EvaluationStore.all()`, validating every persisted M26C row, then
applies M26C effective-selection semantics, constructs every unit, takes the complete intersection,
and records A-only, B-only, ineligible, target-mismatch, and duplicate exclusions. This prevents a
caller from authoritatively submitting five favorable IDs.

`ComparisonCohort` is frozen and content-addressed under `m26d-comparison-cohort-v1`, with comparison
policy `m26d-shared-unit-comparison-v1`. Its identity is intentionally directional: A/B and B/A have
different identities because roles and the signed A-minus-B metric reverse. Its identity binds both contenders, time/family/semantic
filters, every paired evaluation ID, every exclusion and reason, two deterministic source M26C
attempt-history provenance manifests, temporal policy, window basis, and influence `0`. Ordering is canonical. `generated_at` is
audit metadata outside identity, so replaying the same universe produces the same cohort ID.

The temporal policy is `unaligned-forecast-horizons-descriptive-v1`. Shared units prove a common
resolved proposition, not a common information set: `source_observed_at` may differ, forecast horizons
are not normalized, `temporal_alignment=UNALIGNED`, and
`information_set_comparability=NOT_PROVEN`. Results are descriptive shared-proposition scoring, not a
controlled causal contest or evidence of superiority. The identity-bound window basis is
`evaluated_at`, M26C evaluation processing/audit time. Thus a January forecast backfilled and scored in
August belongs to an August M26D processing window; the window is not a forecast, decision,
determination, or finalization window.

No comparison database was added. The cohort is reproducibly derived from M26C's append-only,
integrity-checked source ledger and already has a stable content identity; persisting the same derived
projection would add ceremony without a consumer requiring durable snapshots. A future durable review
workflow can store this canonical object without changing comparison semantics.

## Metrics and evidence limit

Metrics are descriptive and exact Decimal only: shared-unit count, A/B mean model Brier, mean paired
`A.model_brier - B.model_brier`, optional complete-pair mean market-relative Brier improvement, and
decision-state composition. Negative `a_minus_b_brier` means A had lower/better Brier on the shared
units. No profit, P&L, ROI, Sharpe, win-rate, realized-edge, trading-performance, significance, or
composite ranking metric is created.

Even 50 or 500 shared markets leave `proven_unique_event_count = None`, `winner = None`, and evidence
`INCONCLUSIVE_INDEPENDENCE`. M26D never selects a winner or champion and never produces promotion,
demotion, quarantine, agent-state, strategy-weight, threshold, research-budget, capital-allocation,
position-sizing, order, or autonomy actions.

## Current comparability matrix

| Agent | Current outcome-performance comparability |
|---|---|
| Event Edge 1.0.0 | Binary outcome evaluation available; only scoreable contender today |
| Cross-Market 1.0.0 | Not comparable; realized two-venue outcome semantics unavailable |
| Breaking Signals 1.0.0 | Not comparable; no compatible probability/outcome evaluation |
| Resolution 1.0.0 | Not comparable; no standalone agent-performance semantics |
| Learning 1.0.0 | Evaluates research; not a competing prediction agent |
| Perps 1.0.0 | Strategy unavailable and disabled |
| Portfolio 1.0.0 | Strategy unavailable and disabled |

Consequently the current projection says “No valid cross-agent comparison yet.” Absence of supported
evaluation semantics does not imply inferiority.

## UI and corruption behavior

Agents and Learning now contain a compact Research Comparison section listing supported and unsupported
contenders, the reason for each unsupported state, independent-event evidence as unavailable, and no
winner. Explanations are deterministic transformations of structured status, not LLM interpretation.
There is no leaderboard.

`EvaluationStore.all()` validates the complete persisted ledger before filtering. Any corrupt row
fails the whole comparison boundary; no survivor-only result is produced. Dashboard reads catch this
and return HTTP 200 with `COMPARISON EVIDENCE UNAVAILABLE` and structured state
`EVIDENCE_UNAVAILABLE`, never a fake zero or partial flattering comparison. A clean one-contender state
remains the distinct `NO_COMPARISON` state. `ResearchCompetitionSnapshot` enforces influence exactly
zero and a structurally absent winner.

## Verification and safety

Focused tests cover the exact direct-construction forgery and fabricated unsupported-evaluation attacks,
self-comparison, semantic authority, version separation, exact shared
intersection, A/B-only exclusions, side/settlement unit separation, deterministic identity,
duplicate ambiguity, exact Decimal metrics and sign, store-backed anti-cherry-picking, 50/500-market
independence limits, 30-day-versus-1-minute temporal misalignment disclosure, processing-window and
temporal identity binding, directional identity/sign reversal, corruption-state distinction, current
reality, HTTP 200 UX, snapshot zero influence, and forbidden imports. The unused `DESCRIPTIVE_ONLY`
state was removed; actual descriptive comparisons remain `INCONCLUSIVE_INDEPENDENCE`.
M26A/B/C, Learning tournament/governance, dashboard, full pytest, Ruff, mypy, and diff checks are part
of the milestone validation record.

No real credentials or network calls were used. No production execution file or M25 credential/Perps
read-only boundary was modified. Production execution is **DISARMED**. Trading and live autonomy remain
off. Production influence is exactly `0`.
