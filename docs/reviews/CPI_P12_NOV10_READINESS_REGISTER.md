# CPI P12 — November 10, 2026 Release Readiness Register

**Status at 2026-10-21:** P12 **NOT FROZEN**. Research only, scientific authority
none, production influence zero, no trading authorization.

**Scheduled BLS release:** October 2026 CPI, November 10, 2026 at 08:30
America/New_York (EST, UTC-05:00) = `2026-11-10T13:30:00Z`.

## Post-merge control-room reconciliation — October 27, 2026

**Current canonical main:** `92960c25ad952da88c1d6852d3088cc4bf9be648` (PR #202 merged).
**Status remains P12 NOT FROZEN; research only; scientific/economic-test authority
false, production influence zero, promotion/trading authority NONE.** This
section supersedes older in-review labels below, which are retained as historical
checkpoint descriptions rather than evidence of a still-open PR.

| Source/checkpoint | Infrastructure now canonical | Still unproved (blocks prospective event freeze) |
| --- | --- | --- |
| BLS calendar (#195/#196) | Browser-attested exact schedule bytes and independently archived receipt | P12 event-specific protocol binding, cutoffs, and independent preclose validation |
| Independent stage handling (#198/#200) | Isolated attempts, create-once receipts, source-failure containment, receipt-only operational gate | Real unattended scheduled execution and scientific evidence reconciliation |
| Kalshi inventory (#199) and raw snapshots (#201) | Fixed-origin, raw-hashed source reads with request/response timestamps and caller-bounded windows | First-party proof of **complete sibling market cohort**, exact event/rules/settlement/close-time identity and deterministic admissible as-of price selection |
| Reuters raw HTTPS acquisition (#202) | Strict first-party HTTPS raw-body framing, timestamps/hashes, stage isolation and crash-conscious receipt persistence; independently reviewed PASS on exact head `4c20cbd233427725c5ab7364f642f37b0cdb16ff` | Prospectively frozen URL/search/source-selection rules, Reuters attribution, publication chronology, independent corroboration and objective missing-source outcomes |

External **schedule cross-check only:** the public BLS calendar at
https://www.bls.gov/schedule/news_release/cpi.htm and November list at
https://www.bls.gov/schedule/2026/11_sched_list.htm continue to list October
2026 CPI for November 10, 2026, 08:30 EST (`2026-11-10T13:30:00Z`).
This 2026-10-27 informational check is **not** an independent browser-attested
raw acquisition, not a replacement for the sealed #196 receipt, and creates no
new timing or scientific authority.

**Next narrowly scoped candidate checkpoint (planning only):** P12 Kalshi
event/contract-identity evidence specification. On an isolated research branch,
inventory first-party event, full paginated sibling market cohort, contract
rules/comparators, reference month, close times and settlement source as
raw timestamped observations. Compare observed market identities against an
explicit candidate protocol and document ambiguity without selecting a price,
authorizing a strategy, or freezing the protocol. Require independent adversarial
review and exact-head CI before any merge. If complete cohort identity or any
contract rule is uncertain, record **UNKNOWN/NOT READY**. In parallel, plan
Reuters locator/discovery and publication-attribution rules **without looking
up or acquiring the November target forecast value**.

Do **not** infer from this documentation update that the November 10 live
test, source admissibility, operational schedule, scientific evidence or trading
has been authorized. Keep P12 NOT FROZEN until every gate below is proved.

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
| Kalshi real quotes | Independently timed, raw, fixed-origin market/candle/quote capture; verified complete sibling cohort, close times and as-of timing; deterministic admissible price selection | Raw inventory and time-guarded single-market snapshot infrastructure merged in #199/#201; complete sibling cohort and frozen price selection still missing |
| Reuters first-party source | Independently launched acquisition, frozen locator/search methodology, bounded and timed raw-source provenance, immutable PASS/NONPASS decision contract | Raw HTTPS capture infrastructure merged in #202. **NOT COMPLETE**: URL discovery, first-party attribution, independently operated corroboration, publication timing and source-selection freeze remain blocking. Do not inspect target value yet |
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

## Reuters raw-source checkpoint — October 21, 2026

A new `services/forecasting/cpi_p12_reuters_raw_acquisition.py` and
`scripts/capture_cpi_p12_reuters_raw.py` provide a separate, standalone
`REUTERS_ACQUISITION` raw-source stage. This checkpoint does not import or
execute P11 preflight or issue live BLS schedule queries. Reuters failure
cannot change the Kalshi or BLS stage receipts.

The proposed transport makes a bounded, TLS-verified first-party HTTPS GET,
with **no redirects**, to an explicit caller-supplied list of **exact
allowlisted hostnames**. It validates a strict pre-release window before and
after every request, records original returned response bytes (base64), SHA256,
HTTP status, local UTC request/response times and unverified source headers,
then exclusively writes a hash-revalidated stage artifact.

The list of URLs is **not a prospectively frozen search protocol**. The
allowlisted host/operator-group names do not prove source independence. HTTP
200 does not prove Reuters attribution, pre-close publication, or any CPI
forecast. The adapter deliberately performs no numeric forecast extraction,
web search, market comparison, or backward-looking observation. Test fixtures
are synthetic and non-target-specific.

The operational stage may report PASS for complete raw HTTP observations,
but these are explicitly stamped **scientific evidence unauthorized**, **P12
unfrozen**, and **production influence zero**. The separate mandatory
next checkpoint is to freeze and verify Reuters source discovery/selection,
publication timing, attribution and cross-host text matching, including an
objective UNKNOWN/FAILURE outcome when provenance is insufficient.

Do not run the actual November 2026 Reuters-source observation until its
locator/search rules and cutoffs are independently reviewed and frozen.
