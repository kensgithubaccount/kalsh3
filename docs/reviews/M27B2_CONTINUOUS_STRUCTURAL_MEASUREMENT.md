# M27B.2 — Continuous Structural-Lead Measurement

## Scope and provenance

- Requested canonical base: `7aa43ea605fb44bc7db2572385bc61382ad5d5e1` (merge of PR #112,
  `cpi-e1-p7-settlement-reconciliation`).
- `origin/main` was fetched and verified as exactly this commit before branching.
- Branch: `m27b2-continuous-structural-measurement`, created fresh from `origin/main` — not from
  any in-progress branch.
- M27B.2 is research/read-only only. It adds no trading, no credential dependency beyond the
  exact reviewed unauthenticated public boundary every other public-read path in this repository
  already uses, and no execution import (see `tests/test_m27b2_architecture.py`).
  `research_only = True` and `production_influence = Decimal("0")` are fixed on every new object
  and asserted throughout the new test suite.

## Accepted historical facts this extends

M27B.2 does not re-run or re-derive these; they are cited as prior accepted evidence from the
canonical M27B.1 committed-SHA acceptance (`code_head` `1880dd46f7418c7c193bb8c343ece6d77d70c720`,
archive `m26h3-acceptance-20260815-165105.sqlite`, artifact
`/Users/ksyme/.kalsh3/evidence/m27b1-committed-acceptance-20260816-044201.json`):

| Metric | Value |
| --- | --- |
| Markets evaluated | 84,724 |
| Events | 10,403 |
| Structurally eligible directional markets | 35,823 |
| Structural (sibling-threshold) cohorts | 5,513 |
| Cohorts rejected/ambiguous | 8 |
| Discovery-only leads | 14 |
| Distinct events carrying a lead | 11 |
| Exact confirmations | 0 |
| Network calls / production writes | 0 / 0 |
| Production influence | 0 |

Those 14 leads are **discovery only**, not arbitrage: they used broad top-of-book quotes, no
fee treatment, and no exact confirmation. That one-shot result cannot answer whether leads are
frequent, persistent, deep, or large enough after costs to be actionable — which is the exact
question M27B.2 adds instrumentation to measure, without re-litigating or replaying that
acceptance.

## Implementation boundary — reused, not reimplemented

M27B.2 adds three new modules under `services/opportunity_engine/` and reuses, without
modification, canonical M27B/M27A/M27J pieces:

- **Discovery**: `services.opportunity_engine.structural.scan_structural_markets` — called
  verbatim by `structural_measurement_runner.run_discovery`. No second scanner exists.
- **Exact confirmation**: `services.opportunity_engine.structural.confirm_structural_lead`,
  fed by `services.opportunity_engine.authoritative_economics.build_authoritative_market_economics`
  (which itself composes `services.market_universe.market_snapshot`,
  `services.market_universe.orderbook_snapshot`, `services.opportunity_engine.live_economics`, and
  `services.opportunity_engine.live_fees` — all unmodified).
- **Contract semantics**: `services.contract_intelligence.specification.ContractSpecificationParser`
  via `SemanticsInputBundle.build` — the SAME generic, family-agnostic parser
  `services.market_universe.router.MarketUniverseRouter` uses for the whole-exchange census. A
  market becomes eligible for exact confirmation the moment its specification reaches
  `SemanticStatus.VALID` and `strategy_supported`; this is not restricted to weather or CPI.
  Any market lacking that (e.g. no admissible settlement-source evidence) fails closed into
  `DISCOVERY_ONLY`, matching this repository's `G1`-style "absence of evidence is never `PASS`"
  discipline elsewhere (see `docs/reviews/KU_A3_1_RESEARCHABILITY_HARD_GATES.md`).
- **Universe refresh**: `services.market_universe.sync.UniverseSynchronizer` and
  `services.market_universe.collect.PublicUniverseTransport` — the exact reviewed, unauthenticated,
  bounded public-GET path the M26H.3 acceptance used to build the 84,724-market archive above.
  `structural_measurement_runner.refresh_universe` duplicates only
  `services.market_universe.collect.collect_evidence`'s few lines of orchestration glue (that
  function does not return its `MemoryUniverseRepository`, and discovery needs the Market/Event
  objects directly) — it calls the identical `UniverseSynchronizer.sync`/`reconcile_events`
  methods, never a second parser or transport.

### New modules

- `services/opportunity_engine/structural_measurement.py` — pure domain layer: the eight
  economics states, the immutable `LeadObservation` record and its six narrow validated
  constructors, `relationship_id`, `compute_lifetime`, and `summarize_run`. No network, no I/O.
- `services/opportunity_engine/structural_measurement_store.py` — `StructuralMeasurementStore`,
  an append-only SQLite evidence store mirroring the WAL/trigger/idempotent-append pattern used
  throughout this repository's other research evidence stores (see
  `docs/PERPS_SHADOW_RESEARCH.md`'s `BookEvidenceStore`).
- `services/opportunity_engine/structural_measurement_runner.py` — the repeated-scan operator:
  universe refresh, discovery, per-lead exact-confirmation attempt, persistence, and a bounded
  `run_forever` loop plus a `--live-public-read`-gated CLI (mirroring
  `services.market_universe.collect`'s own explicit opt-in convention).

## Measurement unit

One sibling cohort belongs to exactly one underlying Event; canonical M27B already enforces this
(`cohort_identity` binds `event_ticker`, `strike_type`, and the canonical custom-strike object).
M27B.2 does not recount threshold siblings as independent opportunities and does not weaken that
invariant anywhere in the new aggregation/reporting layer.

### `relationship_id` vs. canonical `lead_id`

Canonical `StructuralLead.lead_id` is content-addressed over, among other things,
`broad_quote_source_hash`/`narrow_quote_source_hash` — the exact quoted prices. It therefore
changes on **every** scan purely because a price moved, by design (it identifies one exact
discovery observation, not a persistent relationship). Naively grouping repeated observations by
`lead_id` would treat every scan's price movement as a brand-new lead and make lifetime tracking
meaningless.

`structural_measurement.relationship_id(lead)` is a new, scan-invariant identity binding only
`cohort_identity`, both tickers, both thresholds, both rules/metadata hashes, and
`source_authority` — i.e. WHAT is being compared, not the fluctuating quote evidence backing any
one observation of it. A change to any of those inputs (e.g. a contract-rules amendment) is
correctly a new relationship; a change in quoted price alone is not. This is the primary key used
for lifetime aggregation and the append-only store's indexing.

Every `LeadObservation` binds: exact event, exact broad/narrow market tickers, thresholds,
`relationship_type` (which ordering relationship was violated), the exact observation timestamp,
exact quote/evidence identities when available, the gross apparent gap, available size/depth when
observable, the fee treatment applied, and the resulting state — per the requested measurement
unit.

## The eight economics states

No state, on its own, ever authorizes a trade. `AFTER_COST_POSITIVE_RESEARCH` is always and only
a labeled research estimate of a positive `formula_adjusted_structural_gap`; it is never treated
as, or convertible into, a profitability or executability claim. Canonical
`confirm_structural_lead` permanently fixes `final_net_profit`/`guaranteed_net_profit` to `None`
(the real, pre-fill exchange fee is definitionally unknown); M27B.2 never manufactures a
substitute for either.

| State | Meaning | Derivation |
| --- | --- | --- |
| `DISCOVERY_ONLY` | The canonical scanner found the lead, but exact confirmation was not reached. | Any acquisition/validation/semantic failure on either leg, or a fee-regime resolution failure (current scope; see below). Always carries an explicit `blocker_reason` — this is also the concrete meaning of "recorded as unconfirmed" per the spec's non-weakening requirement. |
| `EXACT_CONFIRMED` | Both legs' executable depth, sides, and canonical formula fee were exactly confirmed, but the fee-adjusted gap is not positive. | `confirm_structural_lead` returned `FINAL_FEE_UNKNOWN_PREFILL` with `formula_adjusted_structural_gap <= 0`. |
| `INSUFFICIENT_DEPTH` | One leg's executable book could not fill the requested quantity. | `confirm_structural_lead` returned `INSUFFICIENT_BROAD_YES_DEPTH`/`INSUFFICIENT_NARROW_NO_DEPTH`. |
| `FEE_UNKNOWN` | Reserved for "depth/sides independently validated, canonical fee regime unresolved" — never claims a positive after-cost edge. | Implemented and fully tested at the domain layer (`record_fee_unknown`); **not yet auto-triggered by the live runner** — see Known scope limits. |
| `AFTER_COST_POSITIVE_RESEARCH` | Same confirmation path as `EXACT_CONFIRMED`, with a positive fee-adjusted gap. | `formula_adjusted_structural_gap > 0`. Still never a profitability claim — no guaranteed or final net profit exists anywhere in this pipeline. |
| `STALE` | The relationship was previously observed with real evidence; this cycle's revisit attempt itself produced evidence that failed an independent freshness check. | Implemented and tested (`record_stale`); the live runner currently folds an orderbook staleness failure into `DISCOVERY_ONLY` with a descriptive blocker rather than this more specific state — see Known scope limits. |
| `DISAPPEARED` | The canonical scan no longer reproduces any lead for this relationship. | `run_scan_cycle` compares the current scan's relationship set against every previously tracked, not-yet-closed relationship; a closed lifetime is never re-closed. |
| `AMBIGUOUS` | The cohort backing a previously tracked relationship became structurally ambiguous (e.g. a later `DUPLICATE_THRESHOLD`/`MIXED_CUSTOM_STRIKE_PRESENCE` abstention) rather than cleanly absent. | The live runner selects `AMBIGUOUS` when both tracked legs are present in canonical ambiguous routes; it never records `DISAPPEARED` for that case. |

`LeadObservation.__post_init__` enforces the above as invariants (not merely documentation): e.g.
constructing an `EXACT_CONFIRMED`/`AFTER_COST_POSITIVE_RESEARCH` observation without
`FeeTreatment.CANONICAL_FORMULA_FEE`, or a `FEE_UNKNOWN` observation carrying a non-`None`
fee-adjusted gap, raises immediately.

## Lifetime

For each `relationship_id`, `compute_lifetime` reports `first_seen_at`/`last_seen_at`,
`observation_count`, `consecutive_observations` (the trailing run of "seen" scans before either
the present or a closing `DISAPPEARED`), `still_active`, and directly-observed —
never modeled — lower/upper lifetime bounds: the lower bound is the exact span between the first
and last scan that saw the relationship; the upper bound (only present once `DISAPPEARED`) is the
exact span to the scan that first failed to reproduce it. A still-active relationship carries no
upper bound (right-censored). This is the literal, direct answer to "do these survive long enough
for a non-colocated system to observe and act": read the lower bound against the configured scan
cadence, never an estimate. `maximum_gross_inversion`, `maximum_confirmed_depth`, and
`maximum_after_cost_gap` are tracked separately — the last one is `None`, not zero, when fee truth
was never available for that relationship.

## Economics discipline

No canonical fee or cost value is recomputed, weakened, or estimated by M27B.2 — every economics
field on an `EXACT_CONFIRMED`/`AFTER_COST_POSITIVE_RESEARCH`/`INSUFFICIENT_DEPTH` observation is
read directly from the canonical `StructuralConfirmation` M27B/M27A already produced. Executable
bid/ask depth is used exclusively; nothing here substitutes a midpoint or last-trade price.
`record_exact_confirmation` fails closed if a caller ever passes a confirmation that does not
belong to the exact lead being recorded (`confirmation.lead_id != lead.lead_id`).

## Credential / authority boundary

Every acquisition M27B.2 performs is the same bounded, unauthenticated, fixed-origin PUBLIC GET
boundary (`services.market_universe.public_read`) every other public-read path in this repository
already uses: `get_market_with_body`, `get_orderbook_with_body` (via
`services.market_universe.orderbook_snapshot.acquire_orderbook_snapshot`), and a direct GET of
`/series/{ticker}` (the same endpoint role CPI-E1-P7 used). **No authenticated credential path was
added, touched, or is required.** `services.kalshi_account_gateway` (the repository's authenticated
read-only account boundary) and every order/execution/risk package are explicitly forbidden
imports, enforced by `tests/test_m27b2_architecture.py`'s AST guard — not merely by convention.

## Run cadence

`structural_measurement_runner.run_forever` supports unattended, repeated, read-only measurement
with a configurable `cadence_seconds` (default `DEFAULT_CADENCE_SECONDS = 900`, a conservative
operational courtesy to the public API). Cadence is never used to imply, compute, or bound any
claim about lead persistence or profitability — `compute_lifetime`/`summarize_run` answer that
question empirically from observed data, independent of whatever cadence produced it. Every
observation is appended, never overwritten; the store's `BEFORE UPDATE`/`BEFORE DELETE` triggers
make this a database-enforced invariant (`tests/test_structural_measurement_store.py`).

## Output

`structural_measurement.summarize_run` reports scans completed, independent cohorts observed,
discovery-lead count, and leads/event as **frequency**; lifetime lower-bound distribution, median,
still-active/disappeared counts as **persistence**; and exact-confirmation rate,
after-cost-positive count, gross-gap distribution, and depth distribution as **after-cost
executability** — three separate sections of one dataclass, never blended into a single score.
Missing-evidence reasons are aggregated by exact blocker string for direct inspection.

## Known scope limits (explicit, not hidden)

- **`FEE_UNKNOWN` and `STALE` are implemented and fully tested as domain-layer constructors, but
  the live runner does not yet auto-trigger either.** `attempt_exact_confirmation` currently
  resolves the fee regime before acquiring per-leg economics; a fee-regime failure and an
  orderbook-staleness failure both currently surface as `DISCOVERY_ONLY` with a descriptive
  blocker (verified by `tests/test_structural_measurement_runner.py`'s
  `test_attempt_exact_confirmation_blocks_on_a_stale_orderbook_snapshot`), rather than the more
  specific `STALE`/`FEE_UNKNOWN` state. The smallest next increment: compute a fee-independent
  gross gap directly from `normalize_live_orderbook` + `walk_depth` (both fee-independent) before
  fee resolution is attempted, and route a staleness failure specifically rather than through the
  generic acquisition-blocked path.
- An incomplete universe refresh fails closed: the cycle returns without discovery, observation
  persistence, or disappearance writes. A relationship returning after `DISAPPEARED` receives a
  new persistence-episode identity, so lifetimes cannot cross the closed observation gap.
- **No empirical lead-frequency, lead-lifetime, or after-cost-executability result exists yet.**
  This checkpoint adds only the instrumentation; it has not been run against live data. The
  smallest trustworthy next step is exactly what the M27B.2 objective describes: run the CLI
  read-only, unattended, for 1–2 weeks, and read the resulting `summarize_run` output.
- `requested_quantity` defaults to one contract — the smallest, most conservative research
  depth-walk size, matching this repository's own "capital exposure begins with a one-contract
  canary" convention elsewhere (see `docs/m28-production-weather-strategy.md`). It does not imply
  any position-sizing recommendation.

## Tests

`tests/test_structural_measurement.py` (13 tests): `relationship_id` stability across quote
changes vs. sensitivity to rules changes; each of the six observation constructors, including
their required-reason and state/fee-treatment invariants; insufficient-depth mapping; the "fee
eliminates apparent lead" case (`EXACT_CONFIRMED` with a non-positive adjusted gap, built from a
real canonical `StructuralConfirmation`); a genuinely positive after-cost case
(`AFTER_COST_POSITIVE_RESEARCH`); a mismatched-confirmation rejection; `STALE`/`DISAPPEARED`/
`AMBIGUOUS` construction and their reason requirements; full `__post_init__` invariant coverage;
lifetime computation (persistence, right-censoring, mixed-relationship rejection); and
frequency/persistence/executability separation in `summarize_run`.

`tests/test_structural_measurement_store.py` (7 tests): persistence and exact round-trip,
idempotent replay, content-collision rejection, direct `UPDATE`/`DELETE` rejection via the
database triggers themselves (not just the Python API), rejection of non-`LeadObservation`
payloads before any database access, multi-relationship listing, and reopen-durability.

`tests/test_structural_measurement_runner.py` (7 tests, real end-to-end wiring with fake
transports — no live network in any test): universe refresh; a monotonic ladder producing zero
leads and an inverted ladder producing exactly one (mirroring canonical M27B's own
monotonic/inverted-ladder distinction) plus a duplicate-sibling cohort producing zero leads;
a full `attempt_exact_confirmation` happy path reaching `AFTER_COST_POSITIVE_RESEARCH` through the
complete market/orderbook/series/fee/specification pipeline; a semantically-invalid specification
("wrong semantic family") blocking closed; a stale orderbook snapshot blocking closed; a
`run_scan_cycle` two-scan sequence closing a lead as `DISAPPEARED` once it stops reproducing; and
`run_forever` respecting `max_iterations` with a fake sleeper (never sleeping after the final
scan).

`tests/test_m27b2_architecture.py` (4 tests): an AST guard asserting none of the three new modules
import any execution/risk/authenticated-credential package, a guard-is-live sanity check, and two
tests proving the guard actually detects a forbidden import in both `from ... import` and
`import ...` forms.

Full-repository verification at this branch: `mypy` — 0 errors across 268 source files; `ruff
check`/`ruff format --check` — clean across the whole repository; `pytest` — 3,061 passed (0
failed) excluding the `postgres` marker (unrelated to this change, requires a live Postgres
service); `bandit -r services` — 0 high-severity findings, no new medium/low findings attributable
to the new modules; `detect-secrets scan --all-files` — exits 0, no new candidates in the new
files.

## Claims not established

M27B.2 establishes no forecast skill, fair value, arbitrage, guaranteed or final net profit,
capital allocation, trading readiness, or execution authority. It has not yet been run against
live data, so it makes no claim about actual lead frequency, actual lead persistence, or actual
after-cost executability on the real exchange — those are exactly the open questions this
instrumentation exists to measure, not to answer by construction. `research_only = True` and
`production_influence = Decimal("0")` remain fixed everywhere. Independent review is required
before any live, unattended run.
