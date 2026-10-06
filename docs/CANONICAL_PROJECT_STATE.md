# Canonical Project State

Last updated: 2026-10-06

This file is the durable handoff record for the Kalshi project. New oversight or implementation sessions should read this file first, then verify GitHub and current runtime state before acting.

Repository and current runtime evidence override stale chat summaries. Experiment ledgers override recollection. Chats are working context, not the canonical record.

## 1. Verified repository checkpoint

Repository: `kensgithubaccount/kalsh3`

Verified canonical main immediately before this state-update PR lands:

- SHA: `3eeb4850db0704dd349e2cf22b9d450694850a30`
- tree: `b04b556f50eb1786890cf5175354d3d8acc591c5`
- commit: merge of PR #182, canonical Perps SQLite connection-lifecycle repair
- production write authority: OFF

This file itself lands through a later state-update PR after that verified checkpoint, so a fresh
session must still verify live `main` before acting rather than treating the SHA
above as a permanent self-reference.

Recent canonical merges relevant to the active work:

- PR #182: deterministic close of every `PerpsEvidenceStore` SQLite connection plus a regression proving the connection is closed before Phase-0 evidence sealing; canonical CI passed with 4404 tests passed and 18 skipped, strict mypy clean, Ruff clean, and all workflow jobs green.
- PR #166: WN-A2-E1 settlement bucket semantics repair. RANGE is inclusive, LT/GT are strict, full source precision is preserved, and unsupported rounding is not invented.
- PR #170: urllib3 2.8.0 security repair. All canonical CI jobs passed before merge.
- PR #171: exact frozen WeatherNext multicity provenance import. The imported experiment/review bytes remain unchanged; root regression tests verify the frozen manifests.
- PR #168: outcome-blind WeatherNext multicity evaluator boundary. It freezes continuous Fahrenheit point-error scoring and explicitly blocks retrospective probability/bucket/P&L reconstruction.
- PR #164: current Perps API/schema authority constraints.
- PR #165: Perps edge research and radar admission plan, with market-bound reference-price lead/lag as the first read-only checkpoint.
- PR #173: first-party Perps spec authority refresh plus offline/read-only reference-lag Phase 0.
- PR #175 / #176: frozen prospective Perps protocol and bounded collector used by the completed October 5-6 Phase-0 campaign.

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

## 7. Perps read-only challenger — Phase 0 closed without promotion

Perps remains DISARMED/read-only with `production_influence = 0`.

The first frozen prospective reference-lag experiment is complete. Its terminal scientific
disposition is:

`INCONCLUSIVE_MIXED — PROMOTION AUTHORITY NONE`

The experiment must not be retrospectively tuned, reclassified, backfilled, retried, or promoted.

### Frozen prospective identity

The completed experiment used:

- ticker: `KXBTCPERP`;
- protocol SHA-256:
  `482677be45be623a443647e347f854db9693ec82371a460adfed2a6ec20fed46`;
- primary horizon: 2 seconds;
- confirmatory horizon: 5 seconds;
- primary independence unit: SESSION;
- no retry;
- no backfill;
- no fees/P&L/orders;
- production influence: zero.

### Final collection ledger

The frozen reconciliation is:

- `P0-S01`: MISSING / `NO_SESSION_DIRECTORY` — original macOS `atrun` scheduler failure;
- `P0-S02`: MISSING / `NO_SESSION_DIRECTORY` — same scheduler failure;
- `P0-S03`: FAILED / `FAILURE_RECEIPT` — collected a full prospective evidence window but failed final seal; canonical status remains FAILED forever;
- `P0-S04`: CAPTURED / `SEALED_RESULT`;
- `P0-S05`: CAPTURED / `SEALED_RESULT`;
- `P0-S06`: CAPTURED / `SEALED_RESULT`;
- `P0-S07`: CAPTURED / `SEALED_RESULT`;
- `P0-S08`: CAPTURED / `SEALED_RESULT`.

Aggregate reconciliation:

- captured sessions: 5;
- failed sessions: 1;
- missing sessions: 2.

S01/S02 were never backfilled. S03 was never retried or retrospectively promoted.

The S03 sealing failure was diagnosed as a SQLite connection-lifecycle defect rather than
intrinsic evidence-DB corruption: the original DB passed integrity diagnostics and a consistent
disposable copy sealed successfully, while a scratch reproduction showed an outstanding WAL
connection causes the final `PRAGMA journal_mode=DELETE` transition to fail with
`database is locked`.

A documented forward-only connection-lifecycle repair was used for S04-S08. PR #182 later
canonicalized that mechanical repair and added regression coverage. The repair does not alter
the scientific status of S03 or any collected evidence.

### Frozen Phase-0 mechanism result

All five captured sessions were eligible under the prespecified gate.

Observed frozen-gate aggregates:

- eligible sessions: 5;
- primary measured 2-second impulses: 167;
- cluster-equal primary signed midpoint mean:
  `-0.000160951206836437278667876082` bps;
- cluster-equal confirmatory 5-second signed midpoint mean:
  `0.06784604770117591310229551974` bps;
- positive-primary-session fraction: `0.4`;
- frozen promising threshold for positive-session fraction: `0.60`.

The primary cluster mean was not positive and only two of five eligible sessions had positive
primary means. Therefore the exact frozen decision is `INCONCLUSIVE_MIXED`, not
`INCONCLUSIVE_INSUFFICIENT` and not `PROMISING_DIAGNOSTIC_CONTINUE_RESEARCH`.

The operator-side read-only analysis artifact was SHA-256
`277fda6f63bbc3186ffbcde0c85f0e7c03042bc9aa370ff63b8786b53d606da3`.
The terminal owner decision artifact was SHA-256
`4b781bf58db440e3b66413ac6a302c6b27fd24e5e822127c1f5f03d2a51ed670`
and explicitly records:

- promotion authority: NONE;
- Phase-1 economic test authorized: false;
- next action: `DO_NOT_PROMOTE_THIS_FROZEN_HYPOTHESIS`.

These operator-local artifact hashes are runtime/evidence handoff facts, not repository-hosted
scientific artifacts unless separately imported and reviewed later.

### Perps disposition

Do not proceed to fee/fill/P&L testing for this exact frozen market-bound reference-price
lead/lag formulation.

The positive average at 5 seconds does not override the failed prespecified primary gate.
Any future 5-10 second, external-index, different-market, or otherwise modified Perps hypothesis
must be treated as a new hypothesis with a newly frozen protocol before untouched evaluation.
The completed sample may inform hypothesis generation but must not become its own validation set.

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
2. Treat the completed market-bound Perps reference-lag Phase 0 as closed:
   `INCONCLUSIVE_MIXED`, promotion authority NONE, no Phase-1 economics for
   that frozen formulation.
3. Do not tune Perps horizons, impulse definitions, or filters against the completed
   sample and call the result validation. Any materially changed Perps idea requires a
   new frozen untouched experiment.
4. Recover and rank the next edge-discovery candidates from canonical repository evidence
   before starting another prospective campaign. Prefer mechanisms with strong source authority,
   executable scalability, and low semantic ambiguity.
5. Keep the prior sibling-threshold structural lane fail-closed unless its unresolved semantic
   timezone and generic-comparator authority gaps are positively repaired; do not infer semantic
   timezone from UTC offsets or invent comparator meaning.
6. Do not start exceptional-edge capital sizing merely because infrastructure exists. Issue #79
   requires calibrated after-cost profitable opportunities first.
7. After October 14, reconcile the complete WeatherNext block before outcome scoring.
8. Run only the already-frozen continuous WeatherNext evaluator.
9. Continue, redesign, or kill research lanes based on prespecified evidence; no automatic
   production promotion.

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
