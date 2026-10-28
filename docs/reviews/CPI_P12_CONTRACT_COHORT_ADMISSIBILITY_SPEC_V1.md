# CPI P12 — Contract identity and point-in-time cohort admissibility (draft v1)

**Status:** SPECIFICATION ONLY — NOT IMPLEMENTED; not a protocol freeze, source
approval, scientific gate, economic test, forecast, price selection, production
permission, or trading authorization. **P12 NOT FROZEN / UNKNOWN_NOT_READY.**

**Base reviewed:** canonical main
`51fb1e650228ad1a1f3737303ea5a662f77aa8d6`, tree
`b57439902dd5452411413aad232555ac6079caaf` (2026-10-27).
Issue #209. No live exchange/Reuters/BLS target request is part of this spec.
Frozen P11 artifacts, semantics, and authority remain untouched.

## 1. Purpose and nonclaims

This document defines the *future evidence requirements* for identifying one
candidate P12 Kalshi CPI event and its entire set of sibling contracts as of a
prospectively specified pre-release cutoff. It deliberately does **not** choose
the event, reference month, cutoffs, market set, strikes, or settlement rules.

Canonical #199 collects bounded raw series/event/one-page inventory; #201
collects caller-selected, time-guarded market raw responses; #206 observes raw
series/event/cursor-paginated siblings and reports
`OBSERVED_PAGES_ONLY_NOT_FROZEN` plus `UNKNOWN_NOT_READY`.
Neither a first-party 200 response, a terminal page, a populated metadata
field, a stage `PASS`, nor consistent repeated observations proves scientific
admissibility. The #200 operational `READY_ALL_REQUIRED_STAGES_PRESENT`
likewise does not confer scientific authority.

This is a **design for a separate future validator**, not a validator or
permission to use any observed P12 metadata for a strategy.

## 2. Source and proof matrix

All URLs in the first three rows are **endpoint templates**, not frozen event
locators, and the current collector's fixed origin is
`https://external-api.kalshi.com`. No alternative host, undocumented query
parameter, API response field, or page is implicitly authorized.

| Claim | First-party evidence to preserve | Cross-check and proof still needed | Default if absent |
| --- | --- | --- | --- |
| Series identity | Exact raw GET `/trade-api/v2/series/KXCPI`, strict JSON `series.ticker`, signed-off path and envelope | Series specification/version and independently attested meaning of CPI series; avoid reusing P11's event | UNKNOWN |
| Event identity, target reference month | Exact raw GET `/trade-api/v2/events/{event_ticker}`; event `ticker` or `event_ticker` and `series_ticker` | Same exact ticker across all endpoints **and** an issuer/exchange-authoritative document explicitly mapping that ticker to the intended index, month, statistic, units, and release; a ticker suffix is not proof | UNKNOWN |
| Sibling candidate set | Raw GET `/trade-api/v2/markets?event_ticker={event_ticker}&limit=100` and every bounded cursor page; `markets[].ticker`, `markets[].event_ticker`, explicit terminal `cursor=""` | Authorized as-of complete-universe evidence (see §3), not merely terminal pagination; reconcile all market identities with event-detail metadata | UNKNOWN |
| Per-market cross-check | Independent raw GET `/trade-api/v2/markets/{ticker}` for **every** candidate ticker; `market.ticker`, `market.event_ticker` | Exact bijection against candidate inventory and event detail; no missing or extra sibling, no mixed event or stale result | UNKNOWN |
| Strike comparator and boundaries | Raw market `strike_type`, `floor_strike`, `cap_strike` plus `rules_primary`/`rules_secondary` where present | Exchange-authoritative, applicable rule text/version giving operator (including strict vs inclusive), rounding, scale, tails, endpoints, and numerical threshold; merely seeing `greater`/`less`/`between` or parseable decimals is insufficient | UNKNOWN |
| Contract timing | Raw `close_time` per market and any applicable event close/settlement fields if available; all exact time-zone offsets preserved | Independent authoritative market/contract terms confirming **which** close/lock/settlement instant applies; compare to prospectively frozen capture cutoff and reviewed BLS release receipt. Do not infer UTC from naive strings | UNKNOWN |
| Settlement result, primary authority | Raw issuer/exchange settlement source descriptions, e.g. `series.settlement_sources` or `event.settlement_sources` **only if actually present** | Exact issuer URL/document, rule version/effective period, official actual-result definition, revision/correction policy and resolution process; an HTTPS link or source name is only a hint, not proof | UNKNOWN |
| Record chronology | For each raw observation: exact path, status, body bytes/base64, digest, length, local request start/end, `observed_at_utc`, capture completion | Trustworthy clock synchronization bound, path/byte identity, source publication/as-of time **where independently available**, and full pre-cutoff temporal consistency | UNKNOWN |

The present #206 adapter does **not** provide an exchange snapshot token, a
historical as-of membership guarantee, an independently authenticated server
publication time, an attested rules document, or issuer confirmation. Those
are **required external proofs to design and review**, not facts presumed to
exist in any response. If the exchange cannot provide a scientifically
defensible complete as-of universe guarantee, this gate stays NOT READY;
do not substitute caller reconstruction, a search result, or later market
history.

## 3. The complete-cohort problem: two separate gates

**A. Structurally observed candidate set (existing capabilities only).**
Bind each raw envelope to the requested origin/path, HTTP 200, strict JSON,
SHA-256 and exact byte length. Parse explicit `cursor` from **every** page;
require terminal `""` within a prospectively fixed finite page limit.
Reject absent/null/non-string/repeated cursor, duplicate ticker, wrong event,
empty/oversized pages, missing page and transport/parse errors. Preserve page
order, observation times and **all** sibling tickers, without quote or strike
selection. A successful A yields only `CANDIDATE_COHORT_OBSERVED`.

**B. Complete, point-in-time authorized cohort (not implemented, not
established).** A future independent authority review must identify a
first-party mechanism with a documented, testable claim about *all* contracts
belonging to the event at time `T`: for example an exchange-issued immutable
as-of inventory/manifest with verifiable time/version and an atomicity
guarantee. Such a mechanism is **hypothetical until demonstrated**. If it
exists, independently verify that all paginated observations share the same
version/snapshot, no pages were inserted, omitted or reissued, and the manifest
matches one-by-one market-detail reads under the authorized snapshot
semantics. Prove no extra, withdrawn, hidden, suspended, replaced, or
reclassified siblings are missing. Record the exact authority document and
supporting raw bytes/hashes. If no defensible completeness mechanism exists,
B stays `UNKNOWN_NOT_READY` **even with any number of identical snapshots**.
Endpoint pagination is not a historical or atomic snapshot guarantee.

**Temporal drift:** two complete scans (if later approved) can test *observed
stability only*. Compare sorted ticker sets, counts, exact per-market rule
fields, market status and close fields, page sequence, page/version markers if
available, and source times. Added/removed markets, changed rules, missing
market detail, mismatched counts or mixed versions are a veto. Equal results
do not establish B in the absence of the authoritative as-of guarantee.
Never backfill missing pre-cutoff evidence with a later snapshot.

## 4. Future point-in-time admissibility contract

All values in this section are **unbound variables**, requiring a separately
reviewed event-specific protocol created **before target exposure**:
`candidate_event`, `reference_month`, `T_start`, `T_cutoff`, allowable
clock offset/error, maximum page count, maximum per-request duration, source
allowlists, rules document identifiers, required market statuses and manifest
verification procedure. A production/test runner must not infer or select them
from the observed forecast or price data.

- Every request start, first-party raw observation, response completion,
  inventory scan completion, independent market-detail corroboration,
  document attestation and evidence publication must be proven no later than
  the prospectively sealed cutoff, after accounting for documented clock
  uncertainty. A local timestamp is **not** a source publication timestamp.
  `T_cutoff` must be strictly before the official release and any relevant
  market close according to the independently bound terms.
- The raw record must preserve the original bytes, base64 digest/length,
  exchange origin, full requested path and pagination tokens, status,
  UTC-local request-start/end and collection timestamps, and evidence
  acquisition method; reject out-of-window, naive, contradictory or reordered
  timestamps. Do not normalize away original offsets or raw input.
- Exchange rule documents and settlement-source documents must be dated or
  versioned and independently attributable as effective **at the intended
  as-of time**. Missing version/effective-period or post-cutoff discovery is
  a veto, even if a later copy appears to agree.
- Repeated GETs within a window may corroborate **consistency**, never confer
  scientific authority or repair a missing atomic completeness proof.
- A network error, 403/429, redirect, timeout, empty/malformed body, overlarge
  response, nonterminal cursor, or unpublished authoritative rule is a source
  failure. That source failure must not rewrite P11 or other P12 stages.

**No early release via rule inference.** The current #206 field-presence
checks (including settlement-source name/HTTPS URL and valid `close_time`)
are diagnostic; they are not settlement authority, index/month proof or
calibrated contract semantics.

## 5. Intended fail-closed classification (proposed, not executable)

Future reviewers must distinguish an invalid raw observation from the absence
of a sufficiently authoritative corroboration:

| Condition | Proposed output | Authority |
| --- | --- | --- |
| Exact bounded source reads, unique terminal pages, identities/fields internally consistent | `CANDIDATE_COHORT_OBSERVED`; final `UNKNOWN_NOT_READY` | NONE |
| Good pages/fields but no approved source for atomic as-of completeness | `UNKNOWN_NOT_READY: COHORT_AUTHORITY_MISSING` | NONE |
| Good identity but reference month, rule version/operator, issuer or settlement document not attested | `UNKNOWN_NOT_READY: CONTRACT_AUTHORITY_MISSING` | NONE |
| Late source publication, post-cutoff page/market detail, unbounded clock uncertainty | `UNKNOWN_NOT_READY: LATE_OR_UNPROVABLE_PIT` | NONE |
| Conflicting identities, invalid raw bytes/digest, malformed/duplicated pages, changed cohort/rules, invalid source | `FAIL_CLOSED: CONTRADICTORY_OR_INVALID_OBSERVATION`; no accepted candidate | NONE |
| Artifact/receipt exists but not durably committed or contains a pending guard | `FAIL_CLOSED: PERSISTENCE_UNCONFIRMED` | NONE |

These are **spec labels only**; they do not change current CLI exit codes,
existing #200 stage classifications, or result schemas. Every eventual
scientific disposition must remain independently gated and is at most
`INCONCLUSIVE` unless all separately reviewed source, freeze, timing and
reconciliation protocols are prospectively satisfied. No state in this
document authorizes `READY`, prices, forecast extraction, edge claims or
orders.

## 6. Mandatory synthetic adversarial fixture catalogue

Only synthetic, event-neutral bytes, no real November Reuters value or real
P12 target selection. For a future implementation, construct paired exact
response envelopes **and** independent contradictory variants; verify
the resulting classification and zero authority on **both first attempt and
retry** where persistence is involved.

| ID | Fixture | Expected scientific disposition |
| --- | --- | --- |
| S01 | Series/event/ticker names match, two pages with explicit terminal cursor, hashes/times valid, all market fields populated | Structurally observed; still UNKNOWN without separate cohort/rules authority |
| S02 | Identical first and second full inventory scans, no authoritative snapshot semantics | UNKNOWN; never infer complete as-of cohort |
| S03 | Sibling added between pages; later market detail differs from inventory | FAIL_CLOSED conflicting cohort |
| S04 | A market removed, renamed, hidden or suspended after page one | FAIL_CLOSED or UNKNOWN on missing as-of proof; never silently filter |
| S05 | Missing/null/duplicated/cyclic/nonterminal cursor or page-bound exhaustion | FAIL_CLOSED; no partial cohort |
| S06 | Duplicate ticker across pages, foreign `event_ticker`, wrong `series_ticker`, conflicting event `ticker` aliases | FAIL_CLOSED |
| S07 | Hash/byte-count/path/status mismatch, duplicate JSON keys, malformed JSON, redirect or truncated response | FAIL_CLOSED |
| S08 | Local request observation precedes request, follows completion, or is after cutoff; clock offset unknown | UNKNOWN/FAIL_CLOSED as applicable; no PIT admissibility |
| S09 | Raw `strike_type=greater` but rule text defines inclusive comparison, different units or rounding | FAIL_CLOSED contradictory semantics |
| S10 | Valid numeric floor/cap but rules/version missing or outdated | UNKNOWN; no comparator inference |
| S11 | `settlement_sources` contains name and HTTPS URL with no issuer document/effective version | UNKNOWN; link alone is not authority |
| S12 | Ticker suffix appears to name October but first-party rule describes a different reference month | FAIL_CLOSED; no ticker-derived month |
| S13 | Market closes before planned sample cutoff or close time differs across sources | FAIL_CLOSED/UNKNOWN; no after-close substitution |
| S14 | Missing market detail, unpublished rules, source 403/429 or timeout for one sibling | UNKNOWN or FAIL_CLOSED; never drop that sibling |
| S15 | Pre-cutoff request returns body only after cutoff; later source reissue agrees | UNKNOWN; no late backfill |
| S16 | Reordered pagination pages with otherwise plausible rows, changed page version or inconsistent set counts | UNKNOWN/FAIL_CLOSED; no atomic completeness |
| S17 | Operational stage receipt PASS, independently unverified market cohort/Reuters source | UNKNOWN; operational PASS is not scientific PASS |
| S18 | Caller attempts to set `p12_frozen`, `scientific_evidence_authorized`, production influence, price selection or trading | Rejected/no authority, even if raw data valid |
| S19 | Previous process left incomplete provisional/pending receipt; retry sees otherwise complete JSON | FAIL_CLOSED; no promotion from uncommitted artifact |
| S20 | Source record obtained after target value exposure or selected only after seeing prices | UNKNOWN/NOT READY; no retrospective recovery |

A synthetic "positive" fixture proves **only** deterministic parsing and
invariant enforcement. Neither S01 nor any green implementation unit test is
evidence that the November 10 event is scientifically admissible.

## 7. Separate gates, implementation order, and stopping rule

1. Independently review this **documentation-only** proposal at exact HEAD
   and pass repository CI. This step changes no executable behavior.
2. Separately verify an authoritative mechanism (if any) for complete as-of
   market membership, effective issuer rules and exact settlement source,
   with timestamped source provenance. Record unsupported fields and
   nonexistence honestly; **do not fabricate an exchange guarantee**.
3. Only after that evidence exists, propose a **new isolated, synthetic-first**
   validator with immutable input record definitions, timing authority,
   adversarial cases above, and independent approval.
4. Separately validate Reuters locator/search provenance, attribution,
   publication chronology and objective missing-source outcomes; bind the
   sealed BLS release/calendar evidence; prove off-host unattended capture,
   hard deadlines, independent scientific reconciliation, and post-release
   truth acquisition. These cannot be bypassed by this specification.
5. Only a *future* separately reviewed and prospectively frozen P12 protocol
   may choose event, cohort, cutoffs or admissible prices. If any mandatory
   proof is still missing at the intended freeze deadline, record
   `P12 NOT READY / NO SCIENTIFIC TEST / INCONCLUSIVE`; no target-informed
   backfill, no waiver and no trade.

**Permanent boundary for this checkpoint:** research only,
`scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`,
`production_influence=0`, `promotion_authority=NONE`,
`p12_frozen=false`, trading authority **NONE**. No implementation, live
endpoint calls, credentials, forecast extraction or November target access.
