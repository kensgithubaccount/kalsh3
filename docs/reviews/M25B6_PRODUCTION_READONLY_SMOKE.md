# M25B6 — Bounded Production Read-Only Perps Smoke

## What this unlocks

M25B6 makes one explicitly human-invoked production read-only Perps evidence smoke composable after a
production credential has separately completed the M25B5 enrollment and verification lifecycle. It does
not run that smoke, enroll or verify a credential, require DEMO first, create a service, or start collection
automatically. No real credential or network was used during implementation and tests.

The manual entry point is:

```text
python -m services.perps_shadow_research.live_smoke \
  --environment production --ticker TICKER \
  --evidence-db /untracked/path/perps.sqlite3 \
  --live-readonly --confirm-production-readonly
```

The optional `--production-credential-store` defaults to M25B5's reviewed production store. It is separate
from the existing DEMO `--credential-store`. Neither environment probes or requires the other's store.

## Production capability gate and ordering

Configuration first requires PRODUCTION, `--live-readonly`, `--confirm-production-readonly`, exactly one
nonblank ticker, a positive window no greater than 60 seconds, and 0 or 1 reconnect. The public orchestration
then accepts only the exact M25B5 `VerifiedProductionReadCredentialProvider` backed by exactly the reviewed
`ProductionReadCredentialStore`. Generic or subclassed providers and duck-typed, wrapped, or subclassed
stores are rejected before any store method is called.

The approved provider must load through that real store and resolve a current internally consistent
`VERIFIED_PRODUCTION_READONLY` record with production target, the reviewed verification method,
matching key ID and keyed fingerprint, verification time, server scopes exactly `('read',)`, and application
scope exactly `read`. Any missing, unverified, disabled, quarantined, corrupt, stale, or mismatched state
fails closed. The shared exact-read contract is checked again when resolved, and the GET/HEAD-only signer is
then constructed.

This entire gate completes before reading wall/monotonic time, constructing the default REST transport,
making any REST call, constructing or using a WebSocket, or creating/mutating the evidence store. The CLI
constructs the environment-specific provider and calls `run_live_smoke()` without a redundant preflight;
the authoritative gate therefore resolves credential material exactly once per invocation.

## Exact network surface and bounds

The production smoke can represent only:

- one public `GET /trade-api/v2/margin/markets/{ticker}` at the closed production REST origin;
- one signed `GET /trade-api/v2/margin/enabled` at that same origin;
- the closed production margin WebSocket URL and signed `/trade-api/ws/v2/margin` handshake path;
- protocol-created subscribe/unsubscribe commands for only `orderbook_delta` and `ticker`.

Production REST retries are zero: the maximum REST attempt surface is exactly two attempts total, one for
each listed endpoint. There are at most two WebSocket connection attempts (`max_reconnects` 0 or 1), each
with a 10-second open timeout and 5-second close timeout. Each connection evidence window is positive and
at most 60 seconds, so with one reconnect the maximum configured evidence-window total is 120 seconds.
There is no discovery, restart, scheduler, autostart, background loop, or unbounded retry.

## Safety and verification truth

Offline adversarial tests cover exact provider/store identity before any `load()`; generic and subclassed
providers; duck, wrapped, exploding, and subclassed stores; every non-verified real-store lifecycle state;
corrupt/mismatched real-store metadata; missing confirmation, ticker, time, and reconnect bounds;
credential-before-side-effect ordering; DEMO compatibility and store separation; sanitized CLI failure; and
a complete successful production smoke after ephemeral enrollment and fake-transport verification through
the real encrypted store, followed by fake Perps REST and WebSockets. The success test asserts exact origins
and paths, two GET-only REST operations, the
single reviewed WebSocket URL/path, only existing read channels and subscribe/unsubscribe commands, clean
termination, and genuine evidence counts.

No POST, PUT, PATCH, DELETE, order, cancellation, transfer, key mutation, portfolio mutation, trading,
production-write, risk authorization, dashboard, scheduler, autonomy, learning, or Predictions realtime
capability is added. `services.production_execution` is untouched, production execution remains DISARMED,
and production influence remains exactly zero. Private key bytes remain absent from argv, environment,
output, repr, exceptions, and snapshots.

After independent review and merge, a human must separately create an exchange key scoped exactly `read`,
enroll it through the M25B5 FD-only CLI, verify it through M25B5's fixed production GET, and then explicitly
invoke the command above with an untracked evidence path. A real smoke result remains pending and must not
be inferred from this milestone's offline success test.

## Post-merge live acceptance

The preceding statements remain the implementation and review truth for M25B6: no real credentials or
network were used, and no real smoke was run during its implementation or review. After M25B6 was merged,
a separately human-invoked, bounded production read-only smoke was completed for canonical ticker
`KXBTCPERP`. It succeeded after the dedicated read-only credential was verified and Perps entitlement had
propagated. The accepted evidence included two snapshots, 66 deltas, and two distinct connection epochs;
all persisted Perps evidence tables retained `production_influence = 0`.

This later result is production observation acceptance only. It does not alter M25B6's offline review
history, enable a collector or scheduler, place an order, or unlock production execution. The authoritative
record, including the credential lifecycle, environment synchronization, evidence counts, and boundaries,
is `M25B7_PRODUCTION_LIVE_ACCEPTANCE.md`.
