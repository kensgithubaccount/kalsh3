# M27B Universal Router + Directional Structural Scanner

## Outcome

M27B implements research-only universal routing and mechanically provable directional-threshold
Structural Leads. Every supplied canonical Market produces a deterministic route or abstention record.
The implementation does not estimate fair value, construct a trade, allocate capital, schedule work, or
connect to execution. Production influence is exactly `Decimal("0")`.

## Empirical scope and limits

The accepted M26H.3 archive probe observed 84,724 Markets under 10,403 parent Events. It observed large
Event groups, 4,163 Events with at least two numeric strikes, and many `greater` and
`greater_or_equal` markets alongside `structured`, `custom`, `between`, `less`, and
`less_or_equal` shapes. Those counts describe that accepted point-in-time archive; they are not asserted
to be permanent live-universe truth.

Event identity alone is insufficient for structural cohorting. Sports Events can contain multiple
players or teams, while financial and commodity Events can contain distinct indexes or contracts. M27B
therefore binds the complete, deterministically canonicalized non-empty `custom_strike` object into
subject and cohort identity. Different objects never compare. Malformed identity, mixed missing/present
identity within an otherwise common Event/strike group, and duplicate thresholds fail closed.

## Supported semantics

Version 1 supports only `greater` and `greater_or_equal` with a finite Decimal `floor_strike`. For
ascending thresholds, YES at the higher threshold is mechanically a subset of YES at the lower
threshold. All other strike shapes route explicitly as unsupported and never produce a Structural Lead.
Titles and subtitles are not used to infer threshold meaning or entity sameness.

Directional discovery additionally requires an ACTIVE binary Market that is neither provisional nor
multivariate. Non-active, non-binary, provisional, and MVE Markets still receive universal `ROUTE_ONLY`
records with explicit reasons, but cannot enter structural cohorts or produce leads. These base gates do
not by themselves prove settlement equivalence.

Within a cohort, routes sort by `(threshold, ticker)`. A single pass retains the lowest logically earlier
YES ask and compares each narrower YES bid against it. Among equal asks—and therefore equal indicative
gross gaps for a given narrower bid—the larger displayed YES-ask size wins, followed by ticker as the
deterministic tie-break. Gap remains the primary discovery priority. This is O(k log k) for a cohort of k
markets, O(N log N) over the scan, and produces at most one strongest lead per narrower contract rather
than a quadratic set of pairs.

Broad `DiscoveryQuotes` remain discovery-only. A lead records
`higher_yes_bid - lower_yes_ask` as an indicative gross gap and records displayed top quantity only when
available. These are transparent research-priority components, not profit, EV, alpha, arbitrage, or a
guaranteed return.

## Exact confirmation boundary

`confirm_structural_lead` is pure and consumes a Structural Lead, the two existing M27A
`MarketEconomicsEvidence` objects, and the two canonical `ContractSpecification` objects. Both
specifications must be strategy-supported, match the lead/evidence identities, share a series, and have
compatible settlement context apart from threshold value and threshold-specific proposition text. The
parser binds each specification to the original Market layer using the canonical
`material_hashes()` rules and metadata hashes. Exact confirmation requires those hashes to equal the
corresponding M27A evidence hashes on both legs, preventing a stale but otherwise semantically compatible
specification from authorizing payout mathematics. These provenance hashes remain distinct from
`semantic_hash` and do not themselves establish semantic validity. The
gate compares settlement/payout model, measured value and subjects, geography, comparator and
inclusivity, unit, measurement and expiration times, authority and normalized settlement-source
semantics, source precedence, and rounding/revision/correction/recount/cancellation/postponement/
early-close/exception rules. It does not compare whole semantic hashes or parser-run source timestamps.

The two exact books must also have exactly equal `orderbook_observed_at` values. M27B v1 therefore
requires one shared caller-assigned observation instant, normally originating from the same bounded batch
snapshot. This does not prove exchange-wire atomicity. Independently valid M27A evidence with different
book observation instants cannot be combined. The function also requires identical hypothetical
quantities, validates rules, metadata, replay, and research-only state, and uses broad YES plus narrow NO
exactly. Missing required depth returns an explicit insufficient-depth state without manufacturing a
cost.

When both sides exist, confirmation reports minimum settlement payout, gross package cost, gross
structural gap, each M27A centicent-formula fee, and the formula-adjusted structural gap. The final
exchange fee remains unknown pre-fill because fill fragmentation, accumulator, and rebate behavior are
unresolved. State remains `FINAL_FEE_UNKNOWN_PREFILL`; final and guaranteed net profit are `None`.

## Coverage and provenance

The immutable manifest counts evaluated, routed, abstained, family and strike-type totals, eligible
markets, accepted and rejected/ambiguous cohorts, leads, and optional exact-confirmation outcomes. It
binds policy version, caller-supplied source authority, and content identity. Retaining caller provenance
does not upgrade it to verified archive authority.

Focused adversarial tests cover deterministic universal routing, complete custom-strike separation,
fail-closed malformed/mixed/duplicate shapes, supported and unsupported semantics, missing quotes,
identity changes, a 1,000-market cohort, a 50,001-market universe, exact side/cost/fee mapping,
insufficient depth, and the zero-influence/no-execution boundary.

## Claims not established

M27B does not establish forecast skill, fair value, positive EV, profitability, arbitrage, capital
allocation, or trading readiness. It adds no `TradeCandidate`, `DecisionReceipt`, `RiskIntent`, order,
scheduler, autonomy, network, or production-execution path.
