# Canonical Project State

Last updated: 2026-10-03

This file is the durable handoff record for the Kalshi project. New oversight or implementation sessions should read this file first, then verify GitHub and current runtime state before acting.

Repository and current runtime evidence override stale chat summaries. Experiment ledgers override recollection. Chats are working context, not the canonical record.

## 1. Verified repository checkpoint

Repository: `kensgithubaccount/kalsh3`

Canonical main:

- SHA: `c94783e0dfbadba2e15d63c3b7b01660cdb17513`
- tree: `1db184627146e24f42a4e89edb97cf256b8f98b7`
- commit: merge of PR #166, WN-A2-E1 canonical settlement bucket boundary repair
- PR #166 reviewed/tested head: `c6d486cbd49da82ca2431d311581e7bc84fba7b1`

Recent weather milestones relevant to current research:

- PR #159: WN-A1 Chicago daily-high research alert merged; research only.
- PR #160: WN-A2 authoritative settlement reconciliation merged after independent review.
- PR #163: WeatherNext bounded-decoding repair merged after the old global-chunk path was shown infeasible under the constrained runtime.
- PR #166: WN-A2-E1 settlement bucket semantics repair merged. RANGE is inclusive, LT/GT are strict, full source precision is preserved, and unsupported rounding is not invented.

Current open research/documentation work at this checkpoint:

- PR #164: Perps API/schema integration constraints; rebased onto current main on 2026-10-03. Documentation only; no execution authority.
- PR #165: Perps edge research and radar admission plan; rebased onto current main on 2026-10-03. Perps remains DISARMED/read-only with zero production influence.
- PR #168: outcome-blind multicity pre-outcome evaluation boundary; under CI/review. It validates the sealed pre-outcome packet schema and freezes continuous point-error scoring only; it does not acquire outcomes, invent probabilities, score P&L, or change the live collector.\n- PR #170: urllib3 2.8.0 security lock repair. All four canonical CI jobs pass; merge remains pending.\n- PR #171: exact frozen multicity provenance import from operator-local source; one commit, 61 added files, no live-runtime change. Pre-commit verification passed all 20 D1 manifest entries and all 32 final independent-review source hashes.

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

## 4. Current active prospective lane — WeatherNext multicity development block

An external prospective experiment named `wn_multicity_prospective_v1` is currently running for the exact date block 2026-10-03 through 2026-10-14.

Important repository-status distinction:

- the governing frozen experiment source and final collector review artifacts currently exist in an operator-local checkout and deployed image, not on canonical GitHub main;
- canonical import of those exact frozen bytes is pending;
- this file records their verified identities and runtime state but does not recreate or modify them.

Verified frozen identities supplied by the operator and matched against the local frozen bytes:

- protocol SHA-256: `56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346`
- pre-first-event amendment SHA-256: `b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000`
- final independently reviewed deployed collector image:
  `sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08`

Frozen scientific constraints include:

- cities: Boston, Miami, Denver, Los Angeles, Seattle;
- observational unit: CITY_DAY;
- city-days are explicitly not independent;
- the same target date is a dependence cluster for primary interpretation;
- exact WeatherNext 00Z initialization;
- exact city-specific 24-hour fixed-local-standard-time windows with no DST shift;
- common scientific decision time 09:00:00Z with start window `[09:00:00Z,09:05:00Z)`;
- no retry;
- no backfill;
- no model fitting;
- no trade.

### October 3 first prospective date

Runtime/operator evidence on 2026-10-03 established:

- the scheduler triggered one visible scientific Cloud Run execution at approximately 08:50Z;
- the live job used the exact independently reviewed image digest above;
- `maxRetries = 0`;
- `taskCount = 5`;
- `parallelism = 5`;
- all 5/5 tasks completed;
- scientific Earth Engine activity began only after the 09:00Z gate;
- each of the five city/date prefixes contained the expected sealed packet structure and an atomic claim;
- no `failure.json` was observed in the October 3 inventory.

Current operational disposition:

`2026-10-03 — VALID PROSPECTIVE COLLECTION`

This is a collection-validity statement only. It is not an accuracy, profitability, market-relative-edge, or promotion result.

## 5. Current scientific/economic state

The multicity block is still accumulating untouched prospective evidence.

Do not:

- inspect emerging outcomes to tune the model or evaluation rule;
- change cities, dates, decision time, target windows, model rule, or deployment image during the frozen block;
- retry/backfill a missed observation;
- count five same-date cities as five independent experiments;
- infer edge from one correct forecast or one profitable anecdote;
- grant trading authority.

Primary independence for the multicity block is the target-date cluster, so the complete block contains at most 12 primary date clusters, not 60 independent observations.

The post-block evaluation must be frozen before outcome scoring and must preserve failures, abstentions, missing observations, incomplete market depth, and exact point-in-time prices rather than filtering them after the fact.

## 6. Parallel work authorized while the block runs

Safe work that does not touch the live prospective logic may proceed:

- canonicalize the already-frozen multicity source/review bytes without editing them;
- freeze the outcome-blind post-block evaluation schema and dependence treatment;
- build read-only operational inventory/health auditing;
- reconcile stale research/documentation branches against current main;
- repair repository security/dependency findings;
- continue read-only/offline challenger research such as Perps mechanism specification and evidence plumbing;
- improve whole-exchange research methodology, testing, and deterministic replay without granting execution authority.

Any work that would change the live multicity scientific record remains blocked until the frozen block ends.

## 7. Current dependency/security finding

CI runs on 2026-10-03 began reporting two HIGH Trivy findings against transitive `urllib3==2.7.0` in `uv.lock`, with a fixed version of 2.8.0.

Treat this as repository dependency hygiene, not as a failure of the Perps documentation changes that happened to trigger fresh CI. Repair it on a separate security branch, regenerate `uv.lock` with the package resolver, and rerun the full security/verification gates. Do not hand-edit resolver hashes.

## 8. Other research lanes

### Perps — read-only challenger

Perps remains DISARMED/read-only with `production_influence = 0`.

The intended first falsifiable edge checkpoint is benchmark -> Kalshi executable-book lead/lag. Existing repository code already provides immutable Perps market metadata, sequence-aware book evidence, market-state evidence, exact timestamp provenance, and append-only research stores.

Do not invent benchmark mappings, impulse thresholds, or latency horizons merely to start collecting. Freeze those only after their authority and interpretation are reviewed.

### GDP

A manually acted-on GDP opportunity was owner-reported correct and profitable. That remains an anecdotal economic result, not proof of repeatable automated GDP alpha. Previously accepted GDP engineering/authority repairs remain closed unless a concrete regression is reproduced.

### Historical/manual radar and specialist probes

Older RADAR-P0, YouTube, USGS, gas, RT, and CPI work remains useful historical context. Do not infer that an old conversation's unfinished status is current. Recover durable artifacts where useful; otherwise mark the lane incomplete/parked rather than reconstructing missing prospective evidence from memory.

## 9. Profitability ladder

Do not collapse these stages:

1. `MECHANISM EXISTS`
2. `INFORMATION / PREDICTIVE ADVANTAGE EXISTS`
3. `AFTER-COST EDGE EXISTS`
4. `REPEATABILITY EXISTS`
5. `CAPACITY EXISTS`
6. `PORTFOLIO VALUE EXISTS`
7. `PRODUCTION READY`

Software completion can support these gates but cannot substitute for them.

## 10. Immediate priority order

1. Leave the October 3-14 multicity collector scientifically untouched while it runs.
2. Import the exact frozen multicity protocol/amendments/final collector review into canonical repository history without changing their bytes.
3. Freeze and review the outcome-blind evaluator before the first post-block scoring.
4. Repair the current `urllib3` security finding and restore green CI.
5. Review/merge or close PRs #164/#165 on their merits; they do not block WeatherNext.
6. After the block closes, reconcile every expected date/city attempt before acquiring/scoring outcomes.
7. Only then run the frozen settlement/forecast/market/economic evaluation.
8. Continue or kill the mechanism based on the predeclared evidence standard; no automatic production promotion.

## 11. Authority

Production write authority remains OFF.

No real-money order, autonomous order, capital allocation, or production-source promotion is authorized by the current WeatherNext collection, the Perps research work, this state update, or any owner-reported profitable anecdote.

## 12. Update protocol

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
