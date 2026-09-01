# CPI-E1-P9A — Historical Price Evidence

## Result

The public, unauthenticated KXCPI acquisition completed on the isolated
`cpi-e1-p9a-historical-price` branch from canonical commit
`7aa43ea605fb44bc7db2572385bc61382ad5d5e1`.

The immutable tracked artifact is under
`evidence/cpi_p9a_historical_price/`: the exact market-inventory response, one
manifest, and one raw JSON response per sibling. The source runtime manifest
remains under ignored `state/`.

| Check | Result |
|---|---:|
| Independent events targeted / persisted | 60 / 60 |
| Siblings targeted / persisted | 474 / 474 |
| 100%-complete events | 60 |
| Siblings with both usable YES and NO entry sides | 267 |
| Siblings missing a usable side | 207 |
| Fresh (quote age <= 1 hour) siblings | 148 |
| Stale siblings | 326 |
| Candle/API holes | 0 |

Tracked size is 50,510,114 bytes uncompressed: 474 raw candle files
(48,423,949 bytes), the 891,842-byte inventory response, and the manifest. The
largest candle response is 524,917 bytes.

Manifest SHA-256:
`d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699`

Original runtime manifest SHA-256:
`d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699`

Final tracked frozen-manifest SHA-256 after self-containment repair:
`290c8697a4fa3b47c67ae471619ac22ffe50cc7fc036156805ffbd5ed358a3e9`

The hashes differ because the tracked manifest adds canonical-base identity,
request identity, selected-candle hashes, tracked artifact paths, and explicit
`RECONSTRUCTED_PUBLIC_HISTORICAL` provenance while preserving the exact raw
responses.

The tracked package includes the exact original market-inventory response at
`market_inventory.json`: 891,842 bytes with SHA-256
`1f0de2b979f10aa3ff378b7b27b1cc34f4729ddf02f0c45c87935fdbb10df998`.
This is the original runtime inventory response, not a new reconstruction.

These responses were retrieved retrospectively from Kalshi's public historical
API. They were not contemporaneous bot observations: `actual_bot_ingest_at` is
null and `prospective_observation` is false.

The original runtime inventory bytes survived and matched the recorded runtime
inventory hash exactly; no replacement inventory request was made.

Every manifest `raw_sha256` recomputes from its corresponding raw response
file. Each sibling has one `underlying_event_id` of the form
`kalshi:<event_ticker>`; siblings are therefore not independent outcomes.

## Semantics and completeness

Acquisition uses a final 90-day window when required by Kalshi's 5,000-period
limit, with 60-minute candles. The selected candle satisfies
`end_period_ts < market.close_time`.

YES entry is `yes_ask`; NO entry is `1 - yes_bid`. Boundary quotes are explicit
missing-side reasons. Midpoint, `price.close`, `price.mean`, and last trade are
retained only as raw source data and are not executable prices.

The canonical contract parser resolved all 474 inventory rows as homogeneous
strict-greater-than (`GT`, symbol `>`) simple binary threshold contracts. No
semantic row was unresolved. Full-lifecycle `volume_fp` is stored as
`retrospective_full_lifecycle_volume` and is explicitly
`point_in_time_feature_eligible=false`.

The artifact grants quote evidence only. Candles do not establish depth,
queue position, fill probability, or large-order execution, and do not grant
M11 fill-simulation authority. Settlement results are not included in the
feature rows.

## Verification

Focused P9A plus historical-client and executable-quote tests: **34 passed**.

After self-containment repair, the expanded offline replay/adversarial suite:
**21 passed**. It covers inventory mutation/deletion, semantic mutation, all
derived quote fields, selected-candle identity, strict duplicate-key/nonfinite
JSON rejection, retrospective-volume quarantine, and the exact 60/474 counts.

Full repository suite: **3052 passed, 3 skipped**. Skips are PostgreSQL tests
because `KALSH3_TEST_POSTGRES_DSN` was not set.

The remaining economic blockers are deliberately outside P9A: exact historical
fee-regime authority, BLS initial-release truth, and historical order-book/fill
evidence.
