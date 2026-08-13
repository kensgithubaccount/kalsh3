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
   Open risk / Account positions underneath. A single conditional note
   ("Below target bankroll" or "Funding status unknown") replaces the old
   policy-card grid.
2. **Portfolio value chart** — the real `account_snapshot_history` sparkline
   (unchanged data source from M23A), with an honest "insufficient history"
   empty state before two real points exist.
3. **Opportunities** — a compact table (Market / Side / Market price / Model
   probability / Edge / Status) from the same typed fields `/opportunities`
   already renders, or "No qualified opportunities yet." with an optional
   one-line reason. Never fabricated rows.
4. **Positions** — a compact table with only the fields this codebase
   actually validates (ticker) plus an explicit **Provenance** column, always
   "Pre-existing" — there is no bot-attribution field anywhere in the read
   path, so nothing is ever inferred as bot-owned.
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

- Every position shown carries `Provenance: Pre-existing` — never "bot",
  "strategy", or "automated" language, because no field anywhere supports
  that claim.
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

`tests/test_m23b_dashboard_simplification.py` (19 tests) covers: exactly five
top-level nav sections with no deep-page labels leaking into the header,
every legacy/deep page (`SURFACES` + `ADVANCED_SURFACES`) still returning
200, the new hubs linking to their sub-pages, the full readiness matrix still
present and complete on `/system`, the primary-action typography fix, the
disconnected-market-data/NOT_STARTED-universe scenario rendering as not-ready
(and the positive ACTIVE/HEALTHY case rendering as ready), Bot P&L staying
unavailable, honest empty states for opportunities, exact (not fabricated)
sparkline point counts, no inferred cash/position split, pre-existing
positions never labeled bot-generated, policy configuration still reachable
on `/risk`, the CSP header byte-for-byte unchanged, no inline `<script>`
anywhere, and the dark-theme/contrast/overflow-containment CSS hooks.
`tests/test_m23a_control_center_redesign.py` was updated in the small number
of places that pinned now-intentionally-redesigned behavior (nav shape,
policy cards on Dashboard, the full readiness matrix on Dashboard, the
decision banner), each with an inline comment explaining why and pointing at
the new test file. Every other pre-existing test — escaping, security
headers, CSRF, session handling, account-gateway truthfulness, the M22/M23A
provenance and non-mutation guarantees — passes unchanged.

## Deferred work

- Per-position validated financial fields (quantity, cost, current value,
  potential payout) — the account gateway's position rows are raw,
  unvalidated Kalshi objects; adding typed fields to the Positions table
  requires validating that schema first, which is out of scope here.
- An event-level activity feed with real timestamps — requires a persisted
  order/fill event log this codebase does not have yet; the Activity section
  intentionally shows only real current counts, not a fabricated timeline.
- Chart date-range controls — see "Visualizations" above.
- Everything listed in "Data intentionally not visualized."
