# WN-MULTICITY-D1 — five-city prospective development design

**PASS — FIVE-CITY PROSPECTIVE DEVELOPMENT DESIGN READY.** This is a design and offline/canary gate. No new scientific job or scheduler was built or enabled; no current-date prospective forecast, model fit, outcome score, trade, or PR occurred. The Chicago collector and its evidence were not edited.

The [protocol](protocol.json) was created and SHA-256 hashed (`56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346`) before the D1 WeatherNext metadata and Kalshi timing inspections. The original candidate 08:30Z decision left just 7.133 minutes on the slowest of seven historical runs. The hash-bound [decision amendment](decision_time_amendment.json) sets one common **09:00Z** decision and a `[09:00,09:05)` scientific-start gate. The [effective policy manifest](freeze_manifest.json) pins the protocol and five append-only amendments, including exact per-city source/window identities, Kelvin/Fahrenheit units, the conditional integer settlement domain, and Earth Engine grid floating-point tolerance. Its SHA-256 is in [freeze_manifest.sha256](freeze_manifest.sha256).

## Frozen city identities

For target date `D`, the start is inclusive and the end on `D+1` is exclusive. All offsets are fixed **local standard time**, with no daylight-saving shift. The source for every row is The Weather Company Kalshi Domestic Daily Climate relay of the specified NWS CLI report; the portal CLI ID and station are independently bound in the protocol and identity amendment.

| City / station | Kalshi series | CLI | Frozen WeatherNext grid (lon, lat) | UTC target on D → D+1 | Required 00Z hours | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Boston / KBOS | `KXHIGHTBOS` | `CLIBOS` | `(-71.00, 42.35)` | `05:00Z → 05:00Z` | 5–28 | `09:00Z` |
| Miami / KMIA | `KXHIGHMIA` | `CLIMIA` | `(-80.30, 25.80)` | `05:00Z → 05:00Z` | 5–28 | `09:00Z` |
| Denver / KDEN | `KXHIGHDEN` | `CLIDEN` | `(-104.65, 39.85)` | `07:00Z → 07:00Z` | 7–30 | `09:00Z` |
| Los Angeles / KLAX | `KXHIGHLAX` | `CLILAX` | `(-118.40, 33.95)` | `08:00Z → 08:00Z` | 8–31 | `09:00Z` |
| Seattle / KSEA | `KXHIGHTSEA` | `CLISEA` | `(-122.30, 47.45)` | `08:00Z → 08:00Z` | 8–31 | `09:00Z` |

The common development forecast is the **maximum of exactly 24 hourly `station_head_temperature_2m_p50` values** from the target date's 00Z WeatherNext initialization, independently within each city's CLI window. Raw values are Kelvin; the unrounded Fahrenheit proxy is `(Kelvin − 273.15) × 9/5 + 32`. There is no bias correction, tuning, or probability model. Canonical integer-Fahrenheit settlement handling is conditional: an exact source-reported integral value maps to the integer domain; a non-integral value fails closed pending authority review. Continuous forecasts are never rounded to buckets.

## Timing and market feasibility

[Historical WeatherNext metadata preflight](weather_ingestion_preflight.json) inspected **seven non-current 00Z runs, September 24–30**, without selecting forecast bands or pixels. Every city had exactly the required 24 unique consecutive hours from one init, with matching init and valid-time metadata. The maximum required ingestion lag was **502.867 minutes** after 00Z for each city; the slowest completion was September 27 at `08:22:52.037603Z`. Margin was **7.133 minutes at 08:30Z** and is **37.133 minutes at 09:00Z**. Thus all five require the common 09:00Z timing repair for the chosen 30-minute operational buffer; no city needs a later time on these seven runs. A future late/missing run fails that city-day without retry. The [historical point fixture](historical_point_fixture_20260927.json) independently sampled all **120 non-current September 27 p50 point-hours**, verified every frozen grid cell and exact valid hour, and supplied real historical values for the offline forecast calculation.

[Kalshi public structural preflight](market_timing_preflight.json) inspected the October 2 events on October 1 after their 14:00Z open. All five events were discoverable; each had six unique active siblings, exact city/CLI/TWC rule identity, structured tails and bounds, bid/ask and size fields for all siblings, and nonempty public YES/NO depth for all **30** sibling markets. Posted market open/close times span October 2 09:00Z. This establishes structural feasibility, not a future quote guarantee: each city-day must recheck active state, six siblings, source/rules/bounds, prices, and depth at its actual decision capture, and reject nonempty outcome fields.

## Separate cloud design and bounded block

The [cloud design](cloud_design.json) uses a separate proposed Cloud Run job with **five parallel tasks, one fixed city per task**, a separate dedicated Earth Engine read-only OAuth secret and runtime service account, and a new multicity-only GCS bucket. The proposed Scheduler, if built, starts an early wrapper at 08:50Z and remains **PAUSED**. Each task waits until 09:00Z without scientific calls, then atomically creates `claims/{city}/{YYYYMMDD}.json` within the experiment prefix using `if_generation_match=0`. All evidence writes are create-only with retries disabled. Cloud Run task retries and Scheduler retries are zero. A city's failure or late start consumes only that city/date claim and cannot retry or overwrite another city's packet. Packet hashes bind exact weather, market, and forecast bytes. Chicago's active job, scheduler, model, and evidence are outside this design.

The first proposed development-only block is **2026-10-03 through 2026-10-14 inclusive**. At this report time, October 3 remains prospective. The maximum is **5 × 12 = 60 raw `CITY_DAY` rows**. City-days are **not assumed independent**; each artifact carries same-date cluster, geographic group, and weather-regime group. No naive sample-size multiplier is calculated. No block is enabled here.

## Offline validation and independent review

Eight offline tests pass: city windows, actual historical 24-hour point coverage, wrong-city/grid and missing/duplicate/late-hour rejection, Kelvin/Fahrenheit maximum, exact source and integer ladder parsing, conditional integer settlement, predecision/late/date gates, duplicate claims, existing-artifact and GCS generation-precondition protection, component hashes, correlation labels, and one-city validation/storage-failure isolation. Ruff, mypy, `compileall`, and `git diff --check` pass. The independent adversarial reviewer found and rechecked repairs to source/window binding, ladder continuity, Kelvin units, 09:00 amendment enforcement, claim/evidence integrity, and outcome leakage, then reported **no remaining D1 design blocker**. The separate job's IAM, credential mount, task wiring, and actual decision-time market availability remain future paused-build/runtime canary gates.

**A separate paused multicity Cloud Run job and Scheduler may now be built and canary-validated. They must not be enabled for scientific acquisition under this D1 result.**
