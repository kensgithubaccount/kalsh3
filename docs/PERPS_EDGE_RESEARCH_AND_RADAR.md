# Perps Edge Research and Radar Admission

Status: canonical research plan. Perps remains DISARMED, read-only, and `production_influence = 0`.

This milestone creates no Perps trading authority. Its purpose is to determine, with point-in-time evidence and conservative after-cost evaluation, whether any repeatable Kalshi Perps signal deserves admission to the regular radar.

## North-star question

Do observable conditions available before a hypothetical decision predict positive, reproducible **after-fee, after-funding, after-slippage, after-adverse-selection** value on Kalshi Perps?

Leverage is never evidence of edge. It affects capital efficiency and liquidation risk only.

## P-PERPS-EDGE-1 stages

A signal family advances only in this order:

1. immutable evidence capture;
2. hypothesis specification before evaluation;
3. historical replay with point-in-time inputs;
4. untouched walk-forward validation;
5. prospective shadow observation;
6. radar admission review.

Failure at any stage returns the family to research or rejects it. There is no automatic promotion.

## Research universe

Market discovery is dynamic and follows `PERPS_MARKET_METADATA_INVARIANTS.md`. Newly listed markets enter evidence collection only. They are not automatically model-, alert-, or execution-eligible.

Asset class and reference/index identity are explicit metadata. Crypto-specific assumptions must not be applied to metals or future non-crypto markets. Unsupported asset/reference combinations abstain.

## Edge families

Research these families independently so a successful result is attributable and falsifiable:

### Reference lag

Measure executable Kalshi bid/ask response to authoritative reference-price movement. For markets with an approved fast benchmark, estimate lead/lag and edge-decay curves by direction, volatility, spread, depth, time of day, and latency.

For the first checkpoint, prefer the market-bound Margin ticker `reference_price` when present. Kalshi documents it as the underlying reference index value scaled to one contract, with its own source `ts_ms`. This binds the benchmark observation to the exact Perps market without inferring a CF Benchmarks/Pyth index from ticker text. Preserve the enclosing ticker's market ticker, market `ts_ms`, local receipt time, and the nested reference `ts_ms` independently.

The Margin ticker channel is coalesced to at most one update per market per second (latest value wins). Phase 0 must therefore treat the market-bound reference stream as a coalesced source and must not claim sub-second benchmark observability or reconstruct discarded intermediate reference ticks. A later high-frequency checkpoint may use the CF Benchmarks 5 Hz or Pyth-underlying feeds only after exact market-to-index identity and timestamp semantics are separately reviewed and frozen.

Primary first experiment: market-bound reference-price movement -> Kalshi executable-book repricing.

### Funding convergence

Reconstruct the funding-window state only from observations available at each timestamp. Test whether accumulated premium/funding state predicts profitable convergence or post-funding behavior after all costs. Receiving funding is not itself positive EV.

### Cross-venue information

Use separately governed external spot/perpetual/futures venues as information sources, not assumed execution venues. Test conditional lead/lag only when reference confirmation, Kalshi freshness, liquidity, and cost gates all pass.

### Passive-liquidity / microstructure

Measure spread, depth, imbalance, replenishment, queue/fill uncertainty, trade arrivals, volatility, mark/reference movement, and post-fill adverse selection. Passive fills must use conservative queue assumptions; touching a quoted price is not a fill.

### Calendar / structural regimes

Evaluate funding boundaries, documented settlement/accounting boundaries, maintenance, weekends, traditional-market opens/closes, and scheduled macro events as explicit regimes. A calendar correlation is not an edge until validated prospectively.

### Relative value

Test related markets only with market-specific reference/index and contract metadata. No relationship may be inferred solely from ticker names. Cross-sectional models must account for different fees, funding, liquidity, contract size, tick size, and asset class.

## Point-in-time evidence contract

Every candidate/rejected candidate must be reconstructible from immutable observations genuinely available at decision time. Preserve, where applicable:

- Kalshi market metadata snapshot/hash and `exchange_index`;
- tick size, contract size, quantity granularity, leverage/margin terms, status, and important-info metadata;
- Kalshi book state, epoch/sequence, spread, depth, receipt/availability timestamps;
- ticker/mark/reference/funding observations and provenance;
- approved external benchmark/venue observations with event, receipt, and availability times;
- applicable fee tier/schedule observed at that time;
- maintenance/account capability state needed by the hypothesis;
- feature/model version and candidate creation/availability/decision/hypothetical-send timestamps;
- raw source lineage sufficient for deterministic replay.

Missing, stale, crossed, gapped, schema-unknown, ambiguously routed, or temporally impossible evidence fails closed.

## Conservative economics

For a hypothetical candidate, compute:

`net_edge = expected_gross_pnl + expected_funding_received - expected_funding_paid - entry_fee - exit_fee - slippage - adverse_selection_penalty - uncertainty_buffer`

All terms use consistent notional/quantity units and exact contract metadata. Fees are modeled on the applicable notional rather than margin posted. Funding is signed and may be zero/unknown. Unknown required economics => ABSTAIN.

Evaluate maker and taker cases separately. A research result must never choose the cheaper fill mode using hindsight.

## Fill and latency realism

Replay must model the full observed latency chain: source event -> source availability -> bot receipt -> feature availability -> decision -> hypothetical send -> conservative fill eligibility.

For taker hypotheses, price against contemporaneously available executable depth and reject size beyond modeled liquidity.

For maker hypotheses, model queue position conservatively and require evidence for a fill. Do not credit a fill merely because the market later traded through the price.

Run sensitivity tests with worse latency, slippage, fees, queue position, and adverse selection. An edge that disappears under small realistic perturbations is not radar-eligible.

## Evaluation discipline

Hypotheses, features, thresholds, holding/exit rules, and primary metrics are frozen before each validation slice.

Use chronological discovery/training and untouched forward validation. Never random-shuffle time-series evidence across the decision boundary. Avoid leakage from revised metadata, future funding outcomes, later book states, or today's contract terms.

Report candidate count, fill-eligible count, abstentions, gross and net expectancy, distribution/tails, drawdown, turnover, holding time, costs by component, regime/asset breakdown, calibration where probabilities are produced, and sensitivity results.

Small samples remain research. Statistical uncertainty must be explicit; do not promote on point estimate alone.

## Prospective shadow gate

A historically successful family must run prospectively with no production influence before radar admission. Prospective logic must be the frozen version evaluated in the preceding gate, except for separately reviewed bug fixes.

Shadow candidates and abstentions are logged before outcomes are known. Outcome scoring must be deterministic and append-only.

## Radar admission

Radar eligibility is per **signal family + market/reference coverage**, never a blanket Perps switch.

An admitted alert must include:

- family: `REFERENCE_LAG`, `FUNDING`, `CROSS_VENUE`, `PASSIVE_LIQUIDITY`, `CALENDAR`, or `RELATIVE_VALUE`;
- ticker and exchange index;
- observed discrepancy/signal and evidence timestamps;
- hypothetical direction and horizon, clearly labeled research/shadow;
- gross edge estimate;
- entry/exit fees;
- expected funding;
- modeled slippage and adverse-selection penalty;
- uncertainty buffer;
- conservative net edge;
- liquidity/depth relevant to the hypothetical size;
- model/version and validation status;
- freshness/metadata/risk gates;
- explicit reasons for any abstention.

Regular radar notifications remain OFF for a family until a reviewed admission decision confirms positive out-of-sample after-cost evidence, sufficient observations/regime coverage, acceptable tail behavior, prospective shadow confirmation, and healthy evidence plumbing.

Radar admission still grants **no execution authority**.

## First checkpoint: benchmark -> Kalshi lead/lag

Implement and evaluate this first because it has a clean falsification test and does not require predicting fundamental direction.

Phase 0 benchmark authority is deliberately narrow:

- include only markets whose Margin ticker evidence contains a valid nested `reference_price`;
- treat `reference_price.price` as the market-bound reference value and `reference_price.ts_ms` as its source timestamp;
- preserve the enclosing market ticker, message `ts_ms`, local receipt/availability time, and evidence fingerprint;
- do not infer or hard-code CF Benchmarks/Pyth index IDs from market ticker/title text;
- do not use the 5 Hz CF feed or Pyth-underlying stream until an exact market-to-index mapping is independently reviewed;
- because Margin ticker messages are coalesced to at most one per market per second, do not claim sub-second benchmark observability from Phase 0.

Evaluation sequence:

1. Select only markets with valid market-bound `reference_price` evidence and reviewed contract metadata.
2. Capture reference and Kalshi executable-book observations with independent source/receipt/availability timestamps.
3. Freeze impulse definition, minimum move, cooldown/de-duplication, and evaluation horizons before evaluating subsequent book movement.
4. Reject temporally impossible rows, stale/gapped books, ambiguous epochs, or reference observations unavailable by the hypothetical decision time.
5. At each valid impulse, snapshot the actually executable Kalshi book known at hypothetical decision/send time.
6. Measure book repricing over predeclared horizons; treat market-ticker coalescing as a known benchmark-resolution limitation.
7. Compute hypothetical economics only when fee, executable depth, slippage, funding, and timing authority are all present; otherwise the row is mechanism-only or ABSTAIN, not zero PnL.
8. Stratify by asset, impulse magnitude, volatility, spread, depth, session, and latency only as predeclared diagnostics; never select a winning subgroup after the fact.
9. Walk forward on untouched periods.
10. If the mechanism survives, freeze a separately reviewed high-frequency mapping/collector before using CF Benchmarks 5 Hz or Pyth-underlying evidence. If it fails, reject or redesign before widening the data source.

The Phase 0 deliverable is a falsifiable answer to whether market-bound reference moves systematically precede executable Kalshi-book repricing at the available source resolution. It is an evidence-backed PASS/BLOCK for further research, not an alert and not a trade.

## Explicit non-authority

P-PERPS-EDGE-1 authorizes research artifacts, deterministic replay, offline evaluation, and separately reviewed read-only evidence collection only. It authorizes no order construction, placement, amendment, cancellation, production sizing, capital allocation, arm/burn/final acknowledgement, or automated Perps execution.
