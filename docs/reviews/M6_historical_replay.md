# M6 Historical + Point-in-Time Replay Review

## Acceptance state

- **Code / deterministic replay / 100k stream:** OFFLINE VERIFIED.
- **Kalshi historical contract, markets, trades, fills, orders, candles:** MOCK VERIFIED against externally supplied current official facts; live API NOT VERIFIED.
- **Point-in-time availability, gaps, rules/fee quality, external replay:** OFFLINE VERIFIED.
- **Production credential:** not required for public history; existing read-only credential is required only for private fills/orders. No write credential exists.
- **Human acceptance:** PENDING.

## Cross-functional findings

- **Quant / ML:** Actual ingestion is immutable and distinct from reconstructed availability. Unknown availability, future corrections, unfinished candles, nonfinal settlement, later rules and later economic vintages are inaccessible. Fidelity is explicit.
- **Trader:** Candles never masquerade as executable depth or reveal an invented intrabar path; strategies fail when required fidelity is absent.
- **Data science:** Labels require finalization. Amendments supersede rather than mutate. Vintage availability prevents revision leakage.
- **Data engineering:** Cutoffs are versioned; backward movement warns. Pagination fails closed, seam overlap deduplicates, raw manifests are content-addressed, and gaps are first-class.
- **Security:** Private history remains behind the existing read-only signer boundary. Replay has no signer, risk authorization, or mutation interface; support exports redact private identifiers.
- **SRE:** Page/run manifests and deterministic checkpoints provide resumable audit state without silently completing failed scans.
- **Product:** System health says PARTIAL and describes rules/fee/availability limitations rather than claiming generic backtest readiness.
- **CFO:** Streaming iterators and bounded acquisition pages avoid whole-history RAM loads; archival lineage is retained without a giant undifferentiated table.

No forecasting, source promotion, risk authorization, signing expansion, or execution was introduced.
