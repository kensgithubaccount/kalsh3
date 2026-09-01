# FR-A2 Trial Ledger

FR-A2 adds `services.forward_reality.trial_ledger`, a deliberately narrow
research-governance boundary. Serious research/model work is registered in
`PLANNED` state before any external outcome scoring. A trial definition is
frozen, content-addressed, and durably stored in an issuer-authenticated
append-only journal; `trial_id` is derived from its immutable definition.

Creation time comes only from the ledger’s internal UTC issuer. The public
caller cannot provide a clock or `created_at`; deterministic clock injection
exists only on a private test seam. Definitions and status events are separate
SQLite records protected by create-only triggers. Status is derived by replaying
validated append-only events and never changes `trial_id` or the definition
fingerprint. Each journal entry is chained, HMAC-authenticated, and covered by
an independently authenticated checkpoint. Missing, reordered, truncated, or
rewritten history fails closed. SQLite is only a rebuildable query index and
never authority. `FAILED` and `ABANDONED` are terminal, so neither can be
removed or rewritten. Duplicate definitions are rejected, and evaluation plans
are canonicalized and hashed with strict JSON-key and finite-number validation.

Every record carries `underlying_event_id`. Threshold contracts or other
sibling markets for the same CPI release, weather event, game, or other
real-world event therefore remain visible but are grouped by
`independent_trial_count`; ticker count is not experiment count.

All records are permanently `research_only=True` and
`production_influence=0`. This module grants no authority for Brier scoring,
PnL, profitability, promotion, capital, execution, or forecast quality.
