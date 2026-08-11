# M18 Operations Hardening — Offline Review

## Scope

M18 adds production-oriented operational controls without enabling production. The runtime remains
production `DISARMED`, autonomy `OFF`, production-write credential `NONE`, and real-money execution `NONE`.
Operational evidence can block readiness; it cannot arm, sign, authorize, or transmit.

## Implemented controls

- Immutable dependency observations and operational snapshots cover PostgreSQL, Redis, NATS, object
  storage, workers, market/account data, reconciliation, unknown activity, halt/compliance/kills, clock,
  disk, backups, and monitoring. Missing, stale, failed, degraded, unknown, or regressed observations fail
  closed for new risk.
- Restart policy always returns `DISARMED` / `OFF`, recovers durable risk reservations and execution
  journals, requires reconciliation, and prohibits automatic replay of an unknown mutation.
- Secret-safe structured JSON events, allowlisted Decimal metrics, opaque trace correlation, and safe
  health/readiness payloads exclude credentials and sensitive identifiers.
- Queue soft/hard limits apply research backpressure, halt new risk at hard saturation, and dead-letter
  poison work after bounded attempts.
- API compatibility requires pinned OpenAPI, AsyncAPI, and changelog hashes; absent evidence or drift
  blocks compatibility.
- Backup manifests require encryption and SHA-256 provenance. Scripts create encrypted PostgreSQL dumps
  and restore only into a disposable `--network none` PostgreSQL instance. Live backup/object-storage and
  restore drills remain unverified.
- Compose adds bounded logs, restart/init policies, health checks, resource ceilings, persistent storage,
  dropped capabilities, no-new-privileges, private networks, and an isolated no-public-port signer.
- CI now validates shell/Compose configuration and defines Bandit, detect-secrets, pip-audit, SBOM, and
  Trivy gates in addition to lint, strict typing, unit tests, and PostgreSQL tests.
- Incident, backup/restore, Oracle deployment, credential rotation, API/database outage, reconciliation,
  emergency-halt, release, and rollback procedures are documented.
- The owner System surface exposes truthful offline operational state and the `$25.00` target / `$50.00`
  hard cap without displaying sensitive material.

## Adversarial and cross-functional review

- **Security:** logs, health, metrics, traces, diagnostics, and support exports redact keys, passwords,
  tokens, signatures, PEM, auth headers, client order IDs, and account IDs. Operations has no signer,
  execution, private-key, or HTTP client dependency.
- **Distributed systems/SRE:** stale dependencies, queue saturation, worker poison messages, clock
  regression, disk pressure, monitoring loss, backup staleness, and unknown state block readiness. Restart
  cannot retry a mutation or create an armed state.
- **Risk/compliance:** human or operational status cannot override reconciliation, halt, holds, kills, or
  M13. Database loss stops state changes rather than buffering financial mutations.
- **Finance/CFO:** budget arithmetic is Decimal; `$25/month` is the target and `$50/month` is a hard
  operational escalation cap. Actual Oracle/provider cost is not fabricated.
- **Product/UX:** the System page explicitly says live operations are not verified and production is
  disarmed. Health output exposes only coarse safe state.
- **Recovery:** restore tooling checks encrypted artifacts and uses a network-isolated target. It never
  restores over production or activates trading.

## Acceptance

- M18 operational policy, degraded modes, observability primitives, redaction, queue/backpressure,
  restart decisions, API drift gate, backup/restore artifacts, migration constraints, deployment config,
  runbooks, owner status, and adversarial tests: **OFFLINE VERIFIED**.
- PostgreSQL-marked test contract: **OFFLINE VERIFIED** in this environment; live multi-worker PostgreSQL,
  Docker Compose runtime, Oracle deployment, TLS, object storage, live backups/restores, alert delivery,
  browser review, and live providers: **NOT VERIFIED**.
- Production write credential: **NONE**. Production state: **DISARMED**. Autonomy: **OFF**.
  Live production mutation and real-money order: **NONE**. Human acceptance: **PENDING**.
