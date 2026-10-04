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

The exact handoff archive was independently re-hashed after upload on 2026-10-04:
`de64c2d61dc8f05d7bb82579672036f54ce29b963e129d1fe3c669d312c995db`.
Its `protocol.json`, pre-first-event amendment, D1 checksum manifest, and final
collector exact-digest review match the identities already embedded in the live
October 3 artifacts. Canonical GitHub import of those unchanged bytes remains a
separate provenance-only change; this evaluator never reconstructs them from
memory.

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

## Exact sealed packet contract recovered before outcome scoring

A representative already-sealed October 3 Boston packet was supplied without
any settlement outcome. Its own `SHA256SUMS.json` matches the copied
`packet.json`, `forecast.json`, `market.json`, `weather.json`,
`delayed_books.json`, `structured_log.json`, and `start.json` bytes.

The frozen receipt establishes that:

- the scientific forecast is one continuous, unrounded Fahrenheit p50 proxy;
- `forecast_status = PRE_OUTCOME_FROZEN`;
- `outcome_not_consulted = true`;
- `no_probability = true`;
- `no_probability_or_edge_calculation = true`;
- `no_bias_correction = true`;
- `no_city_specific_tuning = true`;
- component hashes bind the exact forecast, market, and weather bytes;
- per-sibling market executability is preserved as a market/economic
  diagnostic and does not erase a valid weather-development observation.

The Phase 0 code therefore validates these exact pre-outcome identities and
component hashes, and freezes deterministic point-forecast error scoring
against a future authoritative full-precision Fahrenheit settlement value.

## Market-relative and economic inference is not available from this block

This block deliberately contains **no WeatherNext probability model**. The
frozen forecast rule also forbids rounding the continuous p50 proxy into the
integer Kalshi ladder. Therefore there is no pre-authorized transformation
from the WeatherNext receipt into six sibling probabilities, a categorical
bucket call, fair YES/NO prices, or expected trade value.

As a result, this block may report deterministic continuous point-forecast
error once authoritative outcomes are available, but it may **not** claim:

- Brier/log-loss improvement versus Kalshi;
- market-relative probability skill;
- a tradable YES/NO edge;
- hypothetical after-cost P&L generated from a retrospectively invented
  mapping;
- production promotion.

Creating a probability distribution, rounding rule, or market-relative model
after seeing the October outcomes would be post-outcome model fitting and is
explicitly prohibited. A later edge experiment must freeze that transformation
before its own prospective outcomes.

## Planned post-block report layers

After the block closes and outcome authority is separately bound, report in
this order:

1. provenance and operational completeness;
2. authoritative settlement reconciliation;
3. point-forecast diagnostics (signed error, absolute error, squared error)
   with primary aggregation over **complete five-city target-date clusters**;
4. partial-cluster and city/regime results as explicitly non-primary
   diagnostics;
5. operational market-executability and delayed-book diagnostics, without
   converting the p50 forecast into a trade;
6. no market-relative probability or after-cost edge statistic for this block,
   because no such WeatherNext probability/economic transformation was frozen
   pre-outcome.

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
