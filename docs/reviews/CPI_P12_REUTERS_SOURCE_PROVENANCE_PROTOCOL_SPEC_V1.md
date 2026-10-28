# CPI P12 — Reuters source discovery, attribution and chronology protocol (proposal v1)

**2026-10-27 / issue #215; master gate ledger #214, items 03–04.**

**Status:** PROPOSED RESEARCH SPECIFICATION ONLY. **P12 NOT FROZEN /
UNKNOWN_NOT_READY**. No real November 2026 CPI Reuters target looked up,
collected, extracted or scored in drafting this document. This document does
**not** freeze an actual source-discovery methodology, event, source locator,
protocol SHA, poll schedule, numerical estimate, permitted scientific evidence,
production influence or trading. Frozen P11 remains terminal and unchanged.

**Canonical starting base:** `6568ceddc96b5c4804d72797e4374c029c49cafe`
(2026-10-27). The already-merged #202 raw HTTPS adapter is
`services/forecasting/cpi_p12_reuters_raw_acquisition.py`. It makes strict,
bounded first-party HTTPS requests to caller-supplied exact URLs; it does not
run discovery/search, prove Reuters authorship/independence, determine
publication times, pick an article, extract a forecast or grant scientific
authority. The #200 decoupled stage receipt `PASS` is operational **only**.

## 1. Source roles and limits

| Role | Candidate documentary scope | What is not implied |
| --- | --- | --- |
| Reuters original issuer | `www.reuters.com` / `reuters.com` are already approved **transport** hostnames in #202 | A page on this host is not independently verified Reuters forecast authorship, publisher release time, unchanged text, legal permission or applicability to the intended metric |
| Third-party repost / syndication | #202 transport also recognizes Yahoo, Nasdaq, TradingView, KFGO and other exact hosts named in its source `HOST_OPERATORS` mapping | A repost is not an independently authored forecast and its display time is not Reuters' original publication instant |
| Article timestamp metadata | HTML timestamp, embedded structured fields, feed fields, HTTP `Date` or cache/last-modified headers if present | Client/server/header time does not prove original first public Reuters availability, and `updated` is not interchangeable with `published` |
| Contemporary independent witness | A separately operated source with attested acquisition time, exact archived raw bytes and verifiable quotation/Reuters provenance | Two web domains copying one syndicated wire item are not two independent source claims; one witness alone does not prove original publish time |
| BLS schedule receipt | Already sealed official schedule evidence from #195/#196 | BLS schedule says when CPI is due, not when Reuters first published its estimate |
| P12 preclose market evidence | Separate first-party Kalshi captures under #201/#206 and future frozen cohort/timing protocol | Quote time, contract close and market pressure must not be used to choose Reuters search criteria retrospectively |
| Reuters Connect, Reuters Agency API or licensed feed | Reuters describes optional Connect/API news search and retrieval: https://reutersagency.com/content-delivery-platforms/reuters-connect/ and https://reutersagency.com/content-delivery-platforms/content-delivery | A marketed commercial API is **not** an authorized free first-party source route; Reuters Connect platform terms at https://www.reutersconnect.com/general-terms limit automated collection and caching/archiving absent consent, subject to any controlling separate agreement. These platform terms must not be automatically applied to a distinct public reuters.com site or licensing contract. |

**Do not confuse three distinct clocks:** independently observed
client request/response time, article field `published_at` /
`updated_at`, and provable *first accessible issuer publication time*.
Only independent publication-time evidence valid under a reviewed prospective
method can establish the latter. No method is currently approved.

## 2. Fields that must be prospectively bound before any target observation

A separate **future** exact-head independently reviewed source-selection
protocol must assign all required values. None is selected by this memo.

- Fixed target reference month/statistic/Reuters evidence question and the
  permitted textual meaning of "forecast"; the **November target value must
  not influence** this binding.
- Documented authorization/licensing for **each** selected route and intended
  action, including automated search/retrieval and durable raw-byte archival.
  In particular, Reuters Connect platform restrictions can prohibit these
  actions without prior written consent or a superseding agreement; do not
  assume Reuters Agency Connect/API access is free or that the same terms
  govern public Reuters.com. The authority/rights proof and any license
  conditions must be checked **before** a route is selected or accessed.
- Explicit first-party issuer search or locator mechanism, including
  documentary proof the discovery route exists and is reproducible. An
  imagined Reuters search API, search engine snippet, unverified web index
  or arbitrary human-picked article is **not** an authorized source.
- Deterministic query terms/templates **without a numeric forecast value**,
  search order, language/locale, source ranking, page limits, tie breaking,
  deduplication and disposition for zero/multiple candidates.
- Source/operator allowlist and independently justified equivalence groups.
  URL hostname/group membership alone grants no Reuters attribution or
  corroborator independence. A third-party reprint may corroborate content
  but not independently validate Reuters' original publishing chronology.
- Bounded retry/timeouts, maximum raw payload and article candidate count,
  TLS/certificate validation, redirects policy, error categories, and exact
  extraction method. Keep original strict transport and atomic receipt
  invariants from #202; do not quietly widen allowlists or follow redirects.
- Prospectively sealed `window_start_utc`, `cutoff_utc`, independent clock
  offset/error budget, independent source-read deadline, article
  publication-time condition, freshness limit and per-candidate admissibility
  order. Publication proof must predate **the actual frozen source cutoff**,
  and the cutoff must be before official release and applicable Kalshi close.
- Exact provenance identities/hashes of the protocol, chosen publication
  source(s), independent corroboration method and pre-release BLS schedule.
  Missing or unverifiable original source proof -> `UNKNOWN_NOT_READY`.

An authorized candidate source **cannot** be selected after the forecast
value or market prices have been observed. A changed article URL or
duplicate wire copy must not be selected retrospectively because it has
better apparent accuracy.

## 3. Proposed deterministic research-only decision order

This is a **spec proposal**, not an executable pipeline or authorized
scientific validator.

1. Reject if the prospective method, independently confirmed authorized
   source access and archival rights, source authority, exact allowed
   source routes, clocks, allowed reference month, contract timing, sealed
   BLS receipt or publication chronology protocol is absent.
   Result: `NOT_READY_PROTOCOL_UNFROZEN`.
2. At runtime **only after future independent authorization**, record
   request start, TLS/host, exact URL/path, bounded raw response bytes,
   SHA-256, byte count, status, source response time and local completion
   with uncertainty. A partially durable or guarded receipt cannot PASS.
3. Bind each candidate to an **independently established Reuters
   publication**: exact source locator, immutable text/body, author/
   publisher identification, original issuer attribution, record version,
   update history, source method and first publicly available time. Use
   issuer documents or another *approved* chronology method, not host
   string or article-embedded timestamps alone.
4. Independently compare any third-party reprint with original bytes,
   identify whether the copy is the **same upstream wire item**, prove
   contemporaneous acquisition and note differences/revisions. A
   repost publication time is *not* the original Reuters timestamp.
5. Apply pre-frozen candidate ranking and conflict rules **before**
   examining numerical forecast statements or subsequent CPI truth.
   If two candidates conflict or publication chronology is ambiguous,
   report `UNKNOWN_NOT_READY`; no favorable selection.
6. Assess **only** an independently corroborated, source-authorized,
   as-of-cutoff candidate for scientific admissibility. Even a valid
   Reuters candidate cannot override missing complete Kalshi cohort,
   as-of contract rules, close times, clock proof, sealed P12 protocol,
   or scientific validator. All gates must independently pass.
7. Operational stage receipt `PASS` is never equivalent to scientific
   `PASS`; nonadmissible source evidence remains archived with a
   reason code and without forecast extraction or backfill.

## 4. Mandatory provenance record in a future implementation

The immutable, source-local (never caller-inferred) record should include:

- Evidence type/schema and research-only/no-production flags, exact
  pre-frozen protocol ID, source/operator candidate ID and group.
- Original request route, method, host, response status, all relevant
  bounded raw bytes, literal original embedded timestamps/headers, full
  body SHA-256, byte count, acquisition interval and clock uncertainty.
- Original issuer link/document, publisher/author attribution, claimed
  first-publication instant, source and method by which that instant was
  *independently corroborated*, any updated/reissued instant and version.
- Repost source lineage (Reuters wire item vs independent reporting),
  operator grouping, documented original publication/availability proof,
  differences between copies and archive provenance.
- Source-discovery transcript: pre-authorized deterministic query/order,
  candidate count/ordering/exclusions and negative search results,
  retries/failures, source chooser decision before content/value exposure.
- Source-local create-once receipt outcome and separate scientific
  eligibility status. **No input flag** can grant
  `scientific_evidence_authorized=true`, `p12_frozen=true` or
  production/trading influence.

Neither a search engine result nor the raw article HTML alone proves
first Reuters publication time. Source timing and text extraction are
**not implemented or attested** by the existing #202 adapter.

## 5. Mandatory proposed negative classifications

| Case | Proposed scientific output | Veto / interpretation |
| --- | --- | --- |
| No frozen discovery procedure, no issuer-supported source route | UNKNOWN_NOT_READY | No article chosen |
| Route requires license/consent for automated retrieval or raw archival and no applicable authorization is documented | NOT_READY / ACCESS_RIGHTS_UNVERIFIED | No collection or archiving; obtain proper rights or use a separately legally accessible source |
| No article, unavailable Reuters page, 403, 429, network failure, timeout | INCONCLUSIVE / SOURCE_UNAVAILABLE | Failure local to Reuters; BLS/Kalshi stages unaffected |
| HTTP redirect, untrusted hostname or unexpected URL | FAIL_CLOSED / SOURCE_IDENTITY_INVALID | Do not silently follow or accept mirror |
| HTML has a Reuters logo or byline but issuer attribution cannot be independently proven | UNKNOWN_NOT_READY / ATTRIBUTION_MISSING | Branding alone no authority |
| Reprint on two hostnames with the same Reuters wire text | UNKNOWN_NOT_READY / CORROBORATION_NOT_INDEPENDENT | No fabricated independent reporter |
| Source headers show a `Date` or `Last-Modified` earlier than cutoff, but issuer publication unproved | UNKNOWN_NOT_READY / PUBLICATION_TIME_UNKNOWN | Headers are not first publication |
| Publisher `dateModified` replaces original `datePublished`, or date field lacks zone | UNKNOWN_NOT_READY / CHRONOLOGY_CONFLICT | No timestamp inference |
| Issuer published after cutoff, or request completed after deadline | INCONCLUSIVE / LATE_SOURCE | No post-cutoff backfill |
| Two eligible documents disagree or duplicate candidates have ambiguous priority | UNKNOWN_NOT_READY / CONFLICT | No cherry-pick |
| Reissue changes quoted forecast before cutoff; provenance/version missing | UNKNOWN_NOT_READY / UNVERSIONED_REISSUE | Must not choose favorable revision |
| Article text retrieved only after seeing market movement or CPI outcome | FAIL_CLOSED / TARGET_EXPOSURE | Prospective test spoiled |
| Corroborator fails but original Reuters content is available | UNKNOWN_NOT_READY / MISSING_REQUIRED_CORROBORATION | Receipt PASS cannot promote |
| Final receipt fsync/guard/write fails, regardless of complete raw source text | FAIL_CLOSED / PERSISTENCE_UNCONFIRMED | No successful stage proof |
| Reuters source fails while Kalshi/BLS do succeed | INCONCLUSIVE / REUTERS_MISSING | No cross-stage suppression; no scientific PASS |
| Candidate had an independently proven first issue and all source tests passed | CANDIDATE_SOURCE_ONLY | Must still independently satisfy **all P12** scientific gates |

These are design-level dispositions and must **not** be added as
authorized production enums or current stage CLI statuses. A future
implementation needs synthetic fail-closed cases, first-run/retry
comparison and fresh independent exact-head review.

## 6. Remaining questions and explicit stop rule

Before any future source protocol can become frozen, determine
**through first-party Reuters documentation or independently verified
issuer evidence**:

1. Does a first-party, stable, legally accessible publisher locator,
   search/archive/feed exist with documented query semantics, article
   identity and update history? What license or written authorization permits
   its automated retrieval and prospective raw-byte retention? Reuters
   Connect platform terms are distinct from public Reuters.com terms, and
   a separate negotiated agreement may prevail.
2. What evidence proves the original article's **first public
   accessibility** and effective revision, rather than cached or
   modified timestamps? Can that be independently attested pre-cutoff?
3. How are independent syndication operators, republished wire items
   and article revisions identified and compared without treating
   duplicates as independent confirmation?
4. Can the source and independent corroborator be acquired in advance
   of a prospective cutoff with stable publication provenance and an
   explicit nonavailability classification?

If any mandatory source or timing claim cannot be proved by the
pre-freeze deadline, P12 MUST remain `NOT_READY / NO_SCIENTIFIC_TEST /
INCONCLUSIVE`, even if raw HTTPS collection works flawlessly. No
forecast/price cherry-picking, no after-cutoff recovery, no trade.

## 7. Next separate implementation checkpoint (not authorized here)

Prepare synthetic, credential-free historical/non-target fixtures to
exercise alternate publication timestamps, same-wire syndication,
copy-edit revision, conflicting candidates, disappearance, source 429,
TLS redirect, skew/late completion and receipt persistence failures.
Then propose a **separate** reviewer-approved, documented first-party
discovery implementation if the publisher actually supports it.
No November target search/capture or value extraction may occur
without the independent prospective source freeze and the remaining
P12 scientific gates.

**Permanent boundary:** `research_only=true`,
`scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`,
`p12_frozen=false`, `production_influence=0`,
`promotion_authority=NONE`, trading `NONE`.
