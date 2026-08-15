# M26H.1 Scoped Complete Evidence Collection Review

Status: implemented and offline verified; independent micro-review found no
blocker or important findings and declared the hardening safe to commit.

## Operational finding and scope decision

The prior M26H live acceptance proved that the public-read collector and M26F
archive worked. A later unfiltered 250-page run archived 25,000 Markets and
50,000 Events and truthfully ended `INCOMPLETE / bounded_truncation`. That valid
partial archive is not reinterpreted or migrated. It demonstrated that the broad
public universe is not a practical completeness unit; increasing the page bound
would not define one.

M26H.1 therefore introduces exactly one repository-reviewed scope:
`OPEN_NON_MVE_V1` (`open-non-mve-v1`). It means currently open,
non-multivariate Markets plus currently open Events, not all Kalshi evidence.
`REVIEWED_SCOPES` is structurally immutable via `MappingProxyType`: runtime
callers cannot add, remove, or replace a reviewed scope, and there is no
registration or loading helper. Exactly `open-non-mve-v1` remains reviewed.
Fake or caller-created scopes remain rejected by the canonical
`OPEN_NON_MVE_V1` object-identity boundary.
Its fixed requests are:

- `/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000`
- `/trade-api/v2/events?status=open&limit=200`

The policy version is `m26h1-reviewed-public-scope-v1`. A deterministic SHA-256
scope ID binds the name, version, endpoints, fixed semantic parameters, and
production influence `0`. Cursors are pagination provenance, not scope identity.

## Runtime boundary

The operator must provide both `--live-public-read` and
`--scope open-non-mve-v1`. Omitting the scope performs no network access and
does not open or create an archive. No arbitrary scope, status, series, ticker,
timestamp, filter, limit, query, URL, method, body, or endpoint is accepted.

The origin remains fixed to `https://external-api.kalshi.com`. The transport is
unauthenticated GET-only, permits only the reviewed Markets and Events paths,
rejects redirects, and uses a finite timeout and response-size ceiling. It
validates exact decoded query keys and values, rejects duplicates, blanks,
unexpected keys, alternate origins/paths/fragments, and control characters.
`http.client.InvalidURL` and other request failures become sanitized
`CollectionError` values.

Pagination uses structured `urlencode` construction. The server cursor is only
the value of one `cursor` parameter, so it cannot inject parameters, paths,
fragments, origins, or headers. Raw and percent-decoded control characters stop
the run before another request. Cursor values and response bodies are never
printed by progress output.

Progress callbacks receive immutable `SyncProgress` objects containing only
`resource`, `pages`, and `records_received`. They no longer receive `SyncRun`
and therefore do not expose `last_cursor`, cursor values, queries, payloads,
headers, exceptions, or archive internals. Without a callback, ordinary library
use remains silent.

This hardening does not change scope-completeness semantics, Market-to-Event
coverage, pagination, archive authority, production influence, M26G state,
M9/statistics, or production execution.

## Completeness and time semantics

`COMPLETE` means both fixed endpoint traversals naturally exhausted their
cursors within `max_pages`, every parsed page and record was valid, and every
unique parsed Market `event_ticker` matched a parsed scoped Event. Extra Events
are allowed. Missing Events are canonicalized as a sorted tuple and make the
overall receipt incomplete; no Event is fabricated.

This does not mean historical completeness, multivariate completeness,
statistical or independent-evidence completeness, or an atomic point-in-time
snapshot. Markets and Events are collected sequentially. The receipt exposes
the collection start/finish and each resource run window; market-to-event
coverage is the conservative consistency boundary. A Markets failure is still
followed by an Events attempt so append-only evidence can be retained, but no
successful resource erases another resource's failed state.

A continuation cursor at `max_pages` remains `PARTIAL / bounded_truncation`.
The ceiling is never raised automatically. `max_pages` accepts only `None` or a
positive non-boolean integer at the synchronizer boundary.

## Archive and downstream authority

Every new acquisition page records the fixed reviewed parameters in existing
M26F provenance; `cursor_in` and `cursor_out` remain separate. Repeated scoped
collections append new observations and run results. The stable scope ID does
not replace differing acquisition IDs or timestamps. Historical broad rows are
never relabeled as scoped complete.

M26G remains unconfigured: no assignments, authority manifests, or reviewed
registry entries were added. M9 statistics and all dashboard, promotion,
strategy, allocation, capital, signer, order, and production-execution paths
remain disconnected. Production execution is DISARMED and production influence
is exactly `Decimal("0")`.

## Offline verification

Deterministic fake transports cover explicit CLI scope gating, exact requests,
cursor encoding/injection, raw and encoded controls, query-shape rejection,
redirect and timeout boundaries, strict page types, natural exhaustion and page
caps, full and missing coverage, malformed resources, repeated appends, fixed
archive provenance, sanitized `InvalidURL`, progress without cursor disclosure,
import inactivity, empty M26G authority, absent M9 integration, and zero
production influence. Tests make no real Kalshi requests and use no credentials
or existing evidence database.
