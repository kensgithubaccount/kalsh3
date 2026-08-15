# M26G Reviewed Independent Evidence Units

## Decision and current truth

M26G introduces a downstream, research-only authority for partitioning verified
exchange events into descriptive evidence units. The real repository-reviewed
registry is empty. Consequently, current runtime truth remains: M26F can prove
historical exchange-event identity, reviewed distinct evidence units are
unavailable, and human-review eligibility is `NOT_ELIGIBLE`.

No real-world assignments were invented. Synthetic assignments exist only in
tests and are not members of the runtime repository trust root.

## Evidence-unit semantics

An evidence unit is one member of a complete partition established by the
versioned reviewed policy. It is not inferred from event ticker, series, category,
date, source, title, contract family, resolution time, or number of markets.
Multiple verified events may map to the same unit. One event cannot map to more
than one unit in one authority.

`PROVEN_DISTINCT_UNDER_POLICY` means only that the reviewed partition contains
distinct descriptive units. It does not claim stochastic or mathematical
independence, uncorrelated outcomes, statistical significance, a p-value, a
confidence interval, strategy superiority, profitability, promotion, capital
eligibility, production readiness, or trading authorization.

## Authority and identity

Repository review is the trust root because Kalshi exchange metadata cannot prove
cross-event independence. The public assessment boundary accepts the complete M26E
manifest, its exact content-addressed M26E assessment, and the M26F archive. It
does not accept assignments, unit IDs, trust booleans, authority enums, or a
caller-selected manifest.

Each reviewed assignment binds the M26F archive authority ID, exact event archive
observation ID, canonical event source hash, event ticker, descriptive series
ticker, unit ID, assignment policy, partition policy, and production influence
`0`. The authority manifest binds sorted assignment IDs, registry version,
reviewed-manifest version, partition policy, and influence `0`. The M26G
assessment binds its source M26E assessment and manifest IDs, the complete sorted
verified event-authority set, all used assignment IDs, unresolved authorities,
authority manifest ID, all M26G policy versions, count/state outputs, and
influence `0`. Processing time is absent from logical identity.

## Complete partition and failure behavior

The M26E complete upstream universe remains authoritative. M26G reconstructs every
`PROVEN` binding against the supplied M26F archive and derives the exact verified
event set; callers cannot provide a favorable subset. Every relevant event must
have exactly one matching reviewed assignment. Missing one row from a 50-event
partition yields `None`, never a 49-survivor count. Wrong archive, observation,
source hash, policy, malformed unit, nonzero influence, conflicting assignment,
corrupt identity, ambiguous registry, incomplete M26F proof, or forged source
assessment fails closed or leaves the authoritative count unavailable.

Assignments for other events in the repository registry do not contaminate a
source assessment; completeness is evaluated over the exact source event set.
Canonical sorting makes insertion order irrelevant. A reviewed-manifest version
or assignment change alters the authority and assessment identities, so later
registry edits cannot silently retain an older assessment identity.

## Counts and review gate

The legacy `proven_independent_evidence_unit_count` field is defined here as
"reviewed distinct evidence units under policy," not mathematical independence.
There is no fallback to market, event, series, category, or date count.

- Events collapsed into fewer units produce `DEPENDENT` under the descriptive
  policy and the number of partition units.
- A complete one-to-one reviewed partition produces
  `PROVEN_DISTINCT_UNDER_POLICY`.
- Missing authority or coverage produces `NOT_PROVEN` and count `None`.
- 49 reviewed units are `NOT_ELIGIBLE`; 50 and 51 are `ELIGIBLE` for human review
  only. Eligibility triggers no mutation or automatic consequence.

## Integration and safety

M26G composes downstream of M26E/M26F and does not change their identity/version
semantics. M26C stored evaluation identities and lifecycle behavior are unchanged.
M26D cohort identities and structural `winner=None` behavior are unchanged. The
dashboard uses a static truthful empty state—"Independent evidence authority —
Not configured"—because no real archive-plus-registry runtime assessment is wired.

M9 `paired_event_interval()` is intentionally disconnected. M26G establishes
sample-unit authority only; it performs no inference and returns no interval.
There is no production execution, order/cancel, sizing, capital, research-budget,
governance, winner/champion, LIVE autonomy, scheduler, autostart, or strategy
mutation integration. Production influence is exactly `Decimal("0")`.

## Known limitations

The registry is repository-controlled application authority, not proof supplied
by Kalshi and not protection against an actor who can modify executed repository
code. No persistence layer for M26G assessments exists yet; deterministic content
identity provides attribution, but retaining historical assessment objects and
their reviewed manifests is future work. Statistical inference over reviewed
units requires a separate later review.
