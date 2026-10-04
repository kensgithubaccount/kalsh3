# Perps Spec Authority Refresh + Reference-Lag Phase 0

Status: **RESEARCH ONLY / DISARMED / production_influence = 0**

Date: 2026-10-04

This checkpoint refreshes the read-only Perps research contract to the current
official Kalshi Margin REST/WebSocket specifications and adds a pure offline
mechanism diagnostic. It grants no alert, order, sizing, capital, or production
authority.

## First-party authority capture

A temporary GitHub Actions probe (PR #172, closed without merge) fetched the
machine-readable files directly from Kalshi on 2026-10-04:

- `https://docs.kalshi.com/perps_openapi.yaml`
  - SHA-256: `d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0`
  - bytes: 146790
  - OpenAPI: 3.0.0
  - document info.version: 0.0.1
- `https://docs.kalshi.com/perps_asyncapi.yaml`
  - SHA-256: `e5cc0f026b8e306e917860b870e23c9152d0d782c33137d42698f051a9dbe824`
  - bytes: 38219
  - AsyncAPI: 3.0.0
  - document info.version: 2.0.0

The probe used `curl -fsSL` against those exact first-party URLs and
`sha256sum` on the returned bytes. The probe branch was not merged.

## REST contract changes now bound

The official `MarginMarket` schema requires:

- `ticker`;
- `status`;
- `title`;
- `contract_size`;
- `underlying_multiplier`;
- `tick_size`;
- `fractional_trading_enabled`;
- `schedule`;
- `exchange_index`;
- `market_version`.

The refreshed parser therefore rejects missing or malformed
`market_version` and `underlying_multiplier`.

`market_version` is part of structural contract identity because Kalshi
documents that it can increase after corporate actions. A version change
invalidates dependent book assumptions.

`underlying_multiplier` is also part of structural contract identity because
it controls underlying units per contract-size unit.

Optional `asset_class` is preserved when present but is not inferred when
absent. Optional `product_metadata` is preserved immutably; it is not granted
execution semantics merely because it exists.

The exact market-bound `reference_price` remains a nested `TickerPrice` of
price plus source `ts_ms`. The official REST description identifies it as the
underlying reference price scaled per contract.

## WebSocket contract changes now bound

The official Margin ticker schema says:

- `reference_price`, when present, is the underlying reference index value
  scaled to one contract;
- its nested `ts_ms` is the index source timestamp;
- message envelopes may contain optional `sending_ts_ms`, the time Kalshi
  queued the frame at the network layer;
- current orderbook `lastUpdateReason` includes
  `CloseCancel`, `HaltCancel`, and `ReduceOnlyCancel` in addition to the
  previously modeled reasons.

The parser preserves optional `sending_ts_ms` rather than conflating it with
the market ticker timestamp, reference source timestamp, local receipt time, or
local availability time.

## Evidence-store rule

New append-only Perps evidence rows explicitly bind:

- `market_version`;
- `underlying_multiplier`;
- `asset_class` where applicable;
- optional `sending_ts_ms` for ticker state.

Legacy stores are not silently rewritten. If an old schema cannot accept the
stronger evidence record, the write fails closed and a new reviewed research
store is required.

## Phase-0 reference-lag mechanism

The first mechanism diagnostic uses only the market's own bound
`reference_price`; it does not infer an external CF Benchmarks or Pyth index
from ticker text.

An **impulse** is every observed nonzero change between two consecutive
market-bound reference observations that:

1. have identical ticker, exchange index, market version, underlying
   multiplier, and metadata identity;
2. have strictly increasing reference source timestamps;
3. have monotonic evidence availability;
4. have a source timestamp no later than local availability.

There is no post-hoc minimum-move threshold in Phase 0.

Because the Margin ticker stream is coalesced to at most one update per market
per second, the frozen diagnostic horizon grid is:

`1s, 2s, 5s, 10s`.

At each horizon, the evaluator uses the latest already-valid book evidence
available no later than the relevant cutoff. If no baseline book exists, the
row is an explicit `NO_BASELINE_BOOK` abstention. If a reconnect boundary
appears between baseline and the horizon state, the row is an explicit
`RECONNECT_WITHIN_HORIZON` abstention.

The measured quantities are quote repricing only:

- best-bid change;
- best-ask change;
- midpoint change when both sides exist;
- midpoint change in basis points when defined.

## Explicitly not measured

Phase 0 does **not** calculate:

- a fair value;
- a probability;
- an order direction;
- fill probability;
- fees;
- funding economics;
- slippage;
- adverse selection;
- hypothetical or realized P&L;
- capacity;
- production readiness.

A positive quote-response relationship is only mechanism evidence. Economic
edge requires a separately frozen checkpoint with executable timing, fee,
funding, liquidity, and adverse-selection authority.

## Independence and interpretation

Individual one-second impulses are serially dependent and must not be reported
as independent experiments. Phase 0 is descriptive/mechanism research.
Any inferential clustering or promotion threshold must be frozen in a later
checkpoint before its evaluation data are examined.

## Authority

This checkpoint remains inside the existing Perps shadow boundary:

- read-only;
- no live orders;
- no order construction;
- no account transfer;
- no production influence;
- no radar admission by itself;
- no autonomous trading authority.
