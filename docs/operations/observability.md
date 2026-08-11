# Observability and alert policy

Structured events use stable event codes, UTC timestamps, allowlisted fields, and opaque trace/span IDs.
Never attach credentials, auth headers, request bodies, signatures, PEM, cookies, account IDs, client order
IDs, natural-language evidence, or high-cardinality market identifiers. Metrics use bounded labels and exact
Decimal values at financial boundaries.

## Signals and deterministic actions

| Signal | Warning | Critical / action |
|---|---:|---:|
| PostgreSQL, Redis, NATS, object archive | degraded once | failed/unknown/stale: block dependent work; PostgreSQL blocks all state changes |
| Queue depth | 5,000 | 10,000: halt new risk |
| Delivery attempts | 3 | 5: dead-letter and alert |
| Account/reconciliation age | 15 seconds | 30 seconds: block new risk |
| Backup age | 24 hours | 26 hours: readiness blocked |
| Restore-drill age | 80 days | 90 days: readiness blocked |
| Disk/inodes | 75% | 85%: halt new risk and preserve journals |
| Clock offset | 250 ms | 1 second or regression: stop signing/new risk |
| Monthly cost | above $25.00 | at/above $50.00: stop optional workloads and owner review |
| Unknown mutation/order/position | immediate | immediate DISARM, reconciliation, and owner page |
| Monitoring heartbeat | one missed interval | two missed intervals: block new risk |

Liveness means the process can answer; readiness means its required current dependencies and safety state
are known. `/healthz` exposes only coarse secret-free state. `/readyz` fails closed in the offline build.
Alert delivery, dashboards, and on-host thresholds are **NOT VERIFIED** until deployed and exercised.
