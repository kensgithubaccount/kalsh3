# AAA gasoline prediction audit v1 — RESEARCH ONLY

Base: 66f94f7543f1dfd67b3011e91e0402d1ac980aa1 (2026-10-21).
No production authority, orders, promotion, or modifications to existing strategy code.

## Scope
Recover *all* historical gas predictions, including losses, abstentions, and unfilled recommendations. A search with zero indexed matches does not prove absence. Chat transcripts, notification history, screenshots, and trade receipts must be separately recovered with source identifiers and UTC timestamps. Do not backfill fabricated prices, outcomes, or timestamps.

Append-only ledger: predictions.jsonl. One JSON object per line. The validator rejects duplicate IDs, missing core identity/chronology, retrospective evidence, or claims of a fill without receipt. Unknown fields stay null; do not infer them.

## Evaluation
For each forecast, verify Kalshi ticker, exact YES/NO rule, AAA date and national regular grade, and the *published* AAA value and snapshot timestamp. Store original bytes/hash and source URL separately. Forecast-time source must predate forecast; later AAA revisions are outcome evidence only. Check precise greater-than boundary and decimal precision. Record ask, available depth, maker/taker fee schedule, latency/slippage, and whether trade actually filled. Unknown economics => no trade-ready result.

Use a frozen, preregistered forecast rule and rolling-origin splits. Fit only on earlier observations; reserve chronological holdout and then prospective shadow. Report Brier score, log loss, calibration by probability bin, baseline comparison, confidence intervals, net executable EV and worst-case sensitivity. Do not optimize thresholds on test results. Distinguish recommendation accuracy from actual realized P&L.

## Promotion
REPAIR REQUIRED until complete historical ledger, independent settlement/source checks, prospective shadow evidence, executable quote/depth receipts, fee verification, independent PASS, and explicit authority approval. A positive backtest alone never grants production authority.
