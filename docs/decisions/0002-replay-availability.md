# ADR 0002: Replay availability is distinct from actual ingestion

## Decision

Point-in-time replay preserves source event, publication, provider receipt, and actual bot-ingest timestamps independently. `replay_available_at` is a separate gate with an explicit basis and quality.

Live observations use actual bot ingest and measured latency. Exchange and primary-source backfills may use their authoritative timestamp plus a documented conservative assumed delay. External reconstructions are descriptive/conservative. Unknown availability is excluded from causal replay.

## Consequences

Historical acquisition never falsifies when this bot received a record. Reconstructed delays are simulation assumptions, never latency measurements. Corrections and retractions are later immutable events. This sacrifices sample size when provenance is weak to prevent lookahead.
