# CPI-E1-P3 Reviewed BLS Historical Publication-Timing Issuer

Status: **RESEARCH-ONLY SEMANTIC ISSUER PREREQUISITE**. P3 is the smallest reviewed
production bridge from canonical P1 source authority to canonical P2 historical PIT
publication evidence. It performs no empirical corpus acquisition and promotes no gate.

## Canonical base and dependencies

Canonical base:

`7c6358b0da5f06ea9d021ed75bdc026f6a048ae6`

P1 authority module:

`services/forecasting/cpi_source_authority.py`

Canonical P1 policy identity remains:

`fea29def84dcfc71f1ce86f268a25f038d02b8482a220e219fe88a2cea2bc3f1`

P2 PIT module:

`services/forecasting/cpi_pit_availability.py`

P3 issuer/parser module:

`services/forecasting/cpi_publication_timing.py`

P3 does not modify P1 or generic historical replay.

## Authority boundary

The public P3 positive API is exactly:

`issue_cpi_publication_evidence(artifact)`

The caller supplies only one exact `CPIHistoricalReleaseArtifact`. There is no caller
parameter for `source_publish_at`, release date, release clock time, timezone, assumed
latency, replay availability, timing semantics, or timing-evidence identity.

P3 revalidates the P2 artifact before parsing. That revalidation fixes the exact runtime
type, P1 profile, P1 historical-initial-release role, initial-release vintage, reviewed
locator, raw bytes, SHA-256, artifact identity, P1 authority identity, P1 policy identity,
research-only posture, and zero production influence.

P3 then independently resolves the same P1 authority and requires exact interface
`BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML`. Calendar, API, PDF, TXT, PPI, and other locators
cannot enter positive issuance.

## Narrow official-source shape audit

Before implementation, a small read-only parser-design inspection was made of official BLS
archived CPI HTML. It was not persisted as a corpus and is not empirical gate evidence.
Observed official header shapes included:

- older fixed-width/preformatted archived HTML where the embargo phrase is split across
  lines and uses uppercase forms such as `UNTIL 8:30 A.M. (EST)` followed by weekday and
  date;
- archived HTML using `8:30 a.m. (EDT)` followed by an optional weekday and exact date;
- later archived HTML using generic `8:30 a.m. (ET)` with either `Month D, YYYY` or
  `Weekday, Month D, YYYY`.

Those observations are parser-design input only. P3 does not persist, enumerate, or claim
coverage over an empirical historical CPI corpus.

## Deterministic HTML normalization

P3 uses only the Python standard-library `HTMLParser`; there is no network or LLM path.

The exact raw artifact bytes are decoded with deterministic Latin-1 byte preservation.
The reviewed timing grammar is ASCII, so this preserves the authoritative timing bytes
without depending on historical page charset declarations. HTML character references are
decoded by `HTMLParser`; script and style text are excluded; remaining visible text is
collapsed only by deterministic whitespace normalization.

The parser requires an actual HTML document shape containing both `<html>` and `<body>`.
Detached plain text carrying an embargo-looking sentence is rejected.

Normalization is bound into timing identity by schema:

`cpi-e1-p3-html-visible-text-v1`

## Accepted actual timing statement

P3 recognizes only the bounded official release-header semantic:

`Transmission of material in this release is embargoed until ...`

Matching is case-insensitive only to accommodate observed historical BLS capitalization.
Whitespace may cross HTML/preformatted line boundaries. The grammar requires, in one
complete statement:

- a valid 12-hour clock with minutes and `a.m.` or `p.m.`;
- an explicit timezone token in parentheses: `EST`, `EDT`, or `ET`;
- optional weekday text;
- full English month name, calendar day, and four-digit year.

If a weekday is present, it must agree with the parsed calendar date.

P3 does not treat generic prose containing `8:30`, scheduled-release language, archive
filename dates, ordinary BLS practice, HTTP metadata, acquisition time, or current BLS
state as publication authority.

## Timestamp derivation

After one complete statement is found, P3 derives the local calendar date and local clock
from that statement alone.

The archive locator is parsed only after the statement date exists. The locator date is a
consistency check, never timestamp authority. A statement/locator date conflict fails
closed.

The final `source_publish_at` is always an aware datetime backed by exact
`ZoneInfo("America/New_York")`.

For `EST`, P3 requires that the stated local instant resolves under historical New York
rules to UTC-05:00. For `EDT`, it requires UTC-04:00. If the token conflicts with the
historical offset on that date, issuance fails.

For generic `ET`, P3 resolves the exact local date/time under date-aware New York rules.
An impossible spring-forward local time is rejected. A locally ambiguous generic-ET time
is also rejected because the source would not identify one exact instant.

Explicit EST/EDT can select only a unique candidate with the corresponding historical
offset.

## Ambiguity and failure posture

P3 fails closed when exact source bytes do not establish one reviewed boundary. This
includes:

- no complete embargo statement;
- date-only or time-only material;
- absent or unsupported timezone semantics;
- malformed date or clock;
- weekday/date conflict;
- impossible New York local time;
- ambiguous generic-ET local time;
- EST used when New York is on EDT or vice versa;
- multiple distinct matching embargo statements;
- statement/locator date conflict;
- detached/non-HTML bytes;
- wrong P1 profile, role, locator, interface, vintage, authority identity, or policy
  identity;
- changed raw bytes, SHA-256, artifact identity, or content identity.

P3 never invents a timestamp. Missing or ambiguous proof remains insufficient evidence.

## Deterministic timing-evidence identity

The caller cannot choose `timing_evidence_identity`.

P3 computes it with the repository `stable_hash` over the minimum reviewed evidence chain:

- P3 policy version `cpi-e1-p3-reviewed-bls-publication-timing-v1`;
- visible-text normalization schema;
- timing-evidence schema `cpi-e1-p3-publication-timing-evidence-v1`;
- exact source artifact ID;
- exact raw artifact SHA-256;
- SHA-256 of the exact normalized matched embargo statement;
- parsed local date;
- parsed local clock;
- parsed timezone token;
- normalized `America/New_York` publication instant;
- exact P1 authority identity;
- exact P1 policy identity.

The exact raw artifact remains available on `CPIHistoricalReleaseArtifact`, so the matched
statement and identity are independently reproducible. A content hash alone does not grant
authority; the hash is only an identity input to the reviewed P3 -> P2 issuance chain.

## P2 issuance binding

P3 is the first and only reviewed production consumer of P2's private issuance seam:

- `_issue_actual_cpi_publication_evidence`
- `_PUBLICATION_AUTHORITY_CAPABILITY`

P3 supplies only parser-derived values and fixed
`CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO`. The canonical output remains
P2's existing `CPIActualPublicationEvidence`; P3 introduces no parallel publication-proof
type.

After issuance, P3 immediately invokes P2 publication-evidence revalidation before
returning the proof.

## Private-seam architecture boundary

The existing repository guard in `tests/test_cpi_pit_availability.py` is deliberately
updated. Exactly two production files may contain either P2 private symbol:

1. the defining P2 module;
2. `services/forecasting/cpi_publication_timing.py`.

Every other `services/**/*.py` remains fail-closed. The P3 tests additionally assert that
both private symbols are actually present in P3 and absent from all other production
modules. A later acquisition layer must call P3's public API and must not receive the P2
capability.

## Adversarial coverage

Focused P3 coverage includes:

- positive explicit EST;
- positive explicit EDT including old preformatted/capitalized source shape;
- positive generic ET;
- exact America/New_York `ZoneInfo` output;
- EST-on-EDT and EDT-on-EST rejection;
- no caller timing/latency/replay parameters;
- date-only, time-only, and missing-timezone rejection;
- conflicting statements;
- malformed date and malformed time;
- impossible DST local time;
- archive filename non-authority;
- statement/locator date conflict;
- calendar, current API, PDF, TXT, and wrong-product locator exclusion;
- exact P1 profile type enforcement;
- changed raw bytes/hash rejection before parsing;
- revised-vintage mutation rejection;
- caller inability to select timing-evidence identity;
- identity change with authoritative statement change;
- deterministic same-artifact semantics and identity;
- detached plain-text rejection;
- exact private-seam allowlist;
- no I/O, acquisition, gate, model, economics, execution, risk, account, credential, or
  order dependencies.

Synthetic/minimal HTML fixtures are marked `TEST FIXTURE ONLY` and are not empirical proof.

## No empirical acquisition

P3 does not enumerate KXCPI markets, download a historical BLS corpus, fetch historical
Kalshi rows, persist ArchiveManifest or DatasetManifest material, create settlement labels,
create G1-G6 receipts, modify A3.2/A4, fit a model, calculate economics, or introduce
execution authority.

## Gate posture

P1 source governance plus P2 conservative PIT policy plus P3 parser/issuer capability are
still prerequisites, not an empirical corpus.

Gate posture remains:

- G1 UNKNOWN
- G2 UNKNOWN
- G3 UNKNOWN
- G4 UNKNOWN
- G5 UNKNOWN
- G6 UNKNOWN

No gate promotion is authorized by P3.

## Changed scope

P3 is limited to:

- `services/forecasting/cpi_publication_timing.py`;
- `tests/test_cpi_publication_timing.py`;
- `tests/test_cpi_pit_availability.py` only for the deliberate private-seam allowlist;
- `docs/reviews/CPI_E1_P3_REVIEWED_BLS_TIMING_ISSUER.md`.

No P1, generic replay, workflow, acquisition, model, economics, or execution file is
modified.

## Smallest next checkpoint

The smallest next checkpoint is focused independent review of this P3 issuer and its exact
private-seam expansion. After review and merge, empirical CPI acquisition may begin only as
a separate checkpoint using P3's public API. P3 itself must not acquire or promote a
corpus.
