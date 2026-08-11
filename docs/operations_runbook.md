# Operations Runbook

Start in offline/read-only mode. Verify migrations, PostgreSQL, Redis, NATS, object archive, sources,
market/account freshness, clock synchronization, disk headroom, monitoring, backup age, API compatibility,
reconciliation, global halt, compliance, and kill states. Missing, stale, ambiguous, or unsupported state
blocks new risk.

Operational procedures:

- [Backup and isolated restore drills](operations/backup_restore.md)
- [Incident response and recovery](operations/incident_response.md)
- [Observability and deterministic alert policy](operations/observability.md)
- [Oracle deployment hardening and rollback](operations/oracle_hardening.md)

Global halt is durable: prohibit new risk, use typed safety cancellation only where state is known,
reconcile, disable signer authorization, alert the owner, and require authenticated human reset. Restart
always yields production `DISARMED`, autonomy `OFF`, no automatic mutation retry, recovered journal and risk
reservation ownership, and reconciliation of every possibly submitted execution.
