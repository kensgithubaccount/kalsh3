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
- Canonical hashing supplies deterministic IDs. A unique logical identity rejects changed
  payloads at the same epoch/SID/sequence/ticker.
- SQLite preserves decimals as text, enforces zero influence, and blocks update/delete.

No PostgreSQL migration was added: tracked conventions permit service-local SQLite stores,
and this is offline research persistence rather than a production schema.

## Verification

- M24 focused tests: **31 passed**.
- Ruff lint and format: **passed**.
- Existing realtime tests: **11 passed, 1 pre-existing macOS signer failure** because
  tracked code uses `/proc/self/fd`; Part A does not modify signer or credential code.

Coverage includes accepted snapshots/deltas, gaps/rejections/staleness, exchange-index and
epoch validation, reconnect provenance, duplicates/collisions/restarts/concurrency, exact
Decimal replay, timestamps, SID/sequence, price/size, database zero influence, append-only
triggers, and architecture isolation.

## Safety conclusion

Live collection remains OFF. The research-only boundary and exactly zero production
influence remain intact. No edge-decay rows or learning connection were added, and no
production execution, risk, canary, autonomy, supervised execution, signer, or credential
code changed.
