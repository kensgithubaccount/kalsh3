# FR-A2 Trial Ledger

FR-A2 adds `services.forward_reality.trial_ledger`, a deliberately narrow
research-governance boundary. Serious research/model work is registered in
`PLANNED` state before any external outcome scoring. A trial record is frozen
and content-addressed; `trial_id` is derived from its immutable definition.

Creation time comes only from the ledger’s trusted issuer clock. The caller
cannot author `created_at`. Status progression is represented by append-only
events and never changes `trial_id` or the definition hash. `FAILED` and
`ABANDONED` are terminal, so neither can be removed or rewritten. Duplicate
definitions are rejected, and evaluation plans are canonicalized and hashed.

Every record carries `underlying_event_id`. Threshold contracts or other
sibling markets for the same CPI release, weather event, game, or other
real-world event therefore remain visible but are grouped by
`independent_trial_count`; ticker count is not experiment count.

All records are permanently `research_only=True` and
`production_influence=0`. This module grants no authority for Brier scoring,
PnL, profitability, promotion, capital, execution, or forecast quality.
