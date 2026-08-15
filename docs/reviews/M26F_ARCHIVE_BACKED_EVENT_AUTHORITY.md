# M26F Archive-Backed Event Authority Review

## Decision

M26F adds a local, append-only SQLite authority for reconstructable M2 universe
acquisitions. It is research-only. Production influence is exactly `0`, and no
execution, allocation, governance, scheduling, strategy, or promotion path is
connected to it.

Here, “authoritative” means verified against the bot's immutable acquisition
archive populated at the configured public-transport synchronization boundary.
It does not mean that Kalshi cryptographically signed the archive.

## Architecture audit

- `historical_replay.archive.ArchiveManifest` hashes raw and normalized page
  material but retains no reconstructable `Market` or `Event` source.
- M2 `Market.parse` and `Event.parse` deterministically calculate metadata/rules
  hashes and retain their complete raw dictionaries.
- `UniverseSynchronizer` previously collected decoded pages and stored only the
  current entity plus hash-version lists in `MemoryUniverseRepository`.
- M26C and related SQLite stores establish the local pattern: WAL, `FULL`
  synchronous writes, canonical payload plus SHA-256, redundant indexed columns,
  append-only triggers, and revalidation on read.
- M26E correctly treated caller observations as `UNVERIFIED`, rejected all
  `ARCHIVE_VERIFIED`/`PROVEN` constructions, and repeated that rejection at the
  manifest/assessment boundary because no archive verifier existed.

## Archive boundary and schema

`UniverseSynchronizer` may be configured with `UniverseObservationArchive`. Each
decoded transport response is handed to the archive before repository upsert.
The archive canonicalizes the complete page, invokes the real M2 parser itself,
and persists three append-only record families. The ordinary archive facade is
read/verification-only. The synchronizer owns an opaque, internally encapsulated
acquisition writer capability; routine callers holding a synchronizer reference
receive no normal writer attribute or accessor, and direct page insertion through
an archive instance is rejected. This is an application API boundary, not an OS
sandbox: arbitrary same-process introspection remains outside the integrity
boundary. The configured transport is itself trusted, so a deliberately configured
fake transport in a test is a legitimate acquisition source for that test.

- `archive_metadata`: stable authority identity and five explicit policy/schema
  versions.
- `acquisition_pages`: provider, endpoint, normalized parameters, run/page/cursor
  lineage, acquisition timestamp, complete canonical payload, hashes, parser and
  policy versions, success/failure, and zero production influence.
- `entity_observations`: page link, kind/ticker/event ticker, complete canonical
  parser source, source/metadata/rules hashes, source/acquisition timestamps,
  provenance and versions, and zero production influence.
- `acquisition_run_results`: one immutable terminal completeness result per sync
  run, including pages, records, malformed count, failure, and finish time.

Update/delete triggers protect metadata and historical rows in normal operation. Identical
identity/content replay is idempotent; different material under an existing
identity is rejected. There is no repair or destructive migration API.

A nonexistent path or intentional zero-byte path is initialized once. Every
pre-existing nonzero database is opened read-only first, integrity checked, and
compared with the exact expected tables, columns, declared constraints, indexes,
uniqueness, and trigger SQL before any writable connection is used. Missing or
altered schema objects, metadata, or policy versions raise `ArchiveError`; open
never recreates them. Existing partial, foreign, emptied, or damaged databases
are not mistaken for new archives.

## Identity, reconstruction, and historical selection

Canonical JSON uses sorted keys, compact separators, UTF-8, and no `default=str`.
UTC timestamps use fixed microseconds and `Z`. SHA-256 identities are domain
separated. Object insertion order cannot affect identity.

Every entity read independently validates the archive authority, every policy
version, redundant page/entity provenance, canonical JSON, page and source
hashes, page and observation identities, exact membership of entity source in
the page, a fresh M2 parse, parsed metadata/rules hashes, ticker relationships,
timestamps, and production influence. Any inconsistency raises `ArchiveError`.

Point-in-time lookup selects the latest acquisition timestamp at or before the
cutoff only after validating every candidate at that timestamp. Conflicting
content at the same timestamp fails closed. Later snapshots never replace an
earlier row.

Archive authority identity is stored in the database, not derived from its file
path. Different stores reject one another's receipts. A transactionally exact
copy retains the authority ID and is defined as a replica of the same logical
archive.

## M26E verification

`UniverseEventObservation.from_entities()` remains unverified and exposes no
authority selector. The archive factory accepts only a store plus Market/Event
observation IDs and an as-of time. It reconstructs both entities and creates a
content-addressed receipt binding store authority, observation/source identities,
tickers, parser-derived metadata, acquisition times, as-of, verification policy,
and zero influence.

Receipts are evidence material, not bearer authority. Binding and assessment
receive the archive dependency explicitly and re-read/revalidate the receipt
against that store. A bare self-consistent or object-model-forged receipt cannot
produce a trusted assessment. Wrong-store use, missing rows, future material, or
inconsistent content/hash material fails closed.

Complete M26C/M26D adapters remain downstream and preserve their existing source
identities. Every required market remains represented; absent evidence stays
unresolved. No historical proof is synthesized for observations predating M26F.

## Proven and not proven

M26F proves:

- acquisition provenance inside the bot's trusted universe synchronization
  boundary;
- reconstructable historical Market/Event snapshots;
- point-in-time exchange-event identity against that archive.

M26F does not prove:

- cryptographic attestation by Kalshi;
- statistical independence of different events;
- statistical significance, strategy superiority, or profitability;
- human-review eligibility from event count;
- production readiness or trading permission.

Therefore 100 or 500 verified markets under one Event count as one proven
exchange event. Distinct verified Event tickers count as distinct exchange
events, but `proven_independent_evidence_unit_count` remains unavailable and the
human-review gate remains ineligible, including at 50 event tickers.

## Operations and dashboard

Read-only status exposes availability, authority ID, page/Market/Event totals,
earliest/latest acquisition time, corruption state, and versions. There is no
admin mutation UI. Runtime dashboard wiring remains unchanged because no real
runtime archive is configured; it truthfully continues to report event evidence
as unavailable rather than presenting demo proof.

## Limitations

- Authority starts only with newly archived acquisitions; there is no M26C/M26D
  backfill.
- The archive authenticates local acquisition integrity, not exchange signatures.
- Ordinary UPDATE/DELETE is blocked, missing or altered schema guards are
  detected at reopen, and inconsistent row/hash rewrites are detected during
  reconstruction. A privileged actor able to coherently rewrite the complete
  trusted SQLite archive and recompute every unkeyed SHA-256 identity is outside
  this integrity model. Filesystem and SQLite access control remain part of the
  trusted computing base; these records are neither tamper-proof nor
  cryptographically attested by Kalshi.
- Universe-wide completeness is recorded separately from individual observation
  authentication and is not treated as statistical evidence.
