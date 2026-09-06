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