# Canonical Project State

Last updated: 2026-10-04

This file is the durable handoff record for the Kalshi project. New oversight or implementation sessions should read this file first, then verify GitHub and current runtime state before acting.

Repository and current runtime evidence override stale chat summaries. Experiment ledgers override recollection. Chats are working context, not the canonical record.

## 1. Verified repository checkpoint

Repository: `kensgithubaccount/kalsh3`

Verified canonical main immediately before this state-update PR lands:

- SHA: `5292e9b062a4103ec4af59a7674e36fbffe74fb1`
- tree: `66ba32da011b121764f9184cb4af103ccd58bec3`
- commit: merge of PR #173, refreshed Perps spec authority and reference-lag Phase 0
- production write authority: OFF

This file itself lands through PR #169 after that verified checkpoint, so a fresh
session must still verify live `main` before acting rather than treating the SHA
above as a permanent self-reference.

Recent canonical merges relevant to the active work:

- PR #166: WN-A2-E1 settlement bucket semantics repair. RANGE is inclusive, LT/GT are strict, full source precision is preserved, and unsupported rounding is not invented.
- PR #170: urllib3 2.8.0 security repair. All canonical CI jobs passed before merge.
- PR #171: exact frozen WeatherNext multicity provenance import. The imported experiment/review bytes remain unchanged; root regression tests verify the frozen manifests.
- PR #168: outcome-blind WeatherNext multicity evaluator boundary. It freezes continuous Fahrenheit point-error scoring and explicitly blocks retrospective probability/bucket/P&L reconstruction.
- PR #164: current Perps API/schema authority constraints.
- PR #165: Perps edge research and radar admission plan, with market-bound reference-price lead/lag as the first read-only checkpoint.
- PR #173: first-party Perps spec authority refresh plus offline/read-only reference-lag Phase 0. Temporary probe PR #172 fetched the official Kalshi Perps OpenAPI/AsyncAPI bytes directly from `docs.kalshi.com` and closed without merge. PR #173 then passed all canonical CI gates before merge.

No current repository state establishes a profitable autonomous strategy or authorizes real-money trading.

## 2. North Star

Build an autonomous edge-compounding research and trading platform whose objective is long-run, after-cost, risk-controlled, capacity-aware income.

The intended progression remains:

Kalshi directional alpha -> whole-exchange strategy discovery -> cross-contract relative value -> legally accessible cross-venue opportunities -> liquidity provision / market making where economically justified -> multi-strategy portfolio allocation -> broader quantitative markets only where the existing architecture provides a demonstrable advantage.

The immediate objective remains narrower:

> Find the first repeatable, credible, after-cost Kalshi edge as quickly as possible without lowering scientific, provenance, or safety standards.

Passing software tests is not profitability evidence.

## 3. Evidence hierarchy

Keep these categories separate:

1. Reported claim.
2. Directly verified repository state.
3. Reproduced software behavior.
4. Independent review.
5. Runtime acceptance.
6. Prospective scientific/economic evidence.
7. Production authorization.

A stronger category must not be inferred from a weaker one.

Historical/manual profitable trades are useful motivation, not proof of a repeatable strategy.

## 4. Active prospective lane — WeatherNext multicity development block

The experiment `wn_multicity_prospective_v1` is running for the exact date block 2026-10-03 through 2026-10-14.

Its governing frozen source and final collector review are now canonical via PR #171.

Frozen identities:

- protocol SHA-256: `56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346`
- pre-first-event amendment SHA-256: `b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000`
- independently reviewed collector image:
  `sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08`

The operator-local provenance handoff was independently verified before canonical import:

- archive SHA-256: `de64c2d61dc8f05d7bb82579672036f54ce29b963e129d1fe3c669d312c995db`
- all 20 D1 manifest entries matched;
- all 32 final independently reviewed source hashes matched;
- duplicated frozen D1 bytes matched;
- secret/private-key checks were empty.

Frozen scientific constraints include:

- cities: Boston, Miami, Denver, Los Angeles, Seattle;
- observational unit: CITY_DAY;
- city-days are explicitly not independent;
- the same target date is the primary dependence cluster;
- exact WeatherNext 00Z initialization;
- exact city-specific 24-hour fixed-local-standard-time windows with no DST shift;
- common scientific decision time 09:00:00Z with start window `[09:00:00Z,09:05:00Z)`;
- no retry;
- no backfill;
- no model fitting;
- no trade.

### October 3 first prospective date

Verified runtime/operator evidence established:

- one visible scientific Cloud Run execution;
- exact reviewed image digest;
- `maxRetries = 0`;
- `taskCount = 5`;
- `parallelism = 5`;
- all 5/5 tasks completed;
- scientific acquisition began only after the 09:00Z gate;
- all five expected city/date prefixes contained sealed packet structure and an atomic claim;
- no `failure.json` was observed.

Disposition:

`2026-10-03 — VALID PROSPECTIVE COLLECTION`

This is a collection-validity statement only. It is not an accuracy, profitability, market-relative-edge, or promotion result.

## 5. Frozen pre-outcome evaluation boundary

PR #168 is canonical.

The October block froze a continuous, unrounded Fahrenheit p50 proxy. It did **not** freeze a WeatherNext probability model or a transformation from that continuous proxy into Kalshi sibling probabilities.

Therefore this block may later score deterministic continuous point-forecast error against authoritative finalized outcomes, including:

- signed error;
- absolute error;
- squared error;
- primary aggregation over complete five-city target-date clusters.

It may **not** retrospectively invent:

- rounding into the Kalshi integer ladder;
- sibling probability assignments;
- Brier/log-loss market-relative skill;
- hypothetical YES/NO fair values;
- after-cost P&L derived from a post-outcome transformation.

Any later market-relative WeatherNext edge experiment must freeze its probability/economic transformation before its own prospective outcomes.

## 6. Current WeatherNext operating rule

Leave the October 3-14 live collector scientifically untouched while it runs.

Do not:

- inspect emerging outcomes to tune the model or evaluation rule;
- change cities, dates, decision time, target windows, model rule, or deployment image;
- retry/backfill a missed observation;
- count five same-date cities as five independent experiments;
- infer edge from one correct forecast or one profitable anecdote;
- grant trading authority.

Read-only health/reconciliation checks are allowed. Failures or missing captures must be recorded as observed, not repaired.

After the block closes:

1. reconcile every expected invocation/city-day before outcome scoring;
2. preserve failures, missing observations, abstentions, and incomplete diagnostics;
3. acquire authoritative outcomes only under the frozen scorer boundary;
4. run deterministic continuous point-forecast evaluation;
5. decide whether the mechanism warrants a separately prespecified prospective edge experiment.

## 7. Perps read-only challenger

Perps remains DISARMED/read-only with `production_influence = 0`.

PR #164 establishes the schema/authority constraints and PR #165 establishes the
research ladder. PR #173 is now canonical and closes the first schema-refresh
and offline mechanism-harness checkpoint.

### Current first-party spec authority

Temporary probe PR #172 fetched the official specification bytes directly from
Kalshi on 2026-10-04, then closed without merge:

- Perps OpenAPI URL: `https://docs.kalshi.com/perps_openapi.yaml`
- OpenAPI SHA-256:
  `d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0`
- OpenAPI bytes: 146790
- Perps AsyncAPI URL: `https://docs.kalshi.com/perps_asyncapi.yaml`
- AsyncAPI SHA-256:
  `e5cc0f026b8e306e917860b870e23c9152d0d782c33137d42698f051a9dbe824`
- AsyncAPI bytes: 38219

The reviewed read-only parser now requires/preserves current structural and
timing authority including:

- `market_version`;
- `underlying_multiplier`;
- optional `asset_class`;
- optional `product_metadata`;
- market-bound timestamped `reference_price`;
- optional `sending_ts_ms`;
- current documented orderbook update reasons.

New book/ticker evidence binds the exact AsyncAPI SHA-256 into the append-only
evidence identity and persistence layer. Market metadata separately retains the
exact OpenAPI source provenance.

### Frozen offline Phase 0 mechanism diagnostic

The first falsifiable checkpoint remains:

> market-bound reference-price movement -> Kalshi executable-book repricing

The canonical Phase 0 harness is mechanism-only and uses the market's own
timestamped Margin ticker `reference_price`. It freezes:

- every observed nonzero market-bound reference change as an impulse;
- source movement labels `UP` / `DOWN`, never LONG / SHORT;
- fixed 1s, 2s, 5s, and 10s horizons;
- exact previous/current market-state evidence IDs;
- connection epoch, ticker SID, structural contract identity, and causal source
  timestamps;
- exact baseline, horizon, and post-horizon continuity-witness book evidence;
- contiguous same-stream orderbook sequence proof;
- explicit abstentions for missing/stale baselines, stream boundaries, missing
  continuity witnesses, sequence gaps, and non-comparable one-sided quotes.

Because the Margin ticker stream is coalesced to at most one update per market
per second, Phase 0 makes no sub-second benchmark-observability claim.

Phase 0 contains no probability model, fees, P&L, order construction, live
collector, alert authority, capital allocation, or production influence. Passing
this software/mechanism checkpoint does not establish predictive advantage or
after-cost edge.

A later CF Benchmarks 5 Hz or Pyth-based checkpoint still requires separately
reviewed exact market-to-index mapping and timestamp authority before any such
data can enter an evaluation.

No Perps alert, order, capital allocation, or execution authority exists.

## 8. Security/dependency state

PR #170 upgraded transitive `urllib3` from 2.7.0 to 2.8.0 after the package resolver produced the exact lock update.

All canonical GitHub CI gates passed before merge, including security/supply-chain.

The operator's local Python 3.13 `make verify` had a separate mypy/psycopg import-environment issue; canonical Python 3.12 CI was the acceptance environment for PR #170.

## 9. Other research lanes

### GDP

A manually acted-on GDP opportunity was owner-reported correct and profitable. That remains an anecdotal economic result, not proof of repeatable automated GDP alpha. Previously accepted GDP engineering/authority repairs remain closed unless a concrete regression is reproduced.

### Historical/manual radar and specialist probes

Older RADAR-P0, YouTube, USGS, gas, RT, and CPI work remains historical context. Do not infer that an old conversation's unfinished status is current. Recover durable artifacts where useful; otherwise mark the lane incomplete/parked rather than reconstructing missing prospective evidence from memory.

## 10. Profitability ladder

Do not collapse these stages:

1. `MECHANISM EXISTS`
2. `INFORMATION / PREDICTIVE ADVANTAGE EXISTS`
3. `AFTER-COST EDGE EXISTS`
4. `REPEATABILITY EXISTS`
5. `CAPACITY EXISTS`
6. `PORTFOLIO VALUE EXISTS`
7. `PRODUCTION READY`

Software completion can support these gates but cannot substitute for them.

## 11. Immediate priority order

1. Leave the October 3-14 WeatherNext collector scientifically untouched except
   for read-only health/reconciliation.
2. Keep the merged Perps Phase 0 mechanism definition frozen; do not tune
   impulses, horizons, continuity rules, or abstention semantics on observed
   results.
3. Before any untouched Perps evaluation slice, freeze a bounded read-only
   collection/evaluation protocol: exact markets, collection window, evidence
   completeness rules, de-duplication/cooldown policy if any, dependence units,
   missing-data treatment, and pass/kill criteria.
4. Only then run a narrow read-only Perps collection using the canonical evidence
   model. Preserve every attempted observation, missing interval, reconnect, and
   abstention; no orders and no production influence.
5. Evaluate mechanism evidence first. Do not add probabilities, fees, P&L, or
   trade simulation unless a later checkpoint explicitly freezes those economic
   rules before its untouched data.
6. After October 14, reconcile the complete WeatherNext block before outcome
   scoring.
7. Run only the already-frozen continuous WeatherNext evaluator.
8. Continue, redesign, or kill research lanes based on prespecified evidence; no
   automatic production promotion.

## 12. Authority

Production write authority remains OFF.

No real-money order, autonomous order, capital allocation, or production-source promotion is authorized by the current WeatherNext collection, the Perps research work, this state update, or any owner-reported profitable anecdote.

## 13. Update protocol

Update this file whenever any of the following happens:

- canonical main changes materially;
- a milestone/PR is merged;
- a required independent review changes acceptance status;
- a strategy is promoted, paused, killed, or reopened;
- a live experiment starts or finishes;
- a prospective profitability result is established;
- an owner-reported economic result is added;
- the active priority order changes;
- a production/scientific authority gate changes.

Every update should distinguish:

- verified GitHub/repository facts;
- owner/runtime evidence;
- independent-review results;
- scientific/economic conclusions.

A fresh session should not reopen accepted historical findings merely because an old chat said they were pending. Reopen only for a concrete regression or new evidence against an existing requirement.
