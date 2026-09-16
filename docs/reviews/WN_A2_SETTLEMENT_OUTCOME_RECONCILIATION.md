# WN-A2 settlement outcome reconciliation

WN-A2 starts only from an existing persisted WN-A1 attempt ID. It reads the exact
persisted ticker, event, date, series, policy, and source identity; callers cannot
replace those values or provide parsed settlement data.

The exact public Kalshi market response is canonical settlement authority. The
response is acquired with GET only, retains the exact request path, raw body, SHA-256,
observation time, and parsed market, and is accepted only for `finalized` markets with
result, settlement value, and settlement timestamp. The historical/live choice is
made from Kalshi's public `/trade-api/v2/historical/cutoff` market-settled cutoff.

TWC is deliberately not part of this P1 implementation. If added, the bounded route
is `https://weather.com/kalshi/api/climate/primary?date=YYYY-MM-DD`, with exact
`CLIMDW` ↔ `KMDW`/`MDW` station mapping, and corroboration can never outrank Kalshi.

The separate fixed-location outcome SQLite journal is append-only. It writes START
before acquisition, then immutable evidence and terminal result rows; duplicate
reconciliation and update/delete are rejected. WN-A1 bytes and identity remain
untouched. A later controlling Kalshi correction must be appended as a new observation,
never silently overwritten.

States are `SETTLED_MATCHED`, `NOT_FINAL`, `CONFLICT`, `SOURCE_UNAVAILABLE`, and
`EVIDENCE_INVALID`. This lane is research-only, has zero production influence, makes
no profitability claim, and adds no settlement-window inference. WeatherNext remains
forecast evidence only; WN-A1 remains frozen.
