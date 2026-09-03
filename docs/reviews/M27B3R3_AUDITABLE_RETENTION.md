# M27B.3R3 auditable retention design

Status: ready for independent review; no pilot was started.

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
next acquisition. Missing/corrupt state, missing active evidence, or an unavailable receipt also
fails closed. The bound is deliberately below the approximately 44 GiB available capacity,
leaving approximately 20 GiB of capacity margin at the approved limit.

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
