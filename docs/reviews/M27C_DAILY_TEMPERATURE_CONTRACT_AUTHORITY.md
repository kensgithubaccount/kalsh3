# M27C Part 1 — Daily-Temperature Contract Authority

## Scope and result

M27C Part 1 adds a deterministic, fail-closed specialist router that constructs the existing
`WeatherContract` only from explicit supported Kalshi daily maximum/minimum temperature rules.
It is research-only. It adds no live forecast, probability, fair value, profitability claim,
allocation, order, execution, or production influence.

The eligibility boundary is the authoritative rule itself, its exact strike metadata, the parent
Event settlement-source declaration, and a repository-reviewed immutable location authority.
Neither the exchange `Climate and Weather` category nor
`services.market_universe.quality.classify()` is an eligibility or alpha boundary. The broad
taxonomy can produce false positives, including Snowflake Inc.

## Reviewed authority and semantics

The versioned authority contains exactly the 20 empirically observed `CLI` climate-product
identifiers and binds each to its canonical location and validated IANA timezone. The identifier is
retained as the settlement/climate-product identifier found in Kalshi's rule; it is not represented
as an ICAO observation-station identifier. Unknown identifiers and city/identifier conflicts
abstain.

Supported v1 rules must establish maximum or minimum daily temperature, exact local calendar date,
Fahrenheit, The Weather Company, and exactly one observed strike shape:

- `between`: `RANGE`, `floor_strike` through `cap_strike`;
- `greater`: `GT`, threshold from `floor_strike`;
- `less`: `LT`, threshold from `cap_strike`, stored in `WeatherContract.lower` because that is the
  existing `EmpiricalDistribution` threshold parameter for `LT`.

No inclusivity, equality, rounding, tick, date-from-expiration, or revision behavior is inferred.
Finite exchange JSON floats are accepted only at this adapter boundary via `Decimal(str(value))`;
global `exact_numeric()` remains strict. Booleans, non-finite floats, malformed strings, missing
bounds, unsupported strike types, and rule/metadata contradictions abstain.

Every router call produces `SUPPORTED` or `ABSTAIN` with a deterministic reason. Supported results
are capability-constructed immutable research records, not trade candidates, receipts, risk
intents, allocations, or orders. Source identity binds canonical rule/strike/Event-source material;
policy identity binds the authority version and complete reviewed mapping. Production influence is
exactly `Decimal("0")`.

## Offline accepted-archive replay

Operator command (the archive path is supplied locally and is not runtime configuration):

```console
python -m scripts.replay_m27c_daily_temperature /path/to/m26f-archive.sqlite
```

Replay of the accepted H3 archive on 2026-08-16 produced:

| Measure | Result |
|---|---:|
| Markets evaluated | 84,724 |
| Supported daily-temperature contracts | 480 |
| `DAILY_MAX` / `DAILY_MIN` | 240 / 240 |
| `between` / `greater` / `less` | 320 / 80 / 80 |
| Unique reviewed CLI identifiers | 20 |
| `degF` | 480 |
| Settlement source | The Weather Company |
| Malformed recognized strict candidates | 0 |
| Production influence | 0 |

The harness opens SQLite read-only and performs no network or credential operation. It invokes the
same production parser whose coverage it reports, so this is deterministic archive coverage/replay
evidence, not independent semantic validation, and it cannot independently establish a
false-negative exclusion rate.

## Safety boundary

`forecast_weather()` and `WeatherSourceRecord` remain research-only and unchanged. Part 1 ends at
trustworthy contract construction: it creates no residual model, central forecast, probability,
fair value, simulated P&L, trading decision, sizing, allocation, order, cancel, or execution path.
`services/production_execution` is unchanged.
