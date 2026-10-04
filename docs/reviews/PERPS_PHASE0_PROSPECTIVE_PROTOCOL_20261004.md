# Perps Reference-Lag Phase 0 — Prospective Protocol Freeze

Status: **FROZEN BEFORE FIRST PROSPECTIVE SESSION / READ-ONLY / DISARMED**

Frozen on: 2026-10-04

Protocol SHA-256:
`74d653afaf2e2b7b830814e99f5fd7dca12cb69b1a025987c680574c3074a1ba`

This is the first untouched prospective mechanism slice after canonical PR #173.
It tests only whether market-bound reference-price movement is followed by
same-direction Kalshi quote repricing. It does not test a tradable strategy.

## Exact collection roster

Environment: **production read-only**

Ticker: **BTCPERP only**

There is no ticker substitution. If BTCPERP is unavailable, closed, not
entitled, schema-incompatible, or lacks usable reference evidence, the affected
session is FAILED/MISSING. Another market may not be substituted after seeing
that result.

Eight one-minute sessions are frozen:

| Session | UTC start |
| --- | --- |
| P0-S01 | 2026-10-05 12:00 |
| P0-S02 | 2026-10-05 15:00 |
| P0-S03 | 2026-10-05 18:00 |
| P0-S04 | 2026-10-05 21:00 |
| P0-S05 | 2026-10-06 00:00 |
| P0-S06 | 2026-10-06 03:00 |
| P0-S07 | 2026-10-06 06:00 |
| P0-S08 | 2026-10-06 09:00 |

Each scientific collection window is exactly 60 seconds. A scheduled start may
be accepted up to 300 seconds late. No retry and no backfill are permitted.

The production read-only boundary is not widened: each session is one bounded
connection with no order capability and `production_influence = 0`.

## Dependence

The primary dependence unit is **SESSION**.

Individual reference impulses inside one session are serially dependent and are
not independent experiments. The eight session clusters are the units used for
the primary mechanism summary.

This is still a small exploratory mechanism sample, not statistical proof of
edge.

## Frozen impulse and horizon rules

The canonical PR #173 Phase-0 rules are unchanged:

- every observed nonzero market-bound reference-price change is an impulse;
- no post-hoc minimum-move threshold;
- reference movement is UP/DOWN, never LONG/SHORT;
- horizons are 1s, 2s, 5s, 10s;
- 30-second book maximum age;
- exact source evidence, contract identity, stream identity, and causal
  timestamps;
- post-horizon same-stream continuity witness;
- contiguous sequence proof;
- explicit abstentions for missing/stale/gapped/boundary/non-comparable rows.

No cooldown or deduplication rule is added in this slice.

## Primary mechanism metric

Primary horizon: **2 seconds**.

Confirmatory horizon: **5 seconds**.

For a measured midpoint row:

- UP reference move -> signed response = midpoint-change-bps;
- DOWN reference move -> signed response = negative midpoint-change-bps.

Within each session, take the arithmetic mean signed midpoint response at each
of the two frozen horizons.

A session is eligible for the primary mechanism summary only when it contains at
least **3 measured midpoint impulses at 2s and at least 3 at 5s**.

Eligible sessions receive equal weight regardless of impulse count.

Minimum evidence before any mechanism disposition:

- at least **5 eligible sessions**;
- at least **30 measured 2-second midpoint impulses** across eligible sessions.

## Frozen result vocabulary

### PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH

Only when minimum evidence is met and all are true:

- cluster-equal 2s mean signed midpoint response > 0;
- cluster-equal 5s mean signed midpoint response >= 0;
- at least 60% of eligible sessions have positive 2s mean response.

This means only that the mechanism deserves another prespecified research
checkpoint.

### NO_APPARENT_MECHANISM

Only when minimum evidence is met and both are true:

- cluster-equal 2s mean <= 0;
- cluster-equal 5s mean <= 0.

### INCONCLUSIVE_INSUFFICIENT

Used when the minimum session/measurement evidence is not met.

### INCONCLUSIVE_MIXED

Used for all other adequate-but-mixed patterns.

No result from this slice authorizes trading or radar admission.

## Missing/failure accounting

Every one of the eight frozen session IDs must appear exactly once in the final
reconciliation as CAPTURED, FAILED, or MISSING.

FAILED/MISSING sessions remain visible in the report. They are never retried,
backfilled, silently removed, or converted to zero-response observations.

## Explicitly outside this checkpoint

This protocol does not calculate or authorize:

- probability or fair value;
- trade direction;
- fees;
- funding economics;
- slippage;
- fill probability;
- adverse selection;
- P&L;
- capacity;
- radar alerts;
- order construction or execution;
- capital allocation;
- production promotion.

If the mechanism looks promising, the next checkpoint must separately freeze
the economic/executable model before any untouched economic sample is observed.
