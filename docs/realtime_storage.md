# Real-time storage and retention

For the 1 OCPU / ~6 GB target, PostgreSQL stores latest ticker/book state, canonical public trades, lifecycle
records, connection/subscription health, gaps/recoveries, and raw-archive manifests. Hot raw WebSocket events
are buffered into deterministic gzip JSONL batches for object storage with epoch, SID, sequence, exchange and
receive timestamps, hashes, and explicit gap markers. Default proposed raw retention is 30 days hot plus
protected lifecycle/trade/manifests longer; the owner must approve retention/cost before deployment.

Queue overflow never discards data invisibly: it marks BACKPRESSURED and opens a gap requiring snapshot
recovery. M6 will consume these manifests for complete replay. PostgreSQL must not become an unbounded table
of every book delta, and the UI never performs exchange-wide scans.
