# CPI-E1-P9A — Historical Price Evidence

## Result

The public, unauthenticated KXCPI acquisition completed on the isolated
`cpi-e1-p9a-historical-price` branch from canonical commit
`7aa43ea605fb44bc7db2572385bc61382ad5d5e1`.

The immutable tracked artifact is under
`evidence/cpi_p9a_historical_price/`: one manifest and one raw JSON response
per sibling. The source runtime manifest remains under ignored `state/`.

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

Tracked size is 49,449,153 bytes uncompressed: 474 raw files (48,423,949
bytes), plus the manifest. The largest raw response is 516 KB.

Manifest SHA-256:
`d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699`

Original runtime manifest SHA-256:
`d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699`

Final tracked frozen-manifest SHA-256:
`6537608b8ea3e0f34c7c3257ead8ed77f65fea0ebccf0d6404a68a7934e49d89`

The hashes differ because the tracked manifest adds canonical-base identity,
request identity, selected-candle hashes, tracked artifact paths, and explicit
`RECONSTRUCTED_PUBLIC_HISTORICAL` provenance while preserving the exact raw
responses.

These responses were retrieved retrospectively from Kalshi's public historical
API. They were not contemporaneous bot observations: `actual_bot_ingest_at` is
null and `prospective_observation` is false.

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

The artifact grants quote evidence only. Candles do not establish depth,
queue position, fill probability, or large-order execution, and do not grant
M11 fill-simulation authority. Settlement results are not included in the
feature rows.

## Verification

Focused P9A plus historical-client and executable-quote tests: **34 passed**.

Offline frozen-artifact replay and adversarial mutation tests: **40 passed**
including missing-file, duplicate-ticker, wrong-event, raw-byte mutation, and
post-close-candle rejection cases.

Full repository suite: **3034 passed, 3 skipped**. Skips are PostgreSQL tests
because `KALSH3_TEST_POSTGRES_DSN` was not set.

The remaining economic blockers are deliberately outside P9A: exact historical
fee-regime authority, BLS initial-release truth, and historical order-book/fill
evidence.
