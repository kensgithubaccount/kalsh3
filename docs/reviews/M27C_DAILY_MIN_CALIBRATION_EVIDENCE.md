# M27C Part 3A — DAILY_MIN Forecast-Vintaged Calibration Evidence

Status: bounded research-only evidence accepted. No DAILY_MIN probability model exists.

## DISCOVERED MINT FAMILY

The reviewed family is `POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z`, archived at
`https://noaa-ndfd-pds.s3.amazonaws.com/wmo/mint/YYYY/MM/DD/YHUZ98_KWBN_YYYYMMDDHHMM`.
The accepted 2024 object is `YHUZ98_KWBN_202406150346`; its GRIB reference cycle is
04Z. The records are `TMIN`, 2 m above ground, GRIB parameter 0/0/5, generating
process 2 (Forecast), statistical process 3 (Minimum), time processing 2.
Their exact GRIB bounds are 04Z–12Z, 20Z–32Z, and 44Z–56Z relative to the 04Z
reference. Midpoint leads are exactly 4h, 26h, and 50h. The first interval is
8 hours in the raw bounds despite the product label `0-12 hour min fcst`.

Grid signature: template 30, 2145 × 1377, Dx 2539.703 m, Dy 2539.703 m.
KMDW extraction returned 41.794091 latitude and raw longitude 272.260017,
normalized to -87.739983, with the requested station authority CLIMDW/KMDW/
USW00014819/America-Chicago.

## REAL EVIDENCE

Historical raw object SHA256: `bbc238769e7e24bd8e41cdf1dcedf8cac13c5b58a27c566b8ad3ce1d678382a0`.
Historical extraction SHA256: `f6402896b210bf6abbd70a93030cd8cbc5b60a3f56e1050eec9accb2dae2817a`.
Reviewed executable: `/Users/ksyme/miniforge3/envs/kalsh3/bin/wgrib2`, version
3.8.0, SHA256 `5aeba76e0165263ad2ce02272485778bdb1ca7aceb5797dc98fce7021c41f02d`.

## TARGET-DATE SEMANTICS

The reviewed MinT rule assigns the local target date from the interval’s local
end date. Thus an overnight interval may span two Chicago dates; it represents
the ending local calendar date. The rule is tested in CST and CDT, including
spring- and fall-transition examples. This is resolved for the reviewed family.

## CURRENT COMPATIBILITY

Current object `YHUZ98_KWBN_202608170346` has SHA256
`6b947eec923299e217ebe55950d32fcf95c0f47f473a460e5245ed5616c71ade` and
extraction SHA256 `5d2450e07a61205517311dd938941186628c8a2c6e63d06c3f7c89c0b1d145b5`.
Its full signature matches the historical family: `CURRENT_MINT_FAMILY_COMPATIBLE = YES`.
No 2026 outcome was fetched.

## JUNE 2024 COVERAGE

Requested target dates: 2024-06-01 through 2024-06-30. Archive scan dates:
2024-05-29 through 2024-06-30. The bounded artifact accepted 31 GRIB objects,
93 raw accepted forecast records, 30 usable TMIN outcomes, and 85 in-range
residual rows. Raw accepted forecast-record counts are 31 at 4h, 31 at 26h,
and 31 at 50h because the scan includes precursor source dates 2024-05-29
through 2024-05-31. In-range residual counts are 28 at 4h, 28 at 26h, and
29 at 50h; selection preserves those counts, for 85 selected target-date/
horizon rows. All 30 target dates are globally present. Exact-horizon missing
target dates are 06-25/06-29 at 4h, 06-26/06-30 at 26h, and 06-27 at 50h.
Rejected/ambiguous source archive dates are 06-25 and 06-29. Artifact SHA256:
`58aabb7800a20b334e618a2ba481075f28eff82e9b03f4a9754e55eb345d3d81`.
Raw object and extraction hashes are preserved in the immutable artifact
`/tmp/m27c-climdw-2024-06-mint-coverage-v2.json`.

This establishes source/semantic feasibility, not statistical adequacy.

## OUTCOME TRUTH / SETTLEMENT LIMITATION

Outcomes are GHCN-Daily `USW00014819` TMIN labels from a current/revised
historical snapshot, with replay fidelity
`FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT`; this is not point-in-time outcome
evidence. The Weather Company remains the Kalshi settlement source.
Part 2C1 remains `NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE` and
`UNVALIDATED_GHCND_PROXY`.

## MODEL STATUS

NO DAILY_MIN PROBABILITY MODEL EXISTS YET. No probabilities, markets, economics,
risk, portfolio, execution, or production path consumes this evidence.

## DAILY_MAX FREEZE

The DAILY_MAX model, evaluator, prospective boundary, settlement mapping, and
Part 2B3 protocol identity remain unchanged.
