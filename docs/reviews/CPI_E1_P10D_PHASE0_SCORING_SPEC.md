# CPI-E1-P10D Phase 0 -- predictive-edge scoring specification freeze

## Result

Froze the deterministic procedure for the first Reuters-vs-Kalshi predictive
comparison over the exact 4-event, positively-proven CPI-E1-P10C Reuters
cohort. **No Reuters-vs-Kalshi score, edge, Brier/log-loss comparison,
accuracy, hit-rate, or P&L/fee/after-cost figure was computed** in this task.
`reuters_vs_kalshi_score_computed: false`, `edge_pnl_fees_computed: false`,
`research_only: true`, `production_influence: "0"` throughout.

## Independent verification of canonical state (not trusted from the task text)

Ran `git ls-remote https://github.com/kensgithubaccount/kalsh3.git HEAD
refs/heads/main refs/heads/cpi-e1-p10c-reuters refs/pull/131/head` directly
against GitHub, then cloned into a fresh isolated worktree
(`/Users/ksyme/worktrees/cpi-e1-p10d-phase0-scoring-spec`, never the shared
checkout, per standing instruction) and confirmed:

- `origin/main` HEAD = `9169160c4583c4a6e353c5eb6c01eb59b816d31c`, tree
  `114569cd2ab1c86f892a529d1c1b6eb24457bff7` -- **matches the SHA/tree
  supplied in the task exactly.**
- `git log --oneline -15` on that main shows `9169160` is
  "Merge pull request #132 from kensgithubaccount/cpi-e1-p10c-reuters",
  and `git merge-base --is-ancestor origin/cpi-e1-p10c-reuters main`
  returns true. **P10C Phase 2 (+2-R1/2-R2/2-R3) is confirmed merged.**
- Ran `build_binding()` (the actual P10A binder, not documentation) against
  this checkout: 42 accepted independent events, 341 accepted sibling rows
  -- unchanged from the frozen Phase 1 identity.

This corrects a stale prior-session note (this agent's own memory) that
still recorded Phase 2 as unmerged on branch `cpi-e1-p10c-reuters`; that
memory has been updated to reflect the now-verified merged state.

## FIRST: existing authority recovered

Searched the repository for P10D/P10E precedent and for any predictor-vs-
market scoring artifact scoped to Reuters
(`grep -rliE "p10d|p10e"`, `grep -rliE "predictive.edge|brier|proper.scoring
|market.implied|calibrat"`): **no P10D/P10E artifact exists, and no
Reuters-vs-Kalshi scoring authority has ever been reviewed.**

However, `services/forecasting/cpi_p10a_binding.py` (reviewed, merged, see
`docs/reviews/CPI_E1_P10A_HISTORICAL_EVIDENCE_BINDING.md`) already defines
and uses, for Kalshi-vs-truth calibration:

- **Market price convention**: primary = YES ask ("crossing price"),
  explicitly diagnostic-labeled *"not a neutral probability estimate"*;
  two-sided midpoint is a non-executable diagnostic only, computed only when
  both bid and ask are valid and non-boundary (`yes_bid` not in
  `(None, "0.0000")` and `yes_ask` not in `(None, "1.0000")`).
- **Aggregation/independence treatment**: sibling rows are clustered by
  event; a per-event mean is taken first (`crossing_price_diagnostic`,
  `event_equal_market_baseline`), then events are weighted equally --
  never a flat mean over all sibling rows.
- **Missing-data handling**: a sibling with no valid non-boundary ask has
  `probability = None` and is excluded from all probabilistic scoring,
  never imputed.

**Classification of existing authority: an existing method needs a narrow
adaptation for per_sibling_market cutoff semantics, not a wholesale new
methodology.** The market-price convention and aggregation/independence
treatment above are reused verbatim, unmodified. The comparison metric
itself (Reuters point forecast vs. Kalshi market) has no precedent and is
frozen fresh below (Questions A-E), because P10A only ever scored Kalshi
against truth, never a third-party predictor against Kalshi.

### The narrow adaptation

P10B's Reuters receipts record a single event-level `decision_cutoff`
(3 of the 4 proven events: `KXCPI-25JUL`, `KXCPI-25DEC`, `KXCPI-26JAN`), or,
for the 1 new Phase-2 event (`CPI-23AUG`), a per-sibling
`sibling_temporal_eligibility` list. The frozen P10C Phase 1 manifest has
**no event-level cutoff at all** (`cutoff_semantics: per_sibling_market`).
Rather than assume the single P10B timestamp transfers to every sibling,
`_eligible_sibling_counts()` recomputes, per event, that every one of its
accepted siblings shares one uniform `sibling_cutoff` in the frozen
manifest, and that the bound Reuters timestamp is strictly before it. All 3
P10B-reused events verified uniform (single cutoff value across all 6
siblings each); this is a **verification**, not an assumption or a new
authority.

## Required input authorities -- exact bindings

**Reuters predictor evidence (4 positively-proven events only):**

| Event | Ref. month | Value | Timestamp field (per-receipt, NOT uniform) | Timestamp (UTC) | Receipt |
|---|---|---|---|---|---|
| CPI-23AUG | 2023-08 | 0.6 | `published_at` | 2023-09-13T10:07:35Z | `docs/reviews/artifacts/cpi-p10c-reuters-phase2/CPI-23AUG/receipt.json` |
| KXCPI-25JUL | 2025-07 | 0.2 | `published_at` | 2025-08-12T04:02:11Z | `docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-25JUL/receipt.json` |
| KXCPI-25DEC | 2025-12 | 0.3 | `governing_published_at` | 2026-01-13T05:03:53Z | `docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-25DEC/receipt.json` |
| KXCPI-26JAN | 2026-01 | 0.3 | `conservative_admissibility_time` | 2026-02-13T05:12:31Z | `docs/reviews/artifacts/cpi-p10b-reuters/KXCPI-26JAN/receipt.json` |

**Discovered and fail-closed against:** the proven-timestamp field name is
*not uniform across receipts* -- `KXCPI-25DEC` and `KXCPI-26JAN`'s
`published_at` fields are literally `null`; the real value lives under a
different key on each. A naive single-field-name reader would silently
treat 2 of the 4 events as having no timestamp. `cpi_p10d_scoring_spec.py`
binds the exact field name per event (`ReutersEventBinding.timestamp_field`)
and fails closed if the bound field is absent or its value disagrees with
the frozen expectation.

**Kalshi historical market evidence:** only the accepted P10A/P10C-manifest
sibling rows for these 4 events (27 total accepted siblings: 9 + 6 + 6 + 6),
read from `docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json`
and `evidence/cpi_p9a_historical_price/manifest.json` -- no invented price
snapshot.

**Temporal eligibility:** per_sibling_market, recomputed (not read from a
receipt's own boolean claim) every build. All 27 accepted siblings across
the 4 events are temporally eligible (Reuters proven-available instant
strictly precedes every one of that event's sibling cutoffs, which are
uniform per event in this cohort).

**Historical truth:** intentionally **not read** by this spec module at
all. Eligibility here is structural (temporal + non-boundary quote) only;
outcome/truth is deferred to the (separately reviewed, future) phase that
actually computes a score.

## Critical scoring questions -- resolved

**A. What does "Reuters beats Kalshi" mean mathematically?**
Point-forecast-vs-probability is not directly comparable without inventing
a calibration transform, which is not authorized. Resolved: **both sources
are reduced to the same directional call against each sibling's own
strict-GT threshold** -- Reuters: `reuters_value > threshold` (the identical
strict comparator the contract's own settlement predicate already uses, so
no new tie/rounding convention is introduced for Reuters); Kalshi:
`yes_ask > 0.5` vs `< 0.5` (`== 0.5` is an explicit tie, excluded from that
sibling's primary-metric denominator). This is symmetric and was fixed
*before* any comparative number was computed -- it was not chosen because it
favors either side.

**B. Market-implied CPI estimate reconstruction?**
**Not attempted.** No authorized interpolation/smoothing method exists
across a sibling ladder to reconstruct a full point estimate or
distribution, and the task instructions explicitly forbid inventing one.
Deferred to a future, separately reviewed phase if ever pursued.

**C. Which Kalshi price?**
YES ask (crossing price), reused verbatim from P10A, as primary; two-sided
midpoint (non-executable) as diagnostic only when both sides are valid and
non-boundary. Never mixed event-by-event -- it is a single frozen constant.

**D. Unit of independence.**
Event-level primary (N=4). The 27 accepted / 19 primary-metric-eligible
sibling rows across those 4 events are diagnostics only, explicitly labeled
non-independent. Aggregation: cluster by event, mean within event, then
equal-weight across the 4 events -- reused verbatim from P10A's own
`event_equal_market_baseline` pattern.

**E. Small-sample interpretation.**
Frozen up front: N=4 cannot establish durable predictive edge. This
checkpoint may conclude only "promising diagnostic," "no apparent
advantage," or "inconclusive" -- **never** promotion to live trading,
capital allocation, or any execution/production change, regardless of the
statistic's value.

## Anti-overfitting / fail-closed rules (frozen, enforced by tests)

1. The 4-event set is exact; a 5th event (including any future
   reclassified UNKNOWN, e.g. `CPI-24JAN`) requires a new reviewed spec
   revision, never silent inclusion.
2. Each event's Reuters timestamp field name and value are bound exactly;
   a field-name or value drift fails closed.
3. Temporal eligibility is recomputed from the frozen manifest every build,
   never read from a receipt's own claim.
4. The market price convention is a frozen constant.
5. Sibling rows are never treated as independent events; the primary
   statistic is always event-equal, never a flat sibling-pooled mean.
6. The primary metric definition is fixed before any comparative score
   exists. **This module contains no function that reads Reuters value,
   Kalshi ask, AND outcome/truth together** -- that composition (the actual
   scoring) is deliberately deferred to a separately reviewed future phase.

No results were inspected while choosing among alternative metrics: the
directional-call metric was fixed from P10A's own existing conventions
(strict-GT comparator, ask-crossing convention, event-equal aggregation)
before any per-sibling call was evaluated.

## Durable artifacts

- `services/forecasting/cpi_p10d_scoring_spec.py` -- `build_phase0_spec()`
  generator, frozen `ReutersEventBinding` roster, frozen metric/aggregation/
  tie/missing-data constants, and `classify_directional_call()` (a pure,
  outcome-free formula for a future phase, tested only against synthetic
  fixtures).
- `scripts/build_cpi_e1_p10d_phase0_scoring_spec.py` -- regeneration script.
- `docs/reviews/artifacts/cpi-p10d-phase0-scoring-spec/spec.json` -- frozen
  evidence, digest `df91b06c665a68f338b9a190cfcbba34e45261785d2aeffeb5e2f52be6a0d650`.
- `tests/test_cpi_p10d_phase0_scoring_spec.py` -- 18 tests: frozen-roster,
  determinism, frozen-evidence match, per-event field-binding, eligible-
  count structural checks, and fail-closed rejection of a synthesized 5th
  event, a wrong timestamp field, and a post-cutoff timestamp; plus 4 tests
  of `classify_directional_call()` against synthetic (non-cohort) fixtures
  only.

All 18 new tests pass; `ruff check` / `ruff format --check` clean on the new
files; full repo `pytest`/`mypy`/`bandit`/`detect-secrets` run separately
(see task response for status).

## Return summary

- Verified canonical main: SHA `9169160c4583c4a6e353c5eb6c01eb59b816d31c`,
  tree `114569cd2ab1c86f892a529d1c1b6eb24457bff7` (matches supplied value,
  independently confirmed).
- Existing scoring authority found: P10A's ask-crossing-price + event-equal
  aggregation convention, reused verbatim; no P10D/P10E precedent for a
  Reuters-vs-market metric existed, so that metric is newly frozen here.
- Exact 4 predictor-proven events: `CPI-23AUG`, `KXCPI-25JUL`,
  `KXCPI-25DEC`, `KXCPI-26JAN`.
- Eligible sibling count per event (primary-metric-eligible / accepted):
  `CPI-23AUG` 5/9, `KXCPI-25JUL` 4/6, `KXCPI-25DEC` 4/6, `KXCPI-26JAN` 6/6
  (total 19/27; the remainder are boundary-excluded, non-invented).
- Proposed primary metric: sibling-level strict-threshold directional
  correctness, event-equal aggregated (see formula above).
- Secondary diagnostics: P10A's own Brier/log-loss (Kalshi side only,
  scoped to this roster), midpoint diagnostic, raw sibling hit-rate
  (explicitly non-independent), per-event breakdown table.
- Market-price convention: YES ask (crossing price) primary, two-sided
  midpoint diagnostic -- reused verbatim from P10A.
- Truth authority: frozen P7/P8 initial-release truth already accepted by
  P10A -- not reacquired, not read in this phase.
- Aggregation/independence: event-level primary (N=4); sibling rows
  (19 eligible / 27 accepted) are diagnostics only, never treated as
  independent events.
- Small-sample interpretation rule: N=4 cannot establish durable edge;
  legitimate conclusions limited to promising-diagnostic / no-apparent-
  advantage / inconclusive; live-trading promotion explicitly forbidden.
- Durable scoring-spec path/digest:
  `docs/reviews/artifacts/cpi-p10d-phase0-scoring-spec/spec.json`,
  `df91b06c665a68f338b9a190cfcbba34e45261785d2aeffeb5e2f52be6a0d650`.
- Tests/validation: `tests/test_cpi_p10d_phase0_scoring_spec.py`, 18/18
  passing; `ruff` clean.

**Classification: PASS -- PREDICTIVE-EDGE SCORING SPEC FROZEN.**

No Reuters-vs-Kalshi result was computed. This branch/commit is not merged;
merge is a separate decision for the user, per standing instruction.
