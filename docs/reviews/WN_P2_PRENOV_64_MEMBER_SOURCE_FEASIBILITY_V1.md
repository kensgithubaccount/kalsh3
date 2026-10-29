# WN-P2 — Pre-November 64-member source feasibility / go-no-go audit

**Date / authority:** 2026-10-29. Read-only source/code audit; **NO LIVE GCS/EE acquisition**, no member pixels, no model calibration, no candidate signal, no trading. Parent issues #242, #238 and #234. Baseline canonical main `dbf57fd9e66e32019a059933542d5569449c109b`.

## Purpose and outcome

**Conclusion: NO EXECUTABLE DISTRIBUTION SOURCE DEMONSTRATED.** A viable WeatherNext 3 raw-member distribution exists as a documented **GCS dataset**, but the five-city, pre-09:00Z, bounded-cost extraction method is not presently demonstrated. The successful October 27 historical Earth Engine read and merged PR #241 one-shot future metadata canary expose per-image *summary statistics*, not the 64 hourly trajectories required for daily-high YES probabilities. Do not substitute a sum/product/normal fit of the hourly marginals or use exposed October outcomes to tune an invented model.

This document **does not freeze a probability transformation**, train a model, elevate the source to scientific custody, approve any fee/market snapshot or authorize paper observations. All Phase 0 model registries remain empty.

## 1. Source capability comparison

| Surface | Native data | What is established | What is not |
| --- | --- | --- | --- |
| Earth Engine `weathernext_3_0_0_0p05deg` | Hourly `station_head_temperature_2m` mean, p10, p25, p50, p75, p90 images | Historical image completeness for 5 city windows; prospective manual metadata-only canary merged | 64 member identities, temporal member dependence, provider raw transport bytes, station-settlement probabilities |
| `gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/` | Raw 64-member Zarr with 6-hour leads and hourly substeps | Chicago decoder previously reviewed against live GCS metadata/chunks, as historical development, with explicit safety bound | A timely, 5-city 24-hour bounded transfer; prospective 00Z source availability at our 09Z cutoff; trusted receipt |
| `gs://weathernext3_statistics_spatial/` | Precomputed mean and quantiles | Another statistics surface | Not a replacement for 64 correlated daily trajectories |

Google sources:
- [WeatherNext 3 Earth Engine guide](https://developers.google.com/weathernext/guides/earth-engine)
- [WeatherNext 3 GCS Zarr guide](https://developers.google.com/weathernext/guides/gcs)
- [WeatherNext distribution surface summary](https://developers.google.com/weathernext/guides/access-forecast)

## 2. Bounded-source geometry — conservative planning, NOT a bill

The canonical `services/forecasting/wn_a1_weathernext_evidence.py` describes `DATA_SHAPE=(64,60,6,3601,7200)`, `DATA_CHUNKS=(1,1,6,3601,7200)`; each chunk is one member and a complete **six-hour lead block over the global 0.05° grid**. Its decoded-byte value is `6×3601×7200×4 = 622,252,800 bytes`. The source comments describe approximately **0.47 GB compressed per global chunk** and a `MAX_TRANSFER_BYTES = 2 GiB` fail-closed cap for one call. It uses six hourly offsets ending at a six-hour lead, not one independent chunk per valid hour.

For the previously frozen five-city target windows, the minimum **lead endpoints needed** for target UTC date 00Z are:

| City | UTC valid-hour range | 6-hour lead endpoints | Member×lead global chunks (64 members) |
| --- | --- | --- | ---: |
| Boston | 5–28 | 6,12,18,24,30 | 320 |
| Miami | 5–28 | 6,12,18,24,30 | 320 |
| Denver | 7–30 | 12,18,24,30 | 256 |
| Los Angeles | 8–31 | 12,18,24,30,36 | 320 |
| Seattle | 8–31 | 12,18,24,30,36 | 320 |
| **Five-city union, if chunks are reused once** | 5–31 | **6,12,18,24,30,36** | **384** |

If each necessary full-grid chunk is fetched exactly once, **384 × 622,252,800 = 238,945,075,200 decoded bytes (~222.5 GiB)** of chunk processing; at the rough **0.47 GB compressed/chunk** *commented* in the reader, compressed transfer would be **~180 GB**, not a measured value or invoice. Real chunk sizes vary; metadata request count, network transfer, server-side slicing support and billing are not demonstrated here. Reusing chunks across stations avoids naive per-city duplication but does not remove the global-chunk problem. This is NOT a verified lower bound on a future optimized storage interface and must not be used to bill or trade.

**Go/no-go consequence:** A naive extension of the existing Chicago GCS reader must **NOT** raise/bypass its 2 GiB cap or claim complete 64-member five-city evidence. A separate bounded-access mechanism and observed costs/read timing must first be independently demonstrated. Even one complete city-day at this chunk granularity is over the current cap by a wide margin.

## 3. Pre-November protocol freezes (future, not performed)

1. **Source and time:** exact source object layout, 00Z initialization, immutable asset/model version, verified `lead_time`+ `lead_subtime` grid, and *observed* arrival before 09:00:00Z must be captured prospectively; a retrospective source-reported ingestion property is not timestamp attestation. No future target refetch/backfill.
2. **Bounded transport:** prove the protocol can return all 64 source members × every 24 forecast valid hours for each predeclared city with bounded requests, bytes, object generation identities, quotas, and hard-abort/no-refetch behavior. Distinguish compressed/decoded/network bytes; log errors and 403s, not assumed access. No five-city full GCS reader is authorized by this audit.
3. **Station and contract:** demonstrate each actual event's named airport/station and **The Weather Company** settlement rule and measurement interval; the frozen CLI fixed-standard-time window is a forecast-development convention, not automatically the TWC aggregation clock interval. Reject any unresolved mismatch.
4. **Probability:** pre-register a defensible mapping from the *same 64 member trajectories* to per-contract YES/NO probabilities, with inclusive range/strict-tail semantics, member completeness, uncertainty and settlement bias handling. Pre-commit before seeing corresponding outcomes; October 3–14 outcomes are already exposed and cannot be sold as pristine probability calibration.
5. **Economics:** preserve *all six sibling* contracts and raw book sides/sizes, timestamps, fees, exact rules, one-contract TAKER_NOW book-walk, latency and missingness. No best-ask midpoint shortcut; no caller-authored price/fee assertion may become source authority.
6. **Forward ledger:** every target date, city, event, six siblings, failed/abstained candidate, unknown source, unavailable quote, and post-outcome result must enter a create-only attempt ledger. Primary independent unit remains the target-date cluster, not 30 contracts/city-days.
7. **Gates:** exact-head CI, changed-file strict mypy, independent detached adverse review, and **separate** model, economics, custody and paper-admission approvals. A full prospective EE metadata receipt is **NOT** one of those positive approvals.

## 4. What we can finish before November 1

- [x] Revalidate canonical infrastructure main and the inherited no-authority boundaries.
- [x] Locate the actual raw 64-member GCS source and estimate the **source-layout** problem conservatively from frozen code.
- [x] Record this `NO EXECUTABLE DISTRIBUTION SOURCE DEMONSTRATED` assessment; preserve October as exposed historical reference.
- [ ] Obtain independent exact-head review of this documentation-only audit before treating it as canonical.
- [ ] Develop an isolated **read-only public Kalshi six-sibling market-economics evidence issuer**, using WN-A1's existing public transport and fee primitives, with a complete failure roster.
- [ ] Draft/test an immutable **prospective all-attempt ledger** (include abstentions and blocked attempts), without upgrading the empty model registry.
- [ ] Agree a bounded safe **GCS member-access feasibility test** separately, without importing WeatherNext forecast pixels into the EE metadata canary.
- [ ] Confirm actual TWC named-station/measurement-window semantics on fresh per-market rules before computing probabilities.

**November 1**: the PR #241 manual 08:00–09:00Z metadata-only canary can observe source metadata *if separately manually invoked*. It cannot prove the 64-member source, custody attestation, price edge, or paper readiness. **November 10–17 paper candidate evaluation remains conditional**, not a calendar commitment.

## Explicit non-authority

`record_type: WN-P2-PRE-NOV-DISTRIBUTION-SOURCE-FEASIBILITY-AUDIT-v1`  
`scientific_evidence_authorized: false`; `model_probability_authorized: false`; `paper_signal_authorized: false`; `trading_authority: NONE`; `production_influence: 0`.
