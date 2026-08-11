# Incident response runbook

## Universal first actions

1. Confirm the durable global halt and production `DISARMED`; autonomy must remain `OFF`.
2. Block new risk, preserve journals and audit evidence, and record a secret-free incident code/time.
3. Do not retry an unknown mutation. Reconcile the original client order reference first.
4. Keep read-only research available only when its own inputs remain current and clearly degraded.
5. Escalate to the owner; never paste credentials, headers, signatures, PEM, or raw account identifiers.

## Database outage

Stop all state-changing workflows. Do not buffer financial mutations in memory. Restore database service,
verify migrations and storage, recover reservations and execution-journal ownership, reconcile exchange
state, and require fresh readiness. If corruption is suspected, restore only through the isolated drill
procedure and keep production disarmed.

## Redis, NATS, object storage, or worker outage

Redis failure disables dependent cache/coordination paths rather than using stale values. A NATS gap or
worker crash stops new risk, resumes only idempotent read work, and dead-letters poison messages after the
bounded delivery count. Object-storage failure blocks archival completeness and backup readiness. After
recovery, resnapshot/replay from the durable cursor and reconcile before clearing the incident.

## API, market-data, or reconciliation outage

Freeze affected candidates and new risk. Treat redirects, schema drift, stale timestamps, sequence gaps,
unknown orders/positions, and ambiguous responses as blockers. Do not substitute another host or provider.
Resume only after current official compatibility, fresh data, and full account reconciliation.

## Signer or credential incident

Activate credential kill, keep the signer disarmed, remove the mounted secret from the signer boundary,
and revoke/rotate the key through the exchange's authenticated human procedure. Never call an API-key
deletion endpoint from M18. Verify read credentials remain separate. Enrollment never arms production.

## Emergency halt and recovery

The owner uses strong reauthentication to halt new risk. Known bot-owned resting orders may only follow
the typed safety cancellation path; never claim cancellation before reconciliation. Reset requires an
authenticated owner, documented cause/remediation, healthy monitoring and clock, reconciled account,
current dependencies, and an independent later activation workflow.
