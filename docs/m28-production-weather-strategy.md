# M28 — Parallel Production Strategy

## Goal

Build a separate adaptive production-learning lane that can reach bounded autonomous real-money
trading as quickly as the evidence permits without changing, contaminating, or shortening the
frozen M27 prospective Chicago experiment.

Chicago daily highs are the first proving ground, not the architecture. M28 must make it cheap to
add the next supported weather city/series, then additional weather outcome types, and eventually
non-weather Kalshi families whose evidence and settlement truth come from entirely different
sources and model classes.

The two tracks run in parallel:

- **M27 research track:** frozen Chicago experiment, prospective evidence, zero production
  influence except the separately supervised August canary policy already reviewed.
- **M28 production track:** adaptive, settlement-authoritative, leakage-resistant model training,
  champion/challenger evaluation, cross-family opportunity ranking, bounded promotion, and
  eventually autonomous execution through the existing M13/M15/M16/M27O safety machinery.

M28 may reuse shared facts and algorithms, but never uses a research configuration as runtime
production authority. Research artifacts may be imported only as immutable evidence or model
inputs and must be rebound into an M28 production artifact with its own training, evaluation, and
promotion identity.

## Architecture rule: configuration, not Chicago hard-coding

The production-learning core is market-family agnostic. A family is registered with:

- a stable family/domain identity;
- a reviewed market-discovery selector;
- an authoritative settlement mapping;
- the governed sources it may consume;
- named feature groups;
- compatible model recipes;
- an enabled/disabled state.

A source is separately registered with its allowed roles (forecast, primary observation,
settlement, market data, cross-venue, news, social, or structured data), supported domains,
authority, freshness bound, and production eligibility.

A model recipe is separately registered with its supported domains, required feature groups,
calibration method, retraining capability, and ensemble capability.

That separation is what lets the same scanner/tournament/promotion/risk machinery move from
`KXHIGHCHI` to multiple weather cities without rewriting the learning core, and later from weather
to economics, politics, sports, crypto, or other Kalshi families by adding reviewed adapters,
sources, settlement mappings, feature groups, and model recipes rather than a new trading system.

## Capabilities retained

M28 is intended to preserve and expand the platform's existing capabilities:

- NOAA/NDFD weather acquisition and provenance;
- historical weather calibration data;
- exact contract semantics and settlement-source metadata;
- live Kalshi market, orderbook, fee, rules, and lifecycle evidence;
- probability calculations and calibrated uncertainty;
- feature/model ablation and source contribution analysis;
- market-relative skill and Brier-score evaluation;
- champion/challenger comparison;
- drift detection and quarantine;
- deterministic risk sizing and portfolio limits;
- immutable prospective prediction records;
- outcome-linked learning after authoritative settlement;
- automatic retraining and challenger creation;
- cross-family opportunity scoring after costs and risk;
- future bounded-policy promotion after predeclared evidence thresholds are met.

## Preserve the useful M27 signal

The frozen M27 Chicago model remains a mandatory heritage baseline for the initial M28 weather
tournament. It may keep producing prospective benchmark probabilities and may be scored against
authoritative settled outcomes alongside every new challenger, but it retains zero direct
production influence and is never retrained in place.

A correct directional result from an M27 prediction is useful evidence that the existing model
contains signal. It is not, by itself, enough to establish calibration, market-relative edge, or
profitability. M28 therefore records those results and compares them over many settled events
rather than either discarding the existing model or promoting it from a single win.

## Non-negotiable production truth

A weather model may not become M28 production-eligible from a physical-temperature proxy alone.
Its labels must be bound to an authoritative settlement mapping for the exact Kalshi family.
That mapping may use the settlement source named by the contract plus Kalshi's final settlement
record, but the mapping must be explicit, versioned, replayable, and independently testable.

For non-weather families the same rule applies: the training target is the exact contract outcome
under its reviewed settlement semantics, not a convenient proxy that merely resembles it.

This is the key distinction from M27: M28 is allowed to learn quickly, but it is not allowed to
learn the wrong target quickly.

## Leakage controls

Every production model must bind to:

1. an immutable feature schema;
2. content-addressed feature artifacts;
3. authoritative resolved labels;
4. a strict prediction cutoff rule;
5. ordered train/validation/test windows;
6. an immutable training dataset manifest;
7. an immutable model artifact;
8. a separate evaluation manifest;
9. a separate production promotion record.

No resolved outcome, post-cutoff observation, post-cutoff market move, or later forecast may enter
a feature set for an earlier prediction.

## Learning architecture

M28 uses a champion/challenger loop within each market family, plus a separate cross-family
opportunity layer.

1. Each family champion produces immutable live predictions.
2. The same live feature snapshot is available to compatible challengers in shadow.
3. After authoritative settlement, outcomes are joined to prior prospective predictions.
4. Retraining creates new immutable challenger models rather than mutating the champion in place.
5. Challengers are evaluated on identical event manifests and temporal holdouts.
6. Poorly calibrated, unstable, concentrated, or drifting challengers are rejected or
   quarantined.
7. Better challengers may move to one-contract canary, then bounded production, only through a
   promotion record and the existing execution/risk gates.
8. Separately, currently promoted family models can be ranked across the entire supported Kalshi
   universe by expected value after fees, uncertainty, liquidity, freshness, concentration, and
   portfolio risk. The best opportunity wins capital; a family does not receive capital merely
   because it was scanned.

This allows continuous machine learning without letting an online optimizer directly rewrite the
live trader.

## Fastest safe implementation sequence

### M28A — production learning + market-agnostic registry

Status: in progress on `m28-production-weather-strategy`.

- authoritative settlement label manifest;
- leakage-resistant temporal split;
- content-addressed training dataset;
- immutable model artifact;
- automated retraining/challenger policy;
- bounded promotion contract;
- prospective prediction record;
- governed source registry;
- market-family registry;
- reusable model-recipe registry;
- frozen M27 heritage-baseline contract.

No network, credentials, risk authorization, production mutation, or orders.

### M28B — authoritative weather settlement dataset

Build exact family-specific settlement mappings and historical labels. Start with `KXHIGHCHI`,
but implement the dataset builder through the family registry so the next validated weather series
is an adapter/configuration addition rather than a fork.

Target output: replayable historical rows containing the information set available at each
prediction cutoff and the exact final Kalshi outcome.

### M28C — train production ensemble

Train multiple candidates rather than betting the project on one model class. Initial tournament:

- frozen M27 empirical residual/calibration model as heritage baseline;
- retrainable empirical residual/calibration challenger;
- logistic/ordinal baseline;
- gradient-boosted tabular model;
- calibrated ensemble/champion blend.

Candidate feature groups may include forecast level, horizon, recent forecast revisions,
historical residual distribution, seasonality, observed conditions available before cutoff,
source agreement/disagreement, contract geometry, and strictly pre-cutoff market information.
Market prices are benchmark features only when the declared strategy permits them; evaluation
must still report skill relative to the market so the model cannot claim edge by merely copying
price.

### M28D — out-of-sample evaluation and promotion evidence

Use rolling-origin / temporal holdouts, event-level scoring, calibration, market-relative skill,
concentration checks, ablation, and drift stress tests.

The first limited-production policy should be intentionally tiny. Historical sample size may
support model evaluation, but real-money exposure still begins with a one-contract canary.

### M28E — autonomous multi-family shadow production loop

Run scheduled acquisition, feature creation, prediction, market comparison, and evidence capture
automatically for every enabled family. Champion and challengers run side by side. No order
required.

The loop must be registry-driven: adding another supported weather family must not require changes
to the core scheduler, learner, scorer, promotion engine, or risk boundary.

### M28F — cross-family opportunity allocator

Rank eligible opportunities from all supported families on a common after-cost/risk basis while
keeping each family's probability model and evidence semantics separate. This is the layer that
ultimately lets the system find the best opportunity across weather first and across Kalshi later.

### M28G — bounded real-money policy

Connect only promoted M28 models to the existing deterministic risk/execution stack. Initial
limits should be narrower than the platform maximums and should expand only through explicit
policy versions supported by settled production evidence.

A model update does not itself authorize a trade. A trade still requires current data, market,
rules, fees, account, risk, halt/kill, exposure, and execution checks.

### M28H — autonomous retraining and promotion

Once enough settled production events exist, scheduled training may automatically create
challengers. Promotion can become policy-gated only after its evidence requirements and capital
bounds are separately reviewed and frozen. Rollback and quarantine remain automatic on adverse
signals.

## Parallelism with M27

M27 and M28 should run simultaneously.

M27 provides clean scientific evidence because it stays frozen. M28 provides speed because it can
incorporate newly settled observations into new challenger versions. Agreement between the tracks
is useful evidence; disagreement is a diagnostic signal, not permission to alter M27.

The same underlying NOAA/Kalshi evidence may be referenced by both tracks when provenance permits,
but each track owns its own model/configuration/promotion identity.

## Expansion path

The intended expansion sequence is:

1. Chicago daily high as the reference implementation;
2. every additional weather family with validated discovery, evidence, and settlement mapping;
3. a weather-wide opportunity allocator choosing among all supported cities/outcome types;
4. additional Kalshi domains, each with its own reviewed source adapters, feature schemas,
   settlement mappings, and model tournament;
5. one common cross-family allocator feeding the existing risk/execution boundary.

The core invariant is that breadth never substitutes for evidence quality. Unsupported families
remain disabled rather than being guessed through a generic model.

## Initial production target

The first M28 target is not 'unlimited autonomy'. It is:

> a settlement-authoritative weather champion that can automatically scan every enabled weather
> family, create immutable predictions, compare itself with the frozen M27 baseline, learn from
> resolved outcomes, rank opportunities after costs and risk, and execute at most one contract
> under existing hard risk controls when a separately reviewed bounded-production policy permits
> it.

Once that loop is proven, capital and market-family coverage can expand incrementally without
redesigning the safety architecture.
