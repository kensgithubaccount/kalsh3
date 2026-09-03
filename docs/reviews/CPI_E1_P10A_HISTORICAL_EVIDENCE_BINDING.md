# CPI-E1-P10A — Historical Evidence Binding + Modelability Gate

## Result

The offline analyzer in `services/forecasting/cpi_p10a_binding.py` mechanically
revalidates the frozen P8, P9A, P9B, and contract-semantics artifacts and derives
the overlap. The integrated head is `8da06d1b0286e60b66dc1b52ffab802068e73d66`,
tree `6869c89e5d4a3da129336110409945ffbb015ed0`, with merge parents in order
`ee85c5ab3fe77e2e6021c671240dfab3801b18ab` and
`eaae7dacc977aefb2cf314c3dda53fd1817b646f`. The prior head was
`ee85c5ab3fe77e2e6021c671240dfab3801b18ab`, tree
`e4e300b477165e9b09c56869f706807cf76bcf45`. It does not recollect evidence
and has no network, account, order, execution, fee, or production influence.

| Quantity | Result |
|---|---:|
| P9A independent events | 60 |
| P9A sibling contracts | 474 |
| truth-overlap events before rule rejection | 46 |
| missing-truth rows | 111 |
| absent/ambiguous-predicate rows | 21 |
| predicate/reference mismatch rows | 1 |
| accepted truth-bound sibling rows | 341 |
| truth-overlap events before rule rejection | 46 |
| fully excluded predicate-authority events | 4 |
| accepted independent events | 42 |
| accepted sibling rows | 341 |
| usable YES-ask crossing rows | 291 |
| two-sided non-boundary rows | 200 |

The accepted counts are derived, not asserted. The 21 absent/ambiguous predicate
rows span `CPI-21JUN`, `CPI-21JUL`, `CPI-21AUG`, `CPI-21OCT`, `CPI-22APR`, and
`CPI-22MAY`; the first four events are fully excluded, while the latter two
retain other authoritative siblings. The 14 later P9A events without canonical
initial-release truth remain rejected.

P9B fee coverage is 0 exact historical rows / 0 events, 272 interval-unproven
rows / 31 events, 110 locator-only rows / 14 events, and 92 unknown rows / 15
events across P9A. Within the 341 accepted P10A rows, the counts are 0 exact,
253 interval-unproven, 18 locator-only, and 70 unknown; the event counts are 0,
28, 3, and 11. Every frozen fee row has `economics_usable=false`; no exact
after-cost backtest is produced.

## Integrated PR scope

The exact PR scope versus canonical main is five P10A files, all added by the
branch: this review, the P7 timing receipt, the P10A runner, the P10A binder,
and its tests (`1093` added lines). Canonical main was absorbed with the normal
merge above; P8, P9A, and P9B evidence bytes were not modified or recollected.

## Authority inventory

- Canonical base: `e8c6faff5a72db6010fd4ae22713b0a0831b947e`;
  tree `353aeba5d99c67c5baa4c72901965b323367ecbf`.
- P8: `docs/reviews/artifacts/bls-annual-zips/archive-{2021..2024}.zip`,
  checked against the four frozen SHA-256 values by
  `load_frozen_target_cohort`; only CPI-U, U.S. city average, all-items, SA,
  MoM current/reference-month cells are accepted.
- P5A publication-time authority: `CPI_E1_P5A_EMPIRICAL_SMOKE_RECEIPT.md`,
  SHA-256 `097434bb64de7750a7793255841db38a00d6e8be3c22aff2517f03989ecfc836`.
- P6 initial-release value authority: `CPI_E1_P6_INITIAL_RELEASE_VALUE_EVIDENCE.md`,
  SHA-256 `4eaf6492ea85a8042b4447a9c0abc66e6e3746af20c0f1f9c4be6ffaab2384b4`.
- P7 settlement reconciliation authority: `CPI_E1_P7_SETTLEMENT_RECONCILIATION.md`,
  SHA-256 `cec4c1bd323a7ce142db6ca53507a70afccc2ebbec917934d6bbc26861c36f20`.
  The machine-readable receipt identifies and verifies all three document
  bytes. Its fixed semantic digest additionally binds every reviewed P5A
  acquisition, artifact, timing, publication, and initial-value identity.
- P9A: `evidence/cpi_p9a_historical_price/manifest.json`,
  `acquisition_manifest.json`, `market_inventory.json`, and the 474 raw
  responses. `validate_frozen_cohort` checks all content hashes, request
  identities, ticker membership, semantic hashes, and strict pre-close candle
  selection.
- Contract semantics: P9A's canonical parser output; only `GT`/`>` rows are
  admitted. Event identity is `kalshi:<event_ticker>`.
- P9B: `evidence/cpi_p9b_fee_authority/event_coverage.json`, validated against
  its frozen manifest, authority timeline, and raw artifacts. Endpoint formula
  agreement is not promoted to interval continuity or economics authority.

## Binding and rejection policy

The join requires exact event grammar, event-to-market identity, exactly one
supported rules predicate, P9A semantic comparator/threshold, initial-release
month/value, finalized settlement result, and P9A evidence/request identities.
A candle is eligible only when `end_period_ts < market_close`. The 291-row
ask/crossing layer requires a valid non-boundary YES ask; the bid may be absent
or at its NO-entry boundary. The 200-row two-sided layer requires both valid
non-boundary bid and ask. Midpoint exists only for that two-sided subset. No
last trade, retrospective volume, or post-cutoff candle is used.

The rejected rows are: 111 `missing authoritative initial-release truth` rows,
21 `contract predicate is absent or ambiguous` rows, and one
`predicate/reference month mismatch`. No placeholder is admitted from P9A
metadata. The isolated mismatch is `CPI-22JUN-T0.2`: rules-derived July 2022
versus the ticker/event's June 2022, threshold `0.2`, comparator `GT`; the
market-inventory row and its P9A semantic/request evidence are the involved
artifacts.

The rule/predicate rejection layer is 22 rows across seven events: 21 absent or
ambiguous rows and one reference-month mismatch. Four events are fully excluded
by predicate authority; the remaining predicate-rejection events retain other
accepted siblings. Accepted independent events are the 42-event truth overlap
after those exclusions, with 341 accepted sibling rows.

## Market baseline

Each applicable sibling is averaged within its event, and then events receive
equal weight; sibling rows therefore do not create independent statistical
weight. The report generated by `scripts/run_cpi_e1_p10a.py` separates:

- ask/crossing evidence: 291 valid non-boundary YES asks across 42 events;
  Brier **0.0884086982** and clipped log loss **0.3163106457**;
- two-sided bid/ask evidence: 200 rows, with bid Brier **0.1021283088**,
  ask Brier **0.1174086801**, and score ranges represented by the bid/ask
  diagnostics;
- midpoint diagnostic: 200 rows, Brier **0.1049709219**, explicitly
  non-executable;
- boundary/one-sided/missing evidence: 141 boundary rows, 139 one-sided rows,
  276 stale rows, and 50 ask-unusable rows, with exact overlapping exclusion
  reasons retained in the report.

Log loss clips diagnostic prices to `[0.01, 0.99]` before natural-log scoring.
- Calibration is descriptive and event-clustered; each bin reports event count
  and sibling-row count after binning individual sibling diagnostics and then
  aggregating within event.
- Fresh/stale and threshold subgroup diagnostics are event-equal-weighted after
  sibling aggregation; every result reports both event and sibling-row counts.

These are quote-evidence diagnostics, not fill truth. The frozen evidence does
not establish depth, queue position, fill probability, slippage, fees, or
after-cost economics.

The event-equal market baseline is therefore Brier **0.0884086982** and clipped
log loss **0.3163106457**, calculated from 291 usable YES-ask crossing rows over
42 events. Boundary count is 141, one-sided count is 139, stale count is 276,
and unusable-ask count is 50; these exclusion counts overlap by design.

## P10A.1 temporal and quote repair

P7 release times are now read only from the durable
`docs/reviews/artifacts/cpi-p7-release-timing.json` receipt. P5A is the
publication-time authority; P6 is the initial-release value authority; and P7
is the settlement-reconciliation authority. Calendar arithmetic is not used.
The repaired values are 2025-08-12 12:30 UTC,
2026-01-13 13:30 UTC, and 2026-02-13 13:30 UTC.

Contract reference months now require the rules predicate. The ticker is only a
cross-check. The exact rejected mismatch is `CPI-22JUN` /
`CPI-22JUN-T0.2`: rules-derived month July 2022 versus event month June 2022;
the P9A inventory row and P9A semantic/request evidence are the involved
artifacts. This is an isolated row-level defect. The three `CPI-21OCT`
placeholder rows are separately rejected as unavailable rule authority. The
repaired cohort is 42 independent events and 341 truth-bound siblings; 14
later events remain outside the P8/P7 truth intersection.

The ask/crossing diagnostic is crossing-price evidence, not a neutral market
probability and has no depth or fill authority. The 200-row subset is the
two-sided non-boundary subset only; it is not the full valid ask cohort.
Subgroup and calibration summaries aggregate siblings within event before equal
event weighting.

## Modelability gate

Admissible existing input: prior P8 initial-release values whose independently
proven `release_instant` is strictly before the relevant cutoff. Missing evidence
is an independent contemporaneous forecast or survey vintage with exact public
availability timestamps, plus point-in-time release-calendar snapshots for every
cutoff. Consequently no challenger model is scored or selected in P10A, and no
revised historical value is manufactured as a feature.

Verdict: **PREDICTOR EVIDENCE ACQUISITION REQUIRED**.

The acquisition specification is deliberately provider-neutral: one professional
CPI consensus or nowcast family with one immutable artifact per release event.
Each artifact must contain the exact reference month, forecast value and unit,
public source URL, raw response bytes or durable receipt, raw SHA-256,
publication timestamp with timezone, retrieval timestamp, and a provenance
record proving the value was public no later than that event's market cutoff.
The provider must cover the repaired event window, preserve original vintages
without later revisions, and expose enough prior releases for a chronological
rolling evaluation. A candidate is rejected if any vintage timestamp is
caller-authored, only recoverable from a current snapshot, published after the
cutoff, revised without the original value, or ambiguous at the reference-month
level. Acquisition should first freeze a small coverage receipt and artifact
inventory; only after independent review may a separate milestone preregister
the rolling baseline and challenger.

The smallest next profitability-relevant action is to acquire and freeze one
small, independently timestamped contemporaneous predictor family for the same
42-event domain, then preregister a rolling prior-release baseline and one
transparent challenger before opening the final test partition. Fees remain a
separate attachment boundary keyed by actual decision timestamp.

## Not established

P10A does not establish market-relative predictive signal, statistical
significance, causal predictability, executable fills, fee-adjusted edge,
profitability, capacity, sizing, risk authorization, or production readiness.

## Differences from the previous P10A report

The prior report was based on `ee85c5ab…` and did not bind P9B. After canonical
main was merged, P8/P9A/contract-semantics counts and market diagnostics were
unchanged: 46 truth-overlap events, 42 accepted events, 341 accepted siblings,
291 usable asks, 200 two-sided rows, Brier 0.0884086982, and clipped log loss
0.3163106457. The material change is the P9B attachment: zero exact historical
fee coverage, with the 253/18/70 bound-row and 28/3/11 bound-event statuses.
This explicitly blocks after-cost economics and does not create a fee-adjusted
prediction or model score.
