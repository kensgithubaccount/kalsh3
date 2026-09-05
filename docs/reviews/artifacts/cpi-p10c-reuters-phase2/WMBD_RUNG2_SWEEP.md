# CPI-E1-P10C Phase 2-R2 — WMBD Rung-2 supplemental sweep (2026-09-05)

## Gap identified

`SEARCH_PROCEDURE.md`'s bounded acquisition-path audit names WMBD as one of
the approved Rung-2 syndication hosts ("the exact host set P10B already
reviewed and committed evidence from: Yahoo Finance, kfgo.com, TradingView
News, Nasdaq, Investing.com, **WMBD**, AOL"), but the literal Rung-2 query
templates (`R2a`/`R2b`) omit `site:wmbd.com` from the `OR`-joined host list.
WMBD was therefore never actually queried at Rung 2 for any event during the
original Phase 2 acquisition run.

This sweep closes that gap. Scope is strictly the missing WMBD Rung-2 checks
for the 36 events below. It does not rerun Rung 1, Rung 2 for any other host,
or Rung 3 for any host, and does not change the v2 procedure, the admission
filter, the >=2-host corroboration rule, the CPI-24JAN R1 decision, the
frozen 42-event cohort, or `per_sibling_market` cutoff semantics.

## Prior, already-established WMBD Rung 3 result (not repeated here)

WMBD Rung 3 (Wayback CDX, `wmbd.com` domain, +/-2 days per event) was already
completed for all 36 events below in a prior session, with a uniformly
negative result: `wmbd.com`'s entire Wayback footprint (sanity-checked across
2020-2025, 507 total captures) is bare domain-root/`robots.txt` 301 redirects
— the domain forwards to `centralillinoisproud.com` and has never had
crawled article content. That finding is reported here for continuity only;
it was not re-verified or re-run by this sweep.

## Events in scope (36)

The 38 events UNKNOWN after Phase 2-R1, minus the 2 P10B-reused UNKNOWN
events (`CPI-21SEP`, `CPI-23JUN`, out of scope — separate, already-reviewed
P10B evidence) = 36 Phase-2-searched UNKNOWN events.

## Procedure

Two exact-date `WebSearch` queries per event, domain-restricted to
`wmbd.com`, per the frozen Rung-2 query template applied to the one omitted
host:

- `Reuters "consumer prices" "{release_date}" site:wmbd.com`
- `Reuters "consumer prices" "{day_before}" site:wmbd.com`

`release_date` / `day_before` are the same frozen-manifest-derived dates
used throughout Phase 2 (release_date = calendar date of the event's
earliest frozen `sibling_cutoff`; day_before = release_date minus 1 day).
Every candidate, had one been found, would still have been subject to the
full unchanged admission filter and the >=2-host corroboration gate before
any terminal-state change.

## Results

72/72 queries executed (36 events x 2 queries). **0 qualifying candidates
found** — every query returned zero results from `wmbd.com`. No candidate
reached the admission filter; none was available to corroborate or
resurrect any of the 36 UNKNOWN events, including the near-miss events
identified in the handoff (`KXCPI-24NOV`, `CPI-22APR`, `CPI-23MAY`,
`CPI-23DEC`, `CPI-24JAN`).

| # | event_ticker | release_date query | day_before query |
|---|---|---|---|
| 1 | CPI-21DEC | no results | no results |
| 2 | CPI-22JUN | no results | no results |
| 3 | CPI-23MAR | no results | no results |
| 4 | KXCPI-24NOV | no results | no results |
| 5 | CPI-22JUL | no results | no results |
| 6 | CPI-22AUG | no results | no results |
| 7 | CPI-22SEP | no results | no results |
| 8 | CPI-22OCT | no results | no results |
| 9 | CPI-22NOV | no results | no results |
| 10 | CPI-22DEC | no results | no results |
| 11 | CPI-24FEB | no results | no results |
| 12 | CPI-24MAR | no results | no results |
| 13 | CPI-24APR | no results | no results |
| 14 | CPI-24MAY | no results | no results |
| 15 | CPI-24JUN | no results | no results |
| 16 | CPI-24JUL | no results | no results |
| 17 | CPI-24AUG | no results | no results |
| 18 | CPI-24SEP | no results | no results |
| 19 | CPI-24OCT | no results | no results |
| 20 | KXCPI-24DEC | no results | no results |
| 21 | CPI-21NOV | no results | no results |
| 22 | CPI-22JAN | no results | no results |
| 23 | CPI-22FEB | no results | no results |
| 24 | CPI-22MAR | no results | no results |
| 25 | CPI-22APR | no results | no results |
| 26 | CPI-22MAY | no results | no results |
| 27 | CPI-23JAN | no results | no results |
| 28 | CPI-23FEB | no results | no results |
| 29 | CPI-23APR | no results | no results |
| 30 | CPI-23MAY | no results | no results |
| 31 | CPI-23JUL | no results | no results |
| 32 | CPI-23SEP | no results | no results |
| 33 | CPI-23OCT | no results | no results |
| 34 | CPI-23NOV | no results | no results |
| 35 | CPI-23DEC | no results | no results |
| 36 | CPI-24JAN | no results | no results |

## Reconciliation

No terminal-state changes. Coverage remains **4 PASS / 38 UNKNOWN / 0
FAILURE = 42**, identical to the post-R1 count. This sweep only adds a
negative-search fact to the record for each of the 36 UNKNOWN events'
existing `reason` field (see `coverage.json`), closing the WMBD Rung-2 gap
so a future audit does not need to repeat it.

## Not established (unchanged)

- No predictor-vs-Kalshi scoring or comparison.
- No Brier score, log loss, hit rate, or calibration.
- No edge, P&L, fees, or after-cost economics.
- No change to trading/capital/execution/production authority
  (`production_influence` remains 0).

## Reproducible

`python3 scripts/validate_cpi_p10c_phase2_reuters_acquisition.py` and
`pytest tests/test_cpi_p10c_phase2_reuters_acquisition.py` both check this
sweep's completeness (72 queries, 36 events, 0 qualifying) against
`coverage.json`.
