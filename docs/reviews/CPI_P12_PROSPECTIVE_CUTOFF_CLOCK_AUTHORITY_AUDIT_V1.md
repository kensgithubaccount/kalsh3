# CPI P12 — Prospective cutoff, clock and publication authority audit (v1)

**Checkpoint:** Issue #221, master November readiness ledger #214 gate #07.
**Reviewed source baseline:** main `5d42caa6975b2435a135166a50549b8131aed66d`, tree `0a45866cf0d0a5223b9ac744ae0d2cb446af0f8b`, 2026-10-28.
**STATUS:** DOCUMENTARY RESEARCH AUDIT ONLY. **P12 NOT FROZEN /
UNKNOWN_NOT_READY**; scientific/economic authority, production influence and
trading authorization **NONE**.

This memo reads the already merged source/CLI implementation and references
the previously reviewed BLS schedule receipt. **No live Kalshi event/order
book, Reuters November target/forecast, BLS source, GCS object, or clock
telemetry was acquired** for this review. It proposes no usable trading
window and supplies no scientific validator or new code authority.

## 1. Time concepts that cannot be substituted

1. **Sealed issuer release time:** the previously browser-attested BLS CPI
   calendar receipt identifies October 2026 CPI release as
   `2026-11-10T13:30:00Z` (08:30 EST). This is an externally sourced
   *event release* instant, not an independently established Kalshi close,
   Reuters first-publication instant, or source retrieval deadline.
2. **Caller input window:** `window_start` and `deadline` passed to
   standalone raw collectors. Both can be checked *mechanically* by code
   but are not prospective scientific authority until immutably bound
   under a separately reviewed event protocol.
3. **Local wall-clock observation:** `datetime.now(UTC)` or an injected
   `clock` callback. These provide timestamp evidence and basic ordering,
   not automatically an authenticated synchronized clock, bounded NTP
   error, monotonic duration or external publisher timestamp.
4. **First-party publication and effective rule time:** issuer/exchange
   first public availability, revision and effective-from timestamps.
   Raw headers, API metadata fields and web page publication strings
   are not independently certified historical publication chronology.
5. **Source-specific market close and deadline:** potentially earlier
   than BLS release. Eligibility requires independently established
   contract rules for **each** sibling and a conservative bound on
   request start, response end, source availability and clock error.

These separate authorities must not be collapsed into a single
`release_at_utc` or a receipt-level PASS.

## 2. Source-code timing matrix: currently implemented vs not proved

| Ref | Canonical code/lines inspected | Positively implemented | Unproved authority or limitation |
| --- | --- | --- | --- |
| C01 | `services/forecasting/cpi_p12_raw_market_snapshots.py:38-123` | Explicit aware UTC start/deadline and `start < deadline`; capture start, pre-GET, envelope observation, post-GET and completion must be inside caller window; source/event/market structural binding; raw transport evidence | Caller-selected tickers/limits/window are not a certified full as-of sibling cohort, close rule or authorized clock-error budget |
| C02 | `services/forecasting/cpi_p12_reuters_raw_acquisition.py:280-390` | Explicit source URL list, start/deadline, per-request pre/post UTC timing, response/completion ordering, no silent redirects, strict raw HTTPS/bounded-byte/status path | URL choice and time window are caller inputs; issuer first-publication and revision chronology, lawful archival rights and independent syndication are unproved; no clock offset uncertainty is proven |
| C03 | `services/forecasting/cpi_p12_contract_identity_evidence.py:108-223` | Every identity/series/event/market GET binds observed time within its local before/after interval; bounded pagination and research-only authority labels | No event-specific window_start/deadline parameters, no cross-page atomic snapshot; `cursor=""` stop is not documented endpoint-specific historical completeness or authoritative terminal semantics |
| C04 | `services/forecasting/cpi_p12_decoupled_collection.py:332-401` | Offline SHA-256, exact month, official locator, source bytes, release instant, type, research-only flags for a previously sealed BLS receipt | Operator-supplied expectation is not the final event protocol; no current GCS version/readback or verified Kalshi close/Reuters first-publication cutoffs |
| C05 | `services/forecasting/cpi_p12_decoupled_collection.py:589-723` | Independent stage-local receipts and operational gate; missing/failed receipts preserve other lane artifacts; gate revalidates hashes and cannot claim scientific PASS | In-process callback stage separation is not independently scheduled OS/cloud job proof; receipt presence has no cutoff/source scientific authority |
| C06 | `scripts/capture_cpi_p12_kalshi_snapshots.py:25-83`; `scripts/capture_cpi_p12_reuters_raw.py:27-80` | CLI requires timezone-aware `--window-start` and `--deadline`, and launches a stage with isolated receipt | A command-line argument is not a frozen protocol. No review-confirmed immutable event-specific deadline binding or measured host clock health |

**Important qualifiers:** `C01` and `C02` are *transport*
time guards, not an issuer attestation that the original information was
published before the applicable cutoff. `C03` observes bounded pages at
different times and does not prove simultaneous, complete event membership.
The historical archive cutoffs documented in PR #212 concern storage
partitioning, not an atomic pre-release cohort snapshot. A valid tuple
of local timestamps cannot prove clocks were accurate, nonstepping or
independent of a compromised caller.

## 3. Prospective cutoff contract: unresolved decisions

A *future*, separately reviewed event-specific protocol must establish
all of the following **before target exposure**. Values are deliberately
left `UNSET` here.

| Mandatory binding | Present status | What makes it independently reviewable |
| --- | --- | --- |
| Exact event/reference month and immutable protocol SHA | UNSET | BLS sealed receipt plus independently proven Kalshi event/contract meaning |
| Each sibling's documented close/expiry and governing rules | UNKNOWN | First-party effective rule version, all-sibling manifest and settlement conditions |
| Reuters first-publication availability and revision history | UNKNOWN | Authorized first-party publisher/independent chronology method and rights |
| Source-specific `window_start` and hard `cutoff` | UNSET | Signed/reviewed policy with no retrospective adjustment; must precede earliest applicable close/release |
| Clock source, UTC normalization, offset/error budget | UNPROVED | Independently logged sync status and bounded error before/during each request |
| Backward step, forward jump, monotonic duration check | UNPROVED AS AN END-TO-END GUARANTEE | Independent timing trace and automated fail-closed regression in release host environment |
| Request start, response completion, source publication cutoff | PARTLY OBSERVED, AUTHORITY UNPROVED | Original raw bytes, calibrated arrival times, publication provenance, error bounds and strict inequalities |
| Late/missing/ambiguous source disposition | PROPOSED ONLY | Frozen no-backfill policy and independent scientific reconciliation |
| Separate scheduled processes and durable off-host receipts | NOT DEMONSTRATED | OS/cloud run IDs, logs, artifact generation/hash readback and failure alerts |

Do not simply set `deadline = BLS release` or infer a Kalshi close
from a ticker. A scientifically admissible observation must satisfy
the **earliest applicable independently verified** deadline; failure
to prove one means `NO SCIENTIFIC TEST / INCONCLUSIVE`.

## 4. Synthetic and release-host adversarial evidence plan

These are **proposed** fixture IDs; no new fixtures or release-environment
runs are claimed here. All cases grant zero scientific/production/trading
authority regardless of raw-stage operational output.

| ID | Counterexample | Required fail-closed scientific outcome |
| --- | --- | --- |
| T01 | Timestamp just before caller-supplied deadline, but independently proven market close is earlier | INCONCLUSIVE / TOO_LATE_FOR_MARKET |
| T02 | Source first published after request completion or after pre-frozen cutoff | INCONCLUSIVE / PUBLICATION_CHRONOLOGY_INVALID |
| T03 | Request starts before cutoff, but response ends at/after cutoff | INCONCLUSIVE / LATE_RESPONSE |
| T04 | Local UTC jumps forward/back while a request is outstanding | INCONCLUSIVE / CLOCK_UNTRUSTED |
| T05 | Clock offset/error bound straddles cutoff even though displayed time is earlier | INCONCLUSIVE / CUTOFF_AMBIGUOUS |
| T06 | Naive, malformed, adversarial timezone or extreme datetime enters timing input | INCONCLUSIVE / CLOCK_INPUT_INVALID |
| T07 | Correct BLS release instant but wrong event-specific Kalshi close | INCONCLUSIVE / CLOSE_AUTHORITY_MISSING |
| T08 | Event/series GET pages span the cutoff or a new sibling appears between pages | INCONCLUSIVE / COHORT_NOT_ATOMIC |
| T09 | Empty market cursor treated as complete-as-of proof | INCONCLUSIVE / PAGINATION_AUTHORITY_MISSING |
| T10 | HTTP Last-Modified is used as Reuters first publication proof | INCONCLUSIVE / PUBLISHER_TIME_UNPROVED |
| T11 | User modifies CLI deadline after seeing the target or market movement | INCONCLUSIVE / PROTOCOL_NOT_FROZEN |
| T12 | A collector succeeds but off-host artifact is missing or cannot be hash-verified | INCONCLUSIVE / DURABILITY_UNPROVED |
| T13 | BLS sealed receipt is unavailable while other two independent lanes run | INCONCLUSIVE / SOURCE_MISSING; preserve other evidence |
| T14 | An earlier successful run is retried with new post-cutoff content | INCONCLUSIVE / BACKFILL_PROHIBITED |
| T15 | Both raw source lanes PASS but no independent scientific validator exists | INCONCLUSIVE / SCIENCE_NOT_AUTHORIZED |
| T16 | Host scheduler was never independently launched despite in-process dry-run PASS | INCONCLUSIVE / SCHEDULER_UNPROVED |

The displayed reason labels are proposed research vocabulary **not**
existing registered runtime enums, tested outputs or authorizing
validation decisions.

## 5. Smallest safe next engineering sequence

1. First establish independently attributable **source/contract close
   and publication authority** through Kalshi Issue #213 and Reuters
   Issue #215. Those inquiries are drafted but **not reported sent**.
   Missing formal support guarantees cannot be repaired by new code.
2. Separately build synthetic-only negative clock/cutoff evaluation
   around *immutable externally reviewed inputs*, never live target
   values. A consistent synthetic case must remain INCONCLUSIVE until
   all scientific authorities have separately passed.
3. Perform release-host isolated synthetic/old-data rehearsals of
   independent OS process scheduling, monitored UTC offset/clock steps,
   receipt guards, crash recovery and off-host hash readback. Do not
   confuse in-process stage callbacks with unattended capture.
4. Only after independently verified source close/issuer publication
   chronology and event binding, propose an exact event-specific
   source/cutoff **freeze** under fresh adversarial review. A document
   merge, 4/4 CI and operational PASS alone do not permit freeze.
5. If any critical proof is missing at the pre-freeze decision,
   explicitly record `NO SCIENTIFIC TEST / INCONCLUSIVE` for
   November 10; no post-release retrospective source or price choice.

**This document confers NO SCIENTIFIC, ECONOMIC, PRODUCTION OR TRADING
AUTHORITY.** Frozen P11 stays unchanged. No target CPI forecast, live
quotes, prospective data capture or BLS refetch occurred.
