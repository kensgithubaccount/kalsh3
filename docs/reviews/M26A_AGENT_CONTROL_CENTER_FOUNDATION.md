# M26A — Agent Control Center Foundation

## Result

M26A adds an owner-facing strategy-agent foundation without adding an execution path, network
collector, credential, scheduler, allocator, or production-data read. Production execution remains
**DISARMED**, trading remains **OFF**, and every M26A agent and Decision Receipt enforces
`production_influence == Decimal("0")`.

## Architecture discovered and reused

The dashboard is a private server-rendered WSGI application. `product.py` owns the product-surface
inventory and centralized navigation; `app.py` owns authentication, routing, and escaped HTML
composition; `dashboard.py` owns the Overview view composition; `store.py` exposes truthful
read-only summaries; `readiness.py` derives fail-closed readiness; and `security.py` owns session and
secret primitives. M26A reuses those boundaries and does not add agent logic to persistence or HTML.

Existing capabilities mapped to the roster are forecasting/opportunity research, breaking-signal
validation, Perps shadow evidence, cross-venue research contracts, contract intelligence,
read-only portfolio/risk state, and learning governance. These mappings describe available code,
not live operation or profitability.

## Agent Registry

`services/agent_control_center/domain.py` defines frozen, slotted agent definitions in one ordered
tuple. Definitions include stable ID, version, mandate, universe, sources, watches, belief, action
condition, risks, availability, autonomy mode, production influence, guardrails, and performance
availability. Import-time validation rejects duplicate IDs. Construction rejects nonzero influence
and any non-disabled mode for a planned or unavailable agent.

The M26A autonomy enum contains only `DISABLED`, `SHADOW`, `PAPER`, and `CANDIDATE`; it has no live
production value. Event Edge, Breaking Signals, Cross-Market, Resolution, and Learning map to
implemented research/governance capability. Perps is `UNAVAILABLE / DISABLED` because M24/M25
provide evidence infrastructure but no Perps strategy. Portfolio is `UNAVAILABLE / DISABLED`
because complete reconciliation-aware strategy integration does not exist.

## Decision Receipt and explanations

The frozen, slotted `DecisionReceipt` captures agent and instrument identity, timezone-aware time,
market/model/fair values, raw and after-cost edge, fees, slippage, confidence, evidence references,
exposure, named Decimal limits, risk results, research decision, rejection reasons, and production
influence. It has no execution import or authorization method.

Canonical JSON uses sorted keys, compact separators, UTC timestamps, stable tuple-to-array ordering,
and Decimal strings so precision and trailing zeros are preserved. Allowed research conclusions are
`WOULD_TRADE`, `NO_TRADE`, `INSUFFICIENT_EVIDENCE`, and `BLOCKED_BY_RISK`. `explain_decision` uses
only audited receipt fields and deterministic branch rules. All negative decisions end with an
explicit reason and “would not trade”; a positive research conclusion still says no order is
authorized. No LLM is involved.

## Dashboard and navigation

The primary hierarchy is now Overview, Agents, Opportunities, Positions, Learning, and System.
Legacy routes remain inventoried and reachable as child/specialist pages. Positions points to the
existing read-only `/portfolio` page and adds no controls.

`/agents` renders name, availability and mode, mandate, watches, belief, zero authority,
performance state, primary risk, decision empty state, and detail link. `/agents/{agent_id}` renders
the owner questions directly: watches, belief, action condition, failure modes, mode, guardrails,
recent decisions, and performance. Unknown IDs return 404. All registry text is HTML escaped.

Overview now includes available-agent count, agents requiring attention, production influence 0,
recent-receipt empty state, and the explicit statement that Trading OFF is expected and safe. It
continues to show opportunities and account positions only from existing truthful summaries.
Opportunity-to-agent association is established structurally through `DecisionReceipt.agent_id`;
existing opportunity rows are not retrospectively attributed without evidence.

## Safety boundary

- No file under `services/production_execution` changed.
- No M25 transport, signer, credential, or production-read boundary changed or accessed.
- No production network call was made.
- No write credential, order control, allocation, sizing, or live autonomy state was added.
- Missing receipts and performance show “No decisions yet” and “Not enough evidence.”
- Learning remains reviewed research governance and cannot rewrite risk limits, credentials,
  execution controls, production Python, or security boundaries.

## Verification

- Focused M26A and dashboard regression suite: 54 passed.
- Full pytest suite: 686 passed, with two pre-existing macOS `os.memfd_create` failures in untouched
  `tests/test_production_execution_m15_complete.py`.
- Repository-wide Ruff: passed.
- Strict mypy over `services`: M26A passed; the run reports the pre-existing macOS typing issue at
  `services/production_execution/security_boundary.py:198` (`os.memfd_create` attribute). That
  protected out-of-scope file was not changed.
- Strict mypy over the agent-control-center and web-dashboard packages: passed (12 source files).
- Bandit on changed service surfaces: no medium/high findings; one pre-existing low-confidence empty
  TOTP fallback warning in `app.py`.
- Detect-secrets on M26A and related dashboard/review surfaces: no findings.
- `git diff --check`: passed.

## Still unavailable

There is no per-agent decision persistence/feed, calibrated per-agent performance ledger, Perps
strategy, reconciled Portfolio strategy, capital competition/allocation, autonomous self-change,
production collector, or production execution. M26A makes no profitability claim.
