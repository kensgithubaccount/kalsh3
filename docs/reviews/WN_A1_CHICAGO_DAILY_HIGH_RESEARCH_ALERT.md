# WN-A1 Chicago Daily-High Research Alert

## Decision

WN-A1 adds the smallest research-only, human-alert-only lane connecting **WeatherNext 3**
forecasts to Kalshi's **current, live** Chicago daily-high temperature markets
(`KXHIGHCHI`). It produces a plain-English `TAKE A LOOK` / `SKIP` / `TOO UNCERTAIN` /
`DATA NOT READY` alert for one target local date. It places no order, previews no order,
authorizes no execution, and carries zero production influence anywhere in the code path.

This is the smallest end-to-end shape, not a finished trading signal: profitability is
never claimed, and several evidentiary gaps (below) mean today's live evaluation cannot
reach `TAKE A LOOK` against real data yet.

## 2026-09-14 repair addendum: five predictable blockers repaired together

The initial implementation below (unchanged as a historical record) disclosed five
predictable blockers rather than working around them. All five are now repaired; this
addendum is the current source of truth where it conflicts with the text below.

1. **Requester Pays GCS access.** `gcs_zarr_reader` now requires an explicit, non-secret
   billing project (`WN_A1_WEATHERNEXT_BILLING_PROJECT` env var -- transport configuration
   only, never forecast authority) and opens
   `gcsfs.GCSFileSystem(project=..., requester_pays=..., token="google_default")` using
   Application Default Credentials, failing clearly (no synthetic evidence) if either is
   unavailable.
2. **Coordinate selection by value.** A new pure function, `select_nearest_grid_coordinate`,
   reads the real `lat_0p05`/`lon_0p05` coordinate arrays and picks the nearest coordinate
   VALUE (converting the requested longitude to the starter guide's 0..360 convention
   first) -- never `round(degrees / 0.05)` raw index arithmetic. Both the requested and
   actually-selected coordinates are retained on `WeatherNextEnsembleEvidence` and bound
   into evidence identity; a selection more than one grid cell away from the request is
   rejected. `test_coordinate_selection_by_value_not_raw_index_arithmetic` proves this
   against the old bug directly.
3. **Trading close is not the settlement window.** `early_close_condition` is now parsed
   only as `trading_cutoff_local`/`trading_cutoff_evidence_text` and never smuggled into
   `WindowStatus`. `WindowStatus` is always `NOT_ESTABLISHED` today -- see
   `MISSING_SETTLEMENT_WINDOW_EVIDENCE` for the exact missing fact. The "Day-window
   semantics" and "Alert policy" sections below describe the *pre-repair* (incorrect)
   behavior; today, `decide_alert`'s existing window ceiling gate hard-caps every real live
   evaluation at `TOO UNCERTAIN` or below.
4. **Side-aware executable economics.** `decide_alert` no longer uses
   `abs(model_yes_probability - yes_best_ask)`. YES and NO are each compared independently
   against their own conservative, fee-inclusive taker debit
   (`wn_a1_market_economics.conservative_taker_debit`), and a side is selected only when its
   own buffered gap clears the frozen thresholds. `test_absolute_gap_without_executable_
   side_is_skipped` is the required counterexample (model YES=10%, YES ask~40%, NO debit=95%
   -> `SKIP`, not a false 30pp flag). The primary alert now names the side plainly ("YES is
   worth checking" / "NO is worth checking").
5. **Canonical end-to-end entrypoint + durable persistence.** `wn_a1_runner.run_canonical`
   is the one live, research-only entrypoint and takes no transport-override parameters at
   all (the injectable composition seam every test uses instead is `_run_evaluation`). A new
   fixed-location append-only SQLite journal, `wn_a1_attempt_store.py`, persists every
   evaluated attempt -- including the full WeatherNext member rows, both grid coordinates,
   both side economics, and `SKIP`/`TOO UNCERTAIN`/`DATA NOT READY` outcomes, not only
   `TAKE A LOOK`. `replay_decision` reopens persisted data in a fresh process and
   recomputes the decision from scratch (re-validating the evidence identity along the
   way), rather than recomputing a hash from an in-memory object. Duplicate persistence of
   the same attempt fails closed.

**Live WeatherNext smoke test: not run.** ADC/Requester-Pays quota-project configuration for
the approved Google account (configured Requester Pays quota project) succeeded, but the
live read failed closed with **HTTP 403**: the account does not have `storage.objects.get` access to
`gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/`. That is a dataset-level
Google Cloud grant the account does not hold, not a bug in `gcs_zarr_reader`'s Requester Pays
wiring; the reader was not weakened or bypassed and no synthetic evidence was substituted.
`LIVE WEATHERNEXT READ NOT RUN -- GOOGLE DATASET PERMISSION DENIED.` See
`docs/IMPLEMENTATION_STATUS.md`'s WN-A1 repair entry for full verification results.
**Superseded 2026-09-21:** access was granted and the read succeeds; see
"WeatherNext decoding-boundary repair" below.

## A load-bearing correction made mid-build

The milestone brief's premise was that current Kalshi documentation states DAILY
high/low temperature markets settle from the **National Weather Service Daily Climate
Report**, with hourly markets settling from **The Weather Company**, and that the
day-window uses NWS local-standard-time semantics (with a documented DST edge case).

Before writing any settlement-source code, the live public Kalshi API was queried
directly (`GET https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=
KXHIGHCHI`, and the corresponding `/events/...` and `/series/...` endpoints, 2026-09-14).
Every currently active `KXHIGHCHI-*` market's `rules_primary` text, and both its event-
and series-level `settlement_sources` fields, name **The Weather Company** -- not NWS.
The same was independently confirmed for `KXHIGHNY`, `KXHIGHMIA`, `KXHIGHDEN`,
`KXHIGHAUS`, and `KXHIGHLAX`.

Kalshi's own help-center article
(`https://help.kalshi.com/en/articles/13823837-weather-markets`, dated 2026-07-22,
confirmed to exist and to say what the brief described) directly **contradicts** the live
contract evidence. Kalshi's `GLOBALTEMPERATURE` rulebook PDF names NWS as the *default*
"Source Agency" but explicitly allows the Exchange to "concretely specify" the per-market
source -- which it has done, live, as The Weather Company.

Per explicit product direction received mid-build, WN-A1 was redirected: **live
market/event/series evidence is controlling authority**, never a help-center claim and
never an assumed default. WN-A1 therefore binds to the real current source (The Weather
Company) through a brand-new, independent authority module, and documents the
contradiction rather than silently picking a side. If Kalshi's live rules text ever
changes, `route_current_daily_high` fails closed on the new shape rather than continuing
to assume today's TWC binding.

## Frozen M27C vs. current-live WN-A1 -- explicit boundary

| | Frozen M27C (`daily_temperature.py`) | WN-A1 (`wn_a1_current_daily_high_authority.py`) |
|---|---|---|
| Scope | 20 cities, historical | Chicago only, current/live |
| Settlement source | "The Weather Company" (frozen constant) | "The Weather Company" (bound fresh from live evidence every call) |
| Policy identity | `m27c-daily-temperature-contract-authority-v1` | `wn-a1-current-live-daily-high-twc-authority-v1` |
| Day window | Not modeled | Civil local day, derived from live `early_close_condition` text |
| Imports | -- | Never imports `daily_temperature.py` or `weather_source_authority.py`'s settlement-authority chain |

Both modules currently agree on the underlying settlement source only because that
happens to be true of today's live data -- WN-A1 does not inherit that fact from M27C; it
independently re-derives it every time from the live `Event`/`Series`/`Market` objects
passed in, and `test_frozen_m27c_semantics_are_not_reused_or_reinterpreted` statically
scans the WN-A1 source file to prove no cross-import exists.

The one deliberately reused, clearly-labeled research/location fact: the CLI identifier
`CLIMDW` and NWS/GHCN identity for Chicago Midway (`KMDW` / `USW00014819`) are pure
geography, independent of who currently settles the contract, and are redeclared locally
(never imported from M27C's authority chain).

## Day-window semantics: real evidence, not an assumption

The live event's own `early_close_condition` field states, verbatim:

> The Last Trading Time will be 11:59 PM local time on September 15, 2026 regardless of
> any data releases or events occurring. ...

This is first-party, per-contract Kalshi evidence -- not a help-center claim -- and it
supports a **civil local-calendar-day** window (local midnight to local midnight,
`America/Chicago`), not the NWS local-standard-time / DST-shifted window the (contradicted)
help article describes for the NWS pathway. `route_current_daily_high` only accepts this
evidence when the exact phrase, naming the contract's own target date, is present;
otherwise `WindowStatus.NOT_ESTABLISHED` and the alert engine hard-caps the decision below
`TAKE A LOOK` (see Alert policy, below) regardless of how large the raw model/market gap
is. Window derivation was verified correct across both 2026 US DST transitions
(`test_window_civil_local_day_correct_across_dst_transitions`).

## Pipeline

```
WeatherNext 3 (GCS Zarr, 64-member ensemble)
  -> wn_a1_weathernext_evidence: bind + validate exact member/hour evidence
  -> wn_a1_probability: member-by-member max, raw probability = matching/64, +-1F stress
Kalshi KXHIGHCHI current live event
  -> wn_a1_current_daily_high_authority: discover + bind current contract, fail closed
  -> wn_a1_market_economics: live orderbook + conservative TAKER_NOW cost (reused M27A primitives)
  -> wn_a1_alert: gate + decide + render plain-English alert
  -> wn_a1_evaluation_record: preserve every evaluated opportunity for later scoring
  -> wn_a1_outcome_lane: DATA MODEL shape only (WN-A2 not implemented)
```

`wn_a1_runner.py` wires these together with every transport (Kalshi getters, WeatherNext
reader, clock) injectable, so no test needs real network access or real Google Cloud
credentials.

## WeatherNext source

- Model: `weathernext_3_0_0`; variable: `station_head_temperature_2m`; unit: Kelvin.
- Expected object layout: `gs://weathernext3_spatial/weathernext_3_0_0/zarr/
  2026_to_present/<YYYYMMDD>_<HH>hr_<member>_preds/predictions.zarr/`, validated against
  the claimed `init_time` before any read.
- All 64 members (`sample` 0..63) required for every retained valid hour; a missing
  member is `DATA NOT READY`, never silently dropped. Duplicate members, wrong
  variable/model/unit, and a `valid_time` that doesn't reconstruct from
  `init_time + lead_time + lead_subtime` are hard rejects.
- Probability is always `matching_members / 64` -- no smoothing, no calibration, no
  ensemble-mean substitution.

**Blocker**: no Google Cloud credentials, `gcloud`/`gsutil`, or the `gcsfs`/`zarr`/
`google-cloud-storage` packages were available in this development environment. The real
reader (`gcs_zarr_reader`, gated behind the new optional `weathernext` dependency group in
`pyproject.toml`) is therefore **untested against the real WeatherNext bucket**. Every WN-A1
test injects a fake in-memory reader; `build_ensemble_evidence`'s validation logic is fully
exercised, the GCS I/O itself is not. **Superseded 2026-09-21:** the reader was exercised live
against the real bucket; see "WeatherNext decoding-boundary repair" below.

## Kalshi market evidence

Reuses the repository's single reviewed public transport
(`services.market_universe.public_read`, `external-api.kalshi.com`, GET-only) and M27E's
independent response re-validation. Market/event/series discovery for one `KXHIGHCHI`
event, and the executable price, come only from the live order book's actual best ask
(`NormalizedBook.yes_best_ask`) -- never the market's displayed/cached price field.
Conservative economics reuse the M27A/opportunity-engine TAKER_NOW book-walk and quadratic
fee formula (`taker_cost`, `calculate_fee`, `current_event_formula_policy`); every
`TakerCost` still carries `FeeEstimateQuality.DETERMINISTIC_FORMULA_ONLY` -- the pre-fill
final exchange fee is never claimed to be known.

## Alert policy (versioned `wn-a1-alert-policy-v1`, frozen for this milestone)

`TAKE A LOOK` requires **all** of: contract `SUPPORTED`; 64/64 members present; market
evidence fresh (<=5 min); raw gap >= 10pp; buffered gap (raw gap minus a flat 5pp
conservative buffer) >= 5pp; settlement-day window established; and no ±1°F boundary-risk
reversal of the preferred sibling contract in the same event. Any other case is `SKIP`
(gap too small) or `TOO UNCERTAIN` (window unestablished or boundary-risk reversal), never
`TAKE A LOOK`. Missing contract/ensemble/market evidence is `DATA NOT READY`. The primary
alert text is restricted to these four headline words and is scanned
(`_assert_no_banned_claims`) to reject "guaranteed," "profitable," "alpha," "arbitrage,"
"free money," "expected profit" before it can ever be returned.

**Consequence of the day-window gap**: because `WindowStatus.NOT_ESTABLISHED` hard-caps
the decision below `TAKE A LOOK`, and because every live KXHIGHCHI market checked *does*
carry a matching `early_close_condition`, the window gate is not expected to be the
blocking factor for live Chicago evaluation today -- but any future contract lacking that
exact phrase will correctly stay capped rather than silently assuming a window.

## Preservation and replay

`EvaluationRecord` binds only immutable evidence identities (WeatherNext evidence
identity, Kalshi snapshot identity, contract policy identity, alert policy version,
decision, gap, timestamp) and is written for **every** evaluated candidate, including
`SKIP` and `TOO UNCERTAIN`, to avoid selection bias in later scoring.
`replay_record_id` recomputes the same `record_id` purely from a record's own persisted
fields, proving fresh-process determinism.

## Outcome lane (WN-A2, not implemented)

No small, bounded, credential-free public-read path to Kalshi/The Weather Company's final
settlement value was identified in this milestone (`weather.com/kalshi`'s "Official
Climate Reports" portal structure was not established as machine-readable without
expanding scope). `wn_a1_outcome_lane.py` defines only the shape a later reconciliation
would populate (`SettlementOutcome`) and is not called by any other WN-A1 module. Next
milestone: **WN-A2: authoritative TWC/Kalshi settlement outcome reconciliation.**

## Safety

Production writes and autonomous trading remain OFF. No module under the `wn_a1_` prefix
imports `production_execution`, `demo_execution`, `execution_simulation`,
`production_gdp_strategy`, `production_weather_strategy`, `risk_engine`,
`supervised_canary`, `bounded_autonomy`, or `kalshi_account_gateway` (credentialed
account access) -- enforced by a static source scan in
`tests/test_wn_a1_safety_boundaries.py`, not just by review. No `"POST"`/`"PUT"`/
`"DELETE"` literal appears in any WN-A1 module; every Kalshi read goes through the shared
GET-only public transport. `research_only=True` and `production_influence=Decimal(0)` on
every dataclass that carries them, checked both per-module and via one cross-module
reflection test.

## Verification

- Focused WN-A1 tests: **93/93 PASS** (`tests/test_wn_a1_*.py`).
- M27A live-market-economics regression: **29 passed, 1 skipped** (psycopg not installed;
  pre-existing, unrelated to this change).
- M27C weather regression (proves frozen lanes unchanged): **201 passed, 1 skipped** (same
  pre-existing psycopg skip).
- Ruff check + format: clean across the whole repository.
- mypy --strict: clean on all 9 new `services/forecasting/wn_a1_*.py` modules.
- Bandit (all severities) on the new modules: 0 findings.
- detect-secrets on every changed file: 0 findings.
- Full `pytest` suite: see the PR/branch report for the exact count; no regression observed
  in any targeted subset run during this milestone.

## WeatherNext decoding-boundary repair (2026-09-21)

Google later granted access, and the store's format was inspected live. Findings (all from
the dataset's own array metadata/coordinates, not object names):

- **Format.** Zarr v3, one store per initialization. Root `zarr.json` is a metadata index only.
  `station_head_temperature_2m` is chunked `[1,1,6,3601,7200]` (one member, one 6-hourly lead,
  six hourly sub-steps, the whole global 0.05-degree grid), `bytes`+`zstd`, a single zstd frame,
  no sharding, ~0.47 GB per chunk object. Longitude is 0..359.95 (`to_0_360` was already right).
- **Time semantics.** `lead_time` = 6, 12, ... 360 **hours**; `lead_subtime` = -5..0 **hours**
  (the six hourly steps ending at `lead_time`); the `datetime` coordinate = `init_time +
  lead_time`. So `valid_time = init_time + lead_time + lead_subtime`, both in hours.
- **Defect repaired.** The reader and validator treated `lead_subtime` as *minutes* (and the
  validator required `0 <= x < 60`), so lead 24 h / sub-step -5 became 23:55Z instead of 19:00Z.
  The field is now `lead_subtime_hours`, validated to the reviewed axis, and there is a single
  `valid_time_from_lead`. The evidence-identity tag moved to `...-v3-lead-subtime-hours` because
  the same numbers now denote different instants.
- **Reader boundary.** `gcs_zarr_reader` now verifies units, the `init_time` coordinate and the
  `datetime` companion (`verify_time_axes`), verifies the array layout, and reads values with
  `decode_zstd_prefix_float32`: one bounded prefix range read of the single zstd frame per
  (member, lead) chunk with a hard `MAX_TRANSFER_BYTES` cap (2 GiB) that fails closed. The
  content hash binds each chunk's whole-object identity (size/generation/md5/crc32c) **and** the
  byte range read with its own SHA-256 (the range hash is not the object hash). Two further
  defects fixed on first live contact: `select_nearest_grid_coordinate` used truthiness on numpy
  arrays, and the chunk key omitted the variable directory (caught by the new fake-based test).
- **Measured cost (payload bytes, sample 0 unless noted).** Cost is set by the sub-step's
  position in the chunk, not constant: sub-step -5 h: 58,720,256 B (12.5% of the object);
  -4 h: 142,606,336 B per member (30.3%); -3 h: 218,103,808 B (46.4%). Fetch granularity is 8 MiB.
  Later sub-steps approach the whole object. A full 64-member Chicago local day still needs
  hundreds of chunks (extrapolated, NOT measured: on the order of 100+ GB), so the reader
  refuses it under the current cap. Range decoding is only cheap for early sub-steps.

## What this milestone does not claim

- Not a settlement-outcome match: WeatherNext temperature is never claimed to equal the
  TWC value that will actually settle the contract.
- Not profitability evidence: `TAKE A LOOK` is a research-alerting heuristic threshold,
  not a promotion gate and not an expected-value claim.
- Not automated trading: no order is placed, previewed, or authorized anywhere in this
  lane.
- Not a finished 20-city product: Chicago, `DAILY_MAX` only, by explicit scope.
