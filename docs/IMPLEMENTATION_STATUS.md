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
| CPI-E1-P6 Initial-Release Value Evidence | Implemented; focused parser/authority tests added; empirical artifact replay requires saved P5A files | Narrow dual-representation CPI-U SA MoM parser binds exact P4/P5A acquisition identity/mode, artifact hash, P1/P2 evidence, reference month, and Decimal one-decimal value. Research-only with production influence 0; no settlement or G3 claim. See `reviews/CPI_E1_P6_INITIAL_RELEASE_VALUE_EVIDENCE.md`. |
| CPI-E1-P7 Settlement Reconciliation | Implemented; three exact public historical KXCPI markets empirically MATCHED; transitive authority repair applied | Capability-gated reviewed Kalshi HTTPS GET, durable exact frozen response bytes, deterministic market/event/series semantic reconstruction, explicit historical `status=finalized` plus settlement timestamp, Decimal-only comparator evaluation, and adversarial provenance tests. Historical API exposes no separate rules-version field, so full content-addressed market-rules identity is repository-derived and remains an independent-review limitation. Research-only with production influence 0. See `reviews/CPI_E1_P7_SETTLEMENT_RECONCILIATION.md`. |
| M9 Source + Model Learning | Complete (offline verified) | Event-level ablation/uncertainty, redundancy/timeliness, multiple-testing control, champion/challenger, bounded proposals, quarantine, rollback/configurations, tournament/budget, replay, 20k fixture and UI pass |
| M10 Opportunity Engine | Complete (offline verified) | Exact YES/NO books, fractional depth, fee fail-closed/versioning, conservative EV, maker/taker uncertainty, liquidity/decay/correlation, cross-venue research, ranking, replay, 50k fixture and UI pass |
| M11 Event Backtests + Fill Simulation | Complete (offline verified) | Arrival-time taker books, aggregate-queue maker assumptions, partial/cancel races, gaps, markouts, three scenarios, advancement gate, cross-venue legs, 100k fixture and UI pass |
| MM-A1 Passive Market-Making Research | Implemented; independent review pending | Content-addressed monotone sibling fair-value curves; exact M27A market/rules/book/fee binding; conservative one-contract shadow quotes; inventory skew; explicit abstentions; M11 maker-fill/markout/settlement receipts. Research only, profitability not established, no order/network/risk authority, production influence 0. See `reviews/MM_A1_AUTONOMOUS_MARKET_MAKING_RESEARCH.md`. |
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
| M25 Live Read-Only Evidence Collection | M25A + M25B1–M25B6 implemented; production read-only live acceptance complete | After the offline M25B6 implementation and review, a separately human-invoked bounded manual production read-only smoke succeeded for canonical ticker `KXBTCPERP`. The real reviewed credential, REST, entitlement, margin WebSocket, reconnect, and append-only evidence path was accepted for production read-only Perps observation. No write action occurred; production execution remains DISARMED, production influence is exactly zero, and trading is not unlocked. See `reviews/M25B7_PRODUCTION_LIVE_ACCEPTANCE.md`. |
| M26A Agent Control Center Foundation | Implemented (offline verified) | Immutable seven-agent truth registry, zero-influence autonomy constraints, immutable deterministic Decision Receipts, structured plain-English explanations, owner-first six-section navigation, `/agents` roster/detail views, and Overview authority/empty-state summary. No production data accessed; production execution DISARMED and production influence exactly 0. See `reviews/M26A_AGENT_CONTROL_CENTER_FOUNDATION.md`. |
| M26B Persisted Agent Decisions | Implemented (offline verified; independent-review corrections applied) | Dedicated append-only canonical Decision Receipt persistence; shared current-authority validation at both new-attribution and append boundaries with registry-independent audited historical restoration; globally unique source attribution; content-bound Cross-Market source packages; deterministic Event Edge/Cross-Market adapters; receipt-derived current beliefs; safe corrupt-history UI state; and unambiguous attributed/unattributed opportunity labels. Event Edge and Cross-Market are the only receipt producers. No performance ledger, execution, network, credentials, scheduler, allocation, or live autonomy; production execution remains DISARMED and production influence exactly 0. See `reviews/M26B_PERSISTED_AGENT_DECISIONS.md`. |
| M26C Outcome-Linked Agent Evaluation | Implemented (offline verified; follow-up independent-review corrections applied) | Append-only settlement maturation and policy-version histories; logical replay idempotency excluding only processing-time `evaluated_at`; deterministic effective receipt/policy selection; contradictory-final and binary-field conflict rejection; exact Decimal Event Edge Brier, market baseline, descriptive decision-level calibration, and optional one-unit research counterfactuals; true aggregate denominators; unique-market reporting with independent-event count explicitly unavailable/inconclusive; store-backed complete-universe manifests explicitly identified as evaluation-attempt history; honest Cross-Market unsupported state; and fail-closed persisted-evidence Agent/Learning views. No trading, execution dependency, network, credentials, allocation, ranking, scheduler, or strategy mutation; production execution remains DISARMED and production influence exactly 0. See `reviews/M26C_OUTCOME_LINKED_AGENT_EVALUATION.md`. |
| M26D Controlled Agent Comparison + Research Competition | Implemented (offline verified; adversarial-review corrections applied) | Versioned trusted comparison-capability authority enforced at contender construction and comparison input; version-specific immutable historical contenders; store-backed complete-universe shared-proposition cohorts; explicit unaligned forecast-horizon/information-set limitation and `evaluated_at` processing-window basis; self-comparison rejection; conservative duplicate-unit exclusion; directional content-addressed manifests; exact paired Decimal Brier diagnostics; distinct corrupt-evidence UI state; and independent-event evidence explicitly unavailable, so no winner, champion, promotion, budget, capital, or trading consequence. Production execution remains DISARMED and production influence exactly 0. See `reviews/M26D_AGENT_COMPARISON_RESEARCH_COMPETITION.md`. |
| M26E Proven Event Identity + Evidence Sufficiency | Implemented candidate-validation infrastructure; archive verification supplied downstream by M26F | M2 exchange fields support deterministic point-in-time candidate market→event grouping, complete M26C/M26D-universe manifests, exact equal-weight within-candidate-event aggregation, and fail-closed conflict handling. Caller IDs/hashes never confer authority. Ordinary observations remain `UNPROVEN`; only the later M26F store-backed adapter can provide verified historical identity. Independent units remain unavailable and review is ineligible. Dashboard is status-only. No winner, governance, budget, capital, or trading consequence; production execution remains DISARMED and production influence exactly 0. See `reviews/M26E_INDEPENDENT_EVENT_IDENTITY_EVIDENCE_SUFFICIENCY.md`. |
| M26F Archive-Backed Historical Event Evidence Authority | Implemented; adversarial-review corrections applied; research-only | A dedicated append-only SQLite archive captures canonical transport responses only through the configured M2 synchronizer's opaque acquisition writer, persists reconstructable Market/Event parser sources, validates exact schema/index/trigger definitions before opening existing archives, never repairs damaged history, rejects non-finite JSON, and binds M26E `ARCHIVE_VERIFIED`/`PROVEN` state to explicit store-backed reconstruction. Ordinary callers, objects, and bare receipts confer no authority. This is local archive verification—not Kalshi attestation or protection against a privileged coherent full-database rewrite. Exchange-event counts do not establish independence; review remains ineligible. M26C/M26D identities are unchanged, old records are not backfilled, dashboard runtime remains unavailable without a real archive, production execution remains DISARMED, and production influence is exactly 0. See `reviews/M26F_ARCHIVE_BACKED_EVENT_AUTHORITY.md`. |
| M26G Reviewed Independent Evidence Units | Implemented; research-only; real registry empty | A downstream content-addressed assessment applies only a repository-reviewed, versioned partition to the complete M26E/M26F verified event set. Assignments bind exact archive authority, event observation, source hash, event and series context; partial, conflicting, changed, wrong-archive, or forged authority cannot produce a count. The legacy count means reviewed distinct descriptive units—not mathematical independence—and only complete coverage can make the 50-unit human-review gate eligible. No M9 inference, winner, governance, budget, capital, strategy, execution, or trading path is connected. Current real reviewed coverage is empty, so the dashboard truthfully reports the authority as not configured, review remains ineligible, production execution remains DISARMED, and influence is exactly 0. See `reviews/M26G_REVIEWED_INDEPENDENT_EVIDENCE_UNITS.md`. |
| M26H Runtime Evidence Collection | Implemented; production acceptance produced a valid partial broad archive | The operator-only public-read collector and M26F archive succeeded technically. An unfiltered 250-page run archived 25,000 Markets and 50,000 Events and truthfully remained `INCOMPLETE / bounded_truncation`, showing that the broad baseline is not a practical completeness unit. See `reviews/M26H_RUNTIME_EVIDENCE_COLLECTION.md`. |
| M26H.1 Scoped Complete Evidence Collection | Implemented; offline/fake-transport verified; scoped live acceptance pending | The CLI now requires the sole reviewed `open-non-mve-v1` scope: open non-MVE Markets (`status=open`, `mve_filter=exclude`, `limit=1000`) plus open Events (`status=open`, `limit=200`). Structured cursor encoding, exact semantic transport validation, control-character rejection, strict page bounds, sanitized request errors, synchronous progress, deterministic scope identity, sequential run windows, and conservative Market-to-Event coverage fail closed. Repeated runs append; old broad partial rows are not relabeled. M26G remains empty, M9 and the dashboard remain disconnected, production execution remains DISARMED, and influence is exactly 0. See `reviews/M26H1_SCOPED_EVIDENCE_COLLECTION.md`. |
| M26H.2 Live Market Schema Compatibility | Implemented; focused regression verified | The canonical Market parser defaults an absent `is_provisional` to false while rejecting present non-booleans, preserves null or absent titles as unavailable, and reads `updated_time` with fail-closed reconciliation against legacy `last_updated_ts`. Collection, archive, authority, execution, and production influence semantics are unchanged. |
| M26H.3 Authoritative Event Coverage Reconciliation | COMPLETE; independently reviewed and live accepted | The accepted `open-non-mve-v2` archive contains 84,724 Markets and 10,403 parent Event tickers; 7 parents were reconciled by exact read and 0 remain unresolved. M26G authority remains unchanged, production execution remains DISARMED, and influence is exactly 0. See `reviews/M26H3_AUTHORITATIVE_EVENT_COVERAGE_RECONCILIATION.md`. |
| M27A Live Market Economics Compatibility | Implemented, independently reviewed, and bounded production read-only live accepted | One bounded authenticated exact-read batch orderbook GET plus current Market/Event/Series point reads verified production read-only universe/economics evidence and exact self-contained TAKER_NOW replay for cent, subpenny, and fractional representatives. All accepted representatives used `current_series`; the Event override path was not separately live accepted. Pre-fill final exchange fee remains unknown, maker opportunity economics remain unsupported, trading remained locked/off, and production influence is exactly 0. See `reviews/M27A_LIVE_MARKET_ECONOMICS_COMPATIBILITY.md`. |
| M27B Universal Router + Directional Structural Scanner | M27B + M27B.1 implemented, independently reviewed, and H3 archive accepted; research-only | Committed-SHA acceptance on `1880dd46f7418c7c193bb8c343ece6d77d70c720` replayed 84,724 Markets / 10,403 Events. All 35,930 supported directional Markets passed strike parsing; `INVALID_FLOOR_STRIKE=0`; 35,823 Markets were structurally eligible across 5,513 cohorts; 14 discovery-only Structural Leads appeared across 11 Events, retaining the original 6 and adding 8. 0 network calls, 0 production writes, 0 exact confirmations, production influence exactly 0. No current executability, fair value, EV, profitability, arbitrage, allocation, autonomy, trading readiness, or execution claim. See `reviews/M27B_UNIVERSAL_ROUTER_STRUCTURAL_SCANNER.md`. |
| M27C Part 1 Daily-Temperature Contract Authority | Implemented and accepted by offline H3 archive replay; research-only | Deterministic specialist routing recognized exactly 480 of 84,724 Markets: 240 `DAILY_MAX`, 240 `DAILY_MIN`; 320 between, 80 greater, 80 less; 20 reviewed CLI identifiers; all `degF` and The Weather Company; 0 malformed strict candidates; production influence exactly 0. Category and `quality.classify()` are explicitly not eligibility because broad taxonomy can match Snowflake Inc. No live forecast, probability, fair value, profitability, allocation, execution, or trading-readiness claim. See `reviews/M27C_DAILY_TEMPERATURE_CONTRACT_AUTHORITY.md`. |
| M27C Part 2A Physical Weather Source Authority | Implemented and accepted by offline static public-source replay; research-only | Immutable authority separates 20 Kalshi CLI climate-product IDs from 20 NWS physical stations and 20 reviewed GHCN-Daily calibration stations while composing Part 1 locations/timezones. Pure vintaged evidence parsers cover NWS station/point-grid and fixed-width GHCNd metadata without transport. Accepted probe reconciled 20/20 NWS IDs, timezones, grids, reviewed GHCNd mappings, TMAX, TMIN, and 2026 endpoints; five distance-heuristic exceptions use explicit manual review. The Weather Company remains settlement source; no forecast/value/trading path; production influence exactly 0. See `reviews/M27C_WEATHER_SOURCE_AUTHORITY.md`. |
| M27C Part 2B1 Forecast-Vintaged Weather Calibration Evidence | Implemented; offline Chicago capture replay and focused adversarial tests verified; research-only | Pure parsers bind NDFD forecast reference time, 12-hour MaxT/MinT descriptor semantics, exact Kelvin conversion, NCSS valid-time coordinates, trusted station/grid evidence, and fixed-width GHCN-Daily TMAX/TMIN labels. Residuals are `observed - forecast` and explicitly carry `FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT`; the current/revised GHCN-Daily label is not claimed to be point-in-time visible. MaxT archive-format acceptance is covered separately from the additive MinT raw-GRIB lane. No probabilities, fair value, edge, profitability, sizing, allocation, execution, network, or production influence. See `reviews/M27C_WEATHER_CALIBRATION_EVIDENCE.md`. |
| M27C Part 3A DAILY_MIN Calibration Evidence | Implemented; bounded real NOAA raw-GRIB evidence and June 2024 acceptance reviewed; research-only | Reviewed `POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z` at exact 4h/26h/50h midpoint leads, with local target date bound to interval end date. June 2024 accepted 31 GRIB objects, 93 records, 30 usable TMIN outcomes, and 85 selected rows; two archive days remain rejected/ambiguous at exact horizons. Current 2026 family compatibility is YES. No DAILY_MIN probability model or production influence. See `reviews/M27C_DAILY_MIN_CALIBRATION_EVIDENCE.md`. |
| M27C Part 2B1.5 Historical Weather Calibration Coverage | Post-2020 raw-GRIB family reviewed; June coverage accepted; research-only | Operator-only bounded collection distinguishes `LEGACY_CHICAGO_MAXT_5KM_YGFZ98` from reviewed `POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z`. Pure raw-GRIB validation requires the reviewed 03Z/MaxT/12-hour/grid signature; wgrib2 remains operator-only. June 2024 completed with 32 accepted GRIB files, 96 forecast records, 87 residuals, and 29 selected rows per horizon bucket; families are not pooled. Part 2B1 is source/residual correctness; Part 2B1.5 is coverage collection; Part 2B2 probability generation remains pending. Production influence exactly 0. See `reviews/M27C_WEATHER_CALIBRATION_COVERAGE.md`. |
| M27C Part 2B2 Physical-Temperature Proxy Probabilities | Model mechanics implemented; corrected replay is non-pristine empirical evidence; research-only | Pure horizon-specific unweighted empirical residual models support only CLIMDW/KMDW/USW00014819 DAILY_MAX post-2020 YGUZ98 at exact 15h/39h/63h midpoints. Strict typed coverage loading, provenance-bound current GRIB evidence, nearest-rank intervals, exact CRPS, locked chronological splits, and nine exact Bonferroni-adjusted equal-tail binomial coverage gates fail closed. The final-holdout evaluator now requires and validates exact TRAIN + VALIDATION manifest provenance before constructing an empirical distribution, preventing the 542-row TRAIN-only incident. The corrected replay used 725 rows per horizon and passed all three CRPS and nine coverage gates, but 2026 is not pristine or independently confirmed because outcomes were exposed. No Forecast, market comparison, edge, EV, risk, or execution integration; production influence exactly 0. See `reviews/M27C_WEATHER_CALIBRATED_PROBABILITIES.md`. |
| M27C Part 2B3 Prospective Blind Weather Confirmation Freeze | Implemented; protocol frozen before period; research-only | Immutable protocol identity predeclares 2026-09-01 through 2027-03-31, with August 2026 operations-only blackout, exact frozen model identities, TRAIN + VALIDATION boundary through 2025-12-31, and unchanged CRPS/Bonferroni policy. A separate forecast-only evidence boundary rejects outcomes, residuals, evaluation metrics, market data, mismatched source semantics, and nonzero production influence without importing GHCN outcome acquisition. Jan-Jul 2026 remains corrected but non-pristine and is not independent prospective confirmation; outcomes are deferred until after 2027-03-31. No weather-model tuning, trading, settlement, or production claim. See `reviews/M27C_PART2B3_PROSPECTIVE_BLIND_WEATHER_CONFIRMATION.md`. |
| M27C Part 2C1 TWC Settlement Mapping Evidence | Implemented; no-go research result; research-only | Separate immutable evidence types distinguish Kalshi authority, TWC vintaged/current values, Kalshi settlement-implied predicates, and GHCN comparisons. Strict loaders reject wrong authority, unofficial TWC data, malformed hashes, unknown fields, August/prospective dates, fabricated point values, and nonzero production influence. Public official documentation establishes API-key historical access but not Kalshi's TWC product/station/day/rounding/revision semantics; no authoritative TWC values or complete historical settlement-implied observations were available. `NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE`; `UNVALIDATED_GHCND_PROXY` remains unchanged. See `reviews/M27C_TWC_SETTLEMENT_MAPPING.md`. |
| M27D Supervised Experimental Weather Canary | Implemented; shadow-only; independent review required | Typed August-only (`2026-08-18` through `2026-08-31`) TAKER_NOW candidate boundary reuses M27A evidence and preserves M13/M15/M16 separation. Predeclared 20pp `research_probability_discrepancy`, frozen model allowlist, current evidence freshness, boundary-mass rejection, exact one-contract cap, deterministic selection, stronger acknowledgement hash, and durable one-submission counter are implemented. No write credential, arm, order, settlement validation, fair-value/edge/EV/alpha claim, or real canary completion. See `reviews/M27D_SUPERVISED_EXPERIMENTAL_WEATHER_CANARY.md`. |
| M27F Live Authenticated Read Acceptance | Implemented; offline/synthetic-transport verified; live acceptance pending | Live production discovery found the candidate itself (`{"read","write::trade"}`, subaccount 0) receives `HTTP 401` from `GET /trade-api/v2/api_keys` -- it cannot prove its own scopes to itself. Split candidate-authority proof into a new operator-only `services/supervised_canary/authority_attestation.py`: a separate, broader management credential performs exactly one GET-only `GET /api_keys` call and produces a secret-free `kalsh3.m27f.candidate-authority.v1` attestation (never receives or touches the candidate's private key). `live_read_acceptance.py` no longer calls, or has a transport capable of calling, `/api_keys`; it independently re-validates a supplied attestation (schema, classification, key-ID hash, exact scopes/subaccount, unique match, source) before any account read, and reuses the unmodified M25/M21/M22 `KalshiAccountClient` GET-only transport for the required subaccount-0 portfolio reads. A further live discovery found the same candidate receives `HTTP 403` from `GET /account/limits` (account-tier metadata with no `subaccount` parameter, out of scope for this candidate); M27F now never calls it, reconciliation derives `subaccount_binding_verified` from the attestation plus the fixed `?subaccount=0` request paths instead of a hardcoded `AccountSnapshot`, and evidence schema is `kalsh3.m27f.live-read-acceptance.v3`. No time-based expiry is applied to the attestation (scoped instead to the exact candidate key-ID hash); documented rationale in the review. `readiness_report.py` gate-unlocking logic is unchanged (there was never a separate limits gate). `enrollment_available()` remains `False`, `ProtectedWriteCredentialStore.install()` still requires `fixture_only`, and `services/production_execution` is untouched (`git diff` empty). No real credential, live call, or mutation in this milestone. See `reviews/M27F_LIVE_AUTHENTICATED_READ_ACCEPTANCE.md`. |
| M27G Protected Write-Credential Enrollment + Real Signer Validation | Code/test/review only; no live enrollment run | Removed the obsolete candidate-self-verification (`verify_live_write_credential_authority` / `require_live_write_authority` / `WriteCredentialServerProof`) from `services/production_execution/enrollment.py` -- live discovery proved the candidate gets `HTTP 401` calling `GET /api_keys` itself. Authority proof now comes from independently re-validating two operator-supplied, secret-free artifacts against the exact candidate key ID: the M27F `kalsh3.m27f.candidate-authority.v1` attestation and a **fresh** `kalsh3.m27f.live-read-acceptance.v3` evidence artifact (freshness re-derived at installation time, not creation time -- historical evidence never authorizes install). The shared structural attestation validator moved to a new neutral module, `services/kalshi_account_gateway/candidate_authority.py` (zero dependency on either `production_execution` or `supervised_canary`), so `production_execution` does not depend back on `supervised_canary` even though `supervised_canary` already depends on `production_execution`. `ProtectedWriteCredentialStore.install_real_credential` (fixture-only `install()` unchanged) requires a distinct `OperatorReleaseAuthorization` bound to an exact candidate fingerprint and owns its entire transaction -- including the real-signer self-test -- under one cross-process `fcntl.flock` exclusive lock (see the M27G delta-repair follow-up below). New non-network `services/production_execution/signer_self_test.py` reuses `security_boundary._rsa_pss_sha256` unmodified to sign a fixed domain-separated non-request challenge and verify it locally via `openssl pkey -pubout` / `dgst -verify`; it can never produce a valid mutating-request signature, never touches transport or the journal. New operator-only `enrollment_cli.py`: private key via inherited FD only, secret-free receipt, prints `PRODUCTION_ARMED: DISARMED` / `REAL_MUTATION: NOT TESTED` / `ORDER_SENT: NO`. `SignAndSendBoundary.production_execute`/`offline_fixture_execute` are byte-for-byte unchanged; no arm CLI added. `enrollment_available()` remains `False` by design (an operator-release capability, not general availability); `readiness_report.py` is unchanged in this milestone (no real receipt exists yet to wire in). 73 focused adversarial tests (13 dedicated to cross-process locking); full M27F/M15/M16 suites and full suite pass; `services/forecasting` untouched. See `reviews/M27G_PROTECTED_WRITE_ENROLLMENT.md`. |

## Runtime truth

- Research data/semantics/signals: M2–M5 code offline verified; no forecast, probability, alpha or opportunity
- Production read-only account and universe/economics evidence: verified; general WebSocket and external
  feeds remain separately unverified where noted
- Production write: disabled and absent
- Production armed: no
- Autonomous trading: off

## M27E live supervised canary readiness follow-up (2026-08-17)

- `IMPLEMENTATION_REVIEW_STATUS`: **SAFE FOR INDEPENDENT REVIEW**.
- `REAL_MONEY_CANARY_READINESS`: **BLOCKED**.
- Fresh public API compatibility: **PASS**. Current official documentation artifacts
  returned 13/13 HTTP 200; artifact SHA256 is recorded in the M27E review.
- Fresh public exchange status: **PASS**, HTTP 200.
- Fresh public CLIMDW discovery: **PASS**, HTTP 200, complete cursor pagination, zero open
  markets. M27D shadow: **ABSTAIN_NO_OPEN_MARKET**.
- PostgreSQL runtime and concurrency: **PASS** against ephemeral localhost PostgreSQL 18.6,
  including process/thread races, rollback, reconnect, restart, and ambiguous recovery.
- Synthetic signer runtime: **PASS**. Live credential/signer validation remains blocked.
- Production read acceptance, account reconciliation, deployment, human acceptance, and
  real-money authorization remain **BLOCKED_BY_CREDENTIAL** or **NOT TESTED** as shown by
  `python -m services.supervised_canary.readiness_report`.
- Production write credential: **NO / NOT INSTALLED**. Production armed: **NO / DISARMED**.
- Real order/mutation: **NO**. Frozen weather files: unchanged. See
  `docs/reviews/M27E_LIVE_CANARY_READINESS.md`.

## M27F live authenticated read acceptance follow-up (2026-08-18)

- Branch was found checked out from a stale pre-M27E commit and reset to `origin/main`
  (`1d6612b`) before implementation began; the stale commit's tree was already identical to
  content on `main`, so nothing was lost.
- Reused, unmodified: the M27E candidate-authority boundary
  (`verify_live_write_credential_authority` / `require_live_write_authority`) and the
  M25/M21/M22 `KalshiAccountClient` GET-only account transport. `KalshiAccountClient` gained
  public `get_balance`/`get_limits`/`get_collection` methods so `refresh()`'s
  read-only-specific scope check can be bypassed for M27F's `{"read","write::trade"}`
  candidate without weakening it for the existing read-only credential path; `refresh()`'s
  own behavior and all 50 pre-existing tests are unchanged.
- New `python -m services.supervised_canary.live_read_acceptance` CLI: private key via
  inherited fd only (never argv/env), never installs a credential, GET-only, secret-free JSON
  evidence artifact (hashes/counts/classifications, key ID hash, never the PEM or raw ID).
- Reconciliation only reports `PASS` when candidate authority passed, all six reads
  (balance/limits/positions/orders/fills/settlements) completed with full pagination, the
  resulting `AccountSnapshot` validated, and total acquisition time is `<= 30s`; any partial
  or stale evidence reports `BLOCKED`/`FAIL`, never a false `PASS`.
- `readiness_report.py` can now unlock `CANDIDATE_KEY_AUTHENTICATED_GET` and the per-endpoint
  `AUTHENTICATED_*`/`ACCOUNT_RECONCILIATION` gates from a fresh, valid M27F artifact, while
  `PRODUCTION_WRITE_CREDENTIAL` (**NOT INSTALLED**), `PRODUCTION_ARMED` (**FAIL/DISARMED**),
  `REAL_MUTATION` (**NOT TESTED**), and `REAL_SIGNER_VALIDATION`
  (**BLOCKED_BY_CREDENTIAL**) are never touched by this evidence.
- `enrollment_available()` remains `False`; `ProtectedWriteCredentialStore.install()` still
  requires `credential.fixture_only`; there is still no merged
  `services/production_execution/enrollment_cli.py`.
- 36 focused adversarial tests (candidate-authority matrix, per-endpoint failure
  classification, pagination, freshness, secret handling, readiness-gate unlocking) plus the
  full 1304-test suite pass. No real credential, live authenticated call, or mutation was
  attempted; frozen weather files are unchanged. See
  `docs/reviews/M27F_LIVE_AUTHENTICATED_READ_ACCEPTANCE.md`.

## M27F candidate-authority attestation split (2026-08-18)

- Live GET-only diagnostics against real production established: a broad temporary bootstrap
  credential's `GET /trade-api/v2/api_keys` returned `HTTP 200` and exactly one matching
  candidate record (`scopes=read,write::trade`, `subaccount=0`); the candidate credential's
  own `GET /trade-api/v2/portfolio/balance?subaccount=0` PASSED; but the candidate
  credential's own `GET /trade-api/v2/api_keys` returned `HTTP 401`. The least-privilege
  candidate cannot prove its own authority by calling `/api_keys` itself -- the M27E/M27F
  design that asked it to do so was structurally incompatible with the least-privilege
  candidate it was meant to validate. No credential installed, production stayed DISARMED, no
  mutation/order occurred while this was discovered.
- New `services/supervised_canary/authority_attestation.py`: a separate management
  credential performs exactly one GET-only `GET /api_keys` call and produces a secret-free
  `kalsh3.m27f.candidate-authority.v1` attestation. The candidate's private key is never an
  input to this module -- only its (non-secret) key ID, so the broad management credential
  and the narrow candidate credential structurally cannot end up in the same process.
- `live_read_acceptance.py` no longer calls, or has a transport capable of calling,
  `GET /api_keys` (removed `authority_transport` parameter entirely). It now takes an
  `authority_attestation` payload and independently re-validates every field (schema,
  classification, key-ID hash, exact scopes/subaccount, unique match, source) before any
  candidate account read -- the artifact's own classification is never merely trusted. Evidence
  schema bumped to `kalsh3.m27f.live-read-acceptance.v2`; `candidate_authority.source` is now
  always `EXTERNAL_SERVER_ATTESTATION` in the evidence artifact.
- No time-based expiry is applied to the attestation. Its validity is scoped to the exact
  candidate key-ID hash it names (Kalshi's documented API-key surface has no scope/subaccount
  update operation, so a key's authority cannot change in place; a deleted/replaced key gets
  a new ID whose hash will not match). The candidate must still authenticate successfully on
  every M27F run regardless of the attestation. Rationale documented in the review rather than
  an arbitrary TTL being invented.
- `KalshiAccountClient`, `readiness_report.py`, and every file under `services/production_execution`
  and `services/forecasting` are unmodified by this revision (`git diff` empty for both
  directories).
- 89 focused tests (30 new attestation-generator/validator tests + 59 revised M27F consumer
  tests, including a structural regression proving the consumer has no `/api_keys`-capable
  transport and a regression reproducing the exact live discovery) plus the full 1357-test
  suite pass; `ruff check`, `ruff format --check`, and `mypy` (204 source files) all pass. No
  real credential, live authenticated call, or mutation was attempted. See
  `docs/reviews/M27F_LIVE_AUTHENTICATED_READ_ACCEPTANCE.md`.

## M27F live discovery repair: remove account-level limits from narrow-candidate acceptance (2026-08-18)

- Live evidence with a valid attestation for the real narrow candidate (authority `PASS`,
  scopes `{"read","write::trade"}`, subaccount `0`): `balance`/`positions`/`orders`/`fills`/
  `settlements` all `SUCCESS` with complete pagination, but `limits` returned
  `SCHEMA_OR_HTTP_FAILURE` (`unexpected upstream status 403`), so reconciliation was
  `BLOCKED`. Root cause: Kalshi's portfolio endpoints (`balance`/`positions`/`orders`/`fills`/
  `settlements`) accept an explicit `subaccount` parameter; `GET /account/limits` is
  documented account-tier metadata for the authenticated user with no `subaccount` parameter
  at all, and the least-privilege candidate is not entitled to it. M27F had incorrectly made
  `/account/limits` mandatory, and `AccountSnapshot.from_payloads` required a `limits` payload
  before it would even hardcode `subaccount = 0` -- so `subaccount_consistent` was never
  actually derived from the candidate's real request paths.
- `services/supervised_canary/live_read_acceptance.py`: M27F no longer calls, or has any code
  path capable of calling, `GET /account/limits`. The required candidate read set is now
  exactly `balance`, `positions`, `orders`, `fills`, `settlements` -- five, not six. Balance
  schema is validated directly by a new M27F-local `_validate_balance_schema` (object;
  `balance`/`portfolio_value`/`updated_ts` integers, not bools; optional `balance_breakdown`
  is an array of objects if present) without storing any account value in evidence, so M27F no
  longer builds or depends on `AccountSnapshot` at all -- it has its own reconciliation model.
  `ReconciliationResult.limits_succeeded` is removed (no misleading `False` for an endpoint
  that is no longer part of the acceptance contract); `subaccount_consistent` is renamed to
  `subaccount_binding_verified` and is derived from (a) the independently re-validated
  attestation's `server_subaccount == 0` and (b) every required portfolio read having
  succeeded against `KalshiAccountClient`'s structurally fixed `?subaccount=0` request paths
  -- never from a payload field that does not exist. Evidence schema bumped explicitly to
  `kalsh3.m27f.live-read-acceptance.v3` / software version `kalsh3.m27f.live-read-acceptance/3`.
- `readiness_report.py` required no changes: it already had no separate account-limits
  readiness gate, and its gate-unlocking logic only reads `reconciliation.classification` and
  the per-endpoint `reads`, neither of which changed shape in an incompatible way.
- `services/kalshi_account_gateway/client.py` and `models.py` (`KalshiAccountClient.get_limits`,
  `KalshiAccountClient.refresh()`, `AccountSnapshot`) are completely unmodified -- that older
  exact-read-only flow may legitimately use account-level limits, and remains a distinct
  security boundary from the M27F narrow `write::trade` candidate. `services/production_execution`
  and `services/forecasting` are untouched (`git diff` empty for both).
- 40 test functions (86 parametrized cases, up from 59) in
  `tests/test_m27f_live_read_acceptance.py`: happy path with exactly five reads and no
  `limits_succeeded` field; a fake transport whose `limits` branch always returns `HTTP 403`
  (matching live evidence) proving that branch is structurally unreachable; explicit assertions
  that the candidate request sequence never contains `/account/limits` or `/api_keys` and that
  every portfolio path carries `subaccount=0`; balance schema adversarial matrix (missing
  field, wrong types, bool-for-integer rejection, malformed `balance_breakdown`) with a
  secret-free-evidence assertion; per-endpoint 401/pagination-failure matrices for all five
  reads; a regression reproducing the exact 2026-08-18 discovery end-to-end
  (`reconciliation=PASS`, `/account/limits` call count `= 0`); freshness boundary
  (`<=30s` passes, `>30s` fails) and consumption-time staleness tests unchanged; and the full
  pre-existing CLI/readiness-report suite adapted to the new schema. `tests/test_account_gateway.py`
  (legacy `KalshiAccountClient`/`AccountSnapshot` tests) required zero changes and pass
  unmodified. Full suite: `1384 passed, 3 skipped` (`KALSH3_TEST_POSTGRES_DSN` not set --
  pre-existing, unrelated). `ruff check .`, `ruff format --check .`, and `uv run mypy` (204
  source files) all pass; `git diff --check` clean; `git diff -- services/production_execution`
  and `git diff -- services/forecasting` both empty.
- No real credential, live authenticated call, credential installation, arming, or mutation
  was attempted while producing this revision. See
  `docs/reviews/M27F_LIVE_AUTHENTICATED_READ_ACCEPTANCE.md`.

## M27G protected write-credential enrollment (2026-08-18)

- Removed the obsolete candidate-self-verification path from
  `services/production_execution/enrollment.py`
  (`verify_live_write_credential_authority` / `require_live_write_authority` /
  `WriteCredentialServerProof`, which authenticated as the candidate to call `GET /api_keys` --
  the exact call M27F's live discovery proved returns `HTTP 401`). Authority proof now comes
  from independently re-validating two operator-supplied, secret-free artifacts against the
  exact candidate key ID: the M27F `kalsh3.m27f.candidate-authority.v1` attestation and a
  **fresh** `kalsh3.m27f.live-read-acceptance.v3` evidence artifact, re-checked for freshness
  at installation time (not creation time).
- New neutral module `services/kalshi_account_gateway/candidate_authority.py` holds the shared
  structural attestation validator, `KNOWN_KALSHI_API_KEY_SCOPES`, and `USER_DATA_FRESHNESS`;
  zero dependency on either `production_execution` or `supervised_canary`, so
  `production_execution` does not depend back on `supervised_canary` even though
  `supervised_canary` already depends on `production_execution`.
- New non-network `services/production_execution/signer_self_test.py`: reuses
  `security_boundary._rsa_pss_sha256` unmodified to sign a fixed domain-separated non-request
  challenge and verify it locally via `openssl pkey -pubout` / `dgst -verify`; structurally can
  never produce a valid mutating-request signature; never touches transport or the journal.
- New operator-only `enrollment_cli.py`: private key via inherited FD only; secret-free
  receipt; prints `PRODUCTION_ARMED: DISARMED` / `REAL_MUTATION: NOT TESTED` / `ORDER_SENT: NO`.
- `SignAndSendBoundary.production_execute`/`offline_fixture_execute` untouched; no arm CLI.
  `enrollment_available()` remains `False` by design. `readiness_report.py` unchanged (no real
  receipt exists yet to wire in).

### Gemini delta repair: cross-process install/rollback safety (2026-08-18)

- Gemini review found the original `_seal_and_write` / `rollback_failed_install` design had a
  cross-process TOCTOU race: two installers could both observe `is_installed() == False`
  before either published, `_atomic_bytes`'s `os.replace` could silently clobber a destination,
  and a failed installer's blind `unlink(missing_ok=True)` could delete a
  concurrently-succeeding installer's valid artifacts. Verdict: **ROLLBACK SAFETY not
  accepted, IMPLEMENTATION_REVIEW_STATUS not safe**; all other review categories accepted.
- Fix: every write-capable `ProtectedWriteCredentialStore` operation now runs inside one
  `fcntl.flock(LOCK_EX)` exclusive lock (`store.exclusive()`) on a dedicated lock file in the
  0700 store directory, held for the *entire* transaction -- state inspection, sealed writes,
  the real-signer self-test, and commit-or-rollback -- and released only in `finally` (also
  released automatically by the kernel on process crash/exit). `install()` (fixture) and
  `install_real_credential()` (real, now taking a `self_test` callable and owning the whole
  transaction) are the only two write-capable entry points and share this same lock -- no
  second, unlocked writer path exists.
- `_atomic_bytes`/`_atomic_text` (temp-file + `os.replace`, which can silently overwrite) were
  replaced by `_create_only_bytes`/`_create_only_text`: a direct `O_CREAT | O_EXCL` open on the
  destination itself, so the kernel atomically refuses to create a file that already exists --
  true no-overwrite, not check-then-write. Safe without its own locking because it is only ever
  called from inside the store's exclusive lock.
- A new commit marker (`installed.marker`, fixed content, published strictly last) makes
  `is_installed()` require a complete, *committed* set, not merely "some files exist." A
  process that crashes between writing the sealed artifacts and writing the marker leaves
  state that reports as NOT installed (fail closed); only a later transaction, under the same
  exclusive lock, may clear it and retry, and it can never touch a committed installation
  (gated by the same `is_installed()` check).
- The public `rollback_failed_install()` is removed entirely. Rollback (`_discard_install`) is
  now private, requires a `_StoreLock` token that only `exclusive()` can mint, and is only
  reachable from inside `install_real_credential`'s own lock span right after that same
  transaction's own `_begin_install` -- so it can only ever remove that transaction's own
  just-written, never-committed artifacts.
- 13 new dedicated concurrency tests use real threads contending on the same `fcntl.flock`
  (per-open-file-description, so genuine kernel contention even within one test process, no
  subprocesses or Postgres needed): two concurrent real installers (exactly one succeeds, the
  self-test never overlaps -- proven via an active-transaction counter); a forced-to-fail
  installer A with a concurrent installer B synchronized via `threading.Event`s (B proven to
  not even enter its transaction until A rolls back and releases the lock); a successful
  installer A followed by a rejected B with A's artifacts proven byte-for-byte unchanged;
  fixture-vs-real races; rollback-never-touches-pre-existing-installation; a forced partial
  write failure recovered by the next transaction; four parametrized partial/corrupt-state
  scenarios (`is_installed()` false for all, each recoverable under the lock); lock release
  after an exception; and lock file permission/no-secret-content checks. 73 total M27G tests
  (up from 60); full M27F/M15/M16 suites and the full test suite pass unchanged;
  `git diff -- services/production_execution/security_boundary.py`,
  `git diff -- services/supervised_canary/live_read_acceptance.py`, and
  `git diff -- services/forecasting` all remain empty. No real credential, network call, or
  mutation was attempted while producing this repair. See
  `docs/reviews/M27G_PROTECTED_WRITE_ENROLLMENT.md`.

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

## M27A acceptance

- Bounded production read-only live acceptance ran at repository head
  `d8ca7db580be18fef63bb8e0d36e4be785b583fc` with requested hypothetical quantity `0.01`.
- Acceptance file: `~/.kalsh3/evidence/m27a-live-acceptance-20260816-033036.json`; JSON SHA256:
  `ca83bc6b44f46b234d6e8dd418d98adca2bc953d84325d177ff6f8c66e0ca199`; log SHA256:
  `08a985fe810c3b32b3202a9527fa4da2225e70103960216cd2abfb2ee51d6a67`.
- The acceptance used one bounded authenticated exact-read batch orderbook GET and current
  Market/Event/Series point reads. It performed no production writes; trading remained locked/off.
- Live representatives were `KXUFCFIGHT-26AUG15MAKMGI-MGI` (`linear_cent`, minimum step `0.0100`,
  fractional depth), `KXGOVFLNOMR-26-JFIS` (`tapered_deci_cent`, minimum step `0.0010`, exact subpenny
  and fractional depth), and `KXPGATOUR-FESJC26-SSCH` (`tapered_deci_cent`, minimum step `0.0010`, exact
  subpenny and fractional depth). YES and NO TAKER_NOW were available and self-contained replay passed
  exactly for all three.
- All accepted representatives used the `current_series` fee-resolution path with multiplier `1` and fee
  policy `kalshi-event-fees-2026-07-07-v1`. The UFC and Elections representatives used `quadratic`; the
  PGA representative used `quadratic_with_maker_fees`.
- The Event override path has real M26H.3 archived shapes and focused/adversarial test coverage, but was
  not separately exercised by this bounded production acceptance; not every fee-resolution branch is
  live accepted.
- Explicit `price_ranges` are tick authority; descriptive structure labels never infer ticks.
- Broad Market top quotes are discovery signals, not executable-depth truth. Exact batch orderbooks bridge
  into M10's canonical complement-normalized `NormalizedBook` with fractional Decimal quantities.
- Complete Event overrides take precedence over exact current Series fee metadata. Fee-change records are
  preserved as scheduled/past metadata and do not silently rewrite the current regime.
- Pre-trade fee evidence separates formula, centicent rounding, fill/balance uncertainty, and rebate or
  accumulator uncertainty. It does not claim an exact final exchange fee without fills.
- TAKER_NOW is the first live-compatible execution-cost path. Maker metadata is represented, but maker
  opportunity economics remain unsupported pending live fill/queue/adverse-selection validation.
- Pre-fill final exchange fee remains `UNKNOWN`, by design. There is no forecasting, fair value, edge,
  profitability, ranking, capital allocation, `TradeCandidate`, `DecisionReceipt`, `RiskIntent`, order
  activity, execution, autonomy, or trading-readiness acceptance. M27A production influence remains
  exactly `Decimal("0")`.

## M27B acceptance

- Original H3 acceptance found 20,287 directionally eligible routes, 40 duplicate-threshold abstentions,
  and 15,603 `INVALID_FLOOR_STRIKE` abstentions among 35,930 supported directional Markets. The latter
  were finite Python floats after JSON decode. M27B.1 narrowly parses finite exchange strike floats via
  `Decimal(str(value))`; `exact_numeric()` remains globally unchanged. M27B independent review is
  complete; M27B.1 review and the required local archive rerun are pending. The previously observed 6
  Structural Leads are provisional incomplete-coverage discovery results, not a final lead set.
- M27B is offline, deterministic, and research-only. Every supplied canonical Market yields a route or
  explicit abstention; unsupported shapes are measurable rather than silently dropped.
- Event ticker alone is not a safe cohort. Complete canonical non-empty `custom_strike` content binds
  subject identity, preventing player, team, index, and contract cross-comparisons. Malformed,
  mixed-presence, and duplicate-threshold groups fail closed.
- Version 1 structural semantics are limited to finite Decimal `floor_strike` values for `greater` and
  `greater_or_equal`. Discovery also requires ACTIVE binary non-provisional non-MVE Markets. All other
  strike types and base-gate failures route explicitly without producing leads.
- Broad quotes are discovery-only. Exact confirmation consumes existing M27A replayable evidence for
  broad YES plus narrow NO at exactly equal quantities, plus compatible strategy-supported canonical
  ContractSpecifications. Each specification's canonical Market rules and metadata hashes must exactly
  match its corresponding M27A evidence hashes. Exact books must share precisely the same caller-assigned
  `orderbook_observed_at`, normally from one bounded batch snapshot; this does not establish exchange-wire
  atomicity. Final exchange fees remain unknown pre-fill, so final/guaranteed net profit remains
  unavailable.
- The accepted M26H.3 probe counts document an observed archive, not permanent live-universe truth.
- No forecasting, fair value, positive EV, profitability, arbitrage, capital allocation,
  `TradeCandidate`, `DecisionReceipt`, `RiskIntent`, order, scheduler, autonomy, network, execution, or
  trading-readiness claim is introduced. Production influence is exactly `Decimal("0")`.

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

## M27P create-only canary state bootstrap (2026-08-22)

- Added operator-only `services/supervised_canary/m27o_state_bootstrap.py` to create the
  single durable SQLite database shared by M16 `CanaryStore` and M13
  `AuthorizationStore`, preserving M27O's atomic one-shot release requirement.
- Bootstrap is local-only and non-networked. It accepts no Kalshi credential or private key,
  imports no live Kalshi transport, cannot approve a canary, cannot issue M13 or M27O
  authorization, cannot arm production, cannot burn the one-real-submission budget, and
  cannot sign or send an order.
- Creation requires the exact operator confirmation
  `INITIALIZE DISARMED ONE-CONTRACT CANARY STATE WITH COMPLIANCE CLEAR`, plus explicit
  actor and reason.
- A successful fresh store must verify: production `DISARMED`; submission count `0`; fill
  count `0`; global halt off; compliance `CLEAR`; all four kill states `NORMAL`; no durable
  loss holds; and zero previews, approvals, canary sessions, risk authorizations, and risk
  reservations.
- The production-canary directory is `0700` and the database is `0600`. Existing database,
  WAL, SHM, or rollback-journal artifacts fail closed rather than being overwritten or
  repaired. Bootstrap WAL data is checkpointed before create-only publication.
- Concurrent bootstrap attempts have exactly one winner through atomic create-only
  publication.
- Verification: Ruff PASS; 10/10 M27P bootstrap tests PASS; 41/41 existing M27O regression
  tests PASS; guarded M27E/F/H/I/J/N/O + M13/M15/M16 regression PASS with `648 passed in
  28.28s`, outbound sockets blocked, isolated HOME/XDG state, and real credential paths
  hidden.
- Real production canary state DB: **NOT CREATED**. Production write credential and candidate
  credential: **UNTOUCHED**. Production remains **DISARMED**. M16 approval: **NONE**. M13
  authorization: **NONE**. M27O execution authorization: **NONE**. Burn: **NONE**. Mutating
  Kalshi call: **NONE**. Order sent: **NO**.
- See `docs/reviews/M27P_CREATE_ONLY_CANARY_STATE_BOOTSTRAP.md`.
