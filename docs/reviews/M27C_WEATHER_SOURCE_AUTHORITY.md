# M27C Part 2A — Physical Weather Source Authority

## Scope and result

M27C Part 2A establishes repository-reviewed physical source identity for the 20 daily-temperature
contracts supported by Part 1. It is research-only authority plus pure parsing of captured public
evidence. It adds no transport, credentials, Kalshi calls, forecast, probability, fair value, alpha,
profitability, allocation, sizing, order, execution, scheduler, autonomy, or production write path.
Production influence is exactly `Decimal("0")`.

The three identifiers have deliberately separate meanings:

- `CLIATL`-style identifiers are Kalshi settlement/climate-product identities. The existing
  `WeatherContract.station_id` retains this meaning.
- `KATL`-style identifiers are current NWS physical stations used to resolve forecast sites.
- `USW00013874`-style identifiers are reviewed GHCN-Daily historical calibration/observation
  stations.

The Weather Company remains the Kalshi settlement source. Neither NWS nor GHCN-Daily is labeled a
`FINAL_OFFICIAL_SETTLEMENT_SOURCE`, and no scalar `WeatherSourceRecord` is manufactured from
metadata.

## Repository authority

`services/forecasting/weather_source_authority.py` composes canonical location and timezone from
Part 1 `SETTLEMENT_LOCATIONS`, then binds those values and the reviewed NWS/GHCN-Daily identifiers
into immutable, capability-created records. Construction validates formats, IANA timezones,
uniqueness, exact 20-key coverage, and exact Part 1 location/timezone agreement. The complete
authority fingerprint is:

`f6f79c7db8c13d7c757fd37558b5baa013cae5f5b2bb9e6dafcef54d426d24c2`

| Climate product | NWS station | GHCN-Daily station |
|---|---|---|
| CLIATL | KATL | USW00013874 |
| CLIAUS | KAUS | USW00013904 |
| CLIBOS | KBOS | USW00014739 |
| CLIDCA | KDCA | USW00013743 |
| CLIDEN | KDEN | USW00003017 |
| CLIDFW | KDFW | USW00003927 |
| CLIHOU | KHOU | USW00012918 |
| CLILAS | KLAS | USW00023169 |
| CLILAX | KLAX | USW00023174 |
| CLIMDW | KMDW | USW00014819 |
| CLIMIA | KMIA | USW00012839 |
| CLIMSP | KMSP | USW00014922 |
| CLIMSY | KMSY | USW00012916 |
| CLINYC | KNYC | USW00094728 |
| CLIOKC | KOKC | USW00013967 |
| CLIPHL | KPHL | USW00013739 |
| CLIPHX | KPHX | USW00023183 |
| CLISAT | KSAT | USW00012921 |
| CLISEA | KSEA | USW00024233 |
| CLISFO | KSFO | USW00023234 |

Contract resolution requires an authority-reviewed CLI identifier, exact Part 1 location and
timezone, and `DAILY_MAX` or `DAILY_MIN`. Unknown or mismatched contracts fail closed.

## Vintaged external evidence

Pure parsers create immutable, capability-bound evidence from captured NWS station GeoJSON, NWS
points/grid GeoJSON, and official fixed-width `ghcnd-stations.txt` / `ghcnd-inventory.txt`
snapshots. Every record binds the repository authority identity, source content hash, acquisition
time, research-only state, and zero production influence. Content identity deliberately excludes
acquisition time: ingesting identical bytes later does not pretend the source content changed.

NWS station coordinates and forecast `gridId/gridX/gridY` are observed runtime evidence, not
permanent authority. Points evidence requires the exact station lookup coordinates, reviewed
timezone, exact-integer grid coordinates, and exact HTTPS
`api.weather.gov/gridpoints/{gridId}/{gridX},{gridY}` origin/path.

GHCN-Daily station identity is permanent repository review, while station/inventory snapshots are
mutable, reconstructed, vintaged external evidence. A snapshot must contain the exact reviewed
station, finite valid coordinates, a name, and valid TMAX and TMIN year intervals. A current
snapshot does not establish that revised data was historically visible at an earlier date.

The five discovery rows `CLIATL`, `CLIDCA`, `CLIMSP`, `CLIMSY`, and `CLISAT` were manually reviewed.
They are accepted by the explicit repository map, not by weakening or encoding a one-kilometre
nearest-station heuristic. Distance remains discovery evidence only.

## Offline acceptance replay

`scripts/replay_m27c_weather_source_authority.py` accepts an operator-supplied probe path and does
no network access, credential access, or writes. Against the accepted 2026-08-16 artifact it found
20 rows, 20 exact NWS station identities, 20 timezone matches, 20 complete grid observations, 20
top-candidate reviewed GHCN-Daily mappings, 20 TMAX inventories, 20 TMIN inventories, and 20 where
both inventories reach 2026. Artifact SHA-256:

`c2ec0d6536fffd9f284fe836a17414ba320e71ee0b1ad512ec79d741f88cc296`

This replay is deterministic evidence reconciliation, not proof that mappings never change. This
milestone establishes source identity only. It proves no forecast skill, profitability, trading
readiness, or production safety beyond the explicitly tested zero-influence boundary.
