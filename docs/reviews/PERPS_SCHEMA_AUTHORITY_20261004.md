# Perps Schema Authority Refresh — 2026-10-04

Status: **READ-ONLY RESEARCH AUTHORITY ONLY**

This checkpoint refreshes the Perps market-metadata and Margin WebSocket parser
contracts without granting any order or production authority.

## Primary current authority

Kalshi's current public documentation is authoritative for the relevant live
schema semantics:

- REST market page: https://docs.kalshi.com/margin-rest/market/get-market
- Margin ticker page: https://docs.kalshi.com/margin-ws/websockets/market-ticker
- Margin orderbook page: https://docs.kalshi.com/margin-ws/websockets/orderbook-updates
- published REST specification URL: https://docs.kalshi.com/perps_openapi.yaml
- published WebSocket specification URL: https://docs.kalshi.com/perps_asyncapi.yaml

On 2026-10-04, the rendered official REST market documentation shows
`market_version`, `underlying_multiplier`, and `exchange_index` in the market
payload, exposes optional `asset_class`, and exposes timestamped
`reference_price`.

The official Margin ticker documentation states that ticker messages are
coalesced to at most one per market per second, latest value winning within the
window. This bounds Phase 0 reference-lag observability.

## Exact-byte cross-check

The current environment could inspect the rendered official documentation but
could not directly ingest the YAML response body from the official specification
URLs. Therefore the exact-byte checksums below come from an independently
synchronized public SDK that explicitly re-vendored Kalshi's upstream specs
after nightly contract drift on 2026-10-03.

Independent transport:

- repository: `TexasCoding/kalshi-python-sdk`
- commit: `1e5e3da97bd36c09f81c30c7f28bee01dc073490`
- commit message: `Reconcile perps market_version drift (v18.0.0) (#528)`

Recorded exact-byte identities:

- Perps OpenAPI SHA-256:
  `d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0`
- Perps AsyncAPI SHA-256:
  `e5cc0f026b8e306e917860b870e23c9152d0d782c33137d42698f051a9dbe824`

The synchronized Perps OpenAPI defines `MarginMarket` with required:

- `ticker`
- `status`
- `title`
- `contract_size`
- `underlying_multiplier`
- `tick_size`
- `fractional_trading_enabled`
- `schedule`
- `exchange_index`
- `market_version`

It describes `market_version` as starting at 1 and increasing after corporate
actions. It describes `underlying_multiplier` as underlying units per
contract-size unit. `asset_class` is optional and may expand over time.
`reference_price` is the underlying reference price scaled per contract and is
a timestamped price.

The synchronized Perps AsyncAPI defines orderbook `lastUpdateReason` values
including `CloseCancel`, `HaltCancel`, and `ReduceOnlyCancel`, and describes
ticker `reference_price.ts_ms` as the index source timestamp.

## Acceptance boundary

For this research-only parser refresh, current rendered official docs plus the
independently synchronized exact-byte hashes are accepted as sufficient to
update fail-closed read-only evidence parsing.

This is **not** sufficient authority for order-capable or production execution.
Before any Perps execution milestone, directly acquire the official spec bytes
from Kalshi, independently hash them, bind the execution-capable parser to those
exact bytes, and separately review order-side semantics.

## Parser consequences

- `market_version` becomes required, exact integer, and >= 1.
- `market_version` participates in structural contract identity.
- `underlying_multiplier` becomes required, exact Decimal, and positive.
- `underlying_multiplier` participates in structural contract identity.
- optional `asset_class` is preserved but never inferred from ticker/title.
- changing `asset_class` changes full metadata identity but not the structural
  contract hash.
- current documented orderbook update reasons are accepted; unknown reasons
  still fail closed.
- market-bound `reference_price` remains the only Phase 0 benchmark authority.
- no CF Benchmarks or Pyth index mapping is inferred.
- production influence remains zero.
