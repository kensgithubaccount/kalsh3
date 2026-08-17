# M27C Part 2C1 — TWC Settlement Authority & Historical Mapping Evidence

## Result

Part 2C1 is a research-only, fail-closed evidence boundary. It does not alter the
weather model or prospective confirmation protocol. The result is:

`NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE`

## What Kalshi actually says

The repository-reviewed Kalshi daily-temperature authority recognizes `CLIMDW`,
location `Chicago`, unit `degF`, measurement `DAILY_MAX`, and settlement source
`The Weather Company` from the strict rule/source structure. The authority does
not establish that the source is `KMDW`, `USW00014819`, an airport observation,
an interpolated location, or a particular TWC product.

Kalshi's public API documentation says series metadata carries settlement sources,
contract URLs, and contract-terms URLs:

- https://docs.kalshi.com/api-reference/market/get-series
- https://docs.kalshi.com/api-reference/market/get-markets

The current public endpoint probe for `/series/CLIMDW` returned HTTP 404 and the
public historical-markets query for `series_ticker=CLIMDW` returned an empty market
list at acquisition time. No orderbook or price endpoint was accessed. The existing
repository archive evidence remains the source of the Part 1 recognition counts,
not a newly retrieved settlement record.

Therefore proven: Kalshi names The Weather Company and the contract identifies
Chicago/CLIMDW. Unresolved: exact TWC product, station/location identifier, daily
window, timezone/DST rule, representation/rounding, and correction/revision policy.

## What TWC authority we could verify

Official IBM/TWC documentation describes:

- site-based time-series observations as physical station observations, with a
  rotating recent-history retention model;
- History on Demand as historical products including blended/gridded data; and
- API access using an API key/account.

Sources:

- https://www.ibm.com/docs/en/environmental-intel-suite?topic=apis-site-based-time-series-observations
- https://www.ibm.com/docs/en/environmental-intel-suite?topic=apis-history-demand
- https://www.ibm.com/docs/en/SSRQLT_suite/topics/WeatherCompanyData-API-Common-Usage-Guide.html

These sources do not connect Kalshi `CLIMDW` to `KMDW`, specify a daily maximum
observation day, define DST handling, establish integer Fahrenheit rounding, or
document Kalshi's revision/freeze policy. No TWC value was collected. Settlement-
vintaged TWC evidence: zero. Current historical TWC snapshot evidence: zero.

Credentials/paywall required: YES for the documented historical API path. No key,
account, terms acceptance, or gated endpoint was used.

## Historical evidence

The typed boundary supports four distinct classes: settlement-vintaged TWC value,
current historical TWC snapshot, Kalshi settlement-implied observation, and GHCN
comparison observation. No 2026-09-01-or-later target entered this work; August
2026 is rejected as mapping evidence. No historical Kalshi event with complete,
usable CLIMDW settlement structure was available from the public endpoint probe.
Usable TWC values: 0. Usable settlement-implied observations: 0. GHCN/TWC
comparable pairs: 0.

## GHCN vs TWC / settlement

No equivalence, transform, or rounding rule was inferred. The comparison code uses
exact `Decimal` predicates only. A winning interval remains an interval; it cannot
be converted into a fabricated point temperature. The code can classify a GHCN
value only as consistent, inconsistent, ambiguous, or structurally insufficient
against an independently captured settlement predicate.

There are no exact-match, delta, DST, revision, or frequency results because no
authoritative TWC values or complete historical settlement-implied observations
were available. `USW00014819` remains a NOAA/GHCN comparison candidate, not TWC
authority.

## Settlement-vintage limitation

No evidence is settlement-vintaged. A TWC historical value retrieved today would be
a current snapshot unless its point-in-time availability and Kalshi use were proven.
The unresolved next dependency is a licensed TWC historical/settlement feed or an
exchange-provided settlement record that identifies the value and vintage.

## Claim boundary

The probability model remains:

`GHCND_PHYSICAL_TEMPERATURE_PROXY`

and:

`settlement_mapping_status = UNVALIDATED_GHCND_PROXY`

Part 2C1 research status is `NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE`. It does not
validate a Kalshi settlement probability, fair value, edge, EV, risk, sizing, or
trading strategy. All new evidence types are immutable, `research_only = True`,
and `production_influence = Decimal("0")`.

## Prospective freeze

Part 2B3 was not changed. No prospective target, outcome, residual, or evaluation
evidence was accessed. The frozen protocol identity remains:

`2a5389f1771b5d33103c053eb6e3cf0467c215e139b5a54e8acd3477678d8281`

