# GAS-A1 prospective freeze

The independent sample unit is one target date, not one threshold market. The collector records
the exact AAA national/state HTML bytes and the exact public Kalshi market/orderbook bytes with
acquisition timestamps and SHA-256 identities. It accepts only ACTIVE `KXAAAGASD` markets whose
rules explicitly name AAA and `gasprices.aaa.com`; unsupported rule shapes are rejected.

Reviewed AAA surfaces:

- `https://gasprices.aaa.com/` — national regular current, yesterday, week-ago, and month-ago rows;
- `https://gasprices.aaa.com/state-gas-price-averages/` — first-party current state regular rows.

Kalshi discovery is series-scoped at `/trade-api/v2/markets?series_ticker=KXAAAGASD&status=open&limit=1000`, followed only by API-returned cursor pages. The collector never scans a generic first page and filters locally. An empty completed series query is `NO_MARKET`; transport/HTTP failure is `DISCOVERY_FAILED`; malformed response is `UNSUPPORTED_RESPONSE`; and an invalid or exhausted cursor is `PAGINATION_INCOMPLETE`.

Cadence for a future 10–15-date run is 2 minutes for Kalshi books and 5 minutes for AAA pages
during the bounded expected update window, with 15-minute outside-window polling. This initial
implementation exposes a single-cycle command; a scheduler must register each cycle before it
calls the command. Failures, unchanged pages, rejected markets, and missing books are durable
rows. No outcome is consulted by the predictor, no alert is emitted, and no execution path is
imported.

Before any latency claim, paired page hash changes are compared using collection timestamps. A
change within 120 seconds is `SYNCHRONIZED`; no paired change is `INDETERMINATE`; only a state
change materially preceding a national change is `STATE_BEFORE_NATIONAL`. Synchronized or
indeterminate results are explicitly reclassified as a next-day forecasting test. Each market
must also have parseable close and settlement timestamps; the collector records whether close is
strictly before the target AAA date and permits no latency interpretation when it is not.

The pre-registered diagnostics are source update time, national/state changes, threshold distance,
book repricing, displayed spread/depth, and fee-inclusive cost once the event fee metadata is
reviewed. No post-outcome threshold or date tuning is permitted.
