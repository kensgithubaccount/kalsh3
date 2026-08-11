# Architecture

The system is event-driven with a hard boundary between research intelligence and capital authorization. Market/account gateways normalize external data; registries version contracts and sources; point-in-time evidence and features feed forecasting and calibration; strategy modules emit candidates; deterministic opportunity, risk, execution-planning, and reconciliation modules decide whether candidates are safe. Only an isolated signer can access a write key.

PostgreSQL is canonical state, Redis is transient cache/locks, NATS JetStream transports events, and an S3-compatible store holds immutable raw evidence. Services exchange versioned schema-validated messages and persist enough provenance to replay every decision.

## Trust boundaries

Research, LLM, and web processes cannot import or contact signer internals directly. The execution gateway has no PEM. A short-lived, single-use risk authorization is required at the signer boundary. Stale data, sequence gaps, unknown order outcomes, reconciliation mismatches, monitoring failures, or restarts block new risk.

## M2 universe boundary

Public market ingestion is signer-free and separate from the authenticated account gateway. Baseline discovery
and overlapped incremental metadata synchronization are distinct flows. PostgreSQL is canonical; immutable
metadata/rules versions and raw payloads prevent silent semantic history loss. REST orderbooks are bootstrap
snapshots only; M3 owns sequence-aware real-time state. Live REST data is explicitly bounded by the persisted
exchange historical cutoff, while M6 owns historical backfill/replay.

## M3 real-time boundary

The authenticated WebSocket handshake reuses only the encrypted M1 exact-read credential. Each connection
creates a new epoch; command IDs and subscription SIDs are distinct, and sequence continuity is enforced per
epoch/SID. Global ticker/trade/lifecycle streams remain lightweight, while full depth is selectively capped.
A gap, reconnect, stale frame, metadata ladder change, or backpressure prevents a book from being healthy until
a fresh verified snapshot. Lifecycle signals enqueue M2 refreshes and never rewrite immutable versions.
