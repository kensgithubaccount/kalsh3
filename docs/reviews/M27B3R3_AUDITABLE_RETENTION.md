# M27B.3R3 auditable retention design

Status: **superseded by `M27B3R4_RETENTION_LEDGER_REPAIR.md`.** An independent review of this
R3 design found 5 confirmed vulnerabilities: missing primary evidence was silently skipped
rather than failing closed; `retention-state.json` was trusted without ever reloading or
validating the receipts it referenced; the symlink/path-redirection guard resolved the path
before comparing it to its own resolved form, making the check a tautology that never raised;
the smoke projection used only the latest scan's delta, so a small scan following a large one
could erase the large scan's evidence from the projection; and the free-space check compared
current free space to the floor alone, ignoring the ledger's own known per-scan growth trend.
No pilot was ever started against this design -- see the R4 doc for the fail-closed repair and
what is actually true of the current code.

## Bound

The operational M27B.3 runner uses lossless zlib storage for canonical acquisition pages and
entity sources. Hashes are still SHA-256 over the canonical UTF-8 JSON, so compression cannot
alter identity, parsing, cursor/page material, semantic inputs, or exact observations. Existing
archives and non-M27B.3 callers remain readable and retain their prior uncompressed contract.

The retention ledger records every scan's database and SQLite `-wal`/`-shm` bytes, SHA-256,
per-scan delta, cumulative bytes, completeness, source files, and `production_influence=0`.
Receipts and state are fsynced and atomically renamed. No active evidence is deleted or updated.
SQLite remains `WAL` + `synchronous=FULL`, with append-only triggers and existing reconstruction
checks.

## Storage gate

The approved hard bound is 24 GiB for 96 scans (15-minute cadence), with an 8 GiB hard free-space
floor. Before each scan, the ledger checks the floor and prior projection. The first bounded smoke
sample measures the actual compressed growth of the active evidence files and projects that growth
across the configured scan count. A projection over 24 GiB raises `RetentionGateError` before the
next acquisition. **As shipped in this R3 revision, missing active evidence was silently skipped
(not failed closed) and `retention-state.json` was never reloaded or validated against its
receipts on reopen — both were confirmed vulnerabilities. See `M27B3R4_RETENTION_LEDGER_REPAIR.md`
for the fail-closed fix.** The bound is deliberately below the approximately 44 GiB available
capacity, leaving approximately 20 GiB of capacity margin at the approved limit.

## Safety and reconstruction boundaries

- incomplete refreshes append no structural observations or lifecycle events;
- partial source pages remain source evidence and are marked incomplete, never measured;
- page/cursor provenance, market/event identity, semantic inputs, quote timestamps, economics
  inputs, authority, and exact structural observations remain reconstructable through the existing
  archive verifier;
- content hashes bind compressed storage to the original canonical bytes;
- receipt publication is atomic and crash-safe; an orphan temporary file is not evidence;
- the runner remains public-read-only and research-only, with no authentication, execution, order,
  capital, signer, or risk authority imports.

Focused adversarial/storage/crash tests and the existing M27B.3 receipt tests pass. No live smoke
or pilot was run by this change. Independent review is required before any live smoke.
