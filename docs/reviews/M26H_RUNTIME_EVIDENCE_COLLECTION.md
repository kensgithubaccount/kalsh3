# M26H Runtime Evidence Collection

## Decision and architecture audit

M26H adds one deliberate, research-only runtime path that collects genuine Kalshi
Market and Event responses into the M26F archive. The entry point is:

```text
python -m services.market_universe.collect \
  --archive /secure/local/path/universe-observations.sqlite \
  --live-public-read
```

The audit found that `UniverseSynchronizer` already owned pagination, parsing,
run-result truth, and the opaque M26F acquisition writer, while
`UniverseObservationArchive` already enforced schema/trigger/index validation,
append-only history, nonzero-database fail-closed behavior, zero-byte/new-store
initialization, and mode `0600` on the database. No concrete runtime public
Market/Event transport or CLI existed. Existing account transports require a
signer and are correctly restricted to account resources, so they were not
reused or widened. Dashboard, worker, Docker, compose, and service startup wiring
do not import or invoke the new collector.

Kalshi's current official Market Data quick start identifies series, events,
markets, and market data as public production endpoints requiring no
authentication. M26H therefore uses no credentials, signer, authorization
header, secret environment value, or account transport. The fixed origin is
`https://external-api.kalshi.com`; implementation and tests made no request to
that origin. A later, separately approved acceptance run is required.

## Explicit gate and closed resource boundary

Running the module is insufficient to contact Kalshi. Without the exact
`--live-public-read` flag, the command returns `NOT STARTED`, performs no network
call, and does not initialize the archive path. The normal CLI has no resource or
URL argument. Its transport can issue only unauthenticated GET requests to the
exact `/trade-api/v2/markets` and `/trade-api/v2/events` collection paths on the
fixed origin. Redirects, alternate origins, path traversal, fragments, arbitrary
queries, duplicate query parameters, and every other resource are rejected
before network I/O.

## Archive lifecycle and orchestration

The operator must provide `--archive`. One `UniverseObservationArchive` and one
`UniverseSynchronizer` are constructed after the live gate, then baseline
`sync("markets")` and `sync("events")` run in that order with the same archive
authority. M26H never inserts archive rows manually and cannot access the
synchronizer's mangled writer. Existing corrupt, partial, foreign, or weakened
nonzero databases fail during archive initialization before acquisition and are
not repaired. New archive files retain M26F's mode `0600`. Repeated runs append
new pages, observations, and immutable run results without deleting, refreshing,
vacuuming, replacing, or rewriting prior evidence.

This is tamper-evident local application evidence. It is not a cryptographically
signed Kalshi attestation and does not protect against a privileged actor who can
coherently replace the database and running application.

## Pagination, completeness, and failures

M26H adds an optional `max_pages` safety bound to the existing synchronizer; the
runtime default is 250 pages per resource and the operator may select another
positive bound. Natural empty-cursor termination remains the only way a run can
be `COMPLETE`. Reaching the bound with a nonempty cursor archives the observed
page and records `PARTIAL` with `bounded_truncation`. Repeated/invalid cursors,
network errors, malformed pages, malformed entities, and archive errors retain
fail-closed M26F/M2 semantics.

The overall collection is complete only when both Market and Event runs are
`COMPLETE`. One failed, partial, malformed, or bounded resource makes the receipt
`INCOMPLETE` and the CLI exits nonzero. A failure does not delete already appended
evidence. Terminal errors expose only the exception type, not messages, response
bodies, headers, credentials, or environment values.

The operator summary reports overall state, observed Market/Event record counts,
local archive path and authority, both run IDs, audit start/end timestamps, and
production influence `0`. No content-addressed receipt identity includes these
processing timestamps; M26H introduces no new logical authority identity.

## M26F, M26G, and inference separation

- M26F is the archive-backed historical exchange-event authority.
- M26G is the repository-reviewed descriptive evidence-unit partition authority.
- M26H explicitly collects public Market/Event material into M26F for later
  operator review.

Collected events are candidates for future human review only. M26H does not
construct evidence-unit assignments or manifests and does not populate the real
M26G registry, which remains exactly empty. It does not call M9 statistical
intervals, calculate independent-event counts, perform inference, rank or mutate
agents/strategies, select winners, allocate research budgets or capital, add
governance, schedule/autostart work, enable live autonomy, or integrate with
production execution. Production remains DISARMED and production influence is
exactly `Decimal("0")`.

## UI and validation scope

The dashboard was not changed. It continues to state that the independent
evidence authority is not configured and does not infer statistical evidence or
eligibility from an M26H archive.

Focused deterministic tests use fake transports only and cover import/startup
silence, live-flag attacks, complete Market+Event collection, same-authority
orchestration, each resource failing, natural pagination, bounded truncation,
repeated append-only runs, malformed payloads, corrupt archives before
acquisition, resource/URL/trading-string injection, sanitized output, M26F direct
writer isolation, empty M26G registry, zero influence, and M9/production-execution
disconnection. No real credentials or live network calls were used.

## Known limitations

This first collector is baseline-only and bounded by pages, response bytes,
per-request timeout, cursor validation, and the operator's explicit invocation;
it has no retry/backoff policy or machine-readable output. A page may already be
durably archived when a later page or resource fails, by design. The in-memory
repository assembled during a run is not itself persisted; M26F is the durable
authority. No live acceptance result is claimed by this milestone.
