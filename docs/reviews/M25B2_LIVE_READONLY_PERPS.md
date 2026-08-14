# M25B2 — Live Read-Only Perps Evidence Boundary

## Contract review (2026-08-13)

Official sources were freshly retrieved before editing. Production REST remains
`https://external-api.kalshi.com/trade-api/v2/margin/`; demo REST remains
`https://external-api.demo.kalshi.co/trade-api/v2/margin/`. Production and demo WebSocket URLs
remain the dedicated margin hosts ending in `/trade-api/ws/v2/margin`. The signed WebSocket target
is the canonical path `GET /trade-api/ws/v2/margin`, excluding host and query. Get Market remains a
public `GET /trade-api/v2/margin/markets/{ticker}` with a top-level `market`; Get Enabled remains an
authenticated `GET /trade-api/v2/margin/enabled` returning required boolean `enabled`.

Raw OpenAPI SHA-256 is
`af990c0e11353da0f06eaa017738646f70460784f3068a6c2460d51a0f21434b`; raw AsyncAPI SHA-256 is
`8af2212c643e5effe105164f1383117a5859c2070a929b888fcda84c70b059cd`. Both match M25B1.
No fundamental or implementation-relevant contract drift was found. The docs do not separately
promise different demo semantics for `/margin/enabled`; the same authenticated boolean contract is
therefore applied fail-closed in both environments.

## Boundary and safety review

The environment enum owns both fixed origins and the signed WebSocket path. REST exposes only
`get_market()` and `enabled()`; the underlying transport has only `get()`. It rejects redirects,
non-JSON, oversized bodies, malformed JSON, NaN/Infinity, ticker mismatches, closed markets, and
unbounded retry. Authentication failure is terminal; 429, network, and 5xx retry only within the
configured bound. Metadata is parsed through M25B1 and persisted before authenticated streaming.

The concrete async transport uses `websockets==16.1.1`, fixed handshake parameters, bounded size
and timeouts, and configured ping/pong. Immediately after `raw = await websocket.recv()` it records
monotonic and UTC clocks, constructs immutable `ReceivedFrame`, and performs no parsing. Application
messages must be the exact object issued by the connection's own `MarginProtocolState` and exactly
match its retained canonical payload, including nested parameters and absence of extra fields.

Each connection creates a non-zero UUID and binds that exact epoch to M25B1. Disconnect invalidates
it. A reconnect creates a new protocol and epoch, resubscribes, and cannot accept an old frame.
Acceptance compares monotonic accepted snapshot/delta/ticker counters against a baseline captured
immediately after each bind, so acknowledgements, other frame types, historical timestamps, and old
epochs cannot substitute for a genuinely accepted snapshot in the new connection epoch.
Final SUCCESS separately compares book and market-state table counts with invocation-local baselines
captured before collection. Only rows inserted by the current invocation count; pre-existing rows and
idempotently rejected replays cannot satisfy persistence acceptance. Documented `unsubscribed` and
list-subscriptions responses remove only their exact command-id-matched pending entries.
M25B1 still owns gap, crossed, stale, replay, collision, evidence, and sensitive delta-field policy.
No raw-frame archive exists.

## Manual runbook and blocker

Prefer demo. Use Linux because the unchanged `RequestSigner` uses `/proc/self/fd`. Select one
explicit ticker and an untracked SQLite path, then require `--live-readonly`. Production additionally
requires `--confirm-production-readonly`; false entitlement is a clean NO-GO. Never pass secrets on
the command line and never run this command from CI.

The existing encrypted read vault was verified to contain key material but no explicit environment
provenance. Reusing it for both demo and production would permit credential/environment confusion.
M25B2 therefore defines an injectable `ExactReadCredentialProvider` and its CLI currently exits
with a sanitized BLOCKER. A real demo or production smoke must not be attempted until a separate,
reviewed composition supplies an exactly-read credential whose environment provenance is explicit.

Success requires genuine initial snapshot, contiguous delta, ticker observation, persisted book and
market-state rows, controlled disconnect, new epoch, fresh post-reconnect snapshot, and clean close.
No delta is INCONCLUSIVE. No synthetic activity may satisfy acceptance.

The live smoke has not been run. Credential composition remains blocked as described above.

Production execution remains DISARMED. Production-write credential remains NONE. No order, cancel,
amend, decrease, transfer, risk write, position sizing, routing, canary, autonomy, learning influence,
or signer-service access exists. `production_influence` remains exactly `Decimal("0")` and SQLite
constrains it to text `0`.
