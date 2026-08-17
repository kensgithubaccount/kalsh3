# M27C Part 2B1.5 — Historical Weather Calibration Coverage

Status: implemented, bounded live collection is fail-closed; 2024 June acceptance is not calibration-ready; research only.

Part 2B1 accepted source-format and residual correctness using three real Chicago
MaxT rows. Part 2B1.5 adds reusable, operator-only public collection and
coverage accounting. It does not claim that the residual distribution is
calibrated or that the forecast has skill. Part 2B2 — probability generation —
remains pending.

## Accepted lane and archive discovery

The initial lane remains exactly `CLIMDW → KMDW → USW00014819`,
`DAILY_MAX`. No other city or `DAILY_MIN` is calibration-ready.

Family identity is explicit: `LEGACY_CHICAGO_MAXT_5KM_YGFZ98` retains the
reviewed 2018 acceptance, while `POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z` is a
separate, raw-GRIB-reviewed post-2020 family. The families are never pooled and
each artifact carries exactly one family identity.

Real discovery was proven through:

`https://www.ncei.noaa.gov/thredds/catalog/model-ndfd-file-old/201806/20180620/catalog.xml`
→ catalog-listed `YGFZ98_KWBN_201806200646`
→ `.../thredds/ncss/grid/.../dataset.xml`
→ `.../thredds/ncss/grid/...?...&accept=csv`
→ the existing `parse_ndfd_descriptor()` and `parse_ndfd_point_csv()` parsers
→ the existing GHCN-Daily `.dly` parser
→ `observed - forecast` residuals.

The descriptor was semantically accepted as the reviewed 12-hour MaxT product;
catalog metadata and dataset names were not treated as semantic authority.
The NCSS interval request is six hours after the descriptor coordinates, as
verified against the returned CSV coordinates. The collector only permits
public HTTPS GETs and keeps transport outside production forecasting paths.

## Bounded live evidence

The original `0/0/5` result was not evidence that the lead-bucket selector was
broken. The collector selected `YGUZ98`, a distinct 2.5 km CONUS grid family.
The reviewed Part 2B1 row is `YGFZ98`: a 5 km grid. Both descriptors happened
to expose 11h/35h/59h coordinates for the 2018 probe, but their grid extents
and returned KMDW grid points differ. The reviewed policy therefore uses
`YGFZ98_KWBN_` for Chicago DAILY_MAX discovery. The WMO filename is only a
candidate filter; every descriptor still passes `parse_ndfd_descriptor()`.

For post-2020 dates, the public AWS index was empirically found at
`noaa-ndfd-pds.s3.amazonaws.com/wmo/maxt/YYYY/MM/DD/`. The index exposed
YGUZ98-family objects but no YGFZ98 objects for representative 2024, 2025, and
2026 dates. NCEI catalogs do expose YGFZ98 entries in the `model-ndfd-file/access`
branch, but the published NCSS descriptor endpoint returned HTTP 500 for the
probed YGFZ98 datasets. No alternate product family or guessed transport was
accepted.

The supplied raw-GRIB probe accepted the post-2020 family semantically with
wgrib2 3.8.0. The raw-GRIB boundary is operator-only. The pure parser requires exactly three
03Z TMAX records, Forecast/Maximum/code-2 semantics, 12-hour intervals at
9–21h, 33–45h, and 57–69h, the 2145×1377 template-30 grid at 2539.703 m, and
finite KMDW point values. It derives the exact interval midpoint and preserves
interval-start, midpoint, and interval-end leads. wgrib2 is not a production
dependency and its executable path is excluded from portable artifact identity.

AWS contained YGUZ98 candidates on 2024-06-15, 2025-06-15, and 2026-06-15.
NCEI catalog discovery located matching 2024 and 2026 entries; its access
catalog has no 2025-06-15 branch. YGUZ98 NCSS descriptor requests for 2024 and
2026 returned HTTP 500 with underlying public S3 403 responses. Point requests
also returned HTTP 500. The direct 2025 attempt returned an underlying
NoSuchKey response. No YGUZ98 sample passed the failed NCSS descriptor/point
path; the family was instead accepted through the independent raw-AWS-GRIB/
wgrib2 semantic boundary below.

The 2024 AWS object was directly downloadable (3,179,584 bytes) and contained
NDFD WMO/GRIB material. The explicit operator extraction accepted its exact
signature using `/Users/ksyme/miniforge3/envs/kalsh3/bin/wgrib2` 3.8.0. No
decoder or dependency was added. The current forecast code provides generic
NWS grid evidence; current-family compatibility is established separately by
the current-date raw-GRIB probe below.

The current-date AWS probe on 2026-08-17 found and downloaded
`YGUZ98_KWBN_202608170246` (3,229,019 bytes, SHA256
`b866f4d81b078b65bfb3845491867cd5f3c47eb8d67a7bd2dfe22081f3d016cb`). The
the same explicit wgrib2 extraction and pure parser accepted the current file:
`CURRENT_FAMILY_COMPATIBLE = YES`. Its reference is 03Z and its grid, TMAX,
level, parameter, process, statistical, time-processing, and 9–21/33–45/57–69h
interval signatures match the reviewed family. This does not establish
settlement-source equivalence or generate probabilities.

The completed real discovery artifact was collected for 2018-06-20 through
2018-06-22, with archive discovery expanded three days earlier to obtain
forecast leads. It contains 25 accepted descriptors, 25 accepted point CSVs,
100 raw residual rows, and 5 selected residual rows. Selected counts are
`0–24h: 0`, `24–48h: 0`, `48–72h: 5`; the window is deliberately a discovery
probe and not a calibration sufficiency claim. Artifact:
`/tmp/m27c-climdw-june20-22-coverage.json`, content identity
`71872b9ce986d72c854bdffbdc5f0bf887670bfbd6570060b4e39fd3a824be99`.

The completed raw-GRIB June 2024 artifact is
`/tmp/m27c-climdw-2024-06-raw-grib-final.json`. It scanned 2024-05-29 through
2024-06-30, discovered 33 AWS indexes, captured 64 candidate objects, accepted
32 03Z GRIB files and 96 forecast records, and rejected 2024-06-25 because
multiple valid 03Z candidates remained. It contains 30 usable GHCN-Daily
outcomes, 87 raw residuals, 87 selected residuals, and 30 unique target dates.
Selected counts are `0–24h: 29`, `24–48h: 29`, and `48–72h: 29`, each 96.67%.
Bucket gaps are June 25, June 26, and June 27 respectively. Artifact SHA256 is
`7ab5436f507ba924a6035b9e8f7a4310661d5fed388aa8a2a077c561e46e6ed6`; file
SHA256 is `cafb3426a11edf285788a7aba9ee3f6c32fbfbbc7c458b25d1e1e80064306eb4`.

The corrected one-target-date post-2020 probe finalized a complete artifact at
`/tmp/m27c-climdw-2024-06-01-coverage.json`, with 0 accepted descriptors,
0 point rows, and 1 missing target date because NCSS returned HTTP 500. Its
artifact SHA256 is `9a3a713dbbad6005a1cd740f30ced9d97a9c83d97a09a059555b947ba3a65478`
and file SHA256 is
`071ebc4582ccb760217a5585167fed83c8e766b81a4dcb6cdfea699432d156db`.
The required 30-day artifact was not declared accepted: repeating known-failing
NCSS requests would add load without producing evidence. A bounded one-candidate
per-scan-day completion was nevertheless persisted at
`/tmp/m27c-climdw-2024-06-coverage.json`: 33 catalog requests, 33 AWS index
requests, 0 descriptors, 0 point CSVs, 0 outcomes, 0 raw residuals, 0 selected
rows, and all 30 target dates missing. Rejections were 33 AWS family-absent
records and 32 NCSS descriptor HTTP-500 records. Artifact SHA256 is
`e8cce4d4f5aa75c3a3e112e54441aba930b78f590b7c8eb4ff6d3dc88e76ade2`; file
SHA256 is `9b4eb836875606b50497d70bf2ae39977c0b7db60d21e0583e8a84b97af288c9`.
No probability, fair value, edge, P&L, position, risk, execution, or production
write was performed.

The collector records all fetched accepted evidence and selected residual IDs;
selection is at most one row for each `(source, measurement, local target date,
lead bucket)`, by distance to the 12h/36h/60h midpoint and then earlier
forecast reference time. Selection never reads residual magnitude or outcome.

Full-range command after independent review:

```text
python scripts/collect_m27c_weather_calibration_coverage.py --source CLIMDW --measurement DAILY_MAX --family POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z --wgrib2-bin /Users/ksyme/miniforge3/envs/kalsh3/bin/wgrib2 --start-date 2024-01-01 --end-date 2026-07-31 --output /tmp/m27c-climdw-2024-2026-coverage.json --checkpoint /tmp/m27c-climdw-2024-2026.checkpoint.json
```

Production influence is exactly `0`. Part 2B2 remains pending.
