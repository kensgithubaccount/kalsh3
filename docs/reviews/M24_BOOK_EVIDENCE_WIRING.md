# M24 Part A — Offline Canonical Book-Evidence Wiring

## Scope reviewed

Part A wires already-parsed book snapshots and deltas through the tracked
`SubscriptionManager` into immutable `BookEvidenceObservation` rows and a dedicated
append-only SQLite research store. It adds no live runner, network client, credentials,
execution path, risk authorization, order semantics, or learning connection.

## Deterministic and fail-closed controls

- A real UUID epoch, exact non-negative SID/sequence/exchange index, explicit ticker
  mapping, and aware UTC receipt and availability timestamps are required.
- Availability is captured after manager application. `BookView.observed_at` and
  `ingested_at` are never treated as local receipt or availability.
- Only current, fresh books with a usable side persist. Gaps, rejected deltas, and stale
  views produce no evidence.
- Snapshot exchange time is `None`; genuine delta exchange time is retained independently.
- A canonical SHA-256 fingerprint captures parsed source-event semantics (including complete
  snapshot levels or genuine delta exchange time) without raw-dict formatting or floats.
- Preflight lookup occurs before manager mutation. Matching fingerprints at the same
  epoch/SID/sequence/ticker return the existing evidence; differing fingerprints fail closed.
  Reused SID/sequence/ticker values remain valid in a new connection epoch.
- Receipt and availability are semantically separate, nondecreasing timestamps. Equality is
  valid when clock resolution reports the same wall-clock instant.
- SQLite preserves decimals as text, enforces zero influence, and blocks update/delete.

No PostgreSQL migration was added: tracked conventions permit service-local SQLite stores,
and this is offline research persistence rather than a production schema.

## Verification

- M24 focused tests: **40 passed**.
- Ruff lint and format: **passed**.
- Full suite: **447 passed, 46 pre-existing macOS signer portability failures** because
  tracked signer code uses Linux-only `/proc/self/fd` or `os.memfd_create`; Part A does not
  modify signer, credential, or execution code.

Coverage includes accepted snapshots/deltas, pre-mutation replay and collision handling,
gap/recovery non-mutation, epoch reuse, gaps/rejections/staleness, exchange-index and epoch
validation, reconnect provenance, a concurrent first-insert race, exact Decimal replay,
equal timestamps, SID/sequence, price/size, database CURRENT/zero-influence constraints,
append-only triggers, and architecture isolation.

## Safety conclusion

Live collection remains OFF. The research-only boundary and exactly zero production
influence remain intact. No edge-decay rows or learning connection were added, and no
production execution, risk, canary, autonomy, supervised execution, signer, or credential
code changed.
