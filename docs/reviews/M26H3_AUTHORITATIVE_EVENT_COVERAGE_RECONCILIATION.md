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

## M27B.3 event-reconciliation capacity repair (bound raised 100 -> 200)

Status: additive repair ready for independent review; no live smoke was run under this bound.
Grants no prospective, trading, capital, or execution authority. This section is an addendum;
nothing above is rewritten -- `MAX_EVENT_RECONCILIATION_REQUESTS = 100` (line 28 above) remains
an accurate historical record of what M26H.3 originally set and this codebase ran with from
introduction (commit `785f8084b5f6ac9f7ef66eb24c048a81a7742735`, "Reconcile authoritative Event
coverage") through the M27B.3R4.3 live smoke, unchanged in between.

**Why the prior bound was insufficient.** A read-only M27B.3 recovery audit found no numeric
derivation anywhere in the repository for why 100 was originally chosen -- not in the introducing
commit (title-only message), not as a comment near the constant, not in this doc, which only
describes the cap's *behavior*. The M26H.3 accepted acceptance run (`docs/IMPLEMENTATION_STATUS.md`)
observed a real reconciliation need of only 7 parents against an 84,724-Market archive -- 100 was
set with large, unexplained headroom over that then-real need, not derived from an API limit,
timing budget, or empirical maximum.

The M27B.3R4.3 post-capacity-repair live smoke then observed, against the current, larger
exchange: **Markets acquisition COMPLETE, 119 pages, 118,214 records, 0 malformed; Events
acquisition COMPLETE, 69 pages, 13,739 records, 0 malformed** -- both broad runs fully succeeded --
yet Market rows referenced 13,878 distinct Event parents against only 13,739 broadly-acquired
Events, leaving **139 missing parents**, exceeding the 100 cap and producing
`reconciliation_status = PARTIAL, reconciliation_failure = "bounded_reconciliation_exceeded"` with
zero point reads attempted. A follow-up read-only audit, querying the smoke's own archive
directly, found the 139 to be ordinary Market-referenced, well-formed, non-malformed tickers
across 30 series families, dominated by recurring hardware-component future-contract series whose
ticker text embeds forward month/year codes reaching into 2027 -- consistent with the normal
scope mismatch between the `/markets?status=open` and `/events?status=open` listing shapes at
current exchange scale that this reconciliation mechanism exists to close, not an acquisition
defect.

**Repair.** `MAX_EVENT_RECONCILIATION_REQUESTS` in `services/market_universe/collect.py` is raised
from `100` to `200` -- a fixed, reviewed, repository-controlled ceiling, chosen with ~44% margin
over the measured current need (139) while remaining below `DEFAULT_MAX_PAGES = 250`, the only
other already-reviewed "bounded sequential public GET loop" precedent in this acquisition
pipeline, without reusing that unrelated number uncritically. This is not a dynamic or unlimited
bound; every other line of `collect.py`, `sync.py`, and `archive.py` is unchanged. The
comparison, `if len(initial_missing) > MAX_EVENT_RECONCILIATION_REQUESTS: ...` (unchanged code,
new threshold), still makes zero point reads and reports `bounded_reconciliation_exceeded` above
the (now 200) cap.

**Unchanged, reconfirmed by test.** Fixed origin `https://external-api.kalshi.com`; unauthenticated
HTTPS GET-only, redirect-free transport; canonical exact-ticker path validation; one Event ticker
per request; deterministic sorted reconciliation order; returned-ticker-must-equal-requested-ticker
validation; malformed or wrong-ticker exact responses still make reconciliation partial; incomplete
broad acquisition still prevents reconciliation; partial reconciliation still means the overall
refresh is incomplete; an incomplete refresh (including one caused specifically by
`bounded_reconciliation_exceeded`) still produces zero structural observations or disappearance
writes upstream in `structural_measurement_runner.py`; the append-only archive, its identity/hash
semantics, and `production_influence = 0` are all unchanged and re-verified directly against
persisted rows. No pacing, retry/backoff, or concurrency was added or is required: the existing
per-request timeout and fully sequential, backoff-free shape (already the reviewed M26H.3 design,
and the only shape ever exercised for this transport anywhere in this repository) are unchanged --
raising the fixed count changes only how many times an unchanged, already-reviewed loop iterates.

**What this does not establish.** No live smoke was run against the new bound. A new bounded smoke
is required before this repair can be considered validated end-to-end against the live public API;
the 24-hour pilot remains unauthorized regardless. This repair adds no authentication, credential,
trading, capital, or execution authority.
