# CPI P12 — Presealed BLS calendar-to-event binding (proposed v1)

**Checkpoint:** Issue #219 / master readiness ledger #214, gate #05.
**Canonical starting main:** `4d8ae7c4ec4a54405955ad8acbfa23e6b05a822c` (2026-10-28).
**Disposition:** RESEARCH-ONLY SPECIFICATION; **P12 NOT FROZEN /
UNKNOWN_NOT_READY**. This is not a live run configuration, event selection
approval, validation report, scientific-source admission, or trade authority.

## 1. Existing sealed receipt and the limits of this checkpoint

The canonical, previously reviewed
`docs/reviews/CPI_P12_BLS_SCHEDULE_RECEIPT_20261021.md` records the
October 21 browser-attested BLS CPI calendar import and subsequent GCS
upload/readback. This proposal **references that existing record**; it did not
download BLS HTML, independently re-read GCS, authenticate an October 28
response, or compute any new raw-source hash.

| Candidate future protocol field | Previously recorded evidence value |
| --- | --- |
| `reference_month` | `2026-10` |
| `release_at_utc` | `2026-11-10T13:30:00Z` (08:30 EST) |
| `source_locator` | `https://www.bls.gov/schedule/news_release/cpi.htm` |
| `source_sha256` | `cece37b1f964eed1cd51215764a55fb26e310da31b441ab9c65758989c9afc17` |
| `receipt_sha256` | `01fc6542b73b0b6d293d91c024d2ab0be7c5ee8928f11f624eb32ac35e50619d` |
| Original HTML length | 55389 bytes |
| Receipt type | `CPI-P12-MANUAL-BLS-CALENDAR-RECEIPT-v1` |
| Receipt import observation | `2026-10-21T16:13:23.761887Z` |
| Source GCS object | `gs://total-market-138523-kalshi-evidence/cpi_p12/20261021/cpi_schedule_20261021.html` |
| Receipt GCS object | `gs://total-market-138523-kalshi-evidence/cpi_p12/20261021/cpi_p12_bls_calendar_receipt_20261021.json` |

The earlier audit says both GCS objects were independently streamed back and
hash-matched at that time. **This checkpoint does not repeat that readback.**
The hashes are exact, previously recorded candidate binding constants, not
proof that an event-specific P12 protocol has been frozen or a receipt is
currently present on the eventual execution host.

## 2. Proposed future event-specific binding, not a freeze

A separately reviewed **prospective protocol** would need to bind, in one
immutable versioned artifact *before any relevant target exposure*:

1. Exact P12 study identifier, authorized CPI event and index/contract
   reference month. Do not derive the actual market/event identity from
   P11's historical tickers or a caller's unverified suffix.
2. The calendar locator, raw source SHA-256, receipt SHA-256, evidence ID,
   reference month `2026-10` and release instant
   `2026-11-10T13:30:00Z` as a coherent, reviewable tuple.
3. Immutable original receipt artifact bytes and path, independently
   reproducible GCS readback/digest and separately preserved original
   HTML bytes, archive object generation/version where available, and
   acquisition/readback provenance. A GCS URI on its own is not proof of
   the object's current contents.
4. Reviewed pre-release collection windows, clock-error budget,
   individual Kalshi and Reuters independent scheduling, publication
   deadlines and receipt storage. All must precede release and applicable
   exchange close under separately established contract terms.
5. A protocol SHA, author/reviewer approval, explicit allowed scientific
   evidence sources, test contingency and no post-freeze source switching.
   None of these fields is frozen or approved by this document.

The intended tuple is a **candidate derived from the prior BLS receipt**.
A reviewer must verify event-to-reference-month identity and the complete
Kalshi contract rules independently. The BLS calendar does not certify
any Kalshi contract or a Reuters article's first publication time.

## 3. Existing offline validator (no implementation changes here)

`services/forecasting/cpi_p12_decoupled_collection.py` already exposes:

- `SealedBLSScheduleExpectation(reference_month, release_at_utc,
  source_sha256, receipt_sha256)`, which normalizes an aware UTC instant and
  checks exact month and SHA-256 forms.
- `verify_presealed_bls_schedule_receipt(receipt_path, expectation)`,
  which performs **no network request**, reads exact local receipt bytes,
  compares full SHA-256, strictly decodes JSON, checks record type and
  official locator, exact month/instant/source SHA and nonauthorizing flags,
  verifies embedded original HTML base64, SHA-256 and exact byte count.
  It returns only a `StageCollection` pointing to the verified receipt.

Failures are `P12ExpectedStageFailure` or
`P12IsolationAuthorityError` as applicable, not independently confirmed
scientific outcomes. This code does **not** verify a currently available
GCS object, determine Reuters publication chronology or resolve Kalshi
as-of membership. It validates an operator-provided expectation, which
must never independently confer scientific approval.

## 4. Mandatory fail-closed prospective adversarial cases

All future fixtures must be synthetic or use previously approved sealed
calendar records; do not fetch target Reuters or live CPI markets.

| ID | Condition | Required disposition |
| --- | --- | --- |
| B01 | Valid exact presealed receipt and expectation tuple | Operational BLS receipt can be verified; scientific P12 remains UNKNOWN_NOT_READY |
| B02 | Missing local receipt, unreadable path, unavailable GCS object | BLS stage source unavailable, not PASS |
| B03 | Receipt SHA differs by one byte, or missing receipt guard/durability proof | Reject; no fallback to unchecked JSON |
| B04 | Embedded raw HTML base64 missing/bad, source hash or byte count differs | Reject even when metadata fields appear plausible |
| B05 | Reference month `2026-09`, wrong October month, wrong UTC instant or naive offset | Reject before any P12 source/scientific status |
| B06 | Official BLS locator, record type or receipt schema contradicts expectation | Reject; no alternative unaudited source |
| B07 | Receipt claims `p12_frozen=true`, science authority or production influence | Reject authority elevation |
| B08 | A P11 event/ticker, P11 protocol receipt or source is substituted | Reject P11/P12 cross-boundary substitution |
| B09 | A later BLS page/refetch conflicts with the presealed receipt | Do not silently replace the original receipt; independent reviewed reschedule decision required |
| B10 | BLS stage fails while synthetic Reuters/Kalshi lanes are separately runnable | Do not suppress or overwrite other stage receipts; overall scientific status still UNKNOWN |
| B11 | Valid source bytes but a different GCS generation/object is substituted | Reject until independent byte/hash/generation verification |
| B12 | Operational stage PASS, but source-clock/cutoff, event rules or Reuters attribution missing | Scientific result INCONCLUSIVE, no freeze |
| B13 | Post-release receipt replaces the pre-release calendar to obtain a better fit | Reject retrospective evidence repair |
| B14 | Release instant used without checking applicable Kalshi close and source publication cutoff | No eligible scientific test |

Fixture B01 is **not** a positive scientific fixture. It proves only
deterministic offline consistency of already preserved evidence.

## 5. Required operational proof before gate #05 can be closed

The future separately authorized checkpoint must obtain an independently
verifiable **off-host readback** of the original sealed source and receipt
without changing the source selection. Record exact bytes, GCS object
identifiers/version, digests, local read times and any access errors. Then
run the existing validator against those exact bytes in the **intended
release-day environment**, with no BLS schedule HTTP dependency.

Verify separate scheduled OS/cloud processes, permanent receipt write
semantics, clock/latency budgets and explicit failure isolation. Review the
final event-specific P12 protocol in an independent clean checkout at exact
HEAD/TREE with 4/4 CI; only then consider a prospective freeze. A valid BLS
receipt is **necessary but not sufficient**: independently proving Reuters
source/time/rights and Kalshi complete cohort/contract semantics remains
mandatory.

The absence of the original GCS/local source bytes, authoritative
event/rule evidence or approved cutoffs must produce **NO SCIENTIFIC TEST /
INCONCLUSIVE**, with no opportunistic source refetch or favorable backfill.

## 6. Permanent authority boundary

Research-only; `scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`, `p12_frozen=false`,
`production_influence=0`, `promotion_authority=NONE`, trading **NONE**.

This document only defines a reviewable *future binding requirement*. It
does **not** freeze November source selection or certify uninterrupted
capture, a market cohort, an economic advantage or a trade.
