# M23B — Trading Dashboard Simplification

This is a presentation/information-architecture milestone. It touches only
`services/web_dashboard` (plus this documentation and tests). No file under
`services/kalshi_account_gateway`, `services/risk_engine`,
`services/production_execution`, `services/supervised_canary`, or
`services/bounded_autonomy` was changed. Production signer: **DISARMED**.
Production-write credential: **NONE**. Bounded autonomy: **OFF**. The
production account gateway remains read-only.

## Problem statement

M23A (previous milestone) made the Control Center substantially more
truthful, but its Overview page still read like an enterprise compliance
console: a nine-check readiness matrix, multiple policy cards, a "capital
composition deferred" text block, a policy-limit chart, and a flat twelve-link
top navigation were all visible above the fold at once. Very little of that
belongs on the first screen a person sees. The system itself needed to stay
exactly as rigorous — the task was to simplify the *experience of
understanding* the system, not the system.

## Visual references and principles (not copied literally)

The task pointed at several public prediction-market trading-bot dashboard
posts as mood references. This session does not have general web/image
browsing tools wired up, so rather than fetch external pages speculatively,
the redesign worked directly from the principles the task itself enumerated
in detail: dark background, one dominant financial number, one prominent
performance chart, compact secondary stats, dense useful tables, small
explicit status indicators, low chrome, minimal prose, few cards, high
information density, easy scanning. No claims, numbers, or copy from any
external post were reproduced; nothing here is presented as a performance
claim of any kind.

## Old vs new information architecture

**Old primary navigation** (flat, all visible at once): Overview,
Opportunities, Breaking Now, Markets, Sources, Learning, Portfolio, Orders &
Trades, Reports, Risk & Safety, System, Advanced — twelve links in the header.

**New primary navigation** — five top-level sections
(`services/web_dashboard/product.py: NAV_SECTIONS`):

| Section | Path | Contains |
|---|---|---|
| Dashboard | `/` | Account value, performance, opportunities, positions, activity, status |
| Markets | `/markets` | Market universe, opportunities, breaking signals |
| Activity | `/activity` (new hub) | Portfolio, orders & trades, reports |
| Strategy | `/strategy` (new hub) | Learning, sources, forecasting, backtests, advanced diagnostics |
| System | `/system` | Health, readiness, risk & safety |

Every old route still resolves (`section_for_path()` maps every `SURFACES`
entry to exactly one section; `assert_navigation_covers_all_surfaces()` fails
closed if a page is ever left uncovered). Each page now renders the five-item
primary nav plus a small secondary "section nav" row listing only the pages
in its own section — so a person on Activity sees Portfolio / Orders & Trades
/ Reports as one-click tabs, without the other three sections' pages
cluttering the header. No JavaScript dropdown was introduced; the section nav
is plain server-rendered links.

`/advanced`'s actual content (forecasting, backtests, and learning summaries)
placed it under **Strategy** rather than **System**, matching what the page
actually shows rather than its historical name.

## Old vs new Dashboard structure

**Old** (`_overview`, ~250 lines in `app.py`): hero readiness section with the
full 9-check matrix and a primary-action panel, a two-column "capital and
risk" section (actual-vs-policy metric grids, a deferred-composition
placeholder, a policy-limits chart, an account-value sparkline), a current
activity grid, a research/opportunities grid, a learning summary, and a
needs-attention section — six major sections, most of them multi-part.

**New** (`services/web_dashboard/dashboard.py: build_dashboard`): one
dominant number, four compact supporting metrics, one chart, two tables, one
short fact list, and a five-item status strip:

1. **Hero** — `REPORTED PORTFOLIO VALUE` (still not called "Equity" — see
   below) as one large tabular-nums figure, with Available cash / Bot P&L /
   Open risk / Account positions underneath, and a plain link to compare
   against the target bankroll on Risk & Safety (see the correctness-pass
   section below for why this replaced an inferred "Below target bankroll"
   conclusion).
2. **Portfolio value chart** — the real `account_snapshot_history` sparkline
   (unchanged data source from M23A), with an honest "insufficient history"
   empty state before two real points exist.
3. **Opportunities** — a compact table (Market / Side / Market price / Model
   probability / Edge / Mode) built only from candidates whose persisted
   `data_mode`/`decision_state` prove them live-research-eligible (see the
   correctness-pass section below), or "No qualified opportunities yet." with
   an optional one-line reason. Never fabricated rows.
4. **Positions** — a compact table with only the fields this codebase
   actually validates (ticker) plus an explicit **Provenance** column,
   defaulting to "Unattributed" — there is no ownership field anywhere in the
   read path, so nothing is ever inferred as bot-owned or as pre-existing
   (see the correctness-pass section below).
5. **Activity** — a short, honest fact list built from real current counts
   (positions/orders/fills/settlements on file, bot-attributed fills), not a
   fabricated timeline — this codebase has no persisted event log to draw a
   real timeline from.
6. **Status strip** — Account / Market data / Risk / Evidence / Trading, each
   a linked chip to its detail page, each using explicit text
   (`✓ OK`, `✕ DISCONNECTED`, `◌ PENDING`, `0 / 50`, `OFF`) so meaning never
   depends on color alone.

The full readiness matrix and its "what needs you most" panel moved to
`/system` verbatim (same categories/checks), reachable in one click from the
status strip or the System section nav. Nothing about the underlying
`readiness.py` derivation was hidden — it is one click deeper, not gone.

## Account vs. Bot provenance rules

This codebase has never recorded bot-vs-human attribution for any position,
order, or fill — the account gateway reads raw Kalshi objects with no
provenance field. M23B's rule, enforced in `dashboard.py` and tested in
`tests/test_m23b_dashboard_simplification.py`:

- Every position shown carries `Provenance: Unattributed` by default — never
  "bot", "strategy", or "automated" language, and (since the correctness
  pass below) never "Pre-existing" either, because no field anywhere
  supports either claim. `_provenance_label()` only ever returns "Bot owned"
  or "Pre-existing" if a future explicit `provenance` field on the row says
  so; today nothing sets that field, so every item is honestly unattributed.
- `Bot P&L` always renders `—` with the note "No attributable live trades
  yet" — it is never computed from existing settlements, which predate this
  product and are not attributable to it.
- The Activity fact list explicitly states "0 bot-attributed fill(s)" as a
  real, current count (zero because no attribution mechanism exists yet), not
  as a claim that the bot has run and produced zero results.
- `Account positions` in the hero is explicitly labeled "Account" (not bare
  "Positions"), and the Positions table is reachable one click away for full
  detail — no de-duplication with a hypothetical "Bot positions" concept that
  does not yet have real data behind it.

## Logic/truthfulness issues corrected

1. **Market-data contradiction.** `readiness.py`'s Research readiness
   category previously had one check ("No unresolved market-data gaps") that
   could read MET while `universe: NOT_STARTED` and `market data:
   DISCONNECTED` were shown elsewhere — technically consistent (zero gaps
   really were recorded) but operationally misleading. It now has four
   checks: **Market universe initialized**, **Live market data connected**,
   **No unresolved market-data gaps**, **Required real evidence sufficient**
   — so a disconnected, never-started market-data system can never make
   Research readiness look healthy again.
2. **Compliance wording.** The compliance check previously said "No
   compliance hold / NOT MET / not established" — confusing, since "not
   established" sounds like a hold exists. `build_readiness` now takes the
   raw `compliance_state` string (not a pre-collapsed boolean) and
   distinguishes three real states: `CLEAR` → "Compliance state is
   established and clear"; `UNKNOWN` (the store's own default, meaning no
   hold has ever been recorded) → "Compliance state has not yet been
   established"; anything else → the real recorded reason. No fail-closed
   behavior changed — only the label matches the fact.
3. **Primary-action typography.** The old markup
   (`<small>What needs you most</small><strong>{label}</strong>`) rendered
   with no space between the two inline elements, producing visible text like
   "What needs you mostRequired real evidence sufficient". The replacement
   uses block-level elements — `<p class=eyebrow>WHAT NEEDS YOU MOST</p>`
   followed by its own `<h3>{label}</h3>` and `<p>{detail}</p>` — which
   cannot run together regardless of label length.
4. **Policy-limit chart rendering as full-width bars regardless of value.**
   The bar fill used an inline `style="width:{n}%"` attribute, and this
   product's CSP is `style-src 'self'` with no `'unsafe-inline'` — which
   silently drops every inline `style` attribute, so every bar fell back to
   its CSS default (visually full width) no matter the real value.
   `charts.py: limit_bars` now draws the fill as an SVG `<rect>` sized by its
   `width` **attribute** (an SVG presentation attribute, unaffected by
   `style-src`), not a CSS style. Per the task, the chart itself was also
   removed from the Dashboard (comparing five unrelated absolute policy
   ceilings on one shared scale wasn't earning its place on the first
   screen); the exact policy numbers, including a `Target bankroll` line item
   that only ever existed on the old Overview, now live on `/risk`. The fixed
   `limit_bars` primitive remains in `charts.py`, unit-tested, for future use
   once real usage-vs-limit data exists to chart meaningfully.
   `composition_bar`'s legend swatch had the same inline-style bug
   (`style="background:..."`) and is fixed the same way, with per-index CSS
   classes.

## M23B correctness pass (follow-up)

After the initial version of this milestone merged, a follow-up truthfulness
pass fixed four remaining gaps, all scoped to `services/web_dashboard/` and
its tests — no execution, signer, risk-policy, credential, autonomy,
strategy, or forecasting file was touched.

5. **Hardcoded "Pre-existing" provenance.** The Dashboard positions table and
   hero metric previously labeled every account position "Pre-existing"
   unconditionally. That happened to match the currently observed positions,
   but the persisted account snapshot carries no baseline/ownership field
   that actually proves a position predates the bot — a manual trade placed
   after deployment would have been mislabeled "Pre-existing" exactly the
   same way. `dashboard.py: _provenance_label()` now defaults every
   position/order/fill to **"Unattributed"** and only ever returns "Bot
   owned" or "Pre-existing" if a row carries a real, explicit `provenance`
   field ("BOT" / "PRE_EXISTING") — which nothing in this codebase sets yet.
   `Bot P&L` stays `—` / "No attributable live trades yet", unchanged,
   because that was already correct. Full M23 account
   reconciliation/provenance is explicitly **not** implemented here; this
   only removes a claim the current data cannot support.
6. **Unfiltered opportunity candidates on the Dashboard.** The Dashboard
   opportunities table previously rendered the first five persisted
   `opportunity_candidate_ui` rows regardless of their `data_mode` or
   `decision_state`, so a `SYNTHETIC TEST` or `HISTORICAL REPLAY` fixture row
   could render next to "Opportunities" indistinguishably from a real live
   signal. `dashboard.py: _eligible_dashboard_candidates()` now only surfaces
   a candidate when both its persisted `data_mode` equals the existing
   `EvidenceMode.LIVE_RESEARCH_DATA` value (from `product.py` — no new
   literal was invented) **and** its `decision_state` is one of the research
   engine's own two affirmative states, `DecisionState.RESEARCH_CANDIDATE` or
   `DecisionState.HIGH_PRIORITY_RESEARCH_CANDIDATE` (from
   `services.opportunity_engine.models` — the domain's real enum, not a
   Dashboard-invented one). Rejected/incomplete/watch-only candidates, and
   any non-live data mode, never render as an "opportunity" regardless of how
   the row looks otherwise. Eligible rows show a **Mode** column ("Research"
   / "High-priority research") and a fixed line — "Research signals only — no
   order is authorized." — so nothing implies executability. Today, with
   market data disconnected and the universe not started, no candidate is
   ever eligible, so the Dashboard still shows the honest empty state; the
   filter exists for when real live-mode research candidates start existing.
7. **`portfolio_value` → bankroll inference.** The hero previously computed
   `portfolio_value < RiskPolicy.bankroll` and rendered "Below target
   bankroll" or "Funding status unknown" — a semantic conclusion built from
   the same `portfolio_value` field the Dashboard elsewhere refuses to treat
   as validated account equity (see "never calls portfolio_value equity"
   above). That inference is removed entirely: the hero now shows only the
   raw reported value plus a plain, non-judgmental link — "compare to target
   bankroll" — to `/risk`, where the real `Target bankroll` figure already
   lives. `RiskPolicy` is no longer imported by `dashboard.py` at all.
8. **Top-bar severity conflated an unresolved state with an active
   emergency.** The top status bar's color/class came directly from
   `derive_global_state()`, which intentionally (and correctly, for backend
   fail-closed purposes) collapses any non-`CLEAR` compliance state —
   including the store's own `UNKNOWN` default, meaning nothing has been
   established yet — into `HALTED`. Rendering that in the top bar meant a
   freshly configured install, before anything has gone wrong, showed the
   same red "System HALTED" as an active compliance `HOLD` or an explicit
   global halt. `derive_global_state()` itself is **unchanged** — it still
   backs `/risk`'s canonical "Risk state" and every readiness/fail-closed
   decision exactly as before. A new, presentation-only function,
   `product.py: derive_display_status()`, drives only the top bar: an
   explicit global halt or an active compliance `HOLD` is `HALTED` (red); an
   `UNKNOWN` compliance state or a stale/disconnected account is `NEEDS
   ATTENTION` (amber); anything else is the restrained default, `LEARNING`.
   Production execution being `OFF` is not a parameter to this function at
   all — it keeps rendering separately as the neutral "Trading OFF" chip and
   never contributes to the severity color. Verified visually: a freshly
   seeded install (matching the realistic local fixture described under
   Testing below) now shows an amber "System NEEDS ATTENTION" top bar
   instead of a red "System HALTED" one, while `/risk` still correctly shows
   canonical `Risk state: HALTED` underneath.

## Accessibility

- Landmarks unchanged: header/nav/main/footer, skip link to `#main-content`.
- The five-item top nav and the per-section secondary nav are each their own
  `<nav aria-label>` landmark with exactly one `aria-current="page"` inside
  each.
- Every chart (`composition_bar`, `limit_bars`, `sparkline`) keeps its
  `role="img"`/`aria-label` summary and an openable `<details>` table of
  exact values — accessible to screen-reader users and anyone who can't parse
  the SVG, not hidden with `sr-only`.
- The status strip and readiness checklist use explicit text
  (`✓ OK` / `✕ DISCONNECTED` / `◌ PENDING`) plus color, never color alone.
- Tables use real `<caption>` (visually hidden via `.sr-only`, not
  `display:none`, so it stays in the accessibility tree), `scope=col`/`scope=row`
  headers, and a `.table-scroll` wrapper so overflow is contained instead of
  breaking the page.
- Focus ring (`:focus-visible`) uses a dedicated accent color distinct from
  the warning/danger semantic colors, so focus is never confused with a
  status indicator.

## Responsive behavior

Verified at 1440 / 1024(≈1080 tablet emulation used 820) / 820 / 390px via
rendered screenshots (below). The header stacks to a column under 900px; the
top nav and section nav scroll horizontally rather than wrapping into a wall;
the hero metric row and status strip wrap to multiple lines with `flex-wrap`;
tables live inside `.table-scroll` (`overflow-x:auto`) so a wide table never
forces the page itself to scroll horizontally; the hero value uses a `clamp()`
so it shrinks gracefully instead of overflowing on narrow viewports; all
interactive targets (nav links, status-strip chips, buttons) keep a 44px
minimum height.

## Security / CSP

No change to `SECURITY_HEADERS` — CSP remains exactly
`default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`,
still with no `'unsafe-inline'`/`'unsafe-eval'`. The one real CSP bug found
(inline `style="width:...` silently dropped by `style-src`) was fixed by
switching to SVG attributes rather than by loosening the policy. No inline
`<script>`, no external font/CDN, no analytics were introduced — the dark
theme is pure CSS shipped from the same `/static/app.css` route as before.

## Code architecture changes

- **`services/web_dashboard/dashboard.py`** (new): the Dashboard's view
  construction — `MetricItem`/`StatusStripItem` dataclasses, small render
  helpers (`_metric_row`, `_status_strip`, `_table`), and
  `build_dashboard()`/`build_status_strip()` — extracted out of `app.py` so
  the page most people look at has its own small, testable module instead of
  living inside the 1000+-line request handler. `app.py`'s job for `/` is now
  just "call `build_dashboard`", matching the task's explicit ask for a
  smaller, more maintainable Dashboard-specific structure without a broader
  framework migration.
- **`services/web_dashboard/product.py`**: `decimal_or_none()` moved here
  from a private `app.py` copy so `app.py`, `dashboard.py`, and any future
  module share one implementation instead of two. `NAV_SECTIONS` /
  `section_for_path()` / `assert_navigation_covers_all_surfaces()` replace
  M23A's `NAV_GROUPS`/`grouped_navigation()` with the new five-section model.
- **`services/web_dashboard/readiness.py`**: `build_readiness()` gained
  `universe_status`/`realtime_state` inputs and switched `compliance_hold`
  (bool) to `compliance_state` (raw string) — see "Logic/truthfulness issues
  corrected" above. Still pure data, no HTML.
- **`services/web_dashboard/charts.py`**: `limit_bars` and
  `composition_bar`'s legend swatch no longer use inline `style=`; palette
  colors retuned for the dark background.
- **`services/web_dashboard/app.py`**: `_layout()` renders the new nav;
  `_overview`/`_readiness_checklist`/`_primary_action_panel`/`_metric_card`
  were removed (moved to `dashboard.py`, and the readiness-matrix renderer
  relocated onto `_system`); two small new hub renderers
  (`_activity_hub`, `_strategy_hub`) follow the same card-grid-with-links
  pattern the pre-existing `_advanced` hub already used, so the product
  stays internally consistent rather than introducing a new pattern.
- Deep pages not touched structurally (`/portfolio`, `/orders`, `/risk`,
  `/learning`, `/sources`, `/forecasting`, `/backtests`, `/markets`,
  `/breaking`, `/opportunities`, `/canary`, `/autonomy`, `/reports`) inherit
  the new dark visual shell automatically because the CSS is global — no
  duplicate redesign effort, matching "don't overdesign every obscure page."

## Visualizations

**Kept/reused from M23A:** the account-value sparkline (now the Dashboard's
single chart, labeled "Reported portfolio value" throughout, never "equity").
**Removed from the Dashboard:** the capital-composition empty-state block and
the policy-limits chart (moved to plain numbers on `/risk`; see above for
why). **New:** none — this pass deliberately added zero new chart types,
favoring two well-designed tables (Opportunities, Positions) and a compact
status strip over more charts, per "use charts only when they improve
understanding; tables remain better for exact transactional records."

Chart date-range controls (1D/1W/1M/ALL) mentioned as a *possible* mockup
element were **not implemented**: `account_snapshot_history` has no
date-bucketing and, in the seed data used for QA, only a handful of real
points exist — a range selector over that would either do nothing or require
fabricating bucket boundaries. Deferred until enough real history
accumulates to make ranges meaningful.

## Data intentionally not visualized (deferred)

Per the task, explicitly deferred rather than fabricated: bot P&L history and
performance curve, trade attribution, per-market exposure, YES vs. NO
exposure, current risk utilization (vs. limits), markout, execution alpha,
model calibration curves, real source-contribution charts, and anything
perpetuals-related. `dashboard.py`'s docstring states the two rules
(provenance honesty; no fabrication) directly so future contributors extend
it the same way.

## Perps future-proofing

No perps code, credentials, routes, or trading capability were added. The
Strategy section's `NAV_SECTIONS` member list is a plain tuple of paths — a
future "Perps Research" or "Cross-Market Signals" destination can be added to
it (and to `SURFACES`) without touching the Dashboard, the other four
sections, or the coverage invariant's logic.

## Safety invariants

Production signer: **DISARMED**. Production-write credential: **NONE**.
Bounded autonomy: **OFF**. Production account gateway: **read-only**. No
order creation/cancel/amend behavior, M13 risk logic, bankroll/reserve
policy, credential handling, API signing, account targeting, subaccount
behavior, autonomy eligibility, strategy/model/forecasting logic, or
source/model promotion rule was changed. No real-money order capability was
added. No secrets were requested, logged, or exposed.

## Testing

`tests/test_m23b_dashboard_simplification.py` (34 tests) covers: exactly five
top-level nav sections with no deep-page labels leaking into the header,
every legacy/deep page (`SURFACES` + `ADVANCED_SURFACES`) still returning
200, the new hubs linking to their sub-pages, the full readiness matrix still
present and complete on `/system`, the primary-action typography fix, the
disconnected-market-data/NOT_STARTED-universe scenario rendering as not-ready
(and the positive ACTIVE/HEALTHY case rendering as ready), Bot P&L staying
unavailable, honest empty states for opportunities, exact (not fabricated)
sparkline point counts, no inferred cash/position split, positions never
labeled bot-generated, policy configuration still reachable on `/risk`, the
CSP header byte-for-byte unchanged, no inline `<script>` anywhere, and the
dark-theme/contrast/overflow-containment CSS hooks.

The **correctness-pass** tests (added in the follow-up covered above) add:
a position/order/fill with no `provenance` field renders "Unattributed", not
"Pre-existing" or "Bot owned" (and a position that does carry an explicit
`provenance` field is honored); a `SYNTHETIC TEST`-mode candidate and a
`HISTORICAL REPLAY`-mode candidate never render on the Dashboard even when
seeded directly into `opportunity_candidate_ui`; a `REJECTED`/`WATCH`
candidate never renders even with a live `data_mode`; an eligible
`LIVE RESEARCH DATA` + `RESEARCH_CANDIDATE` row does render, labeled
"Research", with "no order is authorized" present and "trade authorized" /
"executable" absent; a low `portfolio_value` never produces "Below target
bankroll"/"Not currently fundable"/"Funded %"; `derive_display_status()` is
unit-tested directly for all four owner-facing outcomes (`UNKNOWN` →
`NEEDS_ATTENTION`, `HOLD` → `HALTED`, explicit global halt → `HALTED`,
stale/disconnected → `NEEDS_ATTENTION`, fully clear → `LEARNING`); a freshly
configured store (default `UNKNOWN` compliance) does **not** render the top
bar as `halted`; an explicit `ComplianceState.HOLD` and an explicit global
halt (set via the real `AuthorizationStore`) **do** render it as `halted`;
and `/risk` still shows the canonical, unweakened `Risk state: HALTED` in
that same freshly configured scenario, proving `derive_global_state()` was
not touched.

`tests/test_m23a_control_center_redesign.py` was updated in the small number
of places that pinned now-intentionally-redesigned behavior (nav shape,
policy cards on Dashboard, the full readiness matrix on Dashboard, the
decision banner, and — in the correctness pass — the bankroll-inference
pills), each with an inline comment explaining why and pointing at the new
test file. Every other pre-existing test — escaping, security headers, CSRF,
session handling, account-gateway truthfulness, the M22/M23A provenance and
non-mutation guarantees — passes unchanged.

## Deferred work

- Full M23 account reconciliation/provenance (a real `provenance` field
  populated from actual ownership evidence, not just read by the Dashboard if
  present) — the correctness pass only stopped claiming "Pre-existing"
  without proof; it does not build the reconciliation system that would let
  the Dashboard honestly say "Pre-existing" or "Bot owned" for a specific
  position.
- Per-position validated financial fields (quantity, cost, current value,
  potential payout) — the account gateway's position rows are raw,
  unvalidated Kalshi objects; adding typed fields to the Positions table
  requires validating that schema first, which is out of scope here.
- An event-level activity feed with real timestamps — requires a persisted
  order/fill event log this codebase does not have yet; the Activity section
  intentionally shows only real current counts, not a fabricated timeline.
- Chart date-range controls — see "Visualizations" above.
- Everything listed in "Data intentionally not visualized."
