# CPI-E1-P11 Phase 0 — prospective Reuters-vs-Kalshi replication freeze

## Status

This checkpoint freezes the first untouched prospective replication of the
reviewed CPI-E1-P10D Reuters-vs-Kalshi directional diagnostic.

No Reuters forecast for the target event was searched, inspected, recorded, or
used to choose this protocol. No target-event BLS outcome exists yet. No
predictive result, fee result, P&L, sizing, fill model, or trading authority is
created here.

Frozen target:

- series: `KXCPI`
- event: `KXCPI-26SEP`
- reference month: September 2026
- BLS release: 2026-10-14 08:30 ET / 12:30Z
- Kalshi event close: 2026-10-14 08:25 ET / 12:25Z
- expected complete sibling count: 14
- research only; production influence exactly zero

The BLS and Kalshi schedule facts were verified from first-party public sources
before the freeze. They must be revalidated from first-party sources again
before collection; any mismatch fails closed.

## Reused reviewed methodology

P11 reuses the narrow P10D/P9A conventions rather than inventing a new metric
after seeing the target event:

1. **Market evidence** — for each sibling, select the unique latest 60-minute
   Kalshi candle whose `end_period_ts` is strictly before that market close.
2. **Primary market price** — YES ask only. Midpoint remains diagnostic only.
   Last trade, candle close, interpolation, and synthesized prices are not
   substitutes.
3. **Reuters call** — YES iff the exact Reuters headline CPI MoM point forecast
   is strictly greater than the sibling threshold; equality is NO.
4. **Kalshi call** — YES iff YES ask > 0.5, NO iff < 0.5, exact 0.5 is a tie
   excluded from the common primary denominator.
5. **Truth** — initial-release BLS headline CPI month-over-month only; later
   revisions are not admissible truth.
6. **Independence** — the CPI release is the independent unit. Sibling rows are
   non-independent diagnostics.

## First-party preflight

Immediately before collection, first-party evidence must still establish all
of the following:

- BLS schedules September 2026 CPI for Oct. 14, 2026 at 08:30 ET;
- Kalshi event identity is exactly `KXCPI-26SEP` in series `KXCPI`;
- the complete event contains exactly 14 siblings;
- every admitted sibling is active/open, simple binary, non-provisional,
  non-MVE, and strict-GT headline CPI MoM for September 2026;
- every admitted sibling closes at exactly 12:25Z.

No threshold subset may be selected by the researcher. The complete qualifying
event is the cohort.

## Market evidence window

The primary market-evidence acquisition window is fixed to
12:05Z through 12:20Z on Oct. 14.

The selected candle itself is determined only by the frozen rule, so transient
public-read failures may receive at most three attempts inside that fixed
window. Nothing acquired after 12:20Z can become primary evidence.

Missing/boundary YES asks are excluded symmetrically for both Reuters and
Kalshi. They are never imputed.

## Artifact layout and immutability

The logical run root is frozen as:

`cpi_e1_p11_prospective_20261014/`

Required terminal evidence layout:

- `protocol.json` — byte-identical copy of this frozen machine-readable spec;
- `preflight.json` — first-party BLS/Kalshi identity and timing checks;
- `kalshi/event.json` — exact bounded first-party event response/evidence envelope;
- `kalshi/candles/<market_ticker>.json` — one exact bounded response/evidence
  envelope per sibling used by the fixed candle selector;
- `reuters/coverage.json` — terminal Reuters acquisition state;
- `reuters/receipt.json` and `reuters/extract.json` only when Reuters
  evidence reaches PASS;
- `bls/initial_release.json` — post-release first-party truth evidence;
- exactly one of `result.json` or `failure.json`;
- `manifest.sha256` covering the terminal run artifacts.

First-party responses retain exact bounded bytes, or an equivalent raw-body
envelope plus SHA-256. Reuters evidence follows the established minimal
reviewable metadata/extract-hash model and does not require committing full
copyrighted wire text.

Every receipt binds the frozen protocol digest and target event. Terminal
artifacts are create-once: no artifact may be rewritten after terminal
issuance. Any correction must be a new append-only receipt.

**No backfill is permitted after 12:20Z on Oct. 14.** A late or missed
collection is `INCONCLUSIVE_INSUFFICIENT`, never reconstructed after the
fact into a prospective PASS.

## Reuters evidence authority

P11 carries forward the reviewed P10B/P10C admission bar.

The search ladder remains:

1. Reuters direct, exact-release-date search/fetch;
2. the already-reviewed syndicated Reuters hosts using exact-release-date and
   day-before queries;
3. Wayback/CDX over the identical approved host set.

PASS requires all of:

- positive Reuters attribution;
- exact September 2026 reference month in the article body/dateline;
- specific prospective headline CPI MoM forecast;
- exact Decimal precision as published;
- positively evidenced governing publication time before every scored sibling
  close;
- at least two independently operated hosts carrying the same Reuters wire
  revision.

A single-host observation remains insufficient even if otherwise persuasive.

Reuters evidence acquisition must complete by 12:20Z. Failure to obtain PASS
evidence is an explicit terminal state, not a reason to weaken the source bar.

## Primary gate

At least four siblings must remain on the common primary denominator. This
predeclared floor matches the smallest primary-eligible event size in the
reviewed P10D historical cohort; it is not selected from the new event result.

For each eligible sibling, Reuters and Kalshi are scored against the same BLS
initial-release truth.

The single-event result is classified as:

- `PROSPECTIVE_SUPPORT_DIAGNOSTIC` if Reuters has strictly more correct
  sibling calls than Kalshi;
- `PROSPECTIVE_CONTRADICTION` if Reuters has strictly fewer;
- `PROSPECTIVE_TIE_NO_ADVANTAGE` if the counts are equal;
- `INCONCLUSIVE_INSUFFICIENT` if preflight/evidence/truth fails or fewer than
  four common eligible siblings remain.

Even a supportive result has **promotion authority NONE** and does **not**
authorize Phase-1 fee/P&L testing. It authorizes only freezing a multi-event
prospective extension before the next CPI release.

## Anti-overfitting and safety

The target event cannot be substituted after this freeze. The market snapshot
rule, Reuters admission rules, threshold cohort rule, directional formulas,
truth source, denominator floor, and decision labels cannot be changed after
seeing Reuters or BLS truth.

No Reuters-to-probability transform is permitted. No fees, P&L, sizing,
slippage, fills, queue position, execution simulation, capital allocation, or
orders are part of P11 Phase 0.

The frozen machine-readable artifact is:

`docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json`

Its frozen digest is:

`e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d`
