# M27C Part 2B1 — Forecast-Vintaged Weather Calibration Evidence

Status: implemented, offline verified, research only.

This milestone establishes a trustworthy boundary for historical forecast →
observed outcome → residual data. It does not establish full point-in-time
replay fidelity.

## Evidence semantics

The captured NDFD descriptor preserves `forecast_reference_time` separately
from the NCSS CSV `time` coordinate. In the accepted Chicago capture the
reference time is `2018-06-20T07:00:00Z`; the three valid-time coordinates are
18Z on June 20, 21, and 22. The CSV coordinate is retained as a valid-time /
interval coordinate. It is not called issuance, acquisition, settlement, or
necessarily the end of the valid interval.

The accepted descriptor is a 12-hour statistical product. DAILY_MAX requires
Temperature, Maximum temperature, Forecast, Maximum statistical process, GRIB
parameter `0 0 4`, and Kelvin units. DAILY_MIN shares these rules with Minimum
temperature, Minimum statistical process, and GRIB parameter `0 0 5`. The
parser does not require a guessed MinT netCDF variable name. It fails closed on
semantic conflicts and unsupported interval metadata.

NDFD Kelvin values are converted exactly with Decimal arithmetic:
`C = K - 273.15`; `F = C * 9 / 5 + 32`. GHCN-Daily `.dly` TMAX/TMIN values are
fixed-width tenths of degrees Celsius and use the same exact Decimal conversion
to Fahrenheit. A nonblank QFLAG makes an outcome unusable; MFLAG and SFLAG are
preserved.

## Accepted Chicago capture

The authority is `CLIMDW → KMDW → USW00014819`, with the returned NDFD grid
point kept distinct from the requested station coordinate. The reviewed
capture produced these rows from the actual `.dly` snapshot:

| local target date | forecast K | forecast °F | observed tenths °C | observed °F | residual °F |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2018-06-20 | 296.5 | 74.03 | 261 | 78.98 | 4.95 |
| 2018-06-21 | 298.7 | 77.99 | 200 | 68 | -9.99 |
| 2018-06-22 | 295.4 | 72.05 | 183 | 64.94 | -7.11 |

The residual sign is deliberately `observed_degF - forecast_degF`. This is
compatible with the existing empirical distribution's later `central forecast
+ residual` semantics; that behavior was not changed.

Target dates are derived by converting the UTC coordinate through the reviewed
source IANA timezone with `ZoneInfo`, including DST behavior. No UTC date,
archive path date, filename timestamp, or forecast reference date is used as a
substitute.

## Replay-fidelity truth and boundaries

Each row is machine-readable as
`FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT`: the NDFD forecast is
forecast-vintaged, while the GHCN-Daily label is a snapshot acquired now and
may be revised. The row does not claim `FULL_POINT_IN_TIME` and does not prove
that the exact GHCN-Daily contents were available at the historical forecast
time.

MaxT real archive-format acceptance is complete for the reviewed Chicago
sample. MinT semantic parsing is implemented from the official parameter rules,
but equivalent real MinT archive-format acceptance remains pending because no
MinT archive transport sample was manually reviewed in this milestone. This
single sample is not a 20-city or MinT historical acceptance.

The module and replay script perform no network access, credentials, Kalshi
calls, production writes, probability generation, fair-value or edge
calculation, P&L calculation, sizing, ranking, capital allocation, trade or
risk object creation, order activity, scheduler, or autonomy. Every evidence
record is `research_only = true` with `production_influence = Decimal("0")`.

Future work, in order:

1. M27C Part 2B2 — calibrated weather probabilities
2. M27D — Complete Edge Truth Ledger
3. M27D.1 — Informed Flow Radar (public-market anomalous-flow research, not
   identification or accusation of individual insiders)
4. M27E — Marginal Capital Ranking
5. M27F — Shadow Portfolio Optimizer
6. M27G — Specialist Expansion
