# October 15–22, 2026 Kalshi API integration gates

Status: specification and test requirements; no execution authority.

Source: https://docs.kalshi.com/changelog (October 15 and October 22 entries). Effective dates must be verified in the target environment. October 22 behavior is **future-dated** as of October 21.

## P-PERPS-EDGE-1: bar-based funding (October 22)

- Version funding reconstruction by market, configuration provenance, and effective time. Never silently apply the prior per-second algorithm to October 22+ observations.
- Obtain and validate authoritative **market-specific bar duration**. A bar is clock-aligned and at least one second. The changelog does not specify a universal bar size or an authoritative discovery field for it: UNKNOWN => ABSTAIN.
- Preserve each constituent second's book/index availability, halt state, source and receipt/availability timestamps, plus raw provenance. A bar is invalid if **any** constituent second has a trading halt, or the bar lacks an order-book sample or index price.
- Apply `funding_invalid_premium_handling` **per bar**: `dampen` includes invalid bars as zero; `exclude` removes invalid bars from the averaging denominator. Never confuse no premium with a measured zero premium.
- Interpret estimate `premium_index` as the last evaluated **bar's** premium and `premium_index_ts` as its final second, not a single-second measurement.
- Keep informational premium-index history separate from finalized funding. Contract-creation metadata is not proof that current clearing configuration is unchanged.
- Reconcile independently reconstructed estimates with official provisional and finalized values, preserving discrepancies. Mismatch, missing bar duration, missing validity, or unknown effective configuration => evidence-quality ABSTAIN, never optimistic funding edge.

Required deterministic negative tests: pre/post effective-date behavior; 1s vs multi-second aligned bars; halt in any second; missing book; missing index; dampen vs exclude denominator; incomplete boundary bars; timezone/clock alignment; missing/changed bar duration; contradictory finalization; no future-data leakage. Test fixtures must not invent an authoritative market bar duration.

## Future FIX emergency market cancel (October 22)

- `OrderMassCancelRequest (35=q)` with `530=1` and `Symbol (55)` is documented for Predictions and Margin; `530=6` is unchanged.
- An accepted `OrderMassCancelReport (35=r)` uses `531=1` and echoes symbol; unknown tickers reject with `532=1`.
- Treat the report as an **acknowledgement, not proof of zero remaining orders**. Reconcile actual open orders/fills across entry methods and account/exchange routing before marking the market flat of resting orders.
- Explicitly distinguish cancellation of resting orders from flattening positions; mass cancel does **not** close exposure.
- Keep this in a future reviewed execution-safety contract only. No FIX writes, emergency cancellation calls, or Perps order authority are granted by this document.

Required negative tests before any execution milestone: wrong symbol/account/shard, unknown ticker rejection, timeout/ambiguous acknowledgement, partial/racing fills, cancel-report/open-order disagreement, repeat/idempotency policy, and inability to verify post-cancel state.

## Predictions balance rate-limit separation (October 15)

- `GET /trade-api/v2/portfolio/balance` uses a dedicated **Predictions balance** token bucket: Basic/Advanced 1 request/second; higher tiers 5 requests/second; no burst.
- Do not debit the general Predictions Read bucket for this endpoint. Do not generalize the rule to Margin balance/risk endpoints.
- Throttle by the applicable credential/account tier and honor HTTP 429 without tight retry loops; account-tier uncertainty must not be treated as high-tier allowance.
- Prefer fill, settlement, and transfer driven reconciliation, with bounded balance refresh when necessary. Missing/stale balance authority must block any dependent production action, never imply zero exposure.

Required tests: independent balance/general-read budgets, no burst, 429/backoff, unknown tier conservative fallback, no accidental Margin application, and event-driven balance reconciliation.

## Intra-exchange transfer GET reconciliation (October 22)

- The optional `client_transfer_id` may be returned by both `GET /portfolio/intra_exchange_instance_transfers` (list) and `GET /portfolio/intra_exchange_instance_transfers/{transfer_id}` (detail). This is a **response-schema addition**, not permission to initiate a transfer.
- Preserve the original caller-generated UUID, exchange-assigned transfer ID, source and destination accounts/instances, amount, creation/request/receipt timestamps, status, and immutable raw GET evidence. Treat UUID identity as scoped to the originating primary-account transfer request; do not extrapolate to numbered subaccounts.
- When a response includes `client_transfer_id`, validate UUID syntax and require an exact match to the locally persisted request identity before joining records. An ID mismatch, conflicting list/detail values, duplicate UUID mapped to distinct transfers, or disagreement in amount, direction, account, or state is a **RECONCILIATION_BLOCK** requiring investigation.
- When the optional field is absent or null, represent it as **UNKNOWN**, not as a negative acknowledgement, failed transfer, or permission to issue another request. Reconcile via exchange transfer ID and independent account/balance evidence; if uniqueness or finality cannot be established, remain blocked.
- A successful HTTP response or a matching UUID alone does not prove transfer finality. Require authoritative transfer status and independent before/after account-balance reconciliation, with freshness and pagination completeness checks. Pending, stale, incomplete, ambiguous, or contradictory evidence must fail closed.
- For an ambiguous write outcome (including timeout), retain the original client UUID and exact immutable request payload. Do not automatically retry, mutate, or create a replacement transfer. Any future retry policy requires separate review of endpoint idempotency, primary-account scope, and the complete reconciliation state.
- Preserve the distinction between primary-account client-ID support and numbered subaccount limitations. Do not infer that `exchange_index` routes margin subaccount transfers; the documented subaccount transfer endpoint ignores it.

Required **deterministic offline tests** before any runtime adoption: matching UUID across list and detail; omitted and null optional fields; malformed UUID; UUID mismatch; list/detail conflict; duplicate UUID across exchange transfer IDs; pagination gaps; wrong account, instance, amount, or direction; stale or pending status; final status without balance corroboration; ambiguous timeout; attempted second request with changed payload; primary-account versus numbered-subaccount scope; and confirmation that no test path invokes transfer POST, credentials, FIX writes, or order endpoints. Use synthetic immutable fixtures only, and verify a RECONCILIATION_BLOCK for every ambiguous or contradictory case.

## Boundary

These are implementation requirements for follow-on checkpoint-scoped code and tests. No live collector change, funding algorithm replacement, FIX write, capital transfer, alert promotion, or trading authorization is implied. Perps remains DISARMED with `production_influence = 0`.
