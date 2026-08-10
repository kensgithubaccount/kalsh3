# Architecture

The system is event-driven with a hard boundary between research intelligence and capital authorization. Market/account gateways normalize external data; registries version contracts and sources; point-in-time evidence and features feed forecasting and calibration; strategy modules emit candidates; deterministic opportunity, risk, execution-planning, and reconciliation modules decide whether candidates are safe. Only an isolated signer can access a write key.

PostgreSQL is canonical state, Redis is transient cache/locks, NATS JetStream transports events, and an S3-compatible store holds immutable raw evidence. Services exchange versioned schema-validated messages and persist enough provenance to replay every decision.

## Trust boundaries

Research, LLM, and web processes cannot import or contact signer internals directly. The execution gateway has no PEM. A short-lived, single-use risk authorization is required at the signer boundary. Stale data, sequence gaps, unknown order outcomes, reconciliation mismatches, monitoring failures, or restarts block new risk.
