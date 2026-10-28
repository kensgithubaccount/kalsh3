# CPI P12 — Kalshi as-of cohort and effective contract-rule source feasibility (v1)

**Checkpoint:** Issue #211, 2026-10-27 (America/New_York); documentation
sources viewed during this checkpoint, through approximately
2026-10-28T01:03Z. The timestamp is a **local research/check time**, not
a source publication, market as-of, or exchange attestation.

**Canonical starting point:** main
`140e59670fdb4afd9315592155de90d480177f82`, tree
`eea06b52f1d1362c8bee65b526923f6c505f32bc`. PR #210 is merged.

**CONTROL:** RESEARCH ONLY. **P12 NOT FROZEN / UNKNOWN_NOT_READY**;
`scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`,
`promotion_authority=NONE`, `production_influence=0`, and **no trading**.
This is a first-party **documentation feasibility audit**, not acquisition of
the November target event or a scientific/PIT admissibility assessment.

## 1. Scope, method, and evidence standard

Reviewed public first-party Kalshi documentation (sources S01–S11), NOT
the real November CPI event/series endpoints, Reuters, current quotes, or
individual contract terms PDFs. No market/API calls or target-value access.
The review used public rendered documentation. **Raw HTTP response bytes were
not separately acquired/archived in a provenance-sealed artifact; source-body
SHA-256 is therefore NOT AVAILABLE.** A failed attempt to download a public
documentation page is not represented as a successful raw capture.
Links and page content are sufficient for this *research memo*, not for
publication-time, future immutable provenance or scientific authority.

Verdict definitions:
- **SUPPORTED (documentation scope)**: the reviewed first-party page
  affirmatively describes the capability/field. It does not mean runtime
  behavior, historical retention or the specific CPI contract was validated.
- **UNSUPPORTED CLAIM (by reviewed documentation)**: the cited page's
  semantics are incompatible with the proposed inference; does not mean the
  exchange could never supply such evidence by another documented interface.
- **UNKNOWN / NOT PROVED**: the required guarantee, authoritative effective
  rule version, or proof was not established by the pages reviewed. Absence of
  a statement is not proof of universal absence.

This report records exactly which claims are supported and where scientific
proof is missing. It does not retrospectively reconstruct a cohort.

## 2. Source register — exact first-party documentary locators

Every entry was viewed as *documentation*, not as the referenced API endpoint.
All page retrieval observations are research-only and were made 2026-10-27 ET
/ early 2026-10-28 UTC. Document edition/effective time for each **UNKNOWN
unless independently explicit**. No content-hash or signed archive is asserted.

| ID | First-party locator | Positive documentation | Inference not supplied |
| --- | --- | --- | --- |
| S01 | https://docs.kalshi.com/api-reference/market/get-markets | `GET /markets` documents `event_ticker`, `limit`, request and response `cursor` fields, next-page use, and timestamp-filter constraints | No endpoint-specific definition of an empty *response* cursor as terminal; no atomic snapshot ID, common as-of instant, cross-page consistency token or complete-at-T warranty |
| S02 | https://docs.kalshi.com/api-reference/events/get-event | `GET /events/{event_ticker}`, event metadata and child markets; `with_nested_markets` / `exclude_markets` options | Historical markets settled before the cutoff are **excluded** from the event response; event child list not a certified all-time/as-of membership manifest |
| S03 | https://docs.kalshi.com/api-reference/market/get-market | One ticker's current market fields, `rules_primary`, `rules_secondary`, `strike_type`, bounds, close/expiration and status fields | No immutable effective rule revision/time or proof that fields were valid at a past cutoff |
| S04 | https://docs.kalshi.com/getting_started/historical_data | Live and historical tiers partition older market/trade records; per-data-type cutoff behavior | Archive rollover cutoff is **not** an event-cohort as-of timestamp or an atomic market inventory version |
| S05 | https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps | `market_settled_ts` and other archive-routing cutoffs | These are not the time at which every sibling in an event was simultaneously observed/created |
| S06 | https://docs.kalshi.com/api-reference/historical/get-historical-markets | `GET /historical/markets`, bounded cursor, `event_ticker` and series filters, archived market fields | No immutable *time-travel* replay/version chain for pre-cutoff membership; later archive cannot backfill a prospectively missed observation |
| S07 | https://docs.kalshi.com/api-reference/historical/get-historical-market | Individual historical market lookup by ticker | An archived latest-record response is not evidence of each previous rules revision or exact past visibility |
| S08 | https://docs.kalshi.com/websockets/market-and-event-lifecycle | Authenticated lifecycle notifications, creation/metadata updates, `sending_ts_ms`, sequence numbers and identifiers | No independently demonstrated initial full snapshot, durable event-log replay, guaranteed gap recovery, or archival membership proof |
| S09 | https://docs.kalshi.com/api-reference/market/get-series | Series describes recurring event template; `contract_url`, `contract_terms_url`, `settlement_sources`, `last_updated_ts` fields | Link/updated timestamp alone does not bind an exact *effective* CPI contract PDF, immutable hash or correction period |
| S10 | https://docs.kalshi.com/api-reference/events/get-event-metadata | Event `settlement_sources` and `market_details` metadata are documented | A named source URL is not independent authoritative settlement interpretation, issue date or revision log |
| S11 | https://help.kalshi.com/en/articles/13823822-market-rules and https://help.kalshi.com/en/articles/13823852-market-bug-bounty-program | Market rules and verification sources are presented; Kalshi explicitly recognizes possible summary/full-rule contradictions and incorrect dates as reportable error classes | No guarantee a specific CPI rule link, source or historic revision is complete/correct/immutable as of T |

Additional documentary discovery only:
https://kalshi.com/regulatory/rulebook and
https://kalshi.com/regulatory/product-certifications show official
regulatory navigation. The accessible page representation is insufficient
to verify a **specific** CPI product filing, effective contract rulebook
edition, amendments, or complete historic source bytes. No such claim is
made here and no linked PDF was reviewed.

## 3. Requested scientific proof versus documented capability

| Required proof | Verdict | What the reviewed first-party material does establish | Remaining required evidence |
| --- | --- | --- | --- |
| Event-filtered bounded list and pagination cursor | SUPPORTED (documentation scope) | S01 documents event filter, page limit, response cursor and next-page use | Pagination support alone does not establish response terminal-marker semantics or complete at-T siblings |
| Empty `GET /markets` response cursor conclusively marks the terminal page | UNKNOWN / NOT PROVED | S01 does not define an empty response cursor as terminal; another endpoint's documentation cannot supply that contract | Require explicit `GET /markets` first-party specification or verifiable issuer clarification; even proven terminal traversal cannot certify atomic at-T membership |
| All contemporaneous siblings were present in one atomic as-of view | UNKNOWN / NOT PROVED | S01/S02 list current markets and S08 emits changes | Exchange-certified snapshot/version/manifest or independently reviewed equivalent with full inclusion and cross-page atomicity guarantee |
| Reconstruct all siblings at prior pre-release T from archival APIs | UNKNOWN / NOT PROVED | S04–S07 provide historical storage partitions and per-market retrieval | Documented historical membership snapshots/versioned event lineage including delist/replace/late-list rules; **no post-cutoff repair** |
| Use `/historical/cutoff` as an atomic sibling inventory timestamp | UNSUPPORTED CLAIM | S04/S05 define it as a *data-tier routing/settlement* boundary | A separate complete-cohort guarantee; do not relabel the archive cutoff |
| Effective contract comparator/units/rounding/tail and rule text at T | UNKNOWN / NOT PROVED | S03 fields exist; S09 links potential contract terms | Issuer/exchange-authenticated exact terms, revision, effective/issued-at, timestamped original bytes, consistent across all siblings |
| Authoritative reference month and index definition for intended P12 event | UNKNOWN / NOT PROVED | S02 event and S09 series identities can be observed | Independently confirmed specific event/series/contract documentation mapping month/index/statistic; not ticker suffix inference |
| Settlement source, corrections/revisions, and expiry/close authority at T | UNKNOWN / NOT PROVED | S02/S09/S10 reference settlement metadata and S03 close/time fields | Actual governing term/version/source document, applicability window, original bytes/hash, interpretation and correction policy |
| Real-time lifecycle change telemetry can assist drift detection | SUPPORTED (documentation scope) | S08 documents notifications and per-message sequencing, requires authenticated WSS | Independently proven complete bootstrap, continuous gap-free collection, reconnection/replay, off-host timestamp/durability, bounded loss |
| Lifecycle feed alone proves historical complete cohort | UNSUPPORTED CLAIM | S08 describes *notifications*, not a sealed initial manifest or guaranteed historical replay | Initial certified snapshot plus independently complete delta/replay semantics |
| Local GET timestamps certify first-party rule publication time | UNSUPPORTED CLAIM | S01–S10 describe APIs or client-facing fields | Issuer attestation of effective source publication, independently logged before frozen cutoff |

**Critical nuance:** S01's response schema contains a `cursor` string, but
its empty-response terminal interpretation is not defined there. A documented
empty-response terminal meaning for a *different* Kalshi endpoint must not be
transferred to `GET /markets`. The current research-only #206 adapter may
interpret `cursor=""` as a traversal stop, but that behavior has no
endpoint-specific terminal-semantic authority from S01 and never proves a
complete cohort. S01's `max_created_ts` or `max_updated_ts` query
parameters are filters, not a documented snapshot isolation guarantee.
The updated-time filter tracks **non-trading metadata** and is restricted
against other timestamp/status filters. Querying values after the event
would not prove when a rule became available to an earlier prospective
observer. S02 explicitly excludes some older historical settled markets;
S04–S07 permit later archive retrieval, not original time-of-knowledge.

**No proof laundering:** an archived file, canonical PR CI PASS, matching
ticker set on two GET traversals, `event.markets`, repeated lifecycle
messages, or a non-empty rule/source field may corroborate an observation
but cannot by itself elevate `cohort_authority` or
`scientific_evidence_authorized`. No single "SUPPORTED" documentary row
confers scientific admissibility.

## 4. Specific Kalshi confirmations required before any validator or freeze

A future **exchange-authenticated written answer** or explicit stable
first-party technical documentation must address all of these. An answer
from search synthesis, third-party forum, or caller is not a substitute.

1. Is there an immutable version/ID or signed roster for **every contract**
   in an event as-of a specified UTC instant (including hidden, suspended,
   replaced, delisted and recently listed markets)? If yes, specify
   version/manifest retrieval, timestamp/effective semantics, exclusions
   and original-byte preservation. If not, state that openly.
2. Does a cursor traverse a fixed snapshot across requests despite
   simultaneous new listings or delistings? Is there an endpoint-specific
   documented atomicity/snapshot lifetime? What constitutes terminal
   pagination and consistency failure?
3. Does GET /events child membership cover markets migrated to historical
   storage? If not, is there a certified union with /historical/markets and
   evidence of **the exact pre-release as-of membership**, rather than
   current/historical latest records?
4. Can an authenticated lifecycle stream supply replayable **all-market**
   creation, delisting, expiry and metadata changes starting from a
   certified baseline, with gap detection, sequence continuity across
   reconnection, retention and publication-delay guarantees?
5. For the relevant CPI event, can authoritative terms be obtained with
   *effective-from/effective-to dates*, amendments, revision IDs, original
   downloadable bytes and independent timestamp/archival provenance?
   Are `contract_url` / `contract_terms_url` immutable identifiers or
   mutable live links?
6. Do the official terms resolve inclusive vs strict strike boundaries,
   units, rounding, tails, reference month/index definition, source
   publication/correction hierarchy, settlement timing and disputed-data
   revisions consistently for each sibling?
7. Are there authoritative machine-readable timestamps for when the
   exact contract terms and settlement documents were **published** and
   accessible, not merely when the exchange returned a GET or marked
   `updated_time`?

Document responses with issuer identity, exact locator, full bytes/hash,
source publication/version/effective time, independent retrieval time
and allowed use. A missing or non-verifiable answer means
**UNKNOWN / NOT PROVED**, not a relaxed requirement.

## 5. Proposed bounded next segment and stopping rule

- **Next candidate segment, if needed:** independent offline collection of
  *public general exchange documentation* only, with sealed raw bytes,
  SHA-256, response headers and retrieval UTC (no target values).
  Separately ask Kalshi for formal API/rule publication semantics through
  approved channels; this report does not claim to have sent a request.
- Do **not** implement or activate scientific validation until the source
  authority exists. If a future code checkpoint is proposed, start with
  synthetic negative fixtures for concurrent listing during pagination,
  stale/missing market, omitted lifecycle delta, historical-tier exclusion,
  rule revision without effective time and cursor drift.
- Reuters source attribution/publication, independently sealed BLS P12
  release binding, unattended preclose capture, hard-cutoff reconciliation,
  postrelease actual settlement and economic calibration remain separate
  unfinished gates.
- If as-of or contract-rule authority remains unproved at the intended
  November freeze deadline: **P12 NOT READY / NO SCIENTIFIC TEST /
  INCONCLUSIVE**. Preserve negative evidence and stop. Do not reconstruct
  with post-cutoff data, select a favorable price, waive a missing sibling,
  query the November Reuters target or trade.

**End-state of this report:** documentary feasibility is partially SUPPORTED
for retrieval primitives, **UNKNOWN** for the two principal scientific
authorities (atomic as-of complete cohort and effective contract/settlement
rules). No live event, raw target observation or authority change occurred.
