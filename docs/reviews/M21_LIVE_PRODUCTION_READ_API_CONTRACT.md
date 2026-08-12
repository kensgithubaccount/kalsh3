# M21 — Live Production-Read API Contract Correction

## Finding and source status

The first production-read setup attempt on the live HTTPS AWS stack reached the one-use setup workflow but
failed with a generic WSGI error. The correction was independently reviewed against the current official
Kalshi documentation and OpenAPI contract facts supplied for this task. Production-read acceptance remains
pending until deployment and a successful retry.

Official references reviewed by the operator: Kalshi's API reference pages for [API keys](https://docs.kalshi.com/api-reference/api-keys/get-api-keys),
[balance](https://docs.kalshi.com/api-reference/portfolio/get-balance),
[limits](https://docs.kalshi.com/api-reference/account/get-account-api-limits),
[positions](https://docs.kalshi.com/api-reference/portfolio/get-positions),
[orders](https://docs.kalshi.com/api-reference/portfolio/get-orders),
[fills](https://docs.kalshi.com/api-reference/portfolio/get-fills), and
[settlements](https://docs.kalshi.com/api-reference/portfolio/get-settlements).

## Current contract facts revalidated

- `GET /trade-api/v2/api_keys` returns `api_keys` items with `api_key_id`, `name`, `scopes`, and
  `subaccount`. Enrollment positively matches one `api_key_id`, requires scopes to equal exactly `["read"]`,
  and positively requires primary subaccount `0`; missing, duplicate, ambiguous, expanded, write-capable, or
  non-primary keys fail closed.
- `GET /trade-api/v2/portfolio/balance?subaccount=0` returns integer-cent `balance` and
  `portfolio_value`, fixed-point `balance_dollars`, and integer `updated_ts`. Optional `balance_breakdown`
  is an array of objects (currently documented with `exchange_index` and fixed-point `balance` fields); it
  may be empty or omitted for a subaccount-restricted API key. The parser validates the documented container
  type without inventing required child fields. Cents are divided by an exact `Decimal(100)`; floats,
  non-object breakdown entries, missing required top-level fields, and negative totals are rejected. No
  undocumented equality between the cent and dollar balance fields is assumed.
- `GET /trade-api/v2/account/limits` returns `usage_tier` and nested `read`/`write` objects containing
  integer `refill_rate` and `bucket_capacity`, plus a required array of grant objects (currently documented
  with fields such as `level`, `source`, and `expires_ts`). The parser rejects non-object grant entries but
  does not invent required child fields. Obsolete flat, incomplete, and floating-point token-bucket shapes
  are rejected. Write limits are informational and grant no write interface or authority.
- `GET` positions (`market_positions`), orders (`orders`), fills (`fills`), and settlements
  (`settlements`) remain cursor-paginated portfolio collections. Every request explicitly supplies
  `subaccount=0`; empty pages are valid, a cursor is followed to exhaustion, and malformed/repeated/failed
  pages discard the complete refresh. Existing fixed-point dollar fields remain strict strings parsed as
  `Decimal`; removed legacy cent-cost fields remain rejected.

No speculative aliases or legacy fallbacks were added. The production account gateway still exposes only
GET/HEAD signing and a fixed production origin.

## Setup and safety review

Authentication/scope rejection, malformed current responses, bounded timeout/upstream failure, rate limit,
and account reconciliation failure now produce a generic category-specific setup message. Exception text,
upstream bodies, API key IDs, PEMs, signatures, setup tokens, passwords, TOTP material, and other submitted
values are never rendered. Validation occurs before owner, authentication, encrypted vault, or consumed-token
configuration is stored; tests prove failures leave setup unused and credentials absent.

Production signer remains **DISARMED**; production-write credential **NONE**; bounded autonomy **OFF**;
subaccount exactly **0**; real-money orders **NONE**. Deployment and successful live production-read
reconciliation, current-doc fetch from CI, live pagination with nonempty account data, and ongoing API drift
monitoring remain pending. Full production readiness is not claimed.
