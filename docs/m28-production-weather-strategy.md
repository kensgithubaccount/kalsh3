# M28 — Parallel Production Weather Strategy

## Goal

Build a separate production weather-learning lane that can reach bounded autonomous real-money
trading as quickly as the evidence permits without changing, contaminating, or shortening the
frozen M27 prospective Chicago experiment.

The two tracks run in parallel:

- **M27 research track:** frozen Chicago experiment, prospective evidence, zero production
  influence except the separately supervised August canary policy already reviewed.
- **M28 production track:** adaptive, settlement-authoritative, leakage-resistant model training,
  champion/challenger evaluation, bounded promotion, and eventually autonomous execution through
  the existing M13/M15/M16/M27O safety machinery.

M28 may reuse shared facts and algorithms, but never uses a research configuration as runtime
production authority. Research artifacts may be imported only as immutable evidence or model
inputs and must be rebound into an M28 production artifact with its own training, evaluation, and
promotion identity.

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
- future bounded-policy promotion after predeclared evidence thresholds are met.

## Non-negotiable production truth

A weather model may not become M28 production-eligible from a physical-temperature proxy alone.
Its labels must be bound to an authoritative settlement mapping for the exact Kalshi family.
That mapping may use the settlement source named by the contract plus Kalshi's final settlement
record, but the mapping must be explicit, versioned, replayable, and independently testable.

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

M28 uses a champion/challenger loop.

1. The current champion produces immutable live predictions.
2. The same live feature snapshot is available to challengers in shadow.
3. After authoritative settlement, outcomes are joined to prior prospective predictions.
4. Retraining creates new immutable challenger models rather than mutating the champion in place.
5. Challengers are evaluated on identical event manifests and temporal holdouts.
6. Poorly calibrated, unstable, concentrated, or drifting challengers are rejected or
   quarantined.
7. Better challengers may move to one-contract canary, then bounded production, only through a
   promotion record and the existing execution/risk gates.

This allows continuous machine learning without letting an online optimizer directly rewrite the
live trader.

## Fastest safe implementation sequence

### M28A — production learning contracts

Status: started on `m28-production-weather-strategy`.

- authoritative settlement label manifest;
- leakage-resistant temporal split;
- content-addressed training dataset;
- immutable model artifact;
- automated retraining/challenger policy;
- bounded promotion contract;
- prospective prediction record.

No network, credentials, risk authorization, production mutation, or orders.

### M28B — authoritative weather settlement dataset

Build exact family-specific settlement mappings and historical labels. Start with `KXHIGHCHI`,
then generalize only to families with equivalent authority.

Target output: replayable historical rows containing the information set available at each
prediction cutoff and the exact final Kalshi outcome.

### M28C — train production ensemble

Train multiple candidates rather than betting the project on one model class. Initial tournament:

- empirical residual/calibration model derived from the existing weather work;
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

### M28E — autonomous shadow production loop

Run scheduled weather acquisition, feature creation, prediction, market comparison, and evidence
capture automatically. Champion and challengers run side by side. No order required.

### M28F — bounded real-money policy

Connect only a promoted M28 model to the existing deterministic risk/execution stack. Initial
limits should be narrower than the platform maximums and should expand only through explicit
policy versions supported by settled production evidence.

A model update does not itself authorize a trade. A trade still requires current data, market,
rules, fees, account, risk, halt/kill, exposure, and execution checks.

### M28G — autonomous retraining and promotion

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

## Initial production target

The first M28 target is not 'unlimited autonomy'. It is:

> a settlement-authoritative weather champion that can automatically scan supported markets,
> create immutable predictions, learn from resolved outcomes, and execute at most one contract
> under existing hard risk controls when a separately reviewed bounded-production policy permits
> it.

Once that loop is proven, capital and market-family coverage can expand incrementally without
redesigning the safety architecture.
