# Point-in-time AAA gasoline / Kalshi order-book protocol

**Research-only. No trading credentials, order placement, or production authority.**

Run from this directory:

```bash
python capture.py --event-prefix KXAAAGASD-26OCT23- --output ./local_snapshots
python -m unittest discover -p 'test_*.py'
python calibrate.py ./verified_aaa_daily.csv
```

The capture fetches raw AAA HTML, paginated Kalshi series discovery, each discovered matching market's raw market metadata and raw orderbook response. Each artifact has request/receive UTC timestamps, URL, HTTP status, headers, SHA256, and byte count. It creates a unique snapshot directory and writes a manifest with COMPLETE or failure status; failed or partial captures MUST NOT be interpreted as synchronized books. Sequential retrieval implies per-strike time skew. An HTTP book is only an observed public response, not a guaranteed fill, and displayed bids must be converted carefully to executable opposing asks. All tickers in the chosen event prefix are retained, not just a preferred strike.

The public API has no signed private order flow. Fees are deliberately marked UNVERIFIED pending independent capture of the exact fee schedule effective at the decision time. No fee multiplier, quote, slippage, latency, or probability is inferred from a price ladder. The collector performs no schedule registration and makes no claims of continuous coverage.

For historical AAA calibration, prepare a CSV with `date,national_regular_usd,source_url,source_sha256,acquired_at_utc` using independently archived authoritative daily snapshots; each date must be consecutive and uniquely sourced. The calibrator refuses missing provenance and gaps. It computes day-over-day changes and a chronological empirical jump-frequency baseline with Laplace smoothing, and reports Brier/log loss. The retrospective constant baseline is diagnostic only and is NOT an investable benchmark. A daily national change distribution is not a strike-specific fair probability without a frozen conditional model. Do not claim positive edge or trading permission from this output.

For settlement provenance, retain a separate raw AAA outcome snapshot and Kalshi resolution metadata with acquisition times and SHA256; reconcile exact strike semantics, AAA revisions, and rounding independently. Never substitute a screenshot, later-edited web page, or the model's forecast for a settlement receipt.

**Known limitations:** no independently sourced historical daily AAA CSV is bundled; this code does not manufacture one. Snapshot storage is local and untracked; do not commit personal tokens or raw files inadvertently. Test in a clean checkout and obtain independent adversarial review before considering merge. The research branch remains isolated from CPI P12 and all production strategies.
