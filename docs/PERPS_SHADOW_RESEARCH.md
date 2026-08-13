# Perps Shadow Research Layer

This layer is intentionally research-only.

It adds immutable data contracts for:

- `exchange_index` as a first-class routing/partition key.
- Separate long, short, and legacy/symmetric leverage estimates.
- Portfolio-level margin observations with nullable risk fields.
- Funding, mark, and reference values when supplied by upstream data.
- Economic latency / edge-decay measurements from signal creation through a
  hypothetical send timestamp.
- A hard `production_influence == 0` invariant.
- Canonical snapshot/delta book evidence with explicit epoch, sequence,
  exchange-index, receipt, exchange, and availability provenance.
- A dedicated append-only SQLite evidence store for deterministic offline replay.

It deliberately adds **no**:

- network client,
- API credential handling,
- order construction,
- order placement/amend/cancel calls,
- sizing,
- execution routing,
- production strategy-weight mutation.

The caller is responsible for supplying already-observed payloads and timestamps.
Unknown upstream fields remain in each observation's recursively read-only `raw`
payload for replay and schema migration. JSON-like mappings and containers are
recursively copied and frozen; unsupported custom or mutable value types are
rejected. All timestamps must be timezone-aware and are normalized to UTC.
Edge-decay artifacts retain every observed value and reject stored edges or
latencies that contradict their inputs.

## Suggested partition key

Persist observations under:

`{subaccount, exchange_index, ticker, observed_at}`

Market metadata that is not account-specific can omit subaccount.

## Edge-decay interpretation

For each research candidate, capture the same market/reference value at:

1. signal creation,
2. signal availability,
3. model decision,
4. hypothetical send.

The layer records edge remaining at each stage and stage-to-stage latency. It
does not decide whether the candidate should be traded.

## Canonical book evidence (M24 Part A)

`ReadOnlyBookEvidencePipeline` accepts already-parsed snapshot and delta events and
composes the existing `SubscriptionManager` with an explicit ticker-to-exchange-index
mapping, injected UTC clock, and dedicated `BookEvidenceStore`. It does not start a
WebSocket or create a network client. Receipt time is explicit; availability is captured
only after successful canonical book application. Snapshot exchange time remains `None`;
delta exchange time is retained from the genuine event.

Only current, fresh books with at least one usable side become evidence. Gapped,
rejected, stale, ambiguously routed, or invalid inputs store nothing or fail closed.
Evidence IDs are deterministic hashes of canonical payloads. Identical replay is
idempotent; a changed payload at the same logical identity is rejected as a collision.

SQLite stores every `Decimal` as exact text, enforces `production_influence = '0'`, and
prohibits updates and deletes. No PostgreSQL migration is required for this dedicated
offline research store. Live collection remains OFF.
