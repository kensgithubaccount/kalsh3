# M25A Read-Only Evidence Runtime Review

Date: 2026-08-13

Scope is strictly offline. M24 remains complete and unchanged. M25A adds a disabled-by-default,
network-incapable scripted runtime and does not activate a collector or use credentials.

## Safety review

- The raw transport boundary timestamps immutable text/bytes before decoding.
- Exactly one synchronous consumer performs decode, classification, canonical mutation, and
  persistence. No queue or parallel application exists.
- Only `orderbook_delta` subscription, snapshots, deltas, and their command responses are allowed.
- Every successful connection receives a new nonzero epoch. Disconnect stales books; old epochs
  cannot mutate the new connection.
- Gap deltas create no evidence. One recovery request per SID is outstanding while an exact set
  of affected configured tickers awaits valid current snapshot evidence. One ticker cannot clear
  recovery for sibling tickers sharing the SID, and reconnect clears the recovery generation.
- A snapshot that mutates canonical state but is stale/unusable at the injected availability time
  creates no evidence, does not update accepted-snapshot health, and remains recovery-required.
- Malformed, unsupported, unexpected, colliding, stale, and persistence-failure paths fail closed.
  Persistence failure after canonical mutation immediately quarantines the runtime.
- SQLite is append-only with WAL, synchronous FULL, 30-second busy timeout, foreign keys enabled
  on store connections, startup quick check, and `production_influence = '0'` constraint.
- Runtime modules have no execution, risk, signer, credential, learning, canary, autonomy, order,
  or mutating HTTP dependency/capability.
- Official subscribed-response fixture coverage drives both `ProtocolState.response()` and the
  offline runtime. Command IDs and subscribed `msg.sid` values require exact non-negative `int`
  values; booleans, negatives, and top-level-only SIDs fail closed.
- `exchange_index` is non-negative and exact-int but is not unique across tickers: neither the
  authoritative M24/M25 specification nor the documented routing/partition-key contract imposes
  a one-to-one ticker mapping.

## Deferred to M25B

A future authenticated read-only collector must validate live market metadata using public
`GET /markets/{ticker}` fields `exchange_index`, `price_level_structure`, and `price_ranges`.
No network lookup, WebSocket library, production connection, credentials, funding, or deployment
activation is present in M25A. Live collection remains OFF.

## Verification

- Focused M24/M25/realtime pytest: 71 passed, 1 unrelated signer test deselected.
- Full pytest: 471 passed, 46 known tracked macOS signer portability failures caused by
  `/proc/self/fd` and unavailable `os.memfd_create`.
- Ruff lint and format: passed across `services` and `tests`.
- Strict mypy for both touched source modules: passed.
- `git diff --check`: passed.
