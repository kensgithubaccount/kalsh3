# M26E Proven Event Identity + Evidence Sufficiency Review

## Corrected architecture and authority audit

M2 parses exchange-supplied `Market.event_ticker`, `Event.ticker`, and `Event.series_ticker`. Those fields
are authoritative facts inside a legitimately observed M2 object. Contract Intelligence carries them for
semantic and sibling-structure work. M6 provides immutable generic replay envelopes and archives, but this
repository does **not** retain a reconstructable immutable historical archive containing the actual parsed
Market/Event objects needed to verify a historical caller-supplied observation independently.

Consequently, a caller-created Market/Event pair plus caller-selected hashes or snapshot IDs is not an
authority. M26E can validate its internal consistency and point-in-time candidate grouping, but cannot
claim that it came from the exchange. The initial implementation incorrectly called such objects proven;
the adversarial review found this and the authority model was corrected.

## Non-escalatable observation trust model

`ObservationAuthorityState` distinguishes `UNVERIFIED` from the reserved `ARCHIVE_VERIFIED` state.
`UniverseEventObservation.from_entities()` always produces `UNVERIFIED`. Its authority field is
`init=False`; no boolean, enum, provenance hash, observation ID, or caller-controlled constructor argument
can elevate it. Construction also rejects a bypassed `ARCHIVE_VERIFIED` value. There is intentionally no
public or internal archive-verification factory today because there is no genuine repository archive to
verify against, and direct construction of a `PROVEN` binding is rejected.

A future milestone may add an archive-backed adapter only when it can validate exact immutable observation
content against a real store/archive object. Merely adding `trusted=True` or accepting an authority enum
would not satisfy that requirement.

## Candidate binding, point-in-time safety, and identity

`EvaluatedMarketEventBinding` is frozen and content-addressed under
`m26e-market-event-binding-v1`. It binds candidate market/event/series tickers, metadata hashes,
observation/provenance identities, observation authority state, observation/as-of times, policy, detail,
and production influence `0`. `Market.event_ticker` must equal `Event.ticker`; there is no prefix, title,
date, settlement-time, or similarity inference.

Only observations at or before `target.source_observed_at` qualify. Later current candidate metadata cannot
rewrite an older cohort. A consistent ordinary observation produces `UNPROVEN` while preserving candidate
event/series identity for deterministic diagnostics. Missing material is `MISSING`; disagreement is
`CONFLICTED`. None/non-datetime timestamps fail cleanly with `EventEvidenceError`.

Observation IDs are immutable across the entire supplied observation universe. Reusing one
`observation_id` with any different identity material—including different tickers, series, metadata hashes,
provenance, authority state, or timestamp—produces `CONFLICTED` before point-in-time selection. The code
does not select latest, first, lexicographically smallest, or favorable content.

## Counts, grouping, dependence, and aggregation

Counts distinguish `market_count`, deterministic `candidate_exchange_event_count`,
`proven_exchange_event_count`, unresolved markets, dependence clusters, and proven independent evidence
units. One hundred or five hundred candidate markets consistently referencing one event produce one
candidate event, never 100/500. Because all currently constructible observations are unverified,
`proven_exchange_event_count` remains `None`. Equal or different series tickers neither collapse events nor
prove independence.

`m26e-within-event-mean-paired-brier-v1` computes an exact `Decimal` mean of paired market Brier differences
inside each consistent candidate exchange event. It emits one descriptive row per candidate group, so 100
markets in one group do not receive 100 times the event-level weight of a one-market group. These candidate
rows are never passed to M9 `paired_event_interval()` and do not prove a statistical sample.

There is no reviewed cross-event dependence authority. `dependence_cluster_count` and
`proven_independent_evidence_unit_count` remain `None`. `m26e-event-evidence-sufficiency-v1` centralizes a
future 50 proven-independent-unit human-review minimum, but it remains unreachable. Review eligibility is
`NOT_ELIGIBLE`, no interval exists, and no statistical-significance claim is made.

## Complete-universe and corruption boundaries

The content-addressed M26E manifest binds the complete upstream source-universe identity, every market and
binding, included sources, exclusions, time/binding policies, and influence `0`. The M26C adapter derives
the complete store-backed effective universe; the M26D adapter consumes the complete cohort with all shared
units and exclusions. Callers cannot submit a favorable list of event IDs. Unresolved markets remain in the
manifest.

Any candidate mapping conflict or observation-ID collision blocks the entire assessment with
`EVIDENCE_UNAVAILABLE`; no market is dropped and survivor-only counts are not reported. Missing/corrupt
M26D source evaluations similarly fail closed.

`unresolved_market_event_count` includes both missing historical observations and internally consistent
but unauthenticated candidate observations.

## M26C, M26D, dashboard, and current product truth

M26C evaluation rows, identities, lifecycle selection, persistence, counts, and performance semantics are
unchanged. M26D cohorts, metrics, temporal disclosure, `proven_unique_event_count=None`, and structural
`winner=None` remain unchanged. M26E is downstream composition only.

The Learning-page Event Identity block is deliberately static/status-only. It is not wired to
`assess_manifest()` because no trusted archived assessment source exists. It explicitly says authoritative
archived evidence is unavailable/not wired, independent units are not proven, and candidate observations
cannot produce authoritative counts. It shows no fake count and no leaderboard.

Current truth: M26E can prove that candidate M2-shaped objects group consistently, but cannot prove those
historical objects were exchange observations. Authoritative historical event identity and statistical
independence are unavailable. Event Edge remains the only outcome-comparable capability; no valid
cross-agent winner exists.

## Governance, budget, and safety

M26E does not import or invoke `GovernanceProposal`, `compare_challenger()`, `FamilyScore`,
`ResearchBudget`, or `allocate_budget()`. It creates no proposal, promotion/demotion, champion, strategy or
threshold change, research budget, capital allocation, position sizing, order, cancel, scheduler, LIVE
autonomy, or profitability claim.

No `production_execution` or M25 credential/Perps boundary file was modified. No credential or network call
was used. Production execution remains **DISARMED**, trading and live autonomy remain off, and every new
domain object enforces `production_influence == Decimal("0")`.

## Verification and known limitation

Focused adversarial tests cover caller-forged Market/Event objects and hashes, the absence of a selectable
trust argument, ordinary observations remaining unproven, same-ID/different-content/different-time
collisions, clean timestamp errors, point-in-time cutoff, later-metadata isolation, 100/500-market candidate
grouping, same/different-series behavior, exact equal-event aggregation, insertion-order independence,
truthful unresolved counts, and zero influence. Regression and full validation results are recorded in the
final handoff.

The remaining limitation is intentional and safety-preserving: no currently reachable path can produce
authoritative `PROVEN` event identity. That requires a future, separately reviewed reconstructable archive
and verification adapter. Caller-selected hashes and IDs will never substitute for it.

Validation on macOS: focused M26E tests passed (23), the requested M26E/M26C/M26D/dashboard/M2/M6/M9
regression set passed (111), the final M26C/M26D/dashboard micro-regression passed (63), and the full suite
completed with 784 passed plus the two known untouched M15
failures caused by macOS lacking `os.memfd_create`. Full Ruff check and format-check passed. Targeted strict
mypy passed; full mypy reported only the same pre-existing `os.memfd_create` attribute error in
`services/production_execution/security_boundary.py`. `git diff --check` passed.
