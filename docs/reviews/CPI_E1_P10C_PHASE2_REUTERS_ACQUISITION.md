# CPI-E1-P10C Phase 2 -- 42-event Reuters historical predictor evidence acquisition

## Result

Attempted deterministic, outcome-blind Reuters historical predictor evidence
acquisition against the exact frozen 42-event CPI-E1-P10C cohort. All 42
events reached an explicit terminal state:

- **5 positively proven** Reuters observations (PASS)
- **37 searched, no qualifying observation found** (UNKNOWN)
- **0 acquisition/authority failures**

5 + 37 + 0 = 42. This is historical predictor evidence acquisition only: no
Reuters-vs-Kalshi scoring, no Brier/log-loss/hit-rate/calibration, no edge,
P&L, fee, or after-cost calculation, and no change to trading/capital/
execution/production authority (`production_influence: 0` throughout).

## Preconditions verified

- Canonical `origin/main`: `84025da3c41fa41c036633b6718e6d72005622a6` (tree
  `514db273f2914e2ec79da824d0f1f1567a410680`), verified via `git fetch` +
  `git ls-remote`, re-verified a second time mid-task after a rate-limit
  interruption -- unchanged both times.
- Frozen Phase 1 manifest
  (`docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json`):
  self-reported `manifest_digest_sha256` matches
  `ff1e54a47cddac3a44e987ea3e099e8a8a339f8d2749c25ddbd961f0c4a6b1be`; 42
  accepted events / 341 accepted sibling rows recomputed programmatically;
  `cutoff_semantics == "per_sibling_market"`; no event-level `decision_cutoff`
  field anywhere in the manifest; accepted-threshold identity confirmed
  `11bc2723d0b75d0ab059f5c677ef061456f54d264592016b9e67402adffedec9`.

## Existing Reuters acquisition authority reused

Recovered and reviewed the canonical P10B Reuters authority
(`docs/reviews/artifacts/cpi-p10b-reuters/`, PR #125, merged into main). It
is a manual, per-event research process (not a coded pipeline): live
web-fetch a Reuters wire item or its syndicated copies, verify Reuters
attribution, reference-month/year, prospective tense, and cross-host
corroboration, then commit a `receipt.json` + `extract.json` pair. All 5
P10B-sampled events (`KXCPI-25JUL`, `KXCPI-26JAN`, `KXCPI-25DEC` PASS;
`CPI-21SEP`, `CPI-23JUN` UNKNOWN) fall inside the frozen 42-event cohort and
were reused as-is after independent validation: re-hashed all 7 committed
P10B artifacts against their declared digests (all matched), re-ran
`scripts/validate_cpi_p10b_reuters_authority.py` against current main (29/29
checks passed).

No code change was required to scale this authority: the same manual
research process, generalized into a fixed, written, outcome-blind procedure
(below), was applied directly to the remaining 37 events without touching
any P8/P9A/P9B/P10A/P10C-Phase-1 code or evidence.

## Deterministic search procedure (v2)

Full text: `docs/reviews/artifacts/cpi-p10c-reuters-phase2/SEARCH_PROCEDURE.md`.

A v1 pass (generic month/year web search) was piloted on 4 events and found
only wrong-year template reposts -- current search indexes are strongly
recency-biased and repeatedly alias month-name queries to the newest
same-template Reuters wire repost rather than the historically correct one.
v2 replaced this with a fixed, audited 3-rung fallback ladder, applied
identically to every event and derived only from frozen manifest facts
(`event_ticker`, `reference_month`, `sibling_cutoff` dates) -- never from
realized CPI values, Kalshi settlement, or Kalshi prices:

1. **Rung 1 -- reuters.com direct**, exact-release-date `site:reuters.com`
   queries.
2. **Rung 2 -- approved syndication hosts** (the exact P10B-reviewed set:
   Yahoo Finance, kfgo.com, TradingView News, Nasdaq, Investing.com, WMBD,
   AOL), exact-release-date queries.
3. **Rung 3 -- Internet Archive Wayback/CDX lookup** of the same host set,
   to recover historical snapshots live search does not reliably surface.

A candidate is admissible only if it is Reuters-attributed, explicitly
discusses the exact reference month/year *verified in the article's own body
text* (never trusted from a title or search snippet -- this caught numerous
wrong-year and wrong-target traps across the run), states a specific numeric
forecast for headline CPI month-over-month in prospective tense, and is
published on/near the expected pre-release wire date. PASS additionally
requires **>=2 independently operated hosts** carrying the identical wire
revision. This corroboration bar was piloted, explicitly confirmed unchanged
after a single-host near-miss (`KXCPI-24NOV`), and held uniformly across all
42 events -- including two other single-host near-misses (`CPI-22APR`,
`CPI-23MAY`) and a same-host-twice near-miss (`CPI-23DEC`) that all
correctly terminated UNKNOWN rather than being stretched to PASS.

## Rate-limit interruption and recovery

Mid-run, 3 of 6 parallel research batches (17 of the 42 events) were cut off
by an Anthropic API session rate limit. Per explicit instruction, none of the
incomplete or undocumented partial claims from that interruption -- including
a bare, evidence-free "PASS" assertion for 2 events with no recorded
supporting detail -- were trusted or preserved. Canonical main and the frozen
Phase 1 manifest were independently re-verified as unchanged before resuming,
and all 17 affected events were re-run from a clean start under the identical
v2 procedure once the rate limit reset. No event was classified as UNKNOWN
merely because of the interruption.

## Positively proven observations (2 new + 3 reused)

| Event | Reference month | Value | Published (UTC) | Cutoff (UTC) | Hosts |
|---|---|---|---|---|---|
| KXCPI-25JUL | 2025-07 | 0.2 | 2025-08-12T04:02:11Z | 2025-08-12T12:29:00Z | Yahoo, kfgo.com (P10B) |
| KXCPI-25DEC | 2025-12 | 0.3 | 2026-01-13T05:03:53Z | 2026-01-13T13:29:00Z | Yahoo, WMBD, TradingView (P10B) |
| KXCPI-26JAN | 2026-01 | 0.3 | 2026-02-13T05:12:31Z | 2026-02-13T13:29:00Z | TradingView, Yahoo (P10B) |
| **CPI-23AUG** | 2023-08 | **0.6** | 2023-09-13T10:07:35Z | 2023-09-13T12:25:00Z | finance.yahoo.com, kfgo.com |
| **CPI-24JAN** | 2024-01 | **0.2** | 2024-02-12T08:19:09Z | 2024-02-13T13:25:00Z | tradingview.com, nasdaq.com |

Both new PASS receipts were independently re-verified by the acquiring
session -- a fresh direct re-fetch of every corroborating host, before the
receipt was written -- reproducing the exact attribution, timestamp, value,
and load-bearing sentence hash reported by the research process. Full
receipts: `docs/reviews/artifacts/cpi-p10c-reuters-phase2/{CPI-23AUG,CPI-24JAN}/`.

## 37 UNKNOWN events

Full per-event reasons: `docs/reviews/artifacts/cpi-p10c-reuters-phase2/coverage.json`.
Recurring, disclosed failure modes across the 37: (1) the only Reuters MoM
figure found is stated in a post-release, retrospective article ("economists
polled by Reuters had forecast..."), which fails the prospective-tense
admission requirement even though it accurately reports what was forecast;
(2) a genuine pre-release Reuters piece exists but states only the
year-over-year figure, not month-over-month; (3) exactly one admissible host
was found (`CPI-22APR`, `CPI-23MAY`) or the same wire item was found twice on
one host (`CPI-23DEC`), correctly failing the >=2-independent-host gate; (4)
kfgo.com is bulk-blocked (HTTP 403) in the Internet Archive for large windows
of 2022, and Wayback's own crawl coverage for `finance.yahoo.com`/other hosts
is sparse or times out for some historical windows -- a genuine corpus/tooling
coverage gap, disclosed per event, never silently converted to a false PASS
or a fabricated failure.

## Confirmations

- No Reuters-vs-Kalshi scoring, edge, P&L, fee, or after-cost calculation was
  performed at any point (`kalshi_scoring_performed: false`,
  `edge_pnl_fees_computed: false`).
- `production_influence: 0` throughout; no trading/capital/execution
  authority touched.
- `cutoff_semantics` remains `per_sibling_market`; no event-level
  `decision_cutoff` was invented.
- Reproducible independently: `python3 scripts/validate_cpi_p10c_phase2_reuters_acquisition.py`
  and `pytest tests/test_cpi_p10c_phase2_reuters_acquisition.py`.

## Classification

**PASS -- 42-EVENT REUTERS EVIDENCE ACQUISITION COMPLETE**

Phase 2 stops here. No predictor-vs-market scoring is authorized or was
performed.
