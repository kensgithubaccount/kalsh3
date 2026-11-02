# October settlement recovery v1 — OFFLINE candidate, LIVE NOT AUTHORIZED

This candidate prepares the fourteen unfinished settlement families from the November 2 audits. It is an offline request planner, narrow response-schema validator and synthetic replay state machine. **It has no network adapter, runnable collector, CLI, credential lookup, callback transport, persistence, scheduler, model, score or positive evidence issuer.** GitHub repository/CI operations are separate from scientific-source acquisition. No new Kalshi, GCS, EE, TWC or NWS request is part of this candidate's validation.

Base: `30fb7d0a8c9c1a9bbf9f38bf496582b75c208195`, including merged PR #257. Existing frozen files and evidence are unchanged. The new digest-bound roster is `experiments/wn_october_settlement_recovery_v1/frozen_request_roster.json`; SHA-256 `ab03a2d4c923837b4aeff3a3ca60b3145c378d114b417de1bf362ac9e28eb6d7`. It includes the exact 84 sibling tickers, original structured ladder/rules, raw-source hash joins, routing-hint provenance, full original 60-row denominator and nineteen earlier HTTP 429 digests.

## Missingness and immutable scope

| Date | City | Original missingness |
|---|---|---|
| 2026-10-10 | Miami | One sibling GET returned 429 |
| 2026-10-10 | Denver | One sibling GET returned 429 |
| 2026-10-11 | Denver | One sibling GET returned 429 |
| 2026-10-11 | Los Angeles | One sibling GET returned 429 |
| 2026-10-12 | Seattle | One sibling GET returned 429 |
| 2026-10-13 | Boston | Three sibling GETs returned 429 |
| 2026-10-13 | Miami | Three sibling GETs returned 429 |
| 2026-10-13 | Denver | Event GET returned 429; six sibling GETs not attempted |
| 2026-10-13 | Los Angeles | Two sibling GETs returned 429 |
| 2026-10-13 | Seattle | Event GET returned 429; six sibling GETs not attempted |
| 2026-10-14 | Boston | One sibling GET returned 429 |
| 2026-10-14 | Miami | One sibling GET returned 429 |
| 2026-10-14 | Denver | One sibling GET returned 429 |
| 2026-10-14 | Seattle | Event GET returned 429; six sibling GETs not attempted |

These are captured-forecast families with missing independently checked settlement evidence, **not October 4's five forecast failures**. Every original city/date remains in the new roster and replay output: 60 city-days, 12 dependent date clusters, 55 captures, five failures, 59 market-depth abstentions and the Miami October 4 reference conflict. The old 46 complete metadata families do not become scientifically admissible. No target is silently replaced, omitted or inferred from a binary payout.

The eighty-four IDs are joined directly to the original GCS `market.json` families, not rediscovered from labels or a fresh event query. The owner's archived raw finalized responses are separately decoded/hashed and matched to the original frozen rules/strikes. Their `settlement_ts` fields are retained **only as immutable, historically untrusted routing hints**. No archived temperature, old score or observation timestamp is made into a new authoritative result. All original files remain in place; raw 429 copies and source-preservation receipts are outside the repository checkout in the new operator evidence directory.

## Bounded future request contract

The candidate itself can issue **zero live requests**. A separately authorized and independently reviewed live adapter would be constrained by this contract:

| Boundary | Frozen v1 limit |
|---|---|
| Origin | `https://external-api.kalshi.com`, TLS; no caller origin |
| Method and headers | GET; fixed Accept JSON / Accept-Encoding identity; no account credentials |
| Concurrency | Exactly one request at a time |
| Initial request | `/trade-api/v2/historical/cutoff` |
| Subsequent requests | Exactly the 84 frozen market IDs, singular current or historical market endpoint |
| Maximum potential requests | **85 total**, including cutoff; no event, series, book, order, account or pagination request |
| Retry budget | **Zero**; no route fallback, retry header override, or optional retry proposal |
| Rate ceiling | Starts separated by at least **3 seconds**, at most 1/3 request per second |
| Per-request absolute deadline | **10 seconds**, including DNS, connect, TLS, headers and body |
| Overall absolute deadline | **900 seconds**, including waits and persistence |
| Raw response body cap | **65,536 bytes**, at most 5,570,560 retained body bytes over 85 responses |
| Header cap | **16,384 bytes**, enforced before buffering in a future adapter |
| Redirects | Reject every 3xx, including same-origin redirects; never follow |
| 403 / 429 | Preserve failure/status/body or bounded prefix; stop all further attempts |
| Other missing/invalid replies | No retry; retain failure; affected family stays unverified |
| Cancellation | Stop active work and all later requests; retain full roster and negative states |

The rate is a conservative chosen ceiling, not a verified provider entitlement or promise of avoiding 429. Request count, bytes, timeouts and logical pacing are checked in simulation. **A simulated elapsed time is not a real monotonic measurement.** No claim of live DNS/TLS/slow-body deadline enforcement follows from this offline code. The existing shared public transport has bounded socket timeouts but is not presented here as implementing this stronger absolute-deadline contract.

## Cutoff routing and accepted response schema

The existing repository-documented public contracts and retained provider payloads define the accepted subset: [cutoff endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps), [current market](https://docs.kalshi.com/api-reference/market/get-market), [historical market](https://docs.kalshi.com/api-reference/historical/get-historical-market). Supporting local implementations include `HistoricalCutoff.parse`, `public_read.py`, `wn_a2_settlement_reconciliation.py` and the full-precision bucket comparator. No current vendor OpenAPI snapshot was fetched in this offline preparation; rechecking current documentation/schema is a mandatory live-adapter review prerequisite. This candidate does not claim full current-vendor-schema conformance.

The cutoff must be an object containing timezone-aware `market_settled_ts`, `trades_created_ts` and `orders_updated_ts`. Compare the exact archived settlement timestamp hint for each market to the newly returned market-settled boundary: strictly older selects `/historical/markets/{ticker}`, strictly newer selects `/markets/{ticker}`. **Exact equality remains unverified and aborts planning** rather than guessing an inclusive boundary or probing both routes. There is no Chicago date heuristic, target-day-end approximation or silent alternate endpoint. A newly returned settlement timestamp differing from the old hint fails closed as `SETTLEMENT_TIME_CHANGED`; later corrections require separate append-only adjudication.

An accepted simulated market envelope requires a complete HTTP 200 JSON response and a singular `market` object. Unknown extra provider fields confer no authority. Required checks are:

- Exact frozen ticker and event; binary market type; unequivocal `finalized` status.
- Exact frozen primary/secondary rule text, strike type and both used/unused structured bounds; one lower tail, four contiguous integer-range buckets and one upper tail verified in the frozen family.
- Nonempty, finite, full-precision string `expiration_value`, never Boolean/float/binary payout; Decimal comparisons without rounding. Values outside the conservative -150°F to 150°F validator range remain unverified rather than being repaired.
- Timezone-aware close and settlement timestamps, close no later than settlement, exact route-hint timestamp agreement. No retrospective acquisition timestamp is assigned.
- YES/NO result, exact $0/$1 payout consistency and inclusive BETWEEN / strict LT / strict GT predicate consistency.
- All six siblings present and exposing the identical full-precision source string. Contradictory temperatures **or differing precision representations** remain unverified.

Malformed JSON, duplicate keys, nonfinite constants, invalid types, missing underlying values, incomplete framing, wrong length/type headers, changed rules or ladders cannot pass because their HTTP status is 200. Retained hashes cover only retained bytes; an oversized/truncated prefix is explicitly not a complete raw response.

## Offline adversarial validation and authority ceiling

`tests/test_wn_october_settlement_recovery.py` uses synthetic payloads only. A module-local autouse fixture denies DNS and socket connections in CI, and local execution additionally uses the OS deny-network profile. It covers valid finalized shape, all required failure cases, cancellation before/during a request, retry/routing prohibitions, body/header/time limits, 60-row preservation and attempts to relabel synthetic results as source or live authority. A consistency result is always `SIMULATED_METADATA_CONSISTENT`, never a source admission. All source-custody, point-score and live flags remain false, including for complete synthetic data. No scorer or probability/economic module is imported.

Reproducible Mac command from repository root (Python 3.12+ with the repository's test tools installed):

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' /Users/ksyme/miniforge3/envs/kalsh3/bin/python -m pytest -p no:cacheprovider tests/test_wn_october_settlement_recovery.py -q
```

## Live blockers deliberately left unresolved

Before any separately authorized live phase, a new review must implement and validate a fixed-origin adapter with absolute deadline supervision, cancellation, bounded read-before-buffering, pinned allowlist/route revalidation at the actual GET sink and no credentials. It must add an atomic create-once execution claim, create-once append-only local raw-body/headers/status/request-start/receipt/monotonic-duration/SHA evidence, interrupted-write recovery and a complete negative ledger before issuing the first request. The current module intentionally has no such execution/persistence capability. Its in-memory raw receipts are synthetic, not live provenance.

Original upstream source/clock/immutability/correction custody, the unresolved Miami historical digest, October 3's retrospective-only scoring chronology and the canonical D1 fixture absence remain unchanged. The original A0 fixture's separate isolated 8/8 replay is not a committed canonical fixture repair. Even complete later settlement responses would not authorize October scoring, probabilities, paper profit or production/trading promotion.

**OFFLINE GO requires the exact candidate's independent PASS and exact-head CI; LIVE NOT AUTHORIZED regardless.** No merge or live authority is implied by branch publication, a draft PR, synthetic tests, CI or an offline review.
