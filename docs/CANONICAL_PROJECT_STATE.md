# Canonical Project State

Last updated: 2026-09-16

This file is the durable handoff record for the Kalshi project. New oversight or implementation sessions should read this file first, then verify GitHub/current runtime state before acting.

Repository and current GitHub state override stale chat summaries. Experiment ledgers override recollection. Chats are working context, not the canonical record.

## 1. Verified repository checkpoint

Repository: `kensgithubaccount/kalsh3`

Canonical main:

- SHA: `02ca01b7c904b2020bc28e6334ad3a0866dc2b20`
- tree: `52d43202cb46d03d8e4412cd7c3ac6a09f3af963`
- commit: merge of PR #160, WN-A2 settlement outcome reconciliation
- main CI run `35116576034`: completed successfully
- open pull requests immediately before this documentation branch was opened: 0

Recent merged engineering milestones relevant to current research:

- PR #157: GDP authority integration repair accepted after independent review and merged. Historical GDP repair findings remain closed unless a concrete regression against an existing requirement is reproduced.
- PR #159: WN-A1 Chicago daily-high research alert merged.
- PR #160: WN-A2 authoritative settlement outcome reconciliation merged after independent delta review PASS. WN-A2 adds durable settlement/outcome reconciliation and fresh-process replay without production influence.
- M9-E1 learning-governance semantic repair was independently reviewed PASS before the current main lineage. Promotion remains fail-closed: no caller-created `STRONGER_EVIDENCE` authority is accepted until a reviewed inferential method exists.

No current repository state establishes a profitable autonomous strategy or authorizes real-money trading.

## 2. North Star

Build an autonomous edge-compounding research and trading platform whose objective is long-run, after-cost, risk-controlled income.

The intended progression remains:

Kalshi directional alpha -> whole-exchange strategy discovery -> cross-contract relative value -> legally accessible cross-venue opportunities -> liquidity provision / market making where economically justified -> multi-strategy portfolio allocation -> broader quantitative markets only where the existing architecture provides a demonstrable advantage.

The immediate objective is narrower:

> Find the first repeatable, credible, after-cost Kalshi edge as quickly as possible without lowering scientific, provenance, or safety standards.

Passing software tests is not profitability evidence.

## 3. Evidence hierarchy

Keep these categories separate:

1. Reported claim.
2. Directly verified repository state.
3. Reproduced software behavior.
4. Independent review.
5. Runtime acceptance.
6. Prospective scientific/economic evidence.
7. Production authorization.

A stronger category must not be inferred from a weaker one.

Historical/manual profitable trades are useful motivation, not proof of a repeatable strategy.

## 4. Reported historical economic outcomes

These are owner-reported outcomes from project conversations. They are not RADAR-P0 prospective observations and must not be used as if they were held-out proof.

- Chicago weather manual opportunity: reported correct and profitable.
- GDP manual opportunity/update: owner reported the prediction was correct and profitable after a subsequent GDP estimate increase.

Repeated mentions of the GDP result are not counted as separate independent wins unless a distinct trade/episode is documented. These outcomes support continued investigation of forecasting/nowcasting and cross-market opportunity discovery. They do not establish calibration, repeatability, capacity, or autonomous profitability.

## 5. Current research strategy

The project is intentionally in an evidence-discovery phase rather than a broad new engineering phase.

Permanent workflow rule:

> mechanism audit -> tiny live probe -> narrow prospective collection -> frozen edge test -> capacity test -> only then production automation.

Do not return to:

> idea -> large collector -> hardening -> review -> discover no edge.

### RADAR-P0 — ACTIVE

Purpose: test whether a fixed Chat-assisted manual process can find useful after-cost Kalshi opportunities across the exchange.

Frozen pilot:

- duration: 7 days
- cadence: approximately 09:00 / 13:00 / 17:00 ET
- fully manual trigger; no cron/cloud automation during the pilot
- maximum 3 distinct `WORTH CHECKING` candidates per scan
- valid outputs: `WORTH CHECKING`, `SKIP`, `TOO UNCERTAIN`
- zero alerts is a valid result
- all scheduled scans, skips, unavailable sources, failures, and no-alert periods remain in the ledger
- primary human-action delay for economics: 5 minutes
- immediate and 15-minute prices may be retained as diagnostics
- independent event clusters, not strikes/rows, are the scientific unit

Durable pilot ledger:

`~/kalshi-radar-p0/`

Expected contents include:

- `PROTOCOL.md`
- `STATUS.md`
- `summary.md`
- `scans/`
- `audit_log.md`
- `ideas_for_next_version.md`

Do not modify the frozen protocol mid-pilot because results look good or bad. Put improvements in `ideas_for_next_version.md`.

Current RADAR-P0 status as of 2026-09-16 after Scan #2:

- Scan #1 initially emitted one NYC-rain `WORTH CHECKING`.
- A contemporaneous audit, before settlement/outcome hindsight, found the call violated an existing rule because the exact settlement-window convention had not been positively verified. Original scan remains unchanged; the audit reclassifies it to `TOO UNCERTAIN` for scoring.
- Scan #2 produced no valid `WORTH CHECKING` alerts.
- WTI crude was skipped for unusably wide spreads.
- NYC low temperature, RCP approval average, and OpenRouter market-share ideas were held as `TOO UNCERTAIN` or dropped when exact source/settlement evidence could not be established.

The current lesson is not that these markets lack edge. It is that apparent discrepancies must survive exact rule/source verification before promotion to an alert.

### YouTube feasibility — ACTIVE

Purpose: test a structural publication-latency mechanism for `KXYTVIEWSD`.

Current reported prerequisite findings:

- settlement source is YouTube Charts
- Global artist daily views are used
- daily contract rules require observation from a New York IP
- a finalized Taylor Swift chart value was reported to exactly match Kalshi's expiration value
- YouTube publication appears delayed relative to the observation day
- markets can close early when values become determinable

A three-publication-cycle watch is running separately. Its job is only to establish whether the settlement-equivalent value becomes visible while the exact Kalshi market remains ACTIVE with positive-margin executable liquidity after fees and delayed checks.

Do not call this profitable until the predeclared promote criteria are satisfied prospectively.

### USGS earthquake probe — KEEP WATCHING

Mechanism under test:

reviewed qualifying USGS earthquake -> threshold condition becomes constrained -> check whether still-open Kalshi liquidity is stale after realistic delay.

Latest reported probe:

- result: `INSUFFICIENT`
- largest reviewed current-day event: magnitude 5.1
- lowest open threshold observed: 5.2
- no trigger occurred; therefore no book/economic test was warranted

Count qualifying threshold-crossing episodes, not calendar days. No crossing is not a failed latency test.

## 6. Parked / killed lanes

### GAS daily official-source latency — KILLED / PARKED

Canonical research branch remains unmerged.

The live close-clock repair established that a Sep 16 target contract closing at `2026-09-16T03:59:00Z` is Sep 15 23:59 America/New_York.

Therefore the market closes before the target local date. The tested official-source latency mechanism cannot exist.

Status:

`GAS DAILY LATENCY THESIS KILLED — FORECASTING LANE NOT YET AUTHORIZED`

Do not reopen as the same strategy. Any next-day gas forecasting idea requires a new mechanism audit.

### Rotten Tomatoes — PARKED / BLOCKED

RT-A1 research code exists on a separate branch, but the live probe did not establish the mechanism.

Current blockers:

- conservative public HTML parsing exposed no reliable score/count/review records for the tested pages
- exact settlement-source identity was not available through the event payload used by the collector
- a permitted, reliable prospective data path for review membership/score evidence has not been established

Do not add architecture until the data/access prerequisite is solved.

### Weather expansion — PAUSED

WN-A1 and WN-A2 engineering are merged and provide a research alert/outcome path.

Further strategy expansion remains constrained by external evidence/access issues, including WeatherNext dataset permission and unresolved exact TWC measurement-window authority where applicable.

Do not start WN-A3 merely because WN-A2 is complete.

### CPI — PAUSED for first-edge objective

CPI remains useful research infrastructure, but its feedback rate is slow and no current reason justifies prioritizing it over faster prospective mechanisms.

### GDP automated activation — PARKED unless reopening evidence is satisfied

GDP authority/integration repairs are accepted. Do not repeatedly reopen closed engineering findings.

A profitable manual GDP call does not by itself clear the remaining scientific/economic gates for automated activation.

## 7. Scientific rules that remain mandatory

- Never cherry-pick only successful alerts.
- Preserve every attempted strategy, abstention, unavailable source, no-fill state, and no-alert scan relevant to the frozen policy.
- Exact contract identity, settlement semantics, source, and timing must be verified before a candidate can become `WORTH CHECKING`.
- Use actual executable prices and depth, not midpoint or cumulative historical volume, for economic claims.
- Include applicable fees and avoid double-counting spread/depth costs.
- Record realistic human/decision latency; a quote that disappears before plausible action is not a manual edge.
- Count independent economic events, not sibling strikes, repeated forecasts, or repeated source observations.
- Cluster correlated outcomes: macro thresholds from one release, multiple strikes on one event, artists in one publication batch, weather cities in one system/date, earthquake aftershock sequences, reviews from one film/release cohort.
- Freeze rules before evaluation. Mid-pilot ideas go into a next-version log.
- No retrospective relabeling of historical data as prospective.
- No production influence, live order placement, signing, or capital deployment without the existing activation gates and explicit authority.

## 8. Profitability ladder

Do not collapse these stages:

1. `MECHANISM EXISTS`
2. `INFORMATION / PREDICTIVE ADVANTAGE EXISTS`
3. `AFTER-COST EDGE EXISTS`
4. `REPEATABILITY EXISTS`
5. `CAPACITY EXISTS`
6. `PORTFOLIO VALUE EXISTS`
7. `PRODUCTION READY`

Software completion can support these gates but cannot substitute for them.

## 9. Current priority order

1. Finish the frozen RADAR-P0 7-day prospective pilot.
2. Finish the three-cycle YouTube feasibility watch.
3. Keep the USGS probe lightweight until qualifying crossings occur.
4. Score outcomes and capacity honestly after evidence exists.
5. Automate only the procedure that survives the manual/frozen evidence test.

Do not start another broad scanner during RADAR-P0. Do not pre-research likely next-scan candidates outside the frozen scan process.

## 10. Immediate next actions

As of this checkpoint:

- run the next scheduled RADAR-P0 scan at the next normal pilot time; do not add off-schedule scans for excitement
- keep the YouTube live watch running through its final planned publication cycle
- keep USGS conditional rather than building a collector before a qualifying trigger
- maintain WN-A2 as merged/closed engineering; no further weather expansion now
- keep RT, gas, CPI, and GDP activation parked unless their documented reopening conditions are met

At the end of RADAR-P0, produce at minimum:

- scheduled scans completed / missed
- markets screened
- distinct underlying events fully analyzed
- `WORTH CHECKING` / `SKIP` / `TOO UNCERTAIN` counts
- retrieval/rule/book failures
- hypothetical after-cost result at analysis time
- hypothetical result after 5-minute human delay
- simple-baseline result
- Chat-assisted result
- displayed/executable capacity
- cluster concentration
- performance with the best event removed

Then choose one disposition for each mechanism:

`ACCELERATE`, `KEEP COLLECTING`, `PAUSE`, or `KILL`.

## 11. Decision log

### 2026-09-15 — Manual radar before software radar

Independent strategy red-team recommended a frozen manual whole-exchange probe before building RADAR-A0.

Reason:

- candidate discovery is unproven
- probability estimation is unproven
- survival of economics through human delay is unproven
- automating all three at once would make failures hard to diagnose

Decision: run RADAR-P0 first. Do not build RADAR-A0 until the manual policy produces useful prospective evidence.

### 2026-09-16 — Scan #1 audit discipline

A NYC-rain alert was downgraded by contemporaneous audit because exact settlement-window authority was inferred rather than verified.

Decision: preserve the original record, preserve the audit, and enforce the existing rule that an unresolved settlement-window/source question is `TOO UNCERTAIN`.

This was not a protocol change.

### 2026-09-16 — WN-A2 complete

Independent delta review returned `PASS — NO BLOCKERS`. PR #160 merged. Current main CI passed.

Decision: weather outcome infrastructure is sufficient for the present research stage; pause new weather feature work.

### 2026-09-16 — Gas latency thesis closed

Local-time close semantics established that the tested daily AAA market closes before its target date.

Decision: kill the official-source latency thesis. Any forecasting version requires a new proposal rather than silently inheriting the old lane.

## 12. Update protocol

Update this file whenever any of the following happens:

- canonical main changes materially
- a milestone/PR is merged
- a required independent review changes acceptance status
- a strategy is promoted, paused, killed, or reopened
- a live experiment starts or finishes
- a prospective profitability result is established
- an owner-reported economic result is added
- the active priority order changes
- a production/scientific authority gate changes

Every update should distinguish:

- verified GitHub/repository facts
- owner-reported runtime/economic facts
- independent-review results
- scientific/economic conclusions

A fresh session should not reopen accepted historical findings merely because an old chat said they were pending. Reopen only for a concrete regression or new evidence against an existing requirement.
