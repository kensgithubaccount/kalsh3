# Implementation Status

| Milestone | State | Verification |
|---|---|---|
| M0 Repository Foundation | Complete (offline verified) | Baseline quality gates pass |
| M1 CODE/UX | Offline verified | Production-read account UI; live acceptance pending |
| M2 CODE | Offline verified | Complete/versioned universe fixtures; live universe pending |
| M3 CODE | Offline verified | Realtime protocol/replay fixtures; live WebSocket pending |
| M4 CODE | Offline verified | Contract semantics/settlement intelligence; live semantics pending |
| M5 CODE / fixtures | Offline verified | Sources/events/signals, Polymarket, adapters, matching, dedupe, health, archive, UI and stress tests pass |
| M5 Polymarket REST / WS / comments | Offline verified fixtures | Public live feeds not connected; no execution/auth code |
| M5 wallet intelligence | Offline verified models | Public descriptive observations only; no copy trading |
| M5 PredictBuddy | Offline verified authorized-import boundary | API unavailable; no scraping; live delivery not configured |
| M5 X | Offline verified interface | SETUP_REQUIRED; token/access/cost approval absent |
| M5 Bluesky | Offline verified fixtures | Jetstream discovery-only; canonical revalidation required |
| M5 RSS | Offline verified fixtures | Allowlist/conditional/correction/SSRF tests; live feeds unverified |
| M5 Reddit | Interface only | SETUP_REQUIRED / ACCESS_REVIEW_REQUIRED |
| M5 matching/dedupe/manipulation defense | Offline verified | Conservative semantic conflicts, lineage, recirculation and flags pass |
| M5 Breaking Now / Sources UI | Offline verified | Escaping, pagination and state fixtures; visual QA pending |
| M5 live external feeds / human acceptance | Not verified / Pending | Network/credentials and Oracle acceptance required |
| M5 production influence | NONE | Enforced by models, DB checks and static architecture tests |
| M6 Historical + Point-in-Time Replay | Complete (offline verified) | Availability provenance, mock historical clients/routing, immutable archives/datasets, gaps, final labels, vintages, deterministic replay and 100k stream pass; live API pending |
| M7 Document + LLM Evidence | Complete (offline verified) | Provider-neutral fixture, mock OpenAI/Anthropic structured output, immutable bundles, prompts, claims, citations, interpretations, contradictions, abstention, cache/budgets, replay, 10k eval and UI pass; live providers pending |
| M8 Forecasting + Calibration | Complete (offline verified) | Immutable market/independent/anchored forecasts, point-in-time features, weather/CPI distributions, walk-forward calibration, proper scoring, registry/cards, 5k grouped test and UI pass; live sources pending |
| M9 Source + Model Learning | Complete (offline verified) | Event-level ablation/uncertainty, redundancy/timeliness, multiple-testing control, champion/challenger, bounded proposals, quarantine, rollback/configurations, tournament/budget, replay, 20k fixture and UI pass |
| M10 Opportunity Engine | Complete (offline verified) | Exact YES/NO books, fractional depth, fee fail-closed/versioning, conservative EV, maker/taker uncertainty, liquidity/decay/correlation, cross-venue research, ranking, replay, 50k fixture and UI pass |
| M11 Event Backtests + Fill Simulation | Complete (offline verified) | Arrival-time taker books, aggregate-queue maker assumptions, partial/cancel races, gaps, markouts, three scenarios, advancement gate, cross-venue legs, 100k fixture and UI pass |
| M12 Full Dashboard Product | Complete (offline verified) | Full information architecture, single global state, owner-first pages, responsive/accessibility/security fixture gates; screenshot and human acceptance pending |
| M13 Deterministic Risk | Complete (offline verified) | Immutable limits, full-fill exposure, reserve/ledger, loss stops, reconciliation holds, kills, halt, exact decisions, one-use reservations, 50k load, and UI pass; live reconciliation pending |
| M14 Demo / Paper Execution | Complete (offline/mock verified) | Shared state machine, demo-only transport/vault, journals, unknown recovery, lifecycle/fills/WS/queue/calibration, 20k fault load, UI; live demo and PostgreSQL integration pending |
| M15 Production Execution Path | Complete (offline verified) | Sign-and-send ADR, typed canonical envelopes, RSA-PSS fixtures, exact M13 binding, one-use journals, fixed host/routes, hardened service and truthful UI; credential NONE, DISARMED |
| M16 Supervised Production Canary | Complete (offline verified) | Exact one-contract previews, live-evidence readiness, strong one-use approval, fresh final gates, one-session locking, partial fills, unknown recovery, auto-disarm and UI; live gates absent |
| M17 Bounded Autonomy | Complete (offline verified) | Off-only policy, evidence snapshots, governance proposals, durable restart constraints and truthful UI; autonomy OFF |
| M18 Operations Hardening | Complete (offline verified) | Fail-closed observability, recovery, encrypted backup/restore design, hardened Compose, CI supply-chain gates and runbooks; live operations pending |
| M19 Final Audit | Complete (offline verified) | Repository-wide audit corrected redirect, exact-fill, signer-oracle, nested-float, XML, auth-DoS, and UI defects; all live/human gates remain pending |
| M20 Live Deployment Corrections | Complete (targeted live evidence + CI pending) | AWS Ubuntu Redis user/capability correction, persistent overcommit prerequisite, Caddy hostname wiring, non-destructive NATS health, generic/AWS runbook and runtime regression; broader live acceptance pending |
| M21 Live Production-Read API Contract Correction | Complete (offline/current-shape verified) | Correct API-key identity/scope, integer-cent balance, nested limits and safe setup errors; live production-read retry pending |
| M22 Read-Only Unrestricted-Key Compatibility | Complete (offline verified) | API-key enrollment accepts absent/null/zero subaccount, rejects all other shapes; runtime subaccount=0 targeting and GET/HEAD-only surface unchanged; live production-read retry pending |
| M23A Control Center UX, Visualization & Maintainability Audit | Complete (offline verified) | State-derived connection labeling, readiness checklist incl. evidence sufficiency, actual-vs-policy capital split with deferred (not inferred) composition, real-data-only SVG charts, navigation regrouping, presentation-layer refactor; no execution/signer/risk-policy files touched; browser visual QA desktop/tablet/mobile |
| M23B Trading Dashboard Simplification | Complete (offline verified) | Five-section navigation preserving every legacy route, redesigned dark compact Dashboard (hero value, real-history chart, opportunities/positions tables, status strip), full readiness matrix relocated to System, Account-vs-Bot provenance labeling, CSP-safe SVG limit-bar fix, compliance/contradiction/typography corrections; no execution/signer/risk-policy files touched; browser visual QA desktop/tablet/mobile |
| M24 Perps Shadow Research | Part A complete (offline verified) | Immutable observations plus canonical snapshot/delta book evidence, explicit epoch/SID/sequence/exchange-index provenance, pre-mutation source-event replay/collision handling, deterministic identity, and append-only SQLite storage. Focused pytest 40 passed; Ruff passed. Full suite: 447 passed with 46 unrelated tracked macOS signer portability failures (`/proc/self/fd` or `os.memfd_create`). Live collection OFF; production influence NONE. |
| M25 Live Read-Only Evidence Collection | M25A + M25B1 + M25B2 + M25B3 + M25B4 + M25B5 implemented; live acceptance pending | M25B5 adds a neutral, separate production exact-read credential store/provider that resolves only after one fixed authenticated production `GET /trade-api/v2/api_keys` uniquely proves the enrolled key has server-side scopes exactly `['read']`; a syntactically valid non-exact scope set quarantines it without necessarily proving write access. DEMO is not a prerequisite. Perps `run_live_smoke()` itself structurally rejects production before provider resolution, network-capable work, or persistence mutation, with no override. M25B4 keeps the environment-neutral GET/HEAD-only signer portable across normal macOS/Linux targets with the documented Linux procfs assumption. No real credential is enrolled or verified and no live smoke was run. Production execution remains disarmed and production influence is exactly zero. |

## Runtime truth

- Research data/semantics/signals: M2–M5 code offline verified; no forecast, probability, alpha or opportunity
- Live account/universe/WebSocket/external feeds: deliberately not connected or verified
- Production write: disabled and absent
- Production armed: no
- Autonomous trading: off

## M6 acceptance

- Kalshi historical contract: MOCK VERIFIED against externally supplied current official facts.
- Market/trade/candle and private fill/order history: MOCK VERIFIED; private history uses only the existing read credential.
- Point-in-time availability, rules/fee reconstruction quality, external replay, gaps, deterministic replay, and 100k streaming: OFFLINE VERIFIED.
- Live historical API: NOT VERIFIED. Human acceptance: PENDING.
- Production writes, autonomous trading, and M5 source influence remain OFF/NONE.

## M7 acceptance

- OpenAI and Anthropic adapters: MOCK VERIFIED; LIVE NOT VERIFIED and no keys requested.
- Fixture/eval, structured validation, prompt-injection architecture, numeric/citation fidelity,
  contract interpretation, contradiction, abstention, replay safety, cost accounting, 10k corpus and UI:
  OFFLINE VERIFIED.
- M7 production influence: NONE. Human acceptance: PENDING.

## M8 acceptance

- Forecast immutability, market reference, independent/anchored separation, weather/CPI fixtures,
  point-in-time safety, calibration, uncertainty, abstention, market-relative scoring, unique-event
  accounting, registry/cards, 5,000-row grouped evaluation and UI: OFFLINE VERIFIED.
- NWS/economic connector policies: MOCK VERIFIED. Live NWS/economic sources: NOT VERIFIED.
- Real settled evidence: INSUFFICIENT. Production influence: NONE. Human acceptance: PENDING.

## M9 acceptance

- Source/model/group ablation, redundancy, timeliness, effective samples, statistical uncertainty,
  multiple-comparison control, champion/challenger, human-gated promotion, 10pp cap, quarantine,
  rollback, family tournament, exploration budget, replay gates, 20,000-row/2,000-event test and UI:
  OFFLINE VERIFIED.
- Real settled learning evidence: INSUFFICIENT REAL EVIDENCE. Production influence: NONE.
  Human acceptance: PENDING.

## M10 acceptance

- YES/NO economics, binary-book complement normalization, fixed-point/fractional depth, historical fee
  selection, rounding boundary, taker walk/slippage, maker/fill uncertainty, conservative gating,
  liquidity/decay/correlation, cross-venue research, ranking, replay manifest, 50,000-row/5,000-event
  test and UI: OFFLINE VERIFIED.
- Current live fee formula/examples and live economics: NOT VERIFIED. Maker fill model: UNVALIDATED.
- Real forecast evidence: INSUFFICIENT REAL EVIDENCE. Production influence: NONE. Human acceptance: PENDING.

## M11 acceptance

- Taker arrival replay, maker aggregate-queue simulation, queue-quality honesty, fractional partial fills,
  cancel/fill races, versioned latency/scenarios, effective fees, markouts, decay timing, walk-forward
  evaluation, event grouping, multiple-comparison manifests, base+adverse advancement, one-leg cross-venue
  risk, gaps, capacity, drawdown, 100,000-attempt/5,000-event streaming test and UI: OFFLINE VERIFIED.
- Maker queue, cross-venue and fee validation use deterministic fixtures: MOCK VERIFIED. Fill model: UNVALIDATED.
- Real execution observations: NOT VERIFIED / NONE. Real strategy evidence: INSUFFICIENT REAL EVIDENCE.
  Production influence: NONE. Human acceptance and screenshot QA: PENDING.

## M12 acceptance

- Information architecture, global state, Overview, Opportunities, Breaking Now, Markets/detail,
  Sources, Learning, Portfolio, Orders & Trades, Reports, Risk & Safety, System, Advanced,
  meaningful empty/stale/error states, financial units, keyboard landmarks, touch targets,
  responsive CSS, hostile-content escaping, security regression, and non-mutation architecture:
  OFFLINE VERIFIED.
- Browser/Playwright desktop, tablet, and mobile screenshot review: PENDING because browser tooling
  is unavailable. Human acceptance: PENDING.
- Production writes: OFF. Production influence: NONE. No risk authorization or execution path exists.

## M13 acceptance

- Immutable hard limits, Decimal ledger and full-fill exposure, reserve protection, daily/weekly/monthly and
  drawdown stops, subaccount-0 reconciliation, external-activity holds, four kill categories, compliance and
  global halt, fail-closed readiness, exact intent binding, five-second one-use authorization, transactional
  reservations, restart recovery, 50,000-evaluation load fixture, audit events, and Risk & Safety UI:
  OFFLINE VERIFIED.
- Account and order-group inputs are fixture reconciliations: MOCK VERIFIED. Real account reconciliation:
  NOT VERIFIED. Production mutation capability: NONE. Human and browser acceptance: PENDING.
- A pass is only `RISK CHECK PASSED` / `PASS_NEXT_GATE`; it is not order, trade, production, signer, or
  execution approval. M14 has started only at a demo/mock/paper boundary after this offline gate passed.

## M14 acceptance

- Mock/paper shared state machine, exact current-V2 request fixtures, demo origin and credential isolation,
  intent/authorization binding, durable pre-send journals, unknown-response reconciliation, partial fills,
  cancel/fill races, amend/decrease, idempotent order/fill streams, gap/disconnect recovery, queue/fee/slippage
  comparison artifacts, mode-separated ledger, 20,000-lifecycle fault load, and UI: OFFLINE/MOCK VERIFIED.
- PostgreSQL SERIALIZABLE/row-lock transaction path is statically tested and CI-configured; local PostgreSQL
  concurrency is NOT VERIFIED because Docker/PostgreSQL are unavailable. Demo order groups: NOT VERIFIED.
- Live demo API and order: NOT VERIFIED. Production write credential: NONE. Production mutation capability:
  NONE. Real-money order: NONE. Human/browser acceptance: PENDING.

## M15 acceptance

- Sign-and-send body-binding ADR, isolated credential classes, immutable canonical envelopes, fixed production
  origin and lifecycle routes, RSA-PSS ephemeral fixtures, timestamp/TTL checks, exact M13 authorization and
  safety-state binding, durable one-use journal, unknown-response/restart handling, reserved write budget,
  static signer topology, red-team suite, and System status: OFFLINE/MOCK VERIFIED.
- PostgreSQL and container runtime integration: NOT VERIFIED locally. Production write credential: NONE.
  Production state: DISARMED. Live production write: NONE. Real-money order: NONE. Human/browser acceptance:
  PENDING.

## M16 acceptance

- Exact one-contract immutable preview, two-minute preview/60-second approval, step-up authentication,
  one-use approval, one-unresolved-session database lock, live-evidence readiness, exchange/user-data freshness,
  final M13/M15 binding, price/rules/state invalidation, partial-fill accounting, unknown recovery,
  first-50-real-fill counter, automatic DISARM, acceptance report, and owner UI: OFFLINE/MOCK VERIFIED.
- Live demo acceptance and production reads: NOT VERIFIED. Production write credential: NONE. Live canary:
  NONE. Real-money order: NONE. Production state: DISARMED. Human/browser acceptance: PENDING.
## M17 acceptance

- Off-only state model, immutable one-contract/one-market ceiling, explicit evidence classification,
  content-addressed snapshots and proposals, SQLite/PostgreSQL constraints, restart recovery, malicious
  environment resistance, signer/transport isolation, static red-team checks, and owner UI: OFFLINE VERIFIED.
- Live supervised canary, production reads/reconciliation, PostgreSQL concurrency, signer runtime, current
  official API compatibility, strategy evidence, and human governance: NOT VERIFIED.
- Autonomy: OFF. Production state: DISARMED. Production write credential: NONE. Production influence: NONE.
  Live production execution: NONE. Real-money order: NONE. Human acceptance: PENDING.

## M18 acceptance

- Dependency/readiness evaluation, structured redacted events, allowlisted metrics, safe tracing and health,
  queue/backpressure controls, restart recovery, API drift detection, cost caps, backup manifests, isolated
  restore tooling, deployment hardening, operational migration, incident/runbook coverage, support redaction,
  CI configuration, adversarial tests, and owner operational status: OFFLINE VERIFIED.
- Docker Compose runtime, Oracle host/TLS/firewall, live PostgreSQL concurrency, Redis/NATS/object storage,
  actual backup and restore drill, live alert delivery, SBOM/Trivy CI execution, browser review, live providers,
  and human acceptance: NOT VERIFIED / PENDING as applicable.
- Production state: DISARMED. Autonomy: OFF. Production write credential: NONE. Live production mutation:
  NONE. Real-money order: NONE.

## M19 final acceptance

- Architecture/safety, deterministic risk, financial/quant/data/model governance, execution/reconciliation,
  security, operations/deployment, UI truthfulness, documentation hygiene, and adversarial re-audit after
  material fixes: OFFLINE VERIFIED / MOCK VERIFIED as detailed in `reviews/M19_FINAL_AUDIT.md`.
- Official current Kalshi API compatibility, GitHub CI for the final commit, live Oracle/Docker/PostgreSQL/
  Redis/NATS/object-storage behavior, production reads/reconciliation, live Demo, backup restore, alerts,
  security scanners/SBOM, browser review, strategy evidence, and human acceptance: NOT VERIFIED / PENDING.
- Production: DISARMED. Bounded autonomy: OFF. Production-write credential: NONE. Live mutation: NONE.
  Real-money order: NONE. No M13 limit was weakened.

## M20 acceptance

- First AWS EC2 Ubuntu 24.04 x86_64 evidence: Redis previously exited 127 because `setpriv` could not drop
  privileges after all capabilities were removed. Running directly as `redis`, while retaining
  `cap_drop: [ALL]` and `no-new-privileges:true`, was LIVE VERIFIED healthy with
  `Ready to accept connections tcp`.
- Caddy hostname injection was LIVE DIAGNOSED and validated with a temporary override; checked-in Compose
  now passes the non-secret hostname explicitly. NATS's destructive `--signal ldm` check was LIVE
  DIAGNOSED and replaced by private monitoring `/healthz`; repeated lifecycle/client smoke coverage is
  checked in, with GitHub CI execution PENDING for this commit.
- Persistent host `vm.overcommit_memory=1`, AWS firewall/hardening, reboot persistence, DNS/TLS, full app
  startup, PostgreSQL, signer isolation at runtime, backup/restore, alerts, production reads/reconciliation,
  Oracle behavior, and long-duration operations remain NOT VERIFIED / PENDING as applicable.
- Production remains DISARMED. Bounded autonomy remains OFF. Production-write credential remains NONE.
  Live production mutation and real-money orders remain NONE. No strategy, model behavior, authorization,
  credential, risk limit, or signer-network boundary changed. Full production readiness is not claimed.

## M21 acceptance

- First live production-read setup reached Kalshi from the healthy HTTPS AWS stack but exposed current API
  response mismatches and surfaced only a generic WSGI error. Current documented response fixtures now
  require `api_key_id` with exactly `["read"]`, integer-cent `balance`/`portfolio_value` plus balance
  metadata, and nested `read`/`write` rate-limit buckets. Decimal-safe parsing rejects floats and obsolete,
  incomplete, duplicate, or ambiguous shapes.
- Positions, orders, fills, and settlements retain explicit subaccount 0, complete cursor pagination, their
  documented collection names, and fail-closed page validation. The setup route now translates credential,
  scope, malformed-response, timeout/unavailability, rate-limit, and reconciliation failures without
  displaying or persisting submitted secrets or partial configuration.
- Independent review against current official Kalshi contract facts additionally corrected optional
  list-form `balance_breakdown`, required fixed-point `balance_dollars`, required limits `grants`, and API-key
  subaccount 0. A deploy and successful live production-read retry remain PENDING before production-read
  acceptance.
- Production signer: DISARMED. Production-write credential: NONE. Bounded autonomy: OFF. Account:
  subaccount 0 only. Gateway: GET/HEAD only. Live mutation and real-money orders: NONE. No strategy, model,
  risk, authorization, credential-handling, or production-write behavior was enabled.

## M22 acceptance

- The second live production-read setup attempt reached API-key enrollment but failed because enrollment
  previously required the returned key's `subaccount` field to equal `0` exactly. The current documented
  `GET /trade-api/v2/api_keys` response describes `api_key_id`, `name`, and `scopes`, and does not document
  a `subaccount` field, so enrollment must tolerate the field being absent.
- API-key enrollment now accepts a matching key when scopes are exactly `["read"]` and `subaccount` is
  absent (the documented shape), or is explicit `null` or the exact integer `0` (accepted conservatively
  for compatibility); nonzero integers, booleans, strings (including `"0"`), arrays, objects, and other
  malformed values fail closed with `AuthenticationRejected`, matching prior behavior for those shapes.
- Balance, positions, orders, fills, and settlements remain explicitly requested with `subaccount=0` on
  every call regardless of the enrolled key's own subaccount metadata; there is still no generic subaccount
  interface, and the gateway remains GET/HEAD only.
- Production signer: DISARMED. Production-write credential: NONE. Bounded autonomy: OFF. Live mutation and
  real-money orders: NONE. No strategy, model, risk, authorization, credential-handling, or production-write
  behavior was changed. Live production-read acceptance with this correction remains PENDING.

## M23A acceptance

- An audit of `services/web_dashboard` found the entire product rendered as inline f-string HTML with no
  view models, ad hoc repeated formatting, a hardcoded `$700.00` reserve figure on Overview that diverged
  from `RiskPolicy`, a flat blocker list duplicated across the banner and Overview, and no real-data
  visualizations anywhere in the product. See `docs/reviews/M23A_CONTROL_CENTER_UX_VISUALIZATION_MAINTAINABILITY_AUDIT.md`
  for the full audit and the changes made in response.
- Global system state now explains itself: a derived, categorized readiness checklist (Connection, Research,
  Risk, Execution, Autonomy readiness) replaces the flat blocker list, with one primary next action
  surfaced. HALTED is visually distinct from a crash: the same restrained amber treatment already used for
  NEEDS ATTENTION, never alarmist red.
- Overview now separates the actual reconciled account (available cash, Kalshi's reported portfolio value,
  positions, exposure) from policy targets (bankroll, protected reserve, active-allocation ceiling), and
  labels a policy bankroll target that exceeds the current reported portfolio value as "Not currently
  fundable" instead of implying it is allocated capital. The previously hardcoded `$700.00` reserve figure is
  now read from `RiskPolicy` like the rest of the product. A pre-merge correctness review found that Kalshi's
  own materials describe `portfolio_value` inconsistently (positions-only value vs. total value including
  cash); Overview no longer calls that field "equity" and no longer infers a cash-vs-positions composition
  from it — the composition chart is deferred until that semantics is positively validated, replaced by an
  honest explanation.
- Two new accessible, server-rendered SVG chart primitives are active on Overview (policy-limit bars and a
  "Kalshi portfolio value over time" sparkline, explicitly not labeled "equity" or "account value"); a third
  (`composition_bar`) exists and is unit-tested but is not currently called, pending validated portfolio-value
  semantics. Every chart exposes an openable exact-value table, never relies on color alone, and renders only
  real reconciled data. A new `account_snapshot_history` table records each successful read-only
  reconciliation's cash/portfolio-value so the sparkline has real history to draw from; before two real points
  exist it shows an honest "insufficient history" state, never a fabricated one. Its reader returns the
  newest `N` observations in chronological order — a pre-merge review caught and fixed an initial version
  that returned the oldest `N` instead whenever more history existed than the requested limit.
- The Overview "REAL ACCOUNT CONNECTED · READ ONLY" eyebrow is no longer a hardcoded literal: it is now
  derived from the real account status and staleness, and can only say "CONNECTED" when the account actually
  is — an errored, never-configured, or stale account gets truthfully different wording instead.
- Readiness now includes a "Required real evidence sufficient" check, informational only, reusing the
  existing governed `promotion_minimum` real-settled-event threshold already shown on `/learning`; it cannot
  render as met while real settled evidence is insufficient, and does not change promotion, strategy, risk,
  execution, or autonomy behavior.
- Primary navigation is now visually grouped (Research / Account / Safety / System) without hiding, removing,
  or reordering any existing page; a new invariant check (`assert_navigation_covers_all_surfaces`) fails
  closed if grouping ever drops or duplicates a surface.
- No file under `services/kalshi_account_gateway`, `services/risk_engine`, `services/production_execution`,
  `services/supervised_canary`, or `services/bounded_autonomy` was touched. Production signer: DISARMED.
  Production-write credential: NONE. Bounded autonomy: OFF. The production account gateway remains
  read-only; no credentials were requested or used. Pre-existing account positions/fills are never labeled
  as bot-generated absent explicit provenance — the product still renders only the raw reconciled fields it
  is given.

## M23B acceptance

- Mission was explicitly experience simplification, not system simplification: no `services/risk_engine`,
  `services/production_execution`, `services/supervised_canary`, `services/bounded_autonomy`, or
  `services/kalshi_account_gateway` file was touched. See
  `docs/reviews/M23B_TRADING_DASHBOARD_SIMPLIFICATION.md` for the full before/after information
  architecture, provenance rules, and screenshots.
- Primary navigation collapsed from a flat twelve-link list to five top-level sections (Dashboard, Markets,
  Activity, Strategy, System) plus a visible secondary nav per section; a new
  `assert_navigation_covers_all_surfaces()` invariant and a reachability test fail closed if any existing
  deep route (`/orders`, `/portfolio`, `/reports`, `/learning`, `/sources`, `/forecasting`, `/backtests`,
  `/risk`, `/system`, `/advanced`, `/opportunities`, `/breaking`, `/markets`) ever stopped being reachable.
  No route was removed or renamed.
- The Dashboard was rebuilt around one hero reported-portfolio-value figure, one real-history sparkline, a
  small metric row, an honest opportunities table, a positions table, and a compact system status strip —
  the full nine-check readiness matrix moved to `/system` (still fully present there, unit-tested to remain
  so) rather than being deleted.
- Account-vs-bot provenance: the current account's positions, orders, fills, and settlements default to
  "Unattributed" everywhere they render, because no persisted field proves either bot ownership or that an
  item predates the bot; "Bot P&L" reads an explicit "No attributable live trades yet" rather than any
  figure derived from those settlements. This is enforced by dedicated tests, not just docstring intent.
- Four live-observed defects were corrected: (1) `build_readiness()` now takes explicit
  `universe_status`/`realtime_state` and can no longer report market-data gaps as resolved while the
  universe is `NOT_STARTED` or market data is disconnected; (2) the compliance check now takes the raw
  compliance state string and distinguishes "established and clear" from "not yet established" from an
  active hold, instead of a collapsed boolean; (3) the primary-action panel now renders eyebrow/title/detail
  as separate block elements, fixing the concatenated "What needs you mostRequired real evidence
  sufficient" text; (4) the policy-limit bar chart no longer relies on inline `style="width:...%"`, which
  `style-src 'self'` silently drops — it now renders per-row SVG using the `width` presentation attribute,
  with the CSP left unchanged and unweakened.
- A follow-up correctness pass on this same milestone fixed four further truthfulness gaps, all confined to
  `services/web_dashboard/` and its tests: (5) the positions table no longer hardcodes "Pre-existing" — it
  defaults to "Unattributed" and only claims real ownership from an explicit `provenance` field, so a
  manual trade placed after deployment can never be misattributed; (6) the Dashboard opportunities table now
  only surfaces a candidate whose persisted `data_mode` is the existing `LIVE RESEARCH DATA` value and whose
  `decision_state` is one of the research engine's own real affirmative states, so a synthetic, historical-
  replay, or rejected/watch-only candidate can never render as if it were a current live signal; (7) the
  hero no longer infers "Below target bankroll" from the still-unvalidated `portfolio_value` field — it
  shows the raw value with a plain link to the real target-bankroll figure on Risk & Safety; (8) the top
  status bar now uses a new presentation-only `derive_display_status()` so an unestablished (`UNKNOWN`)
  compliance state renders as amber "NEEDS ATTENTION" rather than the same red "HALTED" as an active
  compliance hold or an explicit global halt — `derive_global_state()` itself, and the canonical `Risk
  state` it drives on `/risk`, are unchanged.
- Charts remain server-rendered SVG only, real-data-only, with accessible titles/descriptions and an
  openable exact-value table; no CDN script, chart library, or inline script was added anywhere in the
  product. The stylesheet was rewritten to a dark-by-default palette using CSS custom properties, keeping
  every previously verified accessibility hook (`:focus-visible`, `min-height:44px` touch targets,
  `overflow-wrap:anywhere`, `prefers-reduced-motion`, responsive breakpoints at 900px/650px).
- Bot performance history, a bot equity curve, trade attribution, per-market/YES-vs-NO exposure, execution
  alpha/markout, model calibration curves, real source-contribution charts, and any perpetuals data or
  trading are deferred, not fabricated: the Dashboard and hubs show honest empty states for all of them, and
  no perpetuals endpoint, credential, order type, or margin/trading capability was added.
- Production signer: DISARMED. Production-write credential: NONE. Bounded autonomy: OFF. Production account
  gateway: read only. No real-money order capability was introduced. This milestone does not claim M23
  reconciliation is complete, does not claim production trading is enabled, and does not claim any bot P&L
  evidence exists — only that the existing account and system state are now presented truthfully and more
  legibly.
