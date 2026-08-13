# KALSHI PRODUCTION V3 — MASTER CODEX BUILD SPECIFICATION

## ROLE AND EXECUTION MODE

You are the principal engineering agent responsible for taking this project from an empty repository to a complete, tested, reviewable, reproducible private GitHub repository.

You are not being asked to make a toy, proof of concept, generic “AI trading bot,” or cosmetic dashboard.

You are building a production-grade prediction-market research, learning, portfolio, risk, execution, and operations platform for Kalshi.

You must work autonomously for as long as useful work remains.

**DO NOT stop after writing a plan.**
**DO NOT stop after scaffolding.**
**DO NOT stop after one milestone.**
**DO NOT repeatedly ask the user questions for choices that can reasonably be resolved by conservative engineering judgment.**
**DO NOT tell the user to perform local development steps.**

Instead:

1. Read this entire specification before writing code.
2. Inspect the current repository, if any.
3. Establish durable repository instructions and project documentation.
4. Create an implementation roadmap with explicit acceptance gates.
5. Begin implementation immediately.
6. Work milestone-by-milestone.
7. Test every milestone.
8. Review every milestone critically from all required professional perspectives.
9. Correct deficiencies before proceeding.
10. Commit coherent milestones.
11. Continue through every milestone that does not require a genuinely human-only action.
12. Push the completed work to a private GitHub repository if repository permissions allow it.
13. Finish with a detailed completion/blocker report.

Do not stop merely because a later milestone requires a human action. Complete everything else first.

If a human-only blocker is encountered, record it in:

`docs/HUMAN_ACTIONS_REQUIRED.md`

then continue all independent work.

Examples of genuine human-only actions:

- providing a Kalshi production API credential;
- providing an LLM provider API key;
- authorizing an actual real-money order;
- choosing to arm autonomous production trading;
- approving an increase in financial risk limits;
- approving a previously unapproved production data source;
- completing GitHub authentication if no authenticated GitHub session exists;
- resolving an external account/legal/compliance issue.

Never invent credentials.

Never request that a private key be pasted into a chat message, source file, GitHub issue, commit, Codex prompt, or log.

---

# 1. MISSION

Build **Kalshi Production v3**, a continuously operating cloud system whose objective is:

> **MAXIMIZE REALISTIC LONG-RUN AFTER-COST EXPECTED VALUE**

subject to:

- survival of capital;
- strict risk limits;
- probability calibration;
- uncertainty;
- liquidity;
- execution quality;
- source reliability;
- model decay;
- operational reliability;
- security;
- legal/compliance eligibility;
- complete auditability.

No strategy, model, source, AI provider, or implementation may claim or imply guaranteed profitability.

The central philosophy is:

> **The system should become better at being selective, not merely better at generating trades.**

The winning behavior may often be:

> **DO NOT TRADE.**

The platform must discover genuine information, modeling, semantic, structural, execution, or capital-allocation advantages rather than treating “using AI” as an edge.

---

# 2. THE FOUR PRODUCTS INSIDE ONE PLATFORM

Build four tightly integrated but security-separated products.

## A. Intelligence + Learning System

Continuously:

- discover the complete relevant Kalshi universe;
- maintain real-time market state;
- understand exact settlement semantics;
- ingest authoritative structured sources;
- ingest alternative-venue information;
- monitor breaking information;
- ingest permitted social/news signals;
- identify cross-market relationships;
- generate point-in-time features;
- produce independent probabilistic forecasts;
- quantify uncertainty;
- compare forecasts with executable market prices;
- freeze predictions before outcomes;
- evaluate forecasts after settlement;
- measure every source’s incremental value;
- measure every model’s incremental value;
- measure execution quality;
- adapt approved model/source weights within hard boundaries;
- test challenger models;
- demote deteriorating models/sources;
- allocate research attention toward promising market families;
- abstain when data or interpretation is unreliable.

## B. Trading System

A deterministic portfolio, risk, and execution system that:

- supports Kalshi demo;
- supports true Kalshi production integration;
- defaults to paper/shadow/read-only behavior;
- supports YES and NO economics;
- models maker versus taker execution;
- models actual fees;
- models spread;
- models expected slippage;
- models queue/fill probability;
- models uncertainty;
- models correlated exposure;
- sizes conservatively;
- places actual production orders only after explicit production activation;
- reconciles every order/fill/position/settlement;
- can halt without an LLM;
- fails closed.

The LLM must never authorize exchange risk.

## C. Private Web Product

Build a genuinely polished browser product.

It must:

- look professionally designed;
- work on desktop;
- work on mobile;
- use plain English first;
- have technically correct labels;
- have zero knowingly dead controls;
- clearly explain why trading is or is not occurring;
- clearly display real account state;
- make important information easy to scan;
- provide deeper technical metrics under Advanced;
- expose emergency controls safely;
- produce secret-free status exports.

## D. True Kalshi Integration

The completed repository must contain production-grade support for the actual Kalshi account.

Support:

- production authentication;
- production market data;
- production WebSockets;
- account balance;
- positions;
- orders;
- fills;
- settlements;
- order placement;
- amendments;
- cancellations;
- order groups;
- rate limits;
- fixed-point pricing;
- fractional contracts where supported;
- real reconciliation.

The repository MUST support real trading technically.

However:

`PRODUCTION WRITE DEFAULT = OFF`

and Codex itself must NEVER submit a real-money order.

---

# 3. CRITICAL CODEX OPERATING RULES

At the beginning of this task:

1. Inspect the workspace.
2. Read all existing files.
3. Determine whether this is:
   - an empty repository,
   - an existing prototype,
   - or a connected GitHub repository.
4. Preserve useful source material but do not inherit unsafe architecture merely because it already exists.
5. Create root `AGENTS.md` as the durable Codex project contract.
6. Create `docs/` as the detailed source of truth.
7. Keep `AGENTS.md` concise enough to remain usable.
8. Put detailed architecture, policies, schemas, and rationale in `docs/`.
9. Create `docs/IMPLEMENTATION_STATUS.md`.
10. Keep that status document current after every milestone.

Do not put this enormous specification verbatim into `AGENTS.md`.

Instead, translate it into:

- `AGENTS.md`
- `docs/product_requirements.md`
- `docs/architecture.md`
- `docs/risk_policy.md`
- `docs/security_model.md`
- `docs/source_policy.md`
- `docs/model_governance.md`
- `docs/implementation_plan.md`
- `docs/production_activation.md`
- `docs/operations_runbook.md`

`AGENTS.md` should point Codex toward those authoritative files.

## Autonomy Rule

Continue executing until:

A. every buildable milestone is complete; or
B. a genuine external blocker prevents ALL useful remaining work.

Do not pause merely to tell the user what you plan to do.

Do the work.

## Assumption Rule

When a non-safety-critical detail is unspecified:

- choose the conservative industry-standard option;
- record it in `docs/assumptions.md`;
- continue.

If the assumption affects real financial risk, credential security, legal eligibility, or irreversible production behavior:

- do not silently decide;
- build the capability safely;
- leave production use disabled;
- record the required human decision.

---

# 4. REVIEW LENSES

At EVERY major milestone, review the implementation from all of these perspectives:

1. Principal software engineer
2. Quantitative researcher
3. Prediction-market trader
4. Portfolio manager
5. Risk manager
6. Machine-learning engineer
7. Data scientist
8. Data engineer
9. Security engineer
10. Site reliability / operations engineer
11. Product manager
12. Senior product designer
13. UX designer
14. Compliance-minded engineer
15. CFO / capital allocator

For each milestone ask:

- What could be wrong?
- What could silently fail?
- What could create false confidence?
- What could leak future information?
- What could create duplicate orders?
- What could make historical P&L look better than reality?
- What could cause an erroneous real-money trade?
- What could make the dashboard misleading?
- What unnecessary complexity exists?
- What important capability is missing?
- What would a sophisticated competitor exploit?
- What happens under degraded inputs?
- Is the current behavior economically sensible?

Record substantive review findings in:

`docs/reviews/`

Fix material findings before proceeding.

---

# 5. DO NOT CONVERT THE OLD DEMO INTO PRODUCTION

This must be a clean production-grade implementation.

Do NOT import as learned production state:

- old SQLite databases;
- old demo observations;
- old model weights;
- old calibration;
- old order records;
- demo credentials;
- deployment secrets;
- flawed “avoided loss” metrics;
- arbitrary demo strategy restrictions.

Useful lessons from the prior prototype:

- infrastructure must restart reliably;
- Caddy state should use Docker-managed volumes;
- deployment secrets must survive safe installer reruns;
- order reads and writes use different current routes;
- private-key formatting errors must be handled clearly;
- account and system health must be understandable;
- secret-free support snapshots are valuable;
- misleading financial labels are unacceptable.

Known strategic defects from the old prototype that MUST NOT return:

- treating repeated snapshots as independent learning observations;
- thousands of observations with zero resolved labels;
- forecasts mechanically anchored below the YES ask;
- negative edge by construction;
- YES-only analysis;
- five-cent-contract strategy restriction;
- first-page / first-100 market sampling;
- alphabetical settlement starvation;
- zero-value market quotes treated as useful predictions;
- giant prediction-table UI;
- “avoided losses” before outcomes resolved;
- separate statuses that contradicted each other.

---

# 6. USER ACCOUNT + CAPITAL POLICY

The user has one Kalshi primary account.

Use:

`subaccount = 0`

Initial bankroll:

`$1,000`

Capital policy:

```text
Total bankroll                     $1,000
Protected reserve                   $700
Initial active capital              $300
Maximum aggregate open risk         $100
Maximum loss per market              $10
Maximum related-event risk           $25
```

Loss controls:

```text
Daily loss stop                      $20
Weekly loss stop                     $50
Monthly loss stop                   $100
Total experiment drawdown stop      $200
```

These are hard maximums.

The adaptive learner may NEVER raise them.

Behavior:

- daily limit reached: block new risk until next trading day;
- weekly limit reached: move production strategy to shadow/approval review;
- monthly limit reached: move production strategy to shadow review;
- total $200 drawdown reached: global production halt requiring human review.

Risk-reducing actions may remain possible when safe.

## Initial Live Size

For the first 50 real-money fills:

- one contract per new order by default;
- do not scale automatically;
- no averaging down simply because price fell;
- no martingale;
- no doubling following losses;
- no more than ten simultaneous market positions;
- per-market maximum loss remains $10;
- aggregate open risk remains $100.

After sufficient live evidence, support conservative fractional Kelly.

Hard limits always override Kelly.

LLM confidence must never directly determine Kelly size.

---

# 7. PROFITABILITY OBJECTIVE

Do not use a fixed monthly-profit target.

Optimize for:

`maximum after-cost expected value subject to risk + calibration + survival`

Evaluate strategies using:

- net P&L after fees;
- return on committed capital;
- maximum drawdown;
- drawdown duration;
- expected value per contract;
- expected value per order;
- Brier score;
- log loss;
- calibration;
- market-relative forecasting skill;
- fill rate;
- cancellation rate;
- post-fill markout;
- edge decay;
- P&L by family;
- P&L by horizon;
- P&L by liquidity;
- P&L by source;
- capital turnover;
- capacity.

Win rate is NOT a primary success metric.

---

# 8. MARKET-FAMILY TOURNAMENT

Do not assume one category deserves capital.

Initial candidate families:

1. Weather
2. U.S. scheduled economic releases
3. Energy / scheduled government data
4. Selected structured sports markets

Research architecture can later support:

- politics/elections;
- law/courts;
- regulation;
- entertainment;
- technology;
- other categories.

Higher-semantic-risk categories stay shadow-only until separately proven.

Score each family on:

- settlement clarity;
- source quality;
- number of repeated opportunities;
- forecastability;
- liquidity;
- spread;
- fees;
- execution quality;
- resolution speed;
- forecast skill versus market;
- incremental source value;
- fill probability;
- after-cost expectancy;
- drawdown;
- data cost;
- operational complexity.

Let evidence select capital allocation.

---

# 9. INITIAL MARKET FILTERS

Initially reject or shadow:

- ambiguous settlement rules;
- unknown settlement authority;
- unsupported multivariate contracts;
- provisional/unvalidated contracts;
- unsupported price structures;
- stale contracts;
- no meaningful executable quotes;
- unacceptable spread;
- inadequate depth;
- changed rules awaiting revalidation;
- markets longer than about 30 days unless approved;
- war/geopolitical markets;
- legal markets without primary-document confirmation;
- markets whose only edge is social rumor;
- markets that cannot be reconstructed point-in-time.

Preferred initial horizon:

`12 hours – 7 days`

Most research focus:

`24 – 72 hours`

Initially prohibit new opening positions inside approximately the last 15 minutes before market close until the relevant strategy has explicitly passed near-resolution latency/execution validation.

---

# 10. BOTH YES AND NO

The system must evaluate the economics of BOTH outcomes.

Internally represent:

- outcome = YES / NO;
- desired exposure;
- executable economics.

Do not reason only in terms of “cheap YES.”

Do not make low-price longshots a strategy.

---

# 11. EDGE PRIORITY

## A. Cross-Market / Logical Relative Value

Research:

- equivalent Kalshi/Polymarket questions;
- logically exhaustive bins;
- mutually exclusive contracts;
- conditional probability consistency;
- related payouts.

Never declare arbitrage using midpoint prices.

Use:

- executable depth;
- fees;
- latency;
- settlement semantics;
- incomplete-leg risk.

Cross-market compatibility statuses:

```text
identical
hedgeable_with_basis
related_only
incompatible
```

Only approved `identical` or explicitly approved `hedgeable_with_basis` relationships may support executable cross-venue strategies.

## B. Domain Nowcasting

Build independent specialist models for weather, macro, energy, and structured sports where supported.

## C. Passive Execution / Structural Liquidity

Only after fair-value models are validated.

Research maker orders, spread capture, structural inconsistencies, queue position, and inventory-aware quoting.

---

# 12. MARKET PRICE AS PRIOR

Kalshi market price is a strong prior.

It is not independent evidence.

Use conceptually:

```text
market prior
+ quantitative independent signals
+ external structured evidence
+ source reliability
+ domain features
-> raw forecast
-> calibration
-> uncertainty-adjusted fair probability
```

Every new model must answer:

> Did this improve on simply using the executable market?

---

# 13. TRADE HURDLES

General starting hurdle:

`expected net edge after all modeled costs ≈ 5%`

Allow adaptation only within:

`4% – 8%`

Initial domain defaults:

```text
Weather/objective data:    6–8 percentage points
Legal/election if enabled: 8–12 percentage points
```

Cross-venue initial hurdle:

```text
gross discrepancy >
  fees
  + expected slippage
  + leg risk
  + semantic reserve
  + 1.5 percentage points
```

and initially approximately:

`absolute discrepancy >= 4 percentage points`

Do not trade when the conservative model uncertainty interval still contains the executable after-cost market probability.

---

# 14. SYSTEM ARCHITECTURE

Use an event-driven architecture with a HARD boundary between research intelligence and capital authorization.

Implement logical services/modules for:

- `kalshi_market_gateway`
- `kalshi_account_gateway`
- `market_universe`
- `contract_registry`
- `eligibility_engine`
- `source_registry`
- `external_gateways`
- `breaking_signals`
- `evidence_store`
- `contract_intelligence`
- `document_intelligence`
- `event_matcher`
- `feature_store`
- `forecast_service`
- `calibration_service`
- `learning_service`
- `strategy_engine`
- `opportunity_ranker`
- `risk_engine`
- `execution_planner`
- `signer`
- `execution_gateway`
- `reconciliation_service`
- `portfolio_ledger`
- `supervisor`
- `reporting_service`
- `web_dashboard`

### `kalshi_market_gateway`

Responsibilities:

- public market REST;
- complete pagination;
- WebSocket market data;
- order-book snapshots/deltas;
- sequence handling;
- trades;
- lifecycle;
- events;
- series;
- fees;
- price structure;
- fractional metadata;
- raw-event persistence;
- rate budgeting.

### `kalshi_account_gateway`

Uses production READ-ONLY credentials.

Responsibilities:

- balance;
- portfolio value;
- positions;
- resting orders;
- fills;
- settlements;
- account/API limits;
- key scope verification;
- reconciliation snapshots.

Never receive production write private key.

### `market_universe`

Maintain the COMPLETE relevant market universe. Never arbitrary first-page/first-100/alphabetical sampling.

### `contract_registry`

Store exact rules, settlement source, timestamps, strike/unit, thresholds, rounding/revisions, early-close behavior, outcome semantics, price structure, fractional support, status, rules hash, metadata hash, and version.

Material rules/metadata changes invalidate prior semantic approval.

### `eligibility_engine`

Reject for lifecycle, liquidity, spread, depth, freshness, unclear settlement, source health, unsupported semantics/price structure, or strategy requirements. Persist machine-readable and human-readable rejection reason.

### `source_registry`

Track ownership, endpoint/domain, allowed families, licensing, cost, credential boundary, primary/secondary/social classification, cadence, latency, coverage, missingness, revisions, corrections, outages, historical contribution, provenance, content hashes, and promotion stage.

### `external_gateways`

Official/permitted adapters for:

- Polymarket
- PredictBuddy
- NWS/NOAA
- BLS
- BEA
- FRED/ALFRED
- EIA
- government feeds
- RSS
- licensed news
- X
- Bluesky
- Reddit official API where permitted
- authorized Telegram
- authorized Discord
- future approved sources

No unauthorized scraping.

### `breaking_signals`

Signal stages:

```text
lead
corroborating
corroborated
candidate_opportunity
invalid
duplicate
manipulation_risk
```

Assess identity, originality, novelty, relevance, freshness, corroboration, manipulation risk, prior source value, venue reactions, and remaining edge.

### `evidence_store`

Every item records at least:

```text
source_event_time
source_publish_time
provider_receive_time where available
bot_ingest_time
```

Backtests use `bot_ingest_time`.

### `contract_intelligence`

LLM-assisted but deterministically validated exact YES/NO semantics, authority, source, deadline/timezone, threshold inclusivity, rounding, revisions, recounts, cancellation/postponement, early-close rules, exceptions, and ambiguities.

### `document_intelligence`

Two-stage:
1. extract atomic contract-independent claims;
2. evaluate relevance to exact settlement semantics.

Store citation/locator, reliability, relevance, novelty, entailment, uncertainty, and YES/NO relationship.

Treat documents as untrusted input.

### `event_matcher`

Use deterministic matching first: entities, event type, outcome, threshold, date/time, timezone, authority, revisions, cancellation. Then embeddings/LLM for candidate semantic comparison. Deterministic final classification only.

### `feature_store`

Point-in-time correctness; feature availability timestamps; no leakage.

### `forecast_service`

Outputs raw/calibrated probability, uncertainty interval, horizon, model/version, calibration version, feature/evidence snapshot IDs, source snapshot, abstention state.

### `calibration_service`

Support logistic/Platt, isotonic, beta, and hierarchical methods.

### `learning_service`

May adapt calibrators, approved model/source weights, abstention thresholds, edge threshold within approved range, polling cadence, market-family routing, and active approved sources.

May NOT adapt source catalog, credentials, code, hosts, signer policy, settlement semantics, maximum order size, risk/loss limits, or kill-switch behavior.

Every adaptation requires version, previous version, evidence window, reason, metric delta, shadow comparison, rollback target, timestamp, actor, and audit record.

### `strategy_engine`

Produces `TradeCandidate`, never exchange orders.

### `opportunity_ranker`

Conceptually rank using expected net value, uncertainty/confidence adjustment, expected fill, capital turnover, liquidity, correlation, and semantic risk. Ranking is not authorization.

### `risk_engine`

Pure deterministic software. Fail closed. No LLM provider imports.

### `execution_planner`

Deterministically choose abstain/post/join/improve/cross/amend/cancel using edge, fees, markout, time decay, fill probability, queue, volatility, market state, and freshness.

### `signer`

Tiny isolated service. Only component allowed production WRITE private key.

Validate approved host/path/method, timestamp freshness, body hash, subaccount 0, risk authorization, max size/price, unique authorization ID, one-time authorization, and expiration.

Target risk authorization: ~5 seconds, single-use.

### `execution_gateway`

Submits approved request but does NOT possess PEM.

### `reconciliation_service`

Compare exchange/local orders, fills, remaining quantity, positions, cash, settlements. Mismatch blocks new risk.

### `portfolio_ledger`

Double-entry-style ledger for cash, fills, fees, settlements, realized P&L, exposure, adjustments, reconciliation.

### `supervisor`

Own health, stale detection, strategy/risk state, kill switches, compliance hold, degradation, incidents.

---

# 15. TECHNOLOGY STACK

Preferred baseline:

```text
Python 3.12+
uv
committed uv.lock
FastAPI
Pydantic v2
SQLAlchemy 2 async
Alembic
httpx async
websockets / compatible async adapter
PostgreSQL 16
Redis 7
NATS JetStream
S3-compatible object-storage interface
OpenTelemetry
Prometheus-compatible metrics
Docker
Docker Compose
Caddy
pytest
Hypothesis
```

Prefer server-rendered Jinja + HTMX + modern CSS unless a SPA is clearly justified.

Use `Decimal` for all financial arithmetic. Do not use binary floats for accounting/risk/execution.

---

# 16. CURRENT KALSHI SOURCE-OF-TRUTH RULE

Kalshi changes its API.

At the start of implementation:

1. access current official Kalshi docs;
2. read docs index;
3. fetch current OpenAPI;
4. fetch current AsyncAPI;
5. inspect changelog;
6. checksum specs;
7. generate/validate internal adapter;
8. store exact spec versions/checksums under `generated/kalshi_specs/`;
9. add CI drift detection.

If internet access is unavailable, do not guess production-write API changes. Continue independent work and record external verification blocker.

Starting endpoint references to reverify:

```text
Production REST:
https://external-api.kalshi.com/trade-api/v2

Production WebSocket:
wss://external-api-ws.kalshi.com/trade-api/ws/v2

Demo REST:
https://external-api.demo.kalshi.co/trade-api/v2

Demo WebSocket:
wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2
```

Current official specifications win if they conflict with this file.

---

# 17. KALSHI AUTHENTICATION

Implement actual RSA-PSS authentication using access key, timestamp, and signature headers.

Signature payload:

`timestamp_ms + uppercase HTTP method + full request path without query string`

Use RSA-PSS + SHA-256 + Base64.

Do not include hostname or query params.

Create deterministic signature-vector tests and reject invalid/stale/unexpected inputs.

---

# 18. SEPARATE READ AND WRITE KEYS

Read key used by account/dashboard/reconciliation monitoring.

Write key only by isolated signer.

Never commit, log, serialize, return, browser-expose, localStorage-store, or support-snapshot any PEM.

Provide HTTPS browser setup for direct credential upload to protected server.

---

# 19. CURRENT ORDER API STARTING REFERENCE

Reverify against current generated spec.

At prompt creation, V2 event-order creation uses:

`POST /portfolio/events/orders`

Current concepts include ticker, client_order_id, side, count, price, time-in-force, self-trade prevention, and optional expiration/post-only/cancel-on-pause/reduce-only/subaccount/order-group/exchange-index fields.

Current V2 uses a single YES order book where bid buys YES and ask sells YES. Internal domain model should represent desired YES/NO economics and translate at adapter boundary.

Read/amend/cancel endpoints must be obtained from current official specs.

---

# 20. ORDER STATE MACHINE

Implement:

```text
PROPOSED
RISK_APPROVED
SUBMISSION_PENDING
ACCEPTED
PARTIALLY_FILLED
FILLED
CANCELLATION_PENDING
CANCELED
REJECTED
UNKNOWN_RECONCILIATION_REQUIRED
```

Every order uses unique UUID `client_order_id`.

HTTP timeout after submission => `UNKNOWN_RECONCILIATION_REQUIRED`, not blind retry.

---

# 21. PRICE + FRACTIONAL SUPPORT

Do not assume integer contracts, one-cent ticks, or legacy cents fields.

Parse fixed-point strings with Decimal, honor market price structures and fractional eligibility, and validate before signing.

---

# 22. MARKET LIFECYCLE

Support current official lifecycle states and transitions, including active/inactive/closed/determined/disputed/amended/finalized concepts where applicable.

Metadata/lifecycle changes may invalidate forecast/trade approval and require cancel/resnapshot/reinterpretation.

---

# 23. WEBSOCKETS

Use WebSockets for high-rate state. Implement authentication where required, heartbeats, sequence tracking, resubscription, jittered reconnect, gap detection, resnapshot, and REST reconciliation. A stale/unreconciled book cannot authorize risk.

---

# 24. RATE LIMITS

Build separate read/write rate budget management. Check current account limits/docs. Cache static data, use WebSockets, back off on 429, reserve emergency cancel capacity, expose health, and never blindly retry non-idempotent orders.

---

# 25. ORDER GROUPS

Use one order group per strategy/risk domain plus emergency group. Exchange-side groups supplement but never replace local deterministic risk controls.

---

# 26. FEES

Never hard-code a universal transaction cost. Track current fee type, multiplier, maker/taker behavior, rounding, and changes. Recompute affected economics on fee changes.

---

# 27. LIVE + HISTORICAL KALSHI DATA

Build routing across live and historical APIs using current historical cutoff logic. Merge markets/trades/fills/orders/candles without duplication or silent gaps.

---

# 28. POLYMARKET

Use official public APIs/interfaces for market discovery, metadata, prices, books, trades, state, comments, and public wallet/leaderboard data where supported.

Use primarily for cross-venue intelligence, event matching, movement/gap alerts, lead-lag analysis, wallet-flow research, and liquidity comparison.

Do not claim semantic equivalence from title similarity.

---

# 29. PREDICTBUDDY

At implementation time verify whether an official API exists.

If yes and authorized, use it.

Otherwise support safe authorized ingestion through user-owned email/Telegram/export paths. Do not scrape authenticated UI.

PredictBuddy starts Candidate/Shadow. Never copy-trade whale activity.

---

# 30. SOCIAL NETWORK + BREAKING INFORMATION

Build modular official/permitted adapters for X, Bluesky, Reddit, Polymarket comments, RSS, authorized Telegram, and authorized Discord.

For every claim identify author/original source, collapse reposts, detect duplication/coordinated bursts, assess bot likelihood, map exact contract, determine market reaction, seek primary confirmation, and timestamp everything.

Social chatter initially informs attention/volatility/investigation priority, not direct orders.

---

# 31. BREAKING SIGNAL PIPELINE

Lifecycle:

```text
new signal
→ timestamp/provenance
→ identity validation
→ duplicate detection
→ contract matching
→ manipulation assessment
→ corroboration
→ forecast recomputation
→ compare with executable market
→ candidate opportunity
→ risk evaluation
```

Dashboard distinguishes informational lead, corroborated development, and executable opportunity.

---

# 32. SOURCE LEARNING

Lifecycle:

```text
CANDIDATE → SHADOW → ELIGIBLE → LIMITED PRODUCTION → APPROVED
```

Possible `QUARANTINED`.

Before production promotion target at least 50 unique relevant resolved outcomes and positive incremental value.

Measure incremental Brier/log-loss/market skill/simulated P&L/live P&L, lead time, uptime, missingness, revisions, correlation, domain/horizon performance.

Initial automatic source-weight change cap: ~10 percentage points/week.

Human approval required for any new production source.

---

# 33. LLM PROVIDERS

Provider-neutral adapters for Anthropic, OpenAI, and deterministic fixtures.

Use LLMs for contract interpretation, semantic comparison, evidence extraction, contradiction analysis, document interpretation, entity assistance, explanations, and hypothesis generation.

Never use LLM for signing, execution, risk override, financial limit changes, deterministic reconciliation, or direct quantity authorization.

Validate outputs locally. Invalid/malformed output => reject.

---

# 34. LLM PROMPT-INJECTION PROTECTION

Treat external source text as untrusted. Delimit untrusted market/document text. Tell model to ignore embedded instructions. Require citations and entailment. Store prompt/model/input hashes/output/validation.

---

# 35. FORECASTING

Start with strong simple baselines. Market prior + independent quantitative/domain evidence + source-weighted evidence → raw probability → calibration → uncertainty-adjusted fair probability.

Keep executable-market baseline always available. Measure incremental value.

---

# 36. WEATHER

Parse exact station/product/local day/measurement/units/threshold/rounding/revision semantics.

Use NWS/NOAA and permitted numerical ensembles.

Store issue times and full predictive distributions. Learn station/horizon/regime bias. Disable affected trading on ambiguity/source failures/conflicts/corrections.

---

# 37. ECONOMICS / MACRO

Support structured research for CPI, PCE, unemployment, payrolls, GDP, claims, Fed decisions, and scheduled releases.

Use official sources such as BLS, BEA, FRED/ALFRED and release calendars. Preserve real-time vintages and revisions. Never leak revised historical data into original-time backtests.

Do not enable release-second production execution until latency/data-rights reviewed.

---

# 38. SPORTS

Only where structured objective data and clear rules exist. Do not use generic social sentiment as model. Evaluate market availability, licensed consensus/odds sources, injuries/status, objective statistics, liquidity, fees, and market efficiency.

---

# 39. FORECAST RECORDS

Every frozen forecast stores unique ID, market, rules version, timestamp, executable bid/ask/baseline, model/calibrator versions, feature/evidence/source snapshots/times, raw/calibrated probability, interval/uncertainty, decision, and abstention reason.

Frozen forecasts are immutable; create new versions instead.

---

# 40. LEARNING COUNTS

Track raw snapshots, frozen forecasts, unique markets/events, settled forecasts/events, and effective sample size separately.

Never present repeated unresolved snapshots as independent learned experience.

---

# 41. FORECAST METRICS

Implement Brier, log loss, calibration curves/error, resolution/discrimination, market-relative Brier skill, time-weighted/domain/source-ablation skill.

Do not promote a model solely because raw Brier is good if it adds no market-relative value.

---

# 42. BACKTESTER

Point-in-time replay mandatory.

Persist metadata/rule versions, books/trades/fees, source revisions/publication/ingestion times, model artifacts, prompts, forecasts, orders/fills/settlements.

Use `bot_ingest_time` for availability. No future leakage.

---

# 43. FILL SIMULATION

Model queue, price-time approximation, cancellations, partial fills, order/WS/cancel latency, adverse selection, pauses, and fee rounding.

Run optimistic/base/adverse scenarios. A strategy profitable only under optimistic assumptions cannot advance.

---

# 44. CROSS-VENUE SIMULATION

Model legs independently including fees, ticks, timestamps, one-leg fills, latency, capital fragmentation, settlement mismatch, semantic basis, venue downtime, and contradictory-resolution maximum loss.

---

# 45. WALK-FORWARD TESTING

Use rolling/expanding evaluation with event grouping/purging to prevent related-event leakage. Select models out of sample.

---

# 46. PROMOTION GATES

Research → Paper/Demo requires executable-market baseline improvement, base/adverse economics, calibration, sample/time diversity, no single-event or temporary-incentive dependence, and security/reconciliation tests.

Production Shadow target:

```text
>=250 unique settled forecasts overall
>=100 unique settled forecasts in intended first live family
```

plus no-worse market-relative calibration, positive modeled after-cost result, and no unresolved critical rule/data defects.

Supervised Live: model recommends, owner approves, one contract, narrow strategy.

Before bounded automation target:

```text
>=50 actual fills
>=30 settled real-money positions
```

plus stable reconciliation/execution/drawdown/operations and approximately four continuous weeks without reconciliation failure.

Capital scaling always requires human decision.

---

# 47. OPPORTUNITY ENGINE

For every candidate compute BOTH YES and NO fair probability, executable price, spread, fees, slippage, uncertainty reserve, liquidity, fill probability, information decay, capital turnover, correlation, expected net edge/value, strategy/rationale/rejection reason.

No five-cent cap.

---

# 48. RISK ENGINE

Pure deterministic logic.

Every new-risk order must pass market/rules/semantic/source/data/book/model/calibrator/price/quantity/edge/exposure/reserve/loss/reconciliation/unknown-order/kill-switch/compliance/order-group/client-ID/risk-authorization checks.

Missing information => REJECT.

---

# 49. EXECUTION

Default limit orders. Use post-only/cancel-on-pause when appropriate/currently supported.

Choose passive vs aggressive based on after-cost EV, information decay, fill probability, queue, and markout.

Track predicted versus actual fills and markouts. Statistical execution models may learn; authorization remains deterministic.

---

# 50. RECONCILIATION

Reconcile after every write, uncertain response, WS reconnect, process restart, and continuously on schedule.

Material mismatch blocks new risk.

Unknown state never causes automatic duplicate replacement.

---

# 51. KILL SWITCHES

Implement strategy/data/portfolio/credential kills.

Global halt:

1. atomically persist halt;
2. prohibit new risk;
3. trigger order groups;
4. cancel remaining resting orders;
5. verify exchange open orders;
6. reconcile cash/positions;
7. disable signer authorization;
8. notify owner;
9. preserve logs/evidence;
10. require human reset.

Every restart/VM reboot => LIVE TRADING DISARMED.

---

# 52. COMPLIANCE HOLD

Independent `COMPLIANCE_HOLD`.

Disable production writes on eligibility/jurisdiction/account/contract-class/terms/operator issues.

Never evade geolocation, operate extra accounts to bypass limits, wash trade, spoof, intentionally self-trade, use unauthorized confidential information, or violate provider terms.

---

# 53. DASHBOARD INFORMATION ARCHITECTURE

Main navigation:

```text
Overview
Opportunities
Breaking Now
Markets
Sources
Learning
Portfolio
Orders & Trades
Reports
Risk & Safety
System
Advanced
```

One unambiguous global state:

```text
LEARNING
READY FOR APPROVAL
AWAITING APPROVAL
TRADING
PAUSED
NEEDS ATTENTION
HALTED
```

Never contradictory modes.

---

# 54. OVERVIEW PAGE

First viewport answers:

1. Can it trade?
2. How much money is at risk?
3. What did it do?
4. What changed?
5. What needs me?

Show explicit blockers, account equity/cash/reserve/active allocation/open risk/worst-case loss/P&L/loss allowance, market/opportunity/position/order/settlement/error counts, model/source/rule changes, and actionable owner issues only.

---

# 55. OPPORTUNITIES PAGE

Use ranked cards, not giant raw tables.

Show market implied probability, system estimate, uncertainty, after-cost edge, confidence, liquidity, decision, and explicit reason for not trading.

Show YES and NO opportunity economics where relevant.

---

# 56. BREAKING NOW PAGE

Show official-source versus rumor state clearly, publication and ingestion latency, Polymarket/Kalshi movement, primary confirmation, manipulation risk, and current action.

Never display rumor as confirmed fact.

---

# 57. MARKET DETAIL PAGE

Show exact question, plain-English settlement interpretation, source/deadlines/rules version, bid/ask, implied/fair probabilities, interval, YES/NO edges, book depth/liquidity, supporting/opposing evidence, source timestamps/reliability, what would change the forecast, decision, proposed size/max loss, strategy, forecast/audit histories.

---

# 58. SOURCES PAGE

For every source show status, coverage/class, freshness/latency/health, unique relevant settled outcomes, incremental Brier/log-loss/simulated/live value, current/previous weight, reason for change, promotion stage, and monthly cost.

---

# 59. LEARNING PAGE

Plain-language explanation first, Advanced metrics second.

Never label skips as avoided losses. Any avoided-loss metric requires resolved explicit counterfactual methodology.

---

# 60. PORTFOLIO PAGE

Use true reconciled account state: cash/equity/P&L/unresolved exposure/positions/worst-case outcomes/correlation/settlement dates/fees.

---

# 61. ORDERS & TRADES PAGE

Show proposed, approved, submitted, resting, partial, filled, canceled, rejected, and unknown/reconciliation-required states. Explain unusual states plainly.

---

# 62. RISK & SAFETY PAGE

Show reserve, active budget, market/event/aggregate caps, loss allowances/drawdown, kill switches, compliance hold, signer/key/reconciliation status, arm/disarm.

Dangerous actions require password re-auth, current TOTP, and explicit confirmation.

---

# 63. SYSTEM PAGE

Show release/Git SHA, service/database/queue/source health, last successful sync, API compatibility/spec checksum, backup/restore status, worker/rate-budget state.

---

# 64. UI / UX QUALITY STANDARD

Do significantly better than prototype.

Requirements:

- clear hierarchy;
- strong typography;
- restrained visual system;
- excellent spacing;
- consistent components;
- responsive cards;
- meaningful empty/error/stale/loading states;
- accessible contrast;
- keyboard usability;
- useful touch targets;
- no mobile overflow;
- no raw JSON except Advanced/downloads;
- no dead controls;
- no ambiguous financial units;
- dollars shown as dollars.

Every disabled control explains why.

---

# 65. CODEX VISUAL QA

Where browser capabilities exist:

1. seed synthetic fixture state;
2. run app;
3. inspect browser;
4. inspect desktop/tablet/mobile;
5. capture screenshots if possible;
6. identify spacing/overflow/copy/dead-control/inconsistency problems;
7. fix;
8. repeat.

Review ~1440 desktop, 1024 tablet, ~390 mobile and scenarios for setup/learning/shadow/opportunity/live/no-opportunity/source failure/reconciliation failure/risk stop/halt/many opportunities/empty portfolio.

HTML compilation is not visual QA.

---

# 66. PRIVATE DASHBOARD SECURITY

Implement secure password hashing, TOTP, one-use recovery codes, CSRF, login throttling, secure sessions/cookies, expiry, CSP, HSTS, clickjacking protection, referrer policy, audit trail.

Do not expose app port 8000 publicly.

---

# 67. REPORTING

Daily operating brief default ~7:00 AM America/New_York.

Weekly learning report default Monday.

Monthly governance report.

Include appropriate status/P&L/exposure/trades/settlements/forecast/source/model/execution/risk/cost/incidents/governance information.

System may recommend financial limit changes but cannot apply them autonomously.

---

# 68. ALERTS

Immediate alerts on global halt, production auth failure, unknown order, reconciliation mismatch, loss stop, source failure near settlement, DB/backup/API-schema/credential problems.

Support dashboard, configurable SMTP email, and generic second-channel webhook.

---

# 69. SUPPORT SNAPSHOT

One-click Markdown + JSON support snapshot with release/Git/service/model/calibrator/source/strategy/cash-exposure/recent-decision/error/reconciliation/API/redacted config information.

Exclude all secrets and unnecessarily identifying/sensitive data.

Automated redaction tests required.

---

# 70. COST POLICY

Target recurring infrastructure/data/LLM spend about $25/month, initial hard ceiling $50/month, excluding development and bankroll.

Track LLM/source/hosting/API cost and display monthly operating cost. Paid-source retention requires measurable incremental value.

---

# 71. DEPLOYMENT TARGET

No local installation for owner.

Initial runtime target:

```text
Oracle Cloud
Ubuntu 24.04
Ampere A1 Flex where compatible
1 OCPU
~6 GB RAM
public IPv4
public subnet
```

Ingress 22/80/443 only.

Never expose app 8000, PostgreSQL, Redis, NATS, object store, or signer.

If resource footprint exceeds VM, measure and document minimum required shape; do not remove essential services silently.

---

# 72. DOCKER

Use Docker Compose.

Where practical use read-only filesystems, drop capabilities, no-new-privileges, tmpfs temporary writes, segmented networks, least egress, and Docker-managed Caddy volumes.

---

# 73. PERSISTENCE

PostgreSQL 16 canonical state, Redis transient/cache/locks, NATS JetStream event transport, object storage abstraction with MinIO dev option.

Implement migrations, backups, protected/encrypted backup handling, restore procedure/test, retention.

---

# 74. REPOSITORY STRUCTURE

Use approximately:

```text
kalshi-production-v3/
├── MASTER_SPEC.md
├── AGENTS.md
├── README.md
├── SECURITY.md
├── Makefile
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── docker-compose.yml
├── docker-compose.dev.yml
├── config/
├── schemas/
├── generated/kalshi_specs/
├── services/
├── strategies/
├── llm/
├── backtests/
├── migrations/
├── scripts/
├── deploy/
├── tests/
└── docs/
    ├── product_requirements.md
    ├── architecture.md
    ├── assumptions.md
    ├── implementation_plan.md
    ├── IMPLEMENTATION_STATUS.md
    ├── threat_model.md
    ├── risk_policy.md
    ├── source_policy.md
    ├── data_model.md
    ├── model_governance.md
    ├── production_activation.md
    ├── deployment.md
    ├── operations_runbook.md
    ├── incident_response.md
    ├── backup_restore.md
    ├── HUMAN_ACTIONS_REQUIRED.md
    ├── decisions/
    ├── reviews/
    └── model_cards/
```

Preserve logical responsibilities even if physical structure evolves.

---

# 75. ENVIRONMENT EXAMPLE

Create `.env.example` with placeholders only for app environment/subaccount, DB/Redis/NATS/object store, LLM provider/model and provider keys, demo key ID/path, production read key ID/path, SMTP, X, PredictBuddy mode.

Do NOT place write-key configuration into general web/research environment. Signer has isolated secret config.

---

# 76. `.gitignore`

Protect `.env*` except example, secrets, PEM/key formats, state/data/backups, DB/logs, virtualenv/caches, build artifacts, OS junk.

Run secret scanners in addition to `.gitignore`.

---

# 77. BROWSER SETUP

Secure first setup:

1. one-time high-entropy setup token;
2. owner username/password;
3. TOTP;
4. recovery codes;
5. production read-only key ID;
6. direct HTTPS PEM upload;
7. key validation/scope verification;
8. primary account 0 validation;
9. account reconciliation;
10. encrypted credential storage.

Never ask user to paste PEM into shell history.

---

# 78. PRODUCTION WRITE ACTIVATION

Separate later wizard:

1. authenticated owner;
2. password re-auth;
3. TOTP;
4. current risk policy;
5. promotion gates;
6. explicit acknowledgement;
7. separate write-capable key upload;
8. scope validation;
9. store only in signer;
10. leave LIVE TRADING DISARMED.

Installing write credential does not arm trading.

Restart always disarms.

---

# 79. IMPLEMENTATION MILESTONES

Proceed continuously and commit after each coherent green milestone.

## M0 — Repository Foundation

Docs, AGENTS, threat/risk/source policies, schemas, dependency lock, baseline lint/type/tests, Docker skeleton, CI skeleton.

## M1 — Production Read-Only Account Control Center

Real production read-only RSA auth, scope validation, account 0 cash/portfolio/positions/orders/fills/settlements/limits, auto refresh, secure UI, support snapshot. No mutations.

## M2 — Complete Market Universe

All relevant series/events/markets, full pagination, incremental updates, metadata, price/fractional/fee/lifecycle persistence. No sampling shortcuts.

## M3 — Real-Time Market Data

WebSocket books/trades/lifecycle/sequence/reconnect/resnapshot/freshness with deterministic replay tests.

## M4 — Contract / Settlement Intelligence

Canonical contract schema, rule hashes/source registry, deterministic+LLM parser interfaces, ambiguity/rule-change handling.

## M5 — Breaking Signals

Polymarket, matching, movement/gap alerts, PredictBuddy architecture, RSS/social interfaces, dedupe, source health, latency, manipulation risk. No production influence.

## M6 — Historical + Replay

Historical routing, raw archive, point-in-time datasets, no-leakage replay.

## M7 — Document / LLM Evidence

Anthropic/OpenAI/fixture adapters, extraction/contradiction/abstention, schemas/citations/prompt-injection defense. Missing provider keys should not block fixture implementation.

## M8 — Forecasting + Calibration

Market prior, baseline/domain models including weather and at least one scheduled objective-data family, calibration, uncertainty, frozen forecasts, champion/challenger.

## M9 — Source + Model Learning

Attribution, ablation, promotion stages, bounded weights, rollback, market-family tournament.

## M10 — Opportunity Engine

YES/NO EV, fees/slippage/fills/uncertainty/liquidity/correlation/ranking.

## M11 — Backtests + Fill Simulator

Walk-forward, optimistic/base/adverse fills, maker/taker, queue/markout, one-leg cross-venue simulation.

## M12 — Full Dashboard Product

All specified pages, realistic fixtures, desktop/mobile visual QA, material UX fixes.

## M13 — Deterministic Risk

Hard policy and property tests. No LLM dependency.

## M14 — Demo Execution

Signer boundary, demo writes, submit/amend/cancel/order groups/reconciliation/kills. If no demo creds, finish mocks/fixtures and document manual demo integration.

## M15 — Production Execution Capability

Real production adapter and isolated signer. Production write disabled. Never place real order.

## M16 — Supervised Canary Product Flow

Recommend/Review/Approve/Reject UI/workflow. Real approval only after human credential installation and gates.

## M17 — Bounded Autonomy Capability

State machine/promotion controls; default OFF.

## M18 — Operations Hardening

Backup/restore/restart/chaos/monitoring/alerts/incidents/API drift/dependency scans/SBOM/release/rollback.

## M19 — Final Audit

Security, quant/leakage, accounting, execution/idempotency, operations, UX, API correctness. Fix findings and run full verification.

## M24 — Perps Shadow Research Layer

Research-only, immutable, auditable observations for perps market metadata and edge-decay analysis,
with zero production influence. `exchange_index` is first-class; long and short leverage estimates
remain separate; portfolio-margin fields remain nullable and uninferred. This milestone adds no
execution, orders, sizing, routing, credentials, networking, or write capability, and enables no
perps trading.

## M25 — Live Read-Only Evidence Collection

Permit a future authenticated READ-ONLY market-data connection for evidence collection while
preserving `production_influence == 0`: no execution, orders, sizing, routing, amend/cancel, risk
authorization changes, canary/autonomy, `services.learning` connection, production-write
credential, or account funding. M25A is a completely offline read-only evidence runtime using
deterministic scripted transport frames. It makes no external network calls, uses no real
credentials, activates no deployment or live collector, implements no concrete production
WebSocket connection, and remains OFF by default. M25A's book runtime is Predictions-shaped and
must not consume Perps frames. M25B adds a parallel Perps/margin path with ticker-only identity,
ordinary bid/ask books, and authoritative `exchange_index`, `contract_size`, `tick_size`, and
`fractional_trading_enabled` metadata from `GET /trade-api/v2/margin/markets/{ticker}`. M25B1 is
offline contract/domain/evidence work only; a later M25B2 owns any concrete authenticated margin
transport and live smoke. No M25A YES/NO evidence is reinterpreted as Perps evidence.

Do not wait for user confirmation between milestones.

---

# 80. TEST TOOLCHAIN

Use current appropriate versions of pytest, pytest-asyncio, Hypothesis, respx, testcontainers, Ruff, mypy strict, Bandit, Semgrep, detect-secrets, pip-audit, Trivy, and Syft/equivalent SBOM.

Test categories: unit, property, contract, integration, replay, security, chaos, UI.

---

# 81. MANDATORY SAFETY TESTS

All must pass:

```text
[ ] All monetary/accounting/risk arithmetic exact.
[ ] No real secrets in repository.
[ ] LLM cannot import signer.
[ ] LLM cannot invoke execution.
[ ] Web/dashboard cannot obtain write PEM.
[ ] Each order has unique client_order_id.
[ ] Strategy orders have order groups.
[ ] Stale book rejects order.
[ ] Sequence gap rejects order.
[ ] Rule change invalidates relevant approval.
[ ] HTTP timeout cannot create duplicate order.
[ ] REST/WS state reconciles after restart.
[ ] Fees and rounding represented correctly.
[ ] Drawdown controls property tested.
[ ] Global halt blocks new risk.
[ ] Global halt cancels resting orders where technically possible.
[ ] Restart returns production to DISARMED.
[ ] Backtest cannot read future data.
[ ] Cross-venue simulation includes one-leg fill.
[ ] Unsupported LLM evidence rejected.
[ ] Uncited material claim rejected.
[ ] Forecast stores model/calibration/source versions.
[ ] Every decision can be reconstructed.
[ ] Monitoring outage fails closed.
[ ] Support export redacts secrets.
[ ] Browser never stores PEM in localStorage.
[ ] Production write key unavailable to research/web services.
```

---

# 82. FAILURE BEHAVIOR

WebSocket disconnect: freeze new orders, reconnect, resnapshot, reconcile.

Unknown POST outcome: mark `UNKNOWN_RECONCILIATION_REQUIRED`; no retry until reconciled.

LLM outage: quantitative-only strategy may continue only if independently approved; otherwise abstain.

Malformed LLM output: reject.

Source outage: freeze dependent strategies.

DB failure: stop new risk.

Clock drift: stop signing.

Fee change: freeze affected economics until refreshed.

Rules change: invalidate forecast and trading approval.

Position mismatch: portfolio halt.

Abnormal fill burst: trigger order-group safety.

Monitoring failure: fail closed after suitable grace threshold.

---

# 83. CI

Create `.github/workflows/ci.yml` and `.github/dependabot.yml`.

CI should cover locked install, schemas/API drift, Ruff, mypy, Bandit, Semgrep, secret scans, pip-audit, unit/property/contract/integration/replay/chaos/UI tests, Docker builds, Trivy, SBOM, Docker-history secret scan, final gate.

CI must contain NO production PEM/key/write secret, NO automatic live deployment, NO automatic live arming, and NO real trade tests.

---

# 84. README

README must clearly explain purpose, architecture, components, intelligence sources, forecasting, learning, source governance, risk, execution, dashboard, demo vs production, setup/deployment/testing/production activation/emergency halt/support snapshot/limitations/no-profit-guarantee.

Explicitly state:

> LLM output never directly authorizes or sizes a Kalshi order.

> Production write credentials are not necessary for research, forecasting, read-only monitoring, or the dashboard.

---

# 85. GITHUB

Target private repository name:

`kalshi-production-v3`

Inspect git/remotes/GitHub auth first.

Use existing connected repo if present.

If needed initialize `main`.

If no remote and GitHub CLI authenticated/permitted, create private repo and remote.

Never make public.

Before every push run secret scan, inspect staged files, ensure no PEM/.env/state/database.

Commit coherent milestones with meaningful messages.

If push/auth unavailable, complete the build/commits anyway, record exact blocker, and provide exact human push command.

---

# 86. SECRET SCANNING BEFORE GIT

Use detect-secrets plus explicit private-key/credential-pattern scans and inspect Git history.

Private repository status is not a substitute for secret hygiene.

---

# 87. ORACLE DEPLOYMENT

Create:

- `deploy/oracle_install.sh`
- `deploy/oracle_upgrade.sh`
- `docs/deployment.md`

Installer: install Docker from official repo, persistent volumes, generate/preserve high-entropy secrets, backup before upgrades, DB/support services/migrations/workers/web/Caddy, health verification, URL/setup token, rollback on failure.

Caddy uses Docker-managed data/config volumes.

No app 8000 exposure.

---

# 88. DEPLOYMENT ACCEPTANCE

Never fake external verification.

Use explicit states:

```text
OFFLINE VERIFIED
MOCK VERIFIED
DEMO VERIFIED
PRODUCTION READ VERIFIED
PRODUCTION WRITE PATH IMPLEMENTED
LIVE WRITE NOT EXECUTED
```

Never call something production verified unless actually verified.

---

# 89. PRODUCTION READ ACCEPTANCE

Later human acceptance:

```text
[ ] account 0 confirmed
[ ] cash matches Kalshi
[ ] portfolio value matches
[ ] positions match
[ ] resting orders match
[ ] fills match
[ ] settlements match
[ ] three automatic refresh cycles succeed
[ ] VM reboot recovers
[ ] key remains encrypted
[ ] support snapshot contains no secrets
```

Build scripts/UI for this.

---

# 90. PRODUCTION WRITE ACCEPTANCE

Repository can implement and verify write path via mocks/demo.

Actual real-money verification is human-controlled.

Never send real order as Codex.

Provide supervised canary runbook with one approved strategy/contract and exact reconciliation steps.

---

# 91. FINAL VERIFICATION COMMANDS

Provide Makefile/task interface for bootstrap, specs, schemas, format/lint/type checks, all test categories, SBOM, and verify.

Final baseline should include locked dependency install, Ruff, mypy strict, pytest, Bandit, detect-secrets, pip-audit, `docker compose config`, and `docker compose build`.

Resolve failures. Do not hide tests with skips except genuine unavailable credential/external integrations, clearly documented.

---

# 92. FINAL AUDIT CHECKLIST

Verify all of the following:

## Account
- production read-only adapter;
- account 0;
- balance/portfolio normalization;
- positions/orders/fills/settlements.

## Market Data
- full universe;
- no arbitrary sampling;
- WS state;
- gap recovery;
- rules versioning;
- lifecycle;
- historical routing.

## Intelligence
- Polymarket;
- event matching;
- breaking signals;
- PredictBuddy path;
- social path;
- manipulation safeguards;
- latency/provenance.

## Forecasting
- market prior;
- independent models;
- calibration;
- uncertainty;
- frozen predictions;
- no leakage.

## Learning
- source attribution;
- champion/challenger;
- bounded/reversible adaptation;
- hard-risk immutability.

## Opportunity
- YES/NO;
- fees/slippage/fill/liquidity/correlation/abstention.

## Risk
- $700 reserve;
- $10 market cap;
- $25 event cap;
- $100 open-risk cap;
- $20 daily stop;
- $50 weekly stop;
- $100 monthly stop;
- $200 total drawdown stop.

## Execution
- unique IDs;
- no blind retry;
- amend/cancel;
- order groups;
- reconciliation;
- kill switch;
- isolated signer.

## Security
- no key in Git/logs;
- LLM/dashboard cannot access signer key;
- TOTP/CSRF/sessions/CSP/redaction/scans.

## UX
- desktop/mobile visual QA;
- no broken/fake controls;
- no misleading labels;
- clear blockers;
- plain English;
- Advanced technical detail.

## Operations
- Docker health;
- backups/restores;
- restart recovery;
- disarm after restart;
- alerts/metrics/logs;
- API drift/dependency scans.

## GitHub
- private repo;
- main branch;
- commits;
- README;
- safe env example/gitignore;
- uv.lock;
- CI;
- green CI if remote execution available.

---

# 93. FINAL COMPLETION REPORT

Produce:

# KALSHI PRODUCTION V3 — COMPLETION REPORT

Repository:
Git remote:
Branch:
Commit SHA:
Release:

## Implementation
Services completed:
Strategies completed:
Dashboard pages:
Data sources:
LLM providers:
Kalshi API components:

## Verification
Tests:
Lint:
Types:
Security scans:
Container scans:
Docker build:
Schema drift:
UI visual QA:

## Runtime Status
Research:
Paper/demo:
Production read:
Production write implementation:
Production write credential:
Production armed:
Autonomous trading:

Use exact truthful values.

## Promotion State

For each strategy:

```text
Research
Shadow
Paper/demo
Supervised-live eligible
Autonomous-live eligible
```

Never imply eligibility without evidence.

## Human Actions Still Required

List only true external/human actions, with action/reason/exact instructions/security considerations.

## Known Limitations

Be explicit.

## Recommended Next Actions

Prioritized.

---

# 94. IMPORTANT DISTINCTION IN FINAL REPORT

These are NOT equivalent:

```text
CODE COMPLETE
OFFLINE VERIFIED
DEMO VERIFIED
PRODUCTION READ VERIFIED
PRODUCTION WRITE PATH VERIFIED IN DEMO
SUPERVISED PRODUCTION ELIGIBLE
AUTONOMOUS PRODUCTION ELIGIBLE
```

Report separately.

---

# 95. NON-NEGOTIABLE FINAL PRINCIPLE

Do NOT judge success by raw observations, number of trades, win rate, LLM calls, amount of automation, or dashboard aesthetics alone.

Judge success by point-in-time correctness, calibration, market-relative skill, after-cost EV, realistic fill quality, source incremental value, execution quality, risk-adjusted capital efficiency, drawdown, reconciliation, reliability, security, and ability to abstain.

Desired behavior:

> Find evidence that genuinely changes fair value before the opportunity disappears.

> Determine whether the market already incorporated it.

> Trade only when meaningful after-cost advantage remains.

> Size conservatively.

> Reconcile every contract and dollar.

> Learn from unique settled outcomes.

> Promote only strategies and sources that prove incremental value.

> Become more selective as evidence improves.

---

# 96. START NOW

Do not respond with only a plan.

Begin by:

1. inspecting repository/workspace;
2. checking Git status/remotes;
3. creating/correcting repository foundation;
4. creating `AGENTS.md` and authoritative docs;
5. writing milestone status tracker;
6. setting up dependencies/testing/CI;
7. running baseline checks;
8. committing Milestone 0;
9. proceeding immediately into Milestone 1;
10. continuing through every buildable milestone.

Use task decomposition internally.

Parallelize independent analysis/testing/review where supported.

Use browser-based visual inspection for dashboard where available.

Continuously update:

`docs/IMPLEMENTATION_STATUS.md`

Do not wait for confirmation between milestones.

Do not enable real-money trading.

Do not request real secrets until a human integration test genuinely requires them.

**Build the complete system now.**

## M25B2 — live read-only Perps evidence boundary

M25B2 is an explicitly enabled manual evidence smoke only. It adds fixed demo/production Perps
REST and WebSocket endpoints, public market metadata preflight, authenticated margin-enabled
preflight, exact-read WebSocket authentication, raw receipt timing, connection epochs, bounded
reconnect, and only `orderbook_delta`/`ticker` subscriptions. It adds no trading or write method,
is not deployed or autostarted, and has exactly zero production influence. Live acceptance remains
pending until a separately reviewed environment-proven exact-read credential provider is composed.
