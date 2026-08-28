# CPI-E1-P2 Conservative Historical PIT Availability Policy

Status: **RESEARCH-ONLY POLICY PREREQUISITE**. This checkpoint establishes only the reviewed conservative historical-availability policy needed before empirical CPI acquisition. It does not acquire a CPI corpus and does not promote any empirical gate.

## Canonical base and P1 dependency

Canonical base: `5b6438d4d3e7155550085a1eedb612d562340ff7`.

Canonical P1 dependency: `services/forecasting/cpi_source_authority.py`.

Pinned P1 policy identity:

`fea29def84dcfc71f1ce86f268a25f038d02b8482a220e219fe88a2cea2bc3f1`

P2 accepts only the P1 profile:

- CPI-U;
- U.S. city average;
- all items / headline;
- seasonally adjusted;
- signed one-month percentage change;
- initial release.

Positive P2 publication timing is bound only to P1 role
`HISTORICAL_INITIAL_RELEASE_DOCUMENT` and an exact reviewed archived-release
locator. The P1 `RELEASE_CALENDAR` role remains scheduled-time evidence only and
cannot mint actual release timing.

## Canonical replay primitive audit

`services/historical_replay/domain.py` already contains the required generic
representation: `AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE`,
`AvailabilityQuality.CONSERVATIVE_ASSUMPTION`, explicit `source_publish_at`,
explicit `assumed_latency`, and the invariant
`replay_available_at == source_publish_at + assumed_latency`. No generic replay
change is required.

`services/historical_replay/replay.py` and `dataset.py` already gate visibility
through `replay_available_at`; P2 introduces no competing decision-cutoff or
dataset model. `archive.py` supplies only generic hashing/persistence precedent
and is not timestamp authority.

`services/production_weather_strategy/forecast_vintage.py` is the strongest
existing PIT issuance precedent: an ordinary exact source artifact is not
historical authority until separately bound to capability-issued publication
proof. P2 reuses this authority split, not weather-domain authority.

`services/document_intelligence/evidence.py` and `models.py` are generic cited
evidence structures, not BLS timing issuers. `services/forecasting/macro.py`
contains caller-authored release timestamps and remains structural/model-fixture
precedent only.

ADR 0002 already requires reconstructed historical availability to preserve
actual bot ingest independently and to use explicit conservative replay delay.

No second generic replay architecture, publication registry, or caller-selectable
trust registry is introduced.

## Positive P2 issuance prerequisites

A positive reconstructed availability requires all of the following:

1. an exact `CPIHistoricalReleaseArtifact`;
2. the exact P1 CPI profile;
3. P1 role `HISTORICAL_INITIAL_RELEASE_DOCUMENT`;
4. an exact reviewed BLS archived-release locator;
5. canonical P1 policy identity and resolved P1 authority identity;
6. exact raw archived-release bytes and SHA-256;
7. content-addressed artifact identity;
8. separately capability-issued `CPIActualPublicationEvidence`;
9. exact timing semantics `ACTUAL_RELEASE_OR_EMBARGO`;
10. an independently evidenced exact BLS release/publication/embargo instant;
11. canonical `America/New_York` `zoneinfo` semantics for that instant;
12. exact timing-evidence identity bound into publication evidence;
13. exact artifact/hash/P1 bindings between publication evidence and source artifact;
14. `actual_bot_ingest_at >= replay_available_at`.

The public positive builder accepts only the exact artifact and issued
publication evidence. It has no caller parameter for `source_publish_at`,
`replay_available_at`, or `assumed_latency`.

## UNKNOWN conditions

Insufficient evidence remains UNKNOWN, not BLOCKED. This includes date-only
release evidence, absent exact actual timing, scheduled-only timing,
delayed/rescheduled releases without an exact actual instant, absent/unusable P1
authority, ambiguous/unsupported locator, missing artifact identity, conflicting
or unusable proof, current/revised-only material, source/hash mismatch, and
unsupported CPI profiles.

`build_unknown_cpi_availability` creates generic
`AvailabilityBasis.UNKNOWN` with no `source_publish_at` and no
`replay_available_at`. It grants no causal-replay eligibility.

## Exact timing evidence boundary

A caller-authored datetime is not publication authority. Ordinary construction
of `CPIActualPublicationEvidence` is rejected. The authority-bearing proof is
capability-issued and bound to the exact archived source artifact, raw hash,
source locator, P1 authority identity, P1 policy identity, and a separate timing
evidence identity.

The archive filename date, reference month, normal 8:30 practice, current HTTP
metadata, current API state, acquisition time, title/category, ticker/family, and
content hash alone cannot issue positive proof.

## Scheduled versus actual release

`RELEASE_CALENDAR` remains eligible only for scheduled timing under P1.
`CPIPublicationTimingSemantics.SCHEDULED_RELEASE` is explicitly rejected by the
positive issuer. Therefore an original scheduled instant cannot become actual
release truth merely because it equals the normal or observed-looking time.

For a delayed or rescheduled release, the old scheduled instant is not used.
An independently evidenced exact actual release/publication/embargo instant is
required; otherwise availability remains UNKNOWN.

## Conservative replay boundary and DST

For positive exact timing:

`source_publish_at = independently evidenced exact actual BLS instant`

`replay_available_at = final representable instant of that actual local BLS release date in America/New_York`

At Python datetime precision this is local `23:59:59.999999` for the actual
release date, constructed with `ZoneInfo("America/New_York")`.

No fixed UTC offset is accepted as publication-timing semantics. `zoneinfo`
therefore applies the correct EST or EDT offset for the date. January and August
examples are covered by tests.

`assumed_latency` is derived deterministically as:

`replay_available_at - source_publish_at`

The caller cannot supply it.

## Actual bot ingest invariant

Positive reconstruction additionally requires:

`actual_bot_ingest_at >= replay_available_at`

The artifact records the actual ingest timestamp independently, normalized to
UTC. P2 never backdates it, never silently sets it to replay time, and never uses
it as publication proof.

## Initial versus revised data

The P2 source artifact is fixed to `INITIAL_RELEASE` and to the P1 archived
initial-release document role. Revision/current-vintage labels are not
caller-selectable. Exact raw bytes and content-addressed artifact identity are
immutable inputs to publication proof, so a later/revised artifact cannot
overwrite the identity to which an earlier timing proof was issued.

This is not a general CPI revision platform. Later seasonal-factor revisions,
current API observations, and later publications remain separate future
observations/lineage.

## Current BLS API posture

The current BLS Public Data API is outside positive P2 authority. The source
artifact constructor reuses the P1 archived-release locator resolver, so API
locators cannot enter the positive path. API data cannot establish the original
historical artifact, original release/embargo instant, revision-zero value, or
PIT availability.

## Issuance, immutability, and validation

P2 source artifacts are frozen, slotted, content-addressed, P1-bound structural
objects. Publication evidence is frozen, slotted, non-ordinary-constructible,
capability-issued, content-addressed, research-only, and zero-production-
influence.

Consumers revalidate exact runtime types, exact `StrEnum` identities, P1
identity, locator, raw hash, artifact identity, timing semantics, timezone,
content identity, and issuer fingerprint before building availability.
`dataclasses.replace`, direct reconstruction, semantic `object.__setattr__`
mutation, mutate-plus-rehash, plain strings, equal-valued noncanonical types, and
cross-artifact proof reuse do not create valid positive evidence.

## What P2 may prove

P2 may prove only that one exact P1-authorized archived CPI release artifact,
with separately issued exact actual publication timing proof, is conservatively
eligible for replay at the end of its actual BLS release date, subject to the
actual-ingest invariant.

That output is compatible with later causal replay requiring
`replay_available_at <= decision_cutoff`.

## What P2 does not prove

P2 does not establish G1 domain/settlement binding, G2 empirical source coverage,
G3 correction-safe Kalshi settlement truth, G4 PASS for an empirical corpus, G5
evidence-unit sufficiency, or G6 economics. It does not modify A3.2/A4, acquire
historical observations, create Archive/Dataset manifests, create Kalshi labels,
group siblings, fit models, calculate edge/economics, model fills, or introduce
account/credential/risk/order/execution authority.

Gate posture remains:

- G1 UNKNOWN
- G2 UNKNOWN
- G3 UNKNOWN
- G4 UNKNOWN
- G5 UNKNOWN
- G6 UNKNOWN

A policy prerequisite is not empirical evidence.

## Adversarial test inventory

The focused P2 tests cover the reachable interfaces for: ordinary positive-proof
construction rejection; absence of caller publication/replay/latency authority;
date-only UNKNOWN; exact instant requirement; naive/fixed-offset rejection;
EST/EDT end-of-day handling; scheduled-vs-actual separation; calendar/API
exclusion; acquisition-time non-substitution; ingest-before-boundary rejection
and equality allowance; exact initial artifact identity; wrong profile and
related-series rejection; P1 policy/authority/locator binding; cross-artifact and
hash mismatch rejection; direct/replacement/mutation/rehash resistance; exact
enum/runtime types; exact reconstructed basis/quality/arithmetic; deterministic
derived latency; and absence of I/O, gate, modelability, economics, execution,
account, credential, risk, or order dependencies.

No speculative interfaces are added merely to create additional attack tests.

## Changed scope

P2 changes only:

- `services/forecasting/cpi_pit_availability.py`
- `tests/test_cpi_pit_availability.py`
- `docs/reviews/CPI_E1_P2_CONSERVATIVE_PIT_POLICY.md`

No workflows or generic replay primitives are modified.

## Smallest next checkpoint

After focused independent review and merge of P2, resume CPI-E1 with bounded
empirical acquisition that binds exact Kalshi contract/domain evidence, exact BLS
archived initial-release artifacts, correction-safe settlement evidence where
available, and P2 timing evidence. G4 may move only from actual acquired and
reviewed evidence; P2 alone never promotes it.

Do not begin M28D-R2, KU-A5, A3.2/A4 integration, or empirical modeling in P2.
