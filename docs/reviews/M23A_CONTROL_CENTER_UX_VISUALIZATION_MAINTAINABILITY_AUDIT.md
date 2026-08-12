# M23A — Control Center UX, Visualization & Maintainability Audit

This work is deliberately separate from live account reconciliation, bankroll changes,
production-write enablement, or trading logic. It touches only presentation code in
`services/web_dashboard` (plus this documentation and a new test file). No file under
`services/kalshi_account_gateway`, `services/risk_engine`, `services/production_execution`,
`services/supervised_canary`, or `services/bounded_autonomy` was changed.

## Audit findings

**Architecture / maintainability**

- The entire product (`app.py`, ~850 lines) rendered every page as one large inline f-string
  per route, mixing domain data access, HTML structure, and formatting in a single expression.
  There were no view models and no reusable presentation components; a change to how money or
  status was displayed had to be repeated at every call site.
- `dollars()` (in `product.py`) was the only centralized formatter; status text, badges, and
  chart-shaped data all had bespoke inline formatting.
- Overview hardcoded `"Protected reserve", "$700.00"` and `"Active allocation", "$0.00"` as
  literal strings, while the Risk & Safety page correctly read the same figures from
  `RiskPolicy()`. The two pages could silently diverge if the policy ever changed.
- The global blocker list on Overview ("Production mutation capability is absent.", "M13
  deterministic portfolio risk is not complete.", …) was a flat, hand-written list disconnected
  from `derive_global_state`'s actual inputs — an operator had no way to see *why* one category
  (say, risk) was fine while another (execution) was not.
- There were no data visualizations anywhere in the product: every "trend," "composition," or
  "distribution" concept was rendered as plain text or omitted entirely.

**Product / UX**

- Global state collapsed to a single word (`HALTED`, `NEEDS ATTENTION`, `LEARNING`) with no
  breakdown of which readiness dimension caused it, and HALTED used the same amber treatment as
  NEEDS ATTENTION already did — not alarmist, but also not explained.
- Capital figures mixed real reconciled values (equity, cash) and policy figures (protected
  reserve, active allocation) in one undifferentiated metric grid, with no visual signal when a
  policy target (e.g. the $1,000 bankroll) exceeds what the account actually holds.
- Primary navigation listed twelve flat links with no grouping, which the task specifically
  flagged as too dense.

**Accessibility / responsive**

- Base landmarks, skip link, focus rings, and touch targets were already solid (see
  `test_responsive_css_has_touch_focus_mobile_and_overflow_guards`); this audit preserved them
  and extended the same conventions to new components.

## What changed

- **`services/web_dashboard/readiness.py`** (new): pure, real-signal-driven readiness
  derivation — five categories (Connection, Research, Risk, Execution, Autonomy readiness),
  each with typed `ReadinessCheck(label, met, detail)` entries. `Execution readiness` and
  `Autonomy readiness` are structurally unmet in this build (no production-write credential
  exists; autonomy is off) — the same fact the product already stated elsewhere, now organized
  instead of duplicated. Research readiness includes a "Required real evidence sufficient" check
  driven by the existing governed `promotion_minimum` / `real_settled_events` threshold already
  shown on `/learning` — informational only, it does not change promotion, strategy, risk,
  execution, or autonomy behavior, and it cannot render as met while real settled evidence is
  insufficient. `primary_action()` returns the single highest-priority unmet check;
  `readiness_summary_text()` is the one place the "N of M readiness checks unmet" sentence is
  written.
- **`services/web_dashboard/charts.py`** (new): accessible, server-rendered SVG chart
  primitives — `composition_bar`, `limit_bars`, `sparkline` — plus `chart_empty_state`. Every
  chart renders an openable `<details>` table of its exact values (helps everyone who can't read
  the SVG well, not just screen-reader users), always shows values as text alongside color, and
  is built only from values the caller already validated as real. No chart fabricates a number.
  `composition_bar` remains available as a tested primitive but is not currently called from
  Overview — see "Deferred: capital composition" below.
- **`services/web_dashboard/product.py`**: added `NAV_GROUPS` / `grouped_navigation()` (visual
  navigation grouping that provably covers every existing surface exactly once — see
  `assert_navigation_covers_all_surfaces()`) and `status_pill()` (one centralized, escaped
  status-label renderer).
- **`services/web_dashboard/store.py`**: added an `account_snapshot_history` table and
  `account_value_history()` reader. `refresh_succeeded()` now also records the real cash/
  portfolio-value point from that refresh (best-effort; a snapshot missing usable values simply
  isn't plotted) and prunes to the most recent 200 points. `account_value_history(limit=N)`
  returns the **newest** `N` observations in oldest-to-newest display order (a pre-merge review
  caught and fixed an initial version that returned the *oldest* `N` instead whenever more than
  `N` points existed). This is the "safe, read-only persistence mechanism" the task allowed when
  a visualization needs data that isn't yet persisted — it does not touch the signer, execution,
  or risk paths, and it stores only values already read from the account.
- **`services/web_dashboard/app.py`**:
  - `_layout()` now renders grouped navigation and a derived one-line state explanation instead
    of the two static "Production writes OFF" / "Research influence NONE" spans.
  - The Overview eyebrow ("REAL ACCOUNT CONNECTED · READ ONLY") is no longer a hardcoded
    literal. `_connection_headline(account_status, stale)` derives it from real state: a
    connected-and-fresh account says "REAL ACCOUNT CONNECTED · READ ONLY"; connected-but-stale
    says "REAL ACCOUNT CONNECTED · DATA STALE"; `error` status says "ACCOUNT CONNECTION NEEDS
    ATTENTION"; anything else (never configured, mid-refresh, etc.) says "READ-ONLY ACCOUNT
    STATUS UNKNOWN". The word "CONNECTED" can now only appear when the account actually is.
  - Overview (`_overview`) was rebuilt into the requested hierarchy: **A.** readiness hero with
    the full checklist and primary action; **B.** capital and risk, split into "Actual account"
    (available cash, Kalshi's reported portfolio value, open positions, exposure) and
    "Policy / target" (bankroll, protected reserve, maximum active allocation), plus the policy
    limits chart and the portfolio-value sparkline; **C.** current activity; **D.** research and
    opportunities, including up to three real persisted candidates; **E.** learning; **F.** needs
    attention, now containing only items not already covered by the readiness checklist
    (previously the same blockers were listed twice).
  - The hardcoded `$700.00` reserve is gone; Overview now reads `RiskPolicy()` like Risk &
    Safety does. When the policy bankroll exceeds the currently reported portfolio value, the
    target-bankroll card is labeled "Not currently fundable" via `status_pill`; when the
    portfolio value is unknown (no reconciled snapshot yet), it says "Funding status unknown"
    instead of guessing either way.

## Visualizations added (real data only)

- **Policy limits** (Overview): protected reserve, active allocation, aggregate open-risk limit,
  related-event limit, and per-market limit, all real `RiskPolicy` values on one shared scale.
  These are the configured ceilings, not usage against them — usage is not charted because it
  is not yet real/reconciled (M13 read-side reconciliation remains pending).
- **Kalshi portfolio value over time** (Overview): a real sparkline built from the new
  `account_snapshot_history` table, labeled as Kalshi's raw `portfolio_value` field rather than
  "equity" or "account value" (see "Deferred: capital composition" below for why). Before two
  real points exist, it renders an honest "insufficient history to chart" state that explains it
  accumulates automatically — never a fabricated trend line.
- **Readiness checklist** (Overview hero): not a chart in the traditional sense, but a direct
  answer to "is the system healthy / can it trade / why not," rendered as a categorized,
  non-color-only checklist, now including real-evidence sufficiency alongside connection, gap,
  compliance, and halt state.

## Deferred: capital composition

Overview no longer renders a cash-vs-positions composition chart, and no longer calls Kalshi's
`portfolio_value` field "equity." A pre-merge review found that current official Kalshi
materials describe `portfolio_value` inconsistently: the `GET /portfolio/balance` API reference
describes it as the current value of open positions, while Kalshi's changelog describes it as
total portfolio value including available balance plus positions. Because those two readings
imply different arithmetic, this presentation layer must not infer `in_positions = portfolio_value
- cash` (with or without clamping the result to zero) — doing so would silently assume one
disputed semantics over the other about a real user's money.

Overview now shows `balance`/`cash` as **"Available cash"** and `portfolio_value` as
**"Reported portfolio value"** — two real, unmodified numbers, presented side by side with no
inferred relationship between them — plus an explicit "Capital composition is deferred" note
explaining why. The composition chart returns once portfolio_value's live semantics are
positively validated against current Kalshi documentation, or once a validated position-level
valuation field exists to compute a real split from. The `composition_bar` chart primitive
itself is unaffected and stays in `charts.py`, still covered by its own unit tests, ready for
that day.

## Visualization ideas deferred (real data does not yet exist or is not reliably typed)

- **Exposure by event/market and YES vs. NO exposure** (Portfolio): `AccountSnapshot.positions`
  rows are the raw, unvalidated Kalshi API objects — this codebase does not assume specific
  field names or types for them beyond `dict`. Charting a YES/NO or per-market exposure
  breakdown would require trusting field names (`market_exposure`, side, etc.) that are not
  validated anywhere in the read path today. Building that chart before the position schema is
  validated would risk silently mis-plotting real account data, which the task explicitly
  prohibits fabricating or guessing. Deferred until position rows have a validated shape.
- **Order lifecycle timeline / fills over time / execution price vs. reference price**
  (Orders & Trades): no timestamped, persisted order-state history exists yet (orders/fills are
  refreshed as a flat current snapshot, not an event log). Deferred until such a history is
  persisted, following the same pattern used for `account_snapshot_history`.
- **Forecast probability vs. market-implied probability, edge vs. execution cost**
  (Opportunities): the underlying `opportunity_candidate_ui` fixture rows are already
  string-labeled ("fair_probability", "executable_price", etc.) rather than typed Decimals, and
  the page already states "INSUFFICIENT REAL FORECAST EVIDENCE." Charting them now would visually
  imply a confidence the product explicitly disclaims elsewhere. Deferred until real evidence
  exists.
- **Calibration / Brier / log-loss trends, source marginal-value** (Learning/Sources): these
  require real settled outcomes; the product already states 0 real settled events. Charting
  zero-sample statistics would misrepresent statistical confidence the task explicitly forbids
  implying. Deferred until real settled evidence accumulates.
- **Source health/freshness/latency panel** (Sources): real per-source fields already exist in
  `external_source_ui` and are good candidates for a small multiples panel; deferred purely for
  scope in this pass, not for a data-availability reason. The `charts.py` primitives added here
  (particularly `limit_bars`'s pattern) are reusable for this as a fast follow.

## Testing

`tests/test_m23a_control_center_redesign.py` adds regression coverage for: readiness derivation
across healthy/unhealthy real signals, primary-action priority ordering, navigation-group
completeness, chart escaping and empty states, `account_snapshot_history` recording/pruning and
its behavior when a snapshot is missing usable money fields, the Overview actual-vs-policy split
and "Not currently fundable" / "Funding status unknown" labeling, the insufficient-history →
sparkline transition once two real snapshots exist, and that pre-existing account
positions/fills are never labeled as bot-generated. It additionally covers, from the pre-merge
correctness pass: `_connection_headline` for every (status, staleness) combination plus Overview
integration tests proving the hero cannot say "CONNECTED" when the account errored, was never
configured, or is stale; that "Required real evidence sufficient" cannot render as met with zero
real settled events and does render as met once the existing governed threshold is reached; that
Overview never renders the word "Equity" or an inferred "In positions" figure and instead shows
the deferred-composition explanation; and that `account_value_history(limit=2)` returns the two
*newest* observations (not the two oldest) when more history exists than the limit.

All pre-existing dashboard tests (`test_dashboard_product_complete.py`,
`test_dashboard_product_partial.py`, `test_m1_security_ui.py`) pass, with one intentional,
documented correction: `test_ui_security_headers_csrf_stale_state_and_downloads` previously
asserted "REAL ACCOUNT CONNECTED" appears even for an account that has never successfully
reconciled — that assertion tested the mislabeling bug this pass fixes, so it now asserts the
truthful "READ-ONLY ACCOUNT STATUS UNKNOWN" instead. Every other pre-existing assertion,
including the exact-substring checks on `/risk`, `/system`, `/portfolio`, and `/orders`, is
unchanged.

## Safety confirmation

Production signer: **DISARMED**. Production-write credential: **NONE**. Bounded autonomy:
**OFF**. The production account gateway remains read-only; this pass requested or used no
credentials. No order creation/cancel/amend behavior, M13 risk logic, bankroll/reserve policy,
credential handling, API signing, account targeting, subaccount behavior, autonomy eligibility,
strategy/model behavior, or source/model promotion rule was changed. No real-money order
capability was added.
