# WN Multicity Prospective Evaluation — Pre-Outcome Phase 0 Freeze

Status: **PRE-OUTCOME / OUTCOME-BLIND / RESEARCH ONLY**

This checkpoint freezes the cohort identity, dependence treatment, and
operational completeness rules for the WeatherNext multicity prospective
block before any post-block outcome scoring is implemented.

It does **not** inspect settlement outcomes, compute forecast accuracy, compare
WeatherNext with Kalshi, compute fees/P&L, fit or tune a model, change the live
collector, or grant production/trading authority.

## Bound prospective identity

- Experiment: `wn_multicity_prospective_v1`
- Frozen protocol SHA-256:
  `56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346`
- Frozen pre-first-event amendment SHA-256:
  `b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000`
- Independently reviewed deployed image digest:
  `sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08`
- Target dates: 2026-10-03 through 2026-10-14 inclusive.
- Cities: Boston, Miami, Denver, Los Angeles, Seattle.
- Frozen observational unit: CITY_DAY.
- Frozen dependence statement: city-days are not independent.

The local frozen protocol/amendment bytes must be imported byte-for-byte into
canonical repository history before a post-outcome scorer is allowed to treat
these hashes as fully self-contained repository authority. This Phase 0
checkpoint binds their already-observed identities but does not rewrite or
reconstruct those source files.

## Primary independence unit

The primary inferential unit is the **target-date cluster**, not an individual
city-day and not an individual sibling market.

The full block therefore has at most 12 primary date clusters, with up to 5
correlated city-day diagnostics inside each cluster. No report may describe the
60 possible city-days as N=60 independent evidence.

## Operational completeness rule

For an expected city/date:

- `packet.json` present and `failure.json` absent => `CAPTURED`;
- `failure.json` present and `packet.json` absent => `FAILED`;
- neither => `MISSING`;
- both => `CONFLICT`, fail closed.

Missing/failed/conflicted observations remain in the denominator and report.
They are never backfilled, silently dropped, or converted into wins/losses.

A date cluster is structurally complete only when all five expected city-days
are `CAPTURED`. This completeness classification is operational only; it is
not a forecast-performance score.

## What is frozen before outcomes

The post-block evaluator must preserve these rules:

1. exact date and city roster above;
2. exact protocol/amendment/image identities above;
3. date-cluster primary dependence treatment;
4. all abstentions, failures, missing observations, and incomplete market
   depth preserved;
5. no retrospective retry/backfill;
6. no rounding of WeatherNext or settlement values unless separately reviewed
   settlement authority explicitly requires it;
7. deterministic, append-only outcome reconciliation;
8. exact contract comparator semantics from the canonical settlement-boundary
   authority;
9. research-only result, `production_influence = 0`;
10. no production promotion based on this 12-cluster block alone.

## Deferred until the frozen source packet is canonicalized

The exact post-outcome scorer is intentionally not implemented in Phase 0
because the multicity frozen source directory and packet schemas currently
exist outside canonical GitHub main.

Before scoring code is added, import and review the exact bytes for:

- `protocol.json`;
- `freeze_manifest.json`;
- every frozen amendment;
- `policy.py` and its checksum manifest;
- final collector review/result artifacts;
- one representative sealed city-day packet schema (without using its outcome
  to choose metrics).

The scorer can then be implemented against real field identities instead of
invented schema assumptions.

## Planned post-block report layers

After the block closes and outcome authority is separately bound, report in
this order:

1. provenance and operational completeness;
2. authoritative settlement reconciliation;
3. point-forecast diagnostics (signed error, absolute error, squared error)
   with date-cluster-equal aggregation;
4. exact sibling-bucket hit diagnostics using reviewed comparator semantics;
5. market-relative diagnostics only under a frozen executable-price convention;
6. after-cost economics only when point-in-time fee and executable-depth
   authority are both available;
7. sensitivity and city/regime breakdowns as diagnostics, never as
   hindsight-selected promotion criteria.

No metric may be silently added because it makes the observed block look
better.

## Decision authority

This block can establish whether the research mechanism deserves additional
prospective study. It cannot by itself satisfy the MASTER_SPEC production
promotion targets, authorize capital, or enable autonomous trading.

The safe result vocabulary is:

- `PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH`;
- `NO_APPARENT_ADVANTAGE`;
- `INCONCLUSIVE`;
- `SCIENTIFIC_INVALIDITY`.

Any stronger production claim requires the normal market-relative,
after-cost, sample-size, walk-forward, prospective-shadow, risk, execution,
and human-authorization gates.
