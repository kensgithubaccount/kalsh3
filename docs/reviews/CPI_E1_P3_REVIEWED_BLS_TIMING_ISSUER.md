# CPI-E1-P3 Reviewed BLS Historical Publication-Timing Parser

Status: **RESEARCH-ONLY STRUCTURAL PARSER**. P3 deterministically parses reviewed-shape
BLS CPI historical HTML. It does not establish acquisition provenance, does not issue
historical PIT publication authority, and promotes no gate.

## Canonical base and blocked head

Canonical base:

`7c6358b0da5f06ea9d021ed75bdc026f6a048ae6`

Independent review identified a raw-byte provenance blocker on exact head:

`71073698529969fc544b5dd52ecf6bf8eb63be66`

The blocked implementation allowed a caller-constructible `CPIHistoricalReleaseArtifact`
containing caller-authored HTML bytes at a P1-authorized locator to be converted into
canonical P2 publication evidence. That was unsafe because:

- P1 authorizes a reviewed source interface and locator shape;
- `CPIHistoricalReleaseArtifact` binds bytes, SHA-256, and structural artifact identity;
- neither property proves that the bytes were actually acquired from BLS;
- therefore caller bytes are not source provenance;
- content identity alone is not source or issuance authority.

The repair decision for this checkpoint is Option C: **P3 is a pure deterministic parser
only**. A later separately reviewed acquisition-bound checkpoint must combine reviewed
transport/acquisition provenance, P3 parsed timing, P1 source authority, and P2 issuance.
That future checkpoint is explicitly not implemented here.

## Dependencies

P1 authority module:

`services/forecasting/cpi_source_authority.py`

P2 structural artifact / PIT policy module:

`services/forecasting/cpi_pit_availability.py`

P3 parser module:

`services/forecasting/cpi_publication_timing.py`

P3 does not modify P1, P2 production semantics, or generic historical replay.

## Public parser API

The positive P3 API is now exactly:

`parse_cpi_publication_timing(artifact)`

The caller supplies one exact `CPIHistoricalReleaseArtifact`. There is no caller parameter
for publication instant, release date, release clock, timezone, assumed latency, replay
availability, or parser observation identity.

The return type is `ParsedCPIPublicationTiming`, a frozen/slots structural observation. It
is deliberately non-authoritative and contains only deterministic parsed facts and exact
bindings needed by a future provenance-bound issuer:

- exact P1 profile and historical source role;
- exact source locator;
- source artifact ID;
- raw artifact SHA-256;
- P1 authority and policy identities;
- normalized matched embargo statement;
- parsed local release date;
- parsed local release clock;
- explicit source timezone token;
- derived `America/New_York` publication instant;
- deterministic observation identity;
- parser policy/schema and text-normalization schema identities;
- `research_only=True`;
- `production_influence=0`.

`ParsedCPIPublicationTiming` is not canonical P2 publication evidence, not replay
`Availability`, not source provenance, not acquisition proof, not gate evidence, and not G4
proof. Ordinary structural construction of this non-authoritative value grants no trust.

## Restored P2 private boundary

P3 has zero access to P2 private publication issuance symbols. It does not reference:

- `_issue_actual_cpi_publication_evidence`;
- `_PUBLICATION_AUTHORITY_CAPABILITY`.

The architecture boundary is restored so those production symbols are permitted only in
their defining module:

`services/forecasting/cpi_pit_availability.py`

The existing P2 architecture regression is restored to its canonical no-production-
consumer form. The P3 regression independently asserts the same repository-wide boundary.
A future acquisition-bound issuer may deliberately reopen that boundary only under a
separate reviewed checkpoint.

## Narrow official-source shape

The previously reviewed parser grammar is preserved without broadening. A small read-only
parser-design inspection of official BLS archived CPI HTML observed older fixed-width or
preformatted headers with uppercase `A.M. (EST/EDT)` and later archived HTML using generic
`(ET)`. Those observations remain parser-design input only; they were not persisted as an
empirical corpus and are not gate evidence.

P3 recognizes only the complete official semantic:

`Transmission of material in this release is embargoed until ...`

The grammar requires:

- a valid 12-hour clock with minutes and `a.m.` or `p.m.`;
- explicit `EST`, `EDT`, or `ET` in parentheses;
- optional weekday;
- full English month, calendar day, and four-digit year.

Matching is case-insensitive only to accommodate reviewed historical capitalization and
whitespace may cross HTML/preformatted line boundaries. The grammar is not a general BLS
parser.

## Deterministic HTML normalization

P3 uses only the Python standard-library `HTMLParser`. It performs no network I/O and has
no LLM or fuzzy-matching path.

Raw artifact bytes are decoded deterministically with Latin-1. Script and style text are
excluded and remaining visible text receives deterministic whitespace normalization. The
parser requires actual `<html>` and `<body>` structure; detached plain text carrying an
embargo-looking sentence is rejected.

Normalization schema remains:

`cpi-e1-p3-html-visible-text-v1`

## P1 and artifact validation

Before parsing, P3 invokes canonical structural artifact validation. That fixes exact
runtime artifact type, CPI profile, historical-initial-release role, initial-release
vintage, reviewed locator, raw bytes/hash/artifact identity, P1 identities, research-only
posture, and zero production influence.

P3 independently resolves P1 and requires exact interface
`BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML`. Calendar, current API, PDF, TXT, PPI, and other
interfaces cannot enter the parser.

This is source-interface authorization only. It is intentionally not interpreted as proof
that the bytes were transported from the locator.

## Timestamp derivation and DST rules

The source statement itself supplies date, clock, and timezone token. The archive locator
date is used only after the statement date is parsed, solely as a consistency check.
Statement/locator conflict fails closed.

The derived instant always uses exact `ZoneInfo("America/New_York")` semantics.

- `EST` must resolve to UTC-05:00 on the stated historical local instant.
- `EDT` must resolve to UTC-04:00.
- generic `ET` is resolved using historical New York rules and must identify exactly one
  local candidate.
- spring-forward nonexistent local times are rejected.
- generic `ET` during the fall-back ambiguous hour is rejected.
- explicit `EST` or `EDT` during an otherwise ambiguous fall-back hour resolves only to the
  unique candidate matching the stated historical offset.

A present weekday must agree with the parsed date.

## Failure posture

P3 fails closed for:

- missing or incomplete embargo statement;
- date-only or time-only material;
- missing/unsupported timezone semantics;
- malformed date or clock;
- weekday/date conflict;
- impossible New York local time;
- ambiguous generic-ET local time;
- EST/EDT historical-offset mismatch;
- multiple distinct matching statements;
- statement/locator date conflict;
- detached/non-HTML bytes;
- wrong P1 profile, role, locator, interface, vintage, authority identity, or policy
  identity;
- changed raw bytes, hash, or artifact identity.

No timing is inferred from archive filename, current BLS state, ordinary release practice,
HTTP metadata, acquisition time, or a calendar page.

## Deterministic observation identity

The caller cannot choose the parser observation identity. P3 hashes the minimum reviewed
parse chain using repository `stable_hash`, binding:

- parser policy `cpi-e1-p3-reviewed-bls-publication-timing-parser-v1`;
- visible-text normalization schema;
- parsed-timing schema `cpi-e1-p3-parsed-publication-timing-v1`;
- exact source artifact ID;
- exact raw artifact SHA-256;
- SHA-256 of the normalized matched statement;
- parsed local date and clock;
- source timezone token;
- derived New York instant;
- exact P1 authority identity;
- exact P1 policy identity.

This identity proves deterministic parse equivalence for the supplied structural artifact.
It does not prove where the bytes came from and cannot grant issuance authority.

## Raw-byte attack regression

The repair intentionally preserves the ability to parse synthetic/caller-authored test
bytes when they are wrapped in a structurally valid P1-authorized artifact. The regression
then proves the security boundary:

- the result has exact runtime type `ParsedCPIPublicationTiming`;
- it is not canonical P2 publication evidence;
- P2 reconstructed-availability construction rejects it;
- P3 exposes no old issuance API;
- P3 source contains neither P2 private issuance symbol;
- P3 constructs no replay `Availability`.

Synthetic HTML remains parser-fixture material only.

## Adversarial coverage

Focused P3 tests cover:

- explicit EST;
- explicit EDT including the old preformatted/capitalized shape;
- generic ET;
- exact America/New_York `ZoneInfo` output;
- EST-on-EDT and EDT-on-EST rejection;
- generic ET fall-back ambiguity rejection;
- explicit EST/EDT fall-back candidate selection;
- no caller timing/latency/replay/identity parameters;
- date-only, time-only, and missing-timezone rejection;
- conflicting statements;
- malformed date/time;
- DST gap rejection;
- archive filename non-authority;
- locator/date conflict;
- calendar/API/PDF/TXT/wrong-product exclusion;
- exact P1 profile type;
- raw-byte/hash mutation rejection;
- revised-vintage mutation rejection;
- deterministic observation identity;
- detached-text rejection;
- fabricated-byte parser-only boundary;
- repository-wide P2 private-seam confinement;
- no I/O, acquisition, gate, model, economics, execution, risk, account, credential, or
  order dependencies.

## No acquisition and gate posture

P3 does not fetch BLS, enumerate KXCPI, create a historical corpus, retrieve Kalshi
historical rows, persist ArchiveManifest/DatasetManifest material, create settlement
labels, modify A3.2/A4, fit a model, calculate economics, or introduce execution authority.

Gate posture remains:

- G1 UNKNOWN
- G2 UNKNOWN
- G3 UNKNOWN
- G4 UNKNOWN
- G5 UNKNOWN
- G6 UNKNOWN

No gate promotion is authorized by P3.

## Changed scope

The repair remains confined to the existing P3 PR scope:

- `services/forecasting/cpi_publication_timing.py`;
- `tests/test_cpi_publication_timing.py`;
- `tests/test_cpi_pit_availability.py` only to restore the canonical private-seam guard;
- this review document.

No P1, generic replay, workflow, acquisition, model, economics, or execution file is
modified.

## Smallest next checkpoint

The immediate next checkpoint is independent delta review of this parser-only provenance
repair on PR #105. Do not merge until that review passes.

After P3 is reviewed and merged, the next implementation checkpoint is a separately
reviewed acquisition-bound issuer that proves transport/source provenance before combining
P3 parsed timing with P1 authority and P2 issuance. That checkpoint must be designed and
reviewed separately; empirical corpus acquisition does not begin from this PR.
