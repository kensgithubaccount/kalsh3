# M26H.3 Authoritative Event Coverage Reconciliation

Status: implemented and offline verified; focused independent review pending.

## Reviewed v2 scope

The runtime registry now contains exactly `OPEN_NON_MVE_V2`
(`open-non-mve-v2`) under policy `m26h3-reviewed-public-scope-v2`. Its scope ID
deterministically binds that new name and policy to the existing fixed broad
queries. V1 remains a historical code constant only: it is not runtime-reviewed,
and existing v1 acquisitions are never reinterpreted as v2.

V2 means a full natural traversal of `status=open,mve_filter=exclude` Markets;
a broad `status=open` Event traversal as an efficient first pass; and exact
parent-Event reconciliation for Market-referenced Events absent from that first
pass. The three phases execute sequentially and are not an atomic exchange
snapshot.

## Bounded current-run reconciliation

Coverage is derived only from the fresh in-memory repository created by the
current `collect_evidence` invocation. The collector runs Markets, then broad
Events, derives and sorts missing parent tickers, and asks the same synchronizer
to acquire each exact Event. Prior archive rows and prior collection repositories
cannot fill a current-run gap.

No request is made when no parent is missing. Reconciliation is capped by the
repository-controlled constant `MAX_EVENT_RECONCILIATION_REQUESTS = 100`; an
initial missing count above the cap makes zero point reads and remains
`INCOMPLETE / bounded_reconciliation_exceeded`. Incomplete broad acquisition
also prevents point reads. Any failed, malformed, missing, or wrong-ticker exact
response makes reconciliation partial. Overall `COMPLETE` requires both broad
runs complete, reconciliation complete or unnecessary, and zero unresolved
parent tickers.

An Event may have changed state after its Market observation. In particular, a
point-read Event may contain active, closed, determined, finalized, or other
legitimate child Market states. Reconciliation proves parent identity and
metadata; it does not infer that a broad-listing omission means closure.

## Public transport and archive authority

The unauthenticated transport remains fixed-origin HTTPS GET-only, redirect-free,
bounded by timeout and response size, and restricted to the two broad query
shapes plus canonical `/trade-api/v2/events/{encoded_event_ticker}` reads with no
query. Empty, noncanonical, path/query/fragment/control injection and alternate
origin targets are rejected. Broad query validation is unchanged.

`UniverseSynchronizer` remains the sole normal acquisition writer. An exact read
archives the decoded response exactly as returned—`{"event": ..., "markets":
[...]}` is never rewritten into a plural page. Existing schema and provenance
represent the page as `events/{ticker}`. The archive creates only the singleton
Event observation from `payload["event"]`; top-level or nested Markets do not
become Market observations. Restoration proves that source exists in the raw
singleton payload and uses the same parser, hashes, archive authority, and
verification policy as plural Event pages. Existing M26F plural pages remain
unchanged.

## Receipt and limits

The receipt reports initial missing, broad matched, reconciled and remaining
tickers, request count, reconciliation status/failure, and the reconciliation
run when one occurred. Operator output omits bodies and cursor material.
Production influence remains exactly `Decimal("0")`.

`COMPLETE` does not establish atomicity, statistical independence,
profitability, strategy quality, or trading readiness. M26G reviewed authority
and its empty real registry are unchanged; no evidence units are created.
