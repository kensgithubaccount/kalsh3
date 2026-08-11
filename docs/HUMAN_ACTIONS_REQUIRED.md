# Human Actions Required

- Run the one-use setup workflow over the deployed HTTPS origin; never paste a PEM into shell history.
- Supply a production **read-only** key only. Setup must positively verify its scope is exactly `[read]` and
  reconcile primary subaccount 0 before persisting its encrypted credential.
- Perform the live production-read acceptance: balance, complete pagination, positions, orders, fills,
  settlements, limits, refresh/stale behavior, logout/recovery login, and support-export inspection.
- Validate Docker Compose/Caddy on the target host, reboot it, verify persistent secrets/state, and perform
  an encrypted backup/restore drill.
- Production write credentials, arming, autonomous trading, and real-money orders remain prohibited.
- Validate M2 against the public production REST API: complete baseline counts, incremental overlap/watermark,
  several >100 orderbook batches, series/event relationships, historical cutoff, and observed API latency.
- Run the PostgreSQL migration and universe worker under Docker; interrupt later pages and restart to verify
  durable PARTIAL state and unchanged watermark. Inspect Markets/System at desktop, tablet, and mobile sizes.

## LIVE READ ACCEPTANCE CHECKPOINT RECOMMENDED AFTER M3

M1+M2+M3 are the first end-to-end production-read system worth validating on Oracle. Using only the owner's
exact-read credential, validate HTTPS setup/account reads, complete REST universe/cutoff, authenticated WS
handshake, automatic Ping/Pong, subscriptions/SIDs/sequences, reconnect/new epoch, snapshot recovery, ticker,
trade, both lifecycle channels, selective depth bandwidth, archive upload, and UI stale/gap states. Do not
provide a write key. This acceptance can run in parallel with later offline milestones.

## M19 prioritized final blockers

The authoritative prioritized list, readiness matrix, non-executing activation checklist, and residual risk
register are in [`docs/reviews/M19_FINAL_AUDIT.md`](reviews/M19_FINAL_AUDIT.md). Every HIGH risk there blocks
activation. M19 does not authorize credential enrollment, arming, autonomy, a canary, or a real-money order.
