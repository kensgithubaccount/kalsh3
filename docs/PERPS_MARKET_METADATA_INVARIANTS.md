# Perps Market Metadata Invariants

Status: canonical integration constraint for future Kalshi Perps work.

This document supplements `docs/PERPS_SHADOW_RESEARCH.md`. It does not expand the current Perps authority boundary: Perps remains research-only / DISARMED unless a later reviewed milestone explicitly grants additional authority.

## Mutable market universe

The Perps integration MUST NOT hard-code a fixed set of supported tickers.

Supported Perps markets are exchange-controlled metadata and may expand, contract, or change over time. The integration must discover the currently supported margin-market universe from the authoritative Kalshi Margin API at runtime or during a bounded metadata-refresh operation.

A previously observed ticker list may be used only as fixture/test data. It is never production authority for market availability.

If current authoritative market discovery is unavailable, stale, internally inconsistent, or cannot be tied to the intended environment/exchange index, the system must fail closed for any behavior that would depend on that metadata. It must not infer that an historically supported asset is still tradable.

## Mutable leverage and margin terms

Leverage limits MUST NOT be hard-coded by ticker.

Leverage and other market-specific margin terms are mutable exchange metadata. Any future strategy, sizing, risk, liquidation-distance, capital-efficiency, or after-cost calculation must use the current authoritative values observed for the specific market and exchange index.

If a required leverage or margin field is absent, stale, malformed, or unsupported by the reviewed schema, the system must abstain from calculations or actions that require it rather than substitute a historical/default value.

Research artifacts may preserve historical leverage observations for point-in-time replay, but replay must use the value that was known/available at the historical decision timestamp rather than today's value.

## Required market-metadata capture

For each discovered Perps market, preserve every authoritative field needed to reconstruct the contract and its risk/economic interpretation. At minimum, where supplied by Kalshi, this includes:

- ticker / market identity;
- `exchange_index`;
- contract size;
- minimum or permitted order/position size and quantity granularity;
- tick size / price granularity;
- fractional-trading capability;
- underlying/reference benchmark or index identity;
- current leverage limit(s) and associated margin terms;
- market/trading status and any availability flags;
- source observation, receipt, and availability timestamps;
- normalized metadata hash plus recursively retained raw payload for schema migration/replay.

Fields absent from the authoritative response remain unknown. Do not synthesize them from ticker naming, UI copy, another asset, or an older snapshot.

## Refresh and change detection

A future live-capable Perps subsystem must treat metadata refresh as part of market-state reconciliation, not one-time installation configuration.

Changes to the supported universe, `exchange_index`, leverage, margin terms, contract sizing, minimum size, reference index, or other structural/economic fields must be detectable and auditable. Structural changes that invalidate existing assumptions must quarantine or abstain until the new metadata has passed the applicable validation/reconciliation boundary.

A newly listed market is not automatically strategy-eligible. Discovery establishes existence only. Separate reviewed gates must establish data quality, model coverage, liquidity, fee/funding economics, risk limits, and execution eligibility.

## Separation from Predictions

Perps metadata and routing remain separate from Predictions contract semantics, market IDs, settlement rules, and account/execution logic. No Predictions-market default may be used to fill a missing Perps field.

`exchange_index` remains a first-class Perps routing/partition key. Any future order-group implementation must enforce Kalshi's same-exchange-index requirement rather than grouping markets solely by ticker or strategy.

## Safety / authority boundary

These requirements improve research correctness and future integration readiness only. They authorize no credential use beyond already-reviewed read-only boundaries, no arm/burn/final acknowledgement, no order construction, no order placement/amend/cancel, no sizing for production, and no capital allocation.

Any future milestone that introduces Perps execution must demonstrate, with negative tests, that stale/unknown market availability and stale/unknown leverage or margin metadata fail closed before order authorization.

## September 17, 2026 API/schema integration constraints

The authoritative Margin market responses from `GET /trade-api/v2/margin/markets` and
`GET /trade-api/v2/margin/markets/{ticker}` expose the market's configured `tick_size`.
The integration MUST treat that returned value as mutable exchange metadata. It must not
derive a price grid from historical observations, ticker identity, or a static default.

A `tick_size` change is structural for order-price validation and book interpretation.
Metadata reconciliation must detect it, invalidate cached price-grid assumptions, and
quarantine/abstain from any dependent simulation or future execution until the refreshed
metadata has passed validation. Historical replay must retain and use the point-in-time
tick size known at the decision timestamp.

Margin market metadata may also contain optional
`product_metadata.important_info.markdown`. Preserve this field, when supplied, in the
normalized/raw metadata evidence and detect changes to it. Because its semantics may
contain market-specific operational or risk information not represented by a dedicated
schema field, a newly present or changed value must be surfaced for review rather than
silently discarded or interpreted as trading authority.

The reviewed Margin WebSocket AsyncAPI is itself a versioned dependency. Nullable fields,
enums, and response-field definitions must be validated against the reviewed current
schema rather than frozen generator assumptions. A schema refresh must not silently
widen accepted runtime inputs; incompatible changes require explicit review.

Subscription acknowledgement is not evidence completeness. Even though Kalshi corrected
a race that could drop events arriving immediately after a `subscribed` acknowledgement,
including on `cfbenchmarks_value_5hz`, collectors must retain sequence/freshness,
epoch/reconnect, and gap-detection protections. No correctness invariant may depend on
the historical bug remaining fixed.

Any future Margin FIX recovery path using `EventResendRequest (35=U1)` must model resend
support as an explicit account/session capability because that functionality may require
account allowlisting. Absence or uncertainty of that capability must fail closed for a
recovery design that depends on it; it must never be assumed from FIX connectivity alone.


## October 1, 2026 Margin market schema additions

The current official Margin market response now marks `market_version` and
`underlying_multiplier` as required fields and exposes optional `asset_class`.
These are research and future-integration authority, not execution authority.

`market_version` is a structural identity field. Kalshi documents that it can
increase after a corporate action such as a stock split. Preserve it
point-in-time, detect changes, and treat a changed version as invalidating
dependent market assumptions. Any future order-capable milestone must bind the
reviewed current version rather than assume that a ticker alone identifies an
unchanged contract.

`underlying_multiplier` is the number of underlying units per contract-size
unit. Preserve it as an exact Decimal and include it in the structural contract
identity. Missing, malformed, or changed multiplier data must fail closed for
any reference-price normalization, notional, basis, relative-value, or future
execution calculation that depends on it.

`asset_class` is optional and may expand over time. Preserve it when present.
It can constrain research-family applicability, but absence must remain unknown;
do not infer an asset class from ticker spelling or title text.

The current Margin `reference_price` is explicitly the underlying reference
price scaled per contract and carries its own source `ts_ms`. The Margin
ticker WebSocket further documents that this underlying reference is supplied by
CF Benchmarks for crypto perps and Pyth for metals, commodities, and other
Pyth-indexed perps. This provides a market-bound reference observation suitable
for future read-only latency/lead-lag research without inferring a benchmark
mapping from ticker names. It does not by itself prove that a trading edge
exists, and it does not authorize replacing the exact market-specific source
with a different external index.

Before runtime code adopts these new required fields, refresh and hash the exact
official Perps OpenAPI/AsyncAPI bytes and update the parser provenance. A
documentation page or generated client alone must not silently widen the parser's
accepted authority.


## October 4, 2026 live `market_version` divergence

A no-secret production REST probe on 2026-10-04 confirmed the active Bitcoin
Margin market is `KXBTCPERP`. Its exact market payload contains the required
`market_version` key with JSON null, despite the reviewed OpenAPI describing
that field as a required integer.

For read-only research evidence, the required-key invariant remains intact but
the value contract is narrowed to **positive int32 or explicit null**. Explicit
null means UNKNOWN and must be preserved literally. Missing values, booleans,
strings, zero, negative values, and out-of-range integers remain invalid.

UNKNOWN must never be replaced with a guessed or default version. Because null
participates in the structural contract hash and full metadata hash, a later
transition to a concrete version is an observable contract-identity change.
This read-only exception grants no execution authority; any future order-capable
milestone must separately establish how a concrete exchange version is bound.
