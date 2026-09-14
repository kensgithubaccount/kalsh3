# M9 Source + Model Learning Governance Review

> **Superseded in part by M9-E1.** The findings below are preserved as the original
> record and are not rewritten. The Quant finding's reference to "intervals" and the
> ML finding's reference to inconclusive low samples were implemented by
> `paired_event_interval()` in a way that treated leave-one-event-out extrema as an
> inferential interval. M9-E1 corrects that: the extrema are a sensitivity diagnostic,
> promotion evidence is INCONCLUSIVE by construction, and promotion additionally
> requires a duplicate-free event manifest bound to the proposal count plus a positive
> incremental effect. See `M9E1_LEARNING_GOVERNANCE_SEMANTIC_REPAIR.md`.

## Acceptance

- Ablation, redundancy/timeliness, event-level uncertainty, multiple comparisons, champion/challenger, proposals, quarantine/drift, rollback/configurations, tournament/budgets, replay gates, 20k/2k grouped fixture and UI: **OFFLINE VERIFIED**.
- Real settled evidence: **INSUFFICIENT REAL EVIDENCE**. Production influence: **NONE**. Human acceptance: **PENDING**.

## Cross-functional findings

- **Quant:** Leave-one-source/model/group ablations compare otherwise identical configurations and are described as observed contribution, not causal impact. Duplicate roots reduce credit. Intervals and concentration are event-level; promotion uses a held-out period and multiple-testing control.
- **Trader:** Timeliness explicitly measures arrival before/after Kalshi reaction. Outcome prediction and market-reaction prediction remain different targets; stale correctness earns no incremental credit.
- **ML / data science:** Development, validation and promotion periods cannot overlap. Same-event challenger comparisons are mandatory. Low samples are inconclusive, 50 unique settled events are required, and single-event concentration blocks confident language.
- **Data engineering:** Evaluations, proposals, configurations, events and rollback targets are immutable/content-addressed. Historical replay selects only configurations effective at replay time; retrospective configurations require an explicit mode.
- **Security / SRE:** Learning imports no signer, account gateway, execution, risk engine, sizing or financial-limit mutation. Quarantine is proposed/fail-closed and rollback reconstructs the exact prior research state.
- **Product / UX / CFO:** Accuracy, timing, originality, redundancy, forecast value and cost are displayed separately. Synthetic data and proposals are explicit; family scores allocate research cadence only and always state capital allocation NOT DETERMINED.

No source/model is declared best, promoted into production, assigned capital, or credited with profit.
