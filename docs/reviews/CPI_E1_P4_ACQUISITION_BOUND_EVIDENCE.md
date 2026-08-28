# CPI-E1-P4 — Acquisition-Bound BLS Source Evidence & Timing Issuance

## Canonical checkpoint

- Canonical base: `70941cb03e246f422c208c9aebc997649660754f`.
- Branch: `cpi-e1-p4-acquisition-bound-bls-evidence`.
- Scope: establish one trustworthy acquisition-to-P2 issuance mechanism for the exact P1-reviewed BLS archived CPI HTML source.
- This checkpoint does not establish empirical CPI corpus completeness, historical coverage, model skill, economics, promotion authority, or trading authority.

## Canonical dependencies

P4 composes three already-canonical checkpoints without changing their semantic authority:

1. **P1 — source authority.** `services/forecasting/cpi_source_authority.py` remains the source/interface/locator authority. Positive P4 acquisition re-resolves the exact canonical CPI profile, `HISTORICAL_INITIAL_RELEASE_DOCUMENT` role, `BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML` interface, exact locator, P1 policy identity, and P1 authority identity.
2. **P3 — parser only.** `services/forecasting/cpi_publication_timing.py` remains a deterministic non-authoritative parser. It receives a canonical structural `CPIHistoricalReleaseArtifact` and derives the historical release/embargo instant only from the exact release bytes.
3. **P2 — publication evidence and replay policy.** `services/forecasting/cpi_pit_availability.py` remains the sole definition of `CPIActualPublicationEvidence`, its validator, and the conservative reconstructed `Availability` policy.

The positive chain is therefore:

`reviewed BLS HTTPS GET -> P4 acquisition evidence -> P1 revalidation -> P2 structural artifact -> P3 parse -> P4 binding -> P2 private issuance -> P2 validation -> P2 conservative availability`.

## Existing acquisition/evidence primitives audited

The implementation audited the requested canonical patterns before introducing P4.

### `services/market_universe/public_read.py`

Reusable ideas:

- fixed HTTPS origin;
- stdlib `http.client.HTTPSConnection` transport;
- explicit timeout and response-size bounds;
- exact response body/status/observation capture;
- no credentials or authenticated request authority.

Not directly reusable:

- its transport is intentionally fixed to the Kalshi public API origin and Kalshi public-read paths. Reusing it for BLS would weaken rather than preserve its reviewed source boundary.

### `services/production_weather_strategy/forecast_vintage.py`

Reusable ideas:

- capability-gated authoritative evidence construction;
- exact artifact/hash binding;
- deterministic evidence identity;
- revalidation before downstream use.

Source-specific differences:

- forecast-vintage evidence governs weather forecast vintages rather than BLS publication provenance.

### Canonical M28B/M28C acquisition-bound paths

Reusable ideas:

- semantic parsing is not acquisition authority;
- fixed-origin public acquisition is a separate trust step;
- exact response hashes bind downstream evidence;
- capability/registry validation rejects direct reconstruction and mutation;
- present-day acquisition time does not by itself establish historical point-in-time availability.

Source-specific differences:

- M28B is Kalshi public settled-market evidence;
- M28C includes NOAA/NCEI climate evidence and weather-specific vintage semantics;
- P4 is limited to the single P1-reviewed BLS archived CPI HTML interface.

### `services/document_intelligence/evidence.py` and `models.py`

Reusable ideas:

- immutable evidence-oriented data structures and explicit source identity.

Not reused as transport:

- document-intelligence evidence is not a generic network provenance transport and does not prove that exact bytes were acquired from BLS.

### `services/historical_replay/*`

P4 does not create a second replay policy. It composes the existing P2 builder, which already returns the canonical historical-replay `Availability` contract.

## Transport authority

P4 adds the source-specific transport in `services/forecasting/cpi_source_acquisition.py`.

The public acquisition API accepts exactly one caller input: the source locator. It does not accept response bytes, status, acquisition time, method, P1 IDs, source publication time, replay time, or timing evidence identity.

Positive transport is fixed to:

- scheme: `https`;
- host: `www.bls.gov`;
- method: `GET`;
- locator: the exact P1-reviewed archived CPI HTML locator;
- credentials: none;
- `Authorization`: absent;
- cookie/session authority: absent;
- redirects: not followed (`http.client.HTTPSConnection` performs the direct request and P4 accepts only the requested reviewed locator);
- timeout: bounded by the P4 transport policy;
- body: read with an explicit maximum plus one byte and rejected if oversized;
- success status: exactly HTTP 200 for positive evidence.

Non-success responses fail closed and cannot enter the positive P2 chain.

## Source-origin proof and exact response binding

`CPIBLSAcquisitionEvidence` binds one successful reviewed response to:

- exact CPI profile;
- exact source role and interface;
- exact source locator;
- reviewed BLS origin;
- exact HTTP method and status;
- exact raw response bytes;
- SHA-256 of those exact bytes;
- exact byte count;
- actual acquisition timestamp;
- bounded transport policy identity;
- acquisition schema identity;
- canonical P1 authority identity;
- canonical P1 policy identity;
- deterministic acquisition evidence/content identity.

The validator re-resolves P1, recomputes the raw-body hash and evidence identity, verifies exact runtime types, and checks the issuer registry. Direct construction, `dataclasses.replace`, `object.__new__`, `object.__setattr__` mutation, mutate-and-rehash attempts, and equal-valued foreign runtime types do not validate as positive acquisition evidence.

A valid P1 locator alone is not acquisition proof. A raw-body hash alone is not acquisition proof. Caller-provided bytes cannot enter the public acquisition API.

## Diagnostic HTTP metadata

`Content-Type`, `Date`, `ETag`, and `Last-Modified` may be retained only as bounded diagnostic metadata. They are not historical publication timing authority and are excluded from the P3 publication-time derivation.

## P1 revalidation

Every positive acquisition re-resolves canonical P1 authority from the exact requested locator. P4 requires the exact CPI-U seasonally adjusted month-over-month initial-release profile, historical-initial-release-document role, archived BLS CPI HTML interface, locator, P1 policy identity, and P1 authority identity.

Caller-stamped P1 IDs are never accepted by a public P4 interface.

## P3 parser binding

`services/forecasting/cpi_evidence_issuer.py` rebuilds the canonical structural `CPIHistoricalReleaseArtifact` from the exact acquired response bytes and the actual P4 acquisition timestamp. P2's artifact validator is run immediately.

P4 then calls `parse_cpi_publication_timing(artifact)` internally. The P3 output must match the exact acquisition on:

- CPI profile;
- source role;
- source locator;
- raw-body SHA-256;
- structural artifact identity;
- P1 authority identity;
- P1 policy identity;
- canonical P3 parser policy/schema identity.

Cross-response or cross-artifact parser-output reuse fails closed. P3 receives no publication authority and remains parser-only.

## P2 issuance binding

Only after successful acquisition validation, P1 revalidation, structural artifact validation, P3 parsing, and exact acquisition/parser binding does P4 access P2's reviewed private issuance seam.

The caller cannot supply `source_publish_at`. P4 passes only the P3-derived `publication_instant`.

The caller cannot supply `timing_evidence_identity`. P4 derives it deterministically from the acquisition evidence identity, transport policy, exact locator/body hash/acquisition time, structural artifact identity, P3 parser identities/observation identity/publication instant, and P1 identities.

The resulting object is canonical `CPIActualPublicationEvidence` and is immediately revalidated by the P2 validator before availability is constructed.

## P2 private-seam boundary

Before P4, canonical P2 tests required zero production consumers of:

- `_issue_actual_cpi_publication_evidence`;
- `_PUBLICATION_AUTHORITY_CAPABILITY`.

P4 deliberately expands that architecture boundary to exactly two production locations:

- `services/forecasting/cpi_pit_availability.py`;
- `services/forecasting/cpi_evidence_issuer.py`.

P3 is forbidden from those names. Acquisition modules and scripts/runners are also forbidden from direct P2 capability access. Live acquisition calls the reviewed P4 public API; it does not receive the P2 capability.

## Three distinct times

P4 intentionally preserves three independent temporal meanings.

### 1. Historical source publication/embargo instant

This is parsed by P3 from the historical release document itself. It becomes P2 `source_publish_at` only after the full P4 acquisition/parser binding validates.

### 2. Historical reconstructed replay availability

This is P2 policy, not transport time. P2 conservatively sets replay availability to the final instant of the actual historical release date in `America/New_York` and derives assumed latency deterministically.

### 3. Actual P4 acquisition timestamp

This records when this P4 transport actually acquired the archived response. A present-day acquisition can therefore prove where the exact bytes came from without pretending that the present-day acquisition happened at the historical publication instant.

The P4 acquisition timestamp is also not required to equal the reconstructed replay time. It must only satisfy the existing P2 constraint that actual ingest is not earlier than the conservative replay boundary for a positive reconstructed availability.

## Availability semantics

P4 composes the existing P2 builder unchanged. Positive output preserves:

- `AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE`;
- `AvailabilityQuality.CONSERVATIVE_ASSUMPTION`;
- replay availability at the end of the actual historical release date in `America/New_York`;
- deterministically derived assumed latency;
- `actual_bot_ingest_at >= replay_available_at`.

No second replay policy was added.

## Failure and UNKNOWN behavior

Any transport, origin, status, size, P1, artifact, P3, binding, P2 issuance, or P2 validation failure closes the positive path. P4 does not infer publication timing from acquisition time, URL filename, HTTP `Date`, `Last-Modified`, `ETag`, or `Content-Type`.

Existing P2 UNKNOWN construction remains available to callers that cannot establish positive reviewed evidence. P4 does not redefine UNKNOWN semantics.

## Deterministic CI and bounded public acquisition

Mandatory CI uses only offline fake-connection fixtures. It does not issue uncontrolled live requests to BLS.

No bounded live BLS proof is claimed by this implementation report unless separately executed and evidenced outside mandatory CI. The implementation environment used for this checkpoint did not have general outbound DNS/network access, so no live BLS acquisition was used as verification evidence.

This absence does not weaken deterministic mechanism tests and does not constitute a historical-coverage claim.

## Gate state and empirical limits

P4 does not mutate empirical research gates. Throughout this checkpoint:

- G1 = UNKNOWN
- G2 = UNKNOWN
- G3 = UNKNOWN
- G4 = UNKNOWN
- G5 = UNKNOWN
- G6 = UNKNOWN

P4 proves an evidence-construction mechanism only. It does not prove that an adequately complete CPI release corpus exists, does not enumerate all KXCPI markets, does not fit a model, and does not establish after-cost edge.

## Exact next empirical checkpoint

The smallest next checkpoint is a separately reviewed, bounded empirical CPI acquisition/coverage checkpoint that uses the P4 public API to acquire a declared small sample of archived CPI releases, records exact acquisition evidence and deterministic issuance results, and begins explicit corpus-coverage accounting without yet claiming completeness or mutating G1-G6.

It must remain separate from this P4 mechanism checkpoint and must not begin a full empirical CPI corpus acquisition implicitly.
