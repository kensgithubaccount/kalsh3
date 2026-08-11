# M3 Real-Time Market Data — Cross-Functional Review

## Acceptance

Code and protocol fixtures are offline verified against the owner-supplied current official documentation
facts. A concrete reviewed WebSocket client is intentionally not homemade while package installation is
blocked. Live authentication, TLS, Ping/Pong, bandwidth, archive upload, and Oracle acceptance remain pending.

## Review findings and corrections

- **Trader:** `use_yes_price=true` is explicit. Both YES and NO levels remain on the YES-price scale and the
  NO side is never complemented again. Executable bid/ask and sizes are exposed; no midpoint exists.
- **Quant / ML / data science:** sequence continuity is scoped to connection epoch plus SID, not market.
  Exchange `ts_ms`, receive wall time, receive monotonic time, persistence time, gaps, and archive hashes are
  distinct, allowing later research to exclude missing intervals instead of training through them.
- **Data engineering:** reconnect creates a new epoch and makes old books stale. Duplicate/out-of-order/gapped
  SID sequences and queue overflow create durable gap semantics. A fresh verified snapshot is required before
  depth becomes healthy. Lifecycle and periodic M2 REST synchronization are complementary because normal
  clock-driven opening/closing need not emit lifecycle events.
- **Security:** handshake signing reuses only the encrypted M1 read credential and exact GET WS path. Market
  transport has no write key or mutation command. Credentials and signatures are absent from raw archives/UI.
- **SRE:** explicit states distinguish authenticating, subscribing, snapshotting, gaps, stale, backpressure,
  reconnecting and failure. Backoff is bounded. Liveness monitors frames without inventing application pings;
  a reviewed client must answer server Ping control frames.
- **CFO:** ticker/trade/lifecycle are lightweight global streams while full depth is a capped selective watch
  set. Gzip batches go to object storage and PostgreSQL retains latest state, indexes, trades, gaps, lifecycle
  and manifests rather than every hot delta row.
- **Product:** UI labels REAL-TIME only in HEALTHY state and distinguishes top-of-book from full depth. System
  exposes epoch, freshness, subscriptions, gaps, reconnects, queue/lag, archive and read-key requirement.

No material offline finding remains. Live/human claims remain withheld.
