# CPI P12 — November 10, 2026 Release Readiness Register

**Status at 2026-10-21:** P12 **NOT FROZEN**. Research only, scientific authority
none, production influence zero, no trading authorization.

**Scheduled BLS release:** October 2026 CPI, November 10, 2026 at 08:30
America/New_York (EST, UTC-05:00) = `2026-11-10T13:30:00Z`.

## Completed and canonical

- PR #195: separate Safari/browser-attested exact BLS CPI schedule source,
  explicit reference-month binding, exact bytes and SHA-256.
- PR #196: exact browser schedule source and receipt archived to GCS and read back
  with matching hashes. Source
  `cece37b1f964eed1cd51215764a55fb26e310da31b441ab9c65758989c9afc17`,
  receipt
  `01fc6542b73b0b6d293d91c024d2ab0be7c5ee8928f11f624eb32ac35e50619d`.
- PR #198: bounded diagnostic that source stages can be attempted independently.
- PR #199: research-only first-party CPI event/market inventory GET and raw
  hash/timestamp evidence.
- PR #200: pre-freeze independent stage orchestration, offline sealed schedule
  receipt verification, receipt-only operational preclose gate and source-failure
  fault injection. Exact CI passed (4516 tests, 18 skipped). The security lock
  updated `certifi` to the Trivy-patched `2026.10.19`.
- Frozen P11 remains terminal and unchanged. No P11 observational backfill.

## Blocking gates before prospective freeze

| Gate | What must be proved | Current state |
| --- | --- | --- |
| Kalshi real quotes | Independently timed, raw, fixed-origin market/candle/quote capture; verified complete sibling cohort, close times and as-of timing; deterministic admissible price selection | Raw inventory merged; independent single-market raw snapshot checkpoint in review. No frozen price selection yet |
| Reuters first-party source | Independently launched acquisition, frozen locator/search methodology, bounded and timed raw-source provenance, immutable PASS/NONPASS decision contract | NOT COMPLETE. Target Reuters forecast must not be inspected before freezing its source/selection rule |
| Frozen contract identity | Select actual P12 event/series, all sibling tickers, exact contract rules, reference month, strike comparators, close times and settlement authority | NOT COMPLETE. Do not reuse September P11 specifics |
| Schedule binding | Event-specific P12 protocol must bind exact presealed BLS receipt SHA, raw HTML SHA, reference month and release instant | Receipt durably preserved; event protocol not frozen |
| Timing and cutoff | Frozen pre-release time windows, deadline, clock skew allowances, contemporaneous request/response timestamps and delayed/failure handling | NOT COMPLETE |
| Scheduler durability | Independently scheduled OS/cloud processes, credentials/permissions where needed, network route and long-running availability proof; verified no BLS HTTP dependency for Kalshi/Reuters | NOT COMPLETE. In-process synthetic callbacks are not unattended execution |
| Failure injection | Live-runtime dry runs for HTTP 403, 429, timeout, network failure, redirect, malformed bytes, missing market, wrong time/rules, delayed Reuters; failure in one lane must not suppress others | Unit-level cases partially complete; unattended end-to-end dry runs not complete |
| Scientific gate | Independent preclose reconciliation of *validated evidence* (not merely receipt presence), exact protocol/checkpoint hashes, strictly no post-cutoff fills, fail-closed INCONCLUSIVE | NOT COMPLETE. Current operational READY is never scientific authority |
| Post-release truth | Reviewed authenticated/attested actual CPI acquisition after release, settlement source alignment, terminal manifest and immutable GCS archive verification | NOT COMPLETE |
| Review/freeze | Exact-head CI, independent adversarial review of raw-source provenance, authority bypass, cutoffs and failure mode; event protocol frozen before any target observation | NOT COMPLETE |

## Suggested sequence (planning targets, not frozen dates)

1. **October 22–28:** Complete source-specific Kalshi snapshot and Reuters
   acquisition designs, independent dry-run source adapters, and review concrete
   Kalshi contract-rule identity *without inspecting the target Reuters value*.
2. **October 29–November 3:** Repeated unattended end-to-end fault-injected
   dry runs on intended release-day infrastructure; verify create-once artifacts,
   hashes, clock offset, alerting and off-host durable uploads.
3. **November 4–6:** Only if all blocking gates actually pass, perform full
   independent review, freeze P12 event-specific protocol and receipt hashes,
   and prevent any further rule/selection/cutoff changes.
4. **November 7–9:** Validate independently scheduled tasks and the operating
   environment *without exposing or preselecting the target Reuters forecast*.
   If missing evidence/acquisition remains, mark P12 NOT READY, not a partial
   scientifically valid run.
5. **November 10:** Collect pre-release Kalshi and Reuters evidence separately
   within frozen windows; run receipt-only operational reconciliation and
   scientific admissibility assessment. Source failure => INCONCLUSIVE with no
   backfill. Only after release may reviewed CPI truth capture and scoring run.

Do not treat a planning date as approval to freeze. Any unresolved blocking gate
supersedes this timeline and requires a no-test/inconclusive decision.

## Current PR checkpoint

`services/forecasting/cpi_p12_raw_market_snapshots.py` reads single-market
public GET responses independently, preserves exact response bytes, source
observation timestamps and SHA-256; and checks an operator-provided time window
before and after each source request. The standalone CLI launches **only** the
Kalshi stage and records an isolated create-once stage receipt.

This is genuine raw-source acquisition infrastructure, but caller-provided
market identities and timing do not yet confer complete-cohort, economic, or
scientific authority. No canonical source selected for Reuters in this PR.
