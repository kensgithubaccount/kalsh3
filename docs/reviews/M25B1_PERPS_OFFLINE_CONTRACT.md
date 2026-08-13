# M25B1 Offline Perps Contract and Evidence Review

Date: 2026-08-13

M25B1 corrects the product boundary by adding a parallel Perps/margin path. Existing Predictions
events, protocol, manager, YES/NO book, runtime, evidence model, and `book_evidence` table are
unchanged and cannot consume Perps frames.

## Contract and safety result

- Required margin-market metadata is strict and immutable. Structural and full-metadata hashes are
  separate; source-contract URL/checksum/retrieval/parser provenance is a separate type.
- Perps snapshots use optional `bid`/`ask` arrays; deltas use ticker, SID, sequence, price, signed
  delta, and `bid|ask`. No market ID or fabricated timestamp exists.
- Client-order and subaccount values are neither retained nor fingerprinted; ephemeral events expose
  only presence flags.
- The canonical book has independent bid/ask sides, exact Decimal arithmetic, tick and quantity
  validation, crossed-book exclusion, stale protection, and structural invalidation. Perps book
  quantities use exact 0.01-contract granularity regardless of the retained
  `fractional_trading_enabled` metadata flag; no unsupported whole-contract rule is inferred.
- `MarginProtocolState` can construct only subscribe, unsubscribe, and list commands for
  `orderbook_delta` and `ticker`. It has no private channels, update, or snapshot-recovery method.
- Gaps persist no delta evidence and require reconnect, a new nonzero epoch, resubscription, and a
  fresh snapshot.
- Book and ticker/funding/mark evidence are distinct. SQLite tables are dedicated, append-only,
  hardened, exact-text Decimal stores with database-enforced zero production influence.
- Ticker replay identity is the exact source-semantic tuple `(connection_epoch, sid, ticker,
  ticker_ts_ms, source_fingerprint)`. Local receipt and availability clocks never affect replay
  classification; exact replays increment health replay telemetry without advancing accepted
  ticker time.
- The runtime is scripted, single-consumer, disabled by default, and has no HTTP, WebSocket,
  credential, deployment, execution, risk, learning, canary, autonomy, signer, or transfer surface.

M25B2 remains the future concrete read-only REST/auth/margin-WebSocket boundary.

## Verification

- M25B1 deterministic tests: 38 passed.
- Focused M25A/M25B1 and realtime tests: 72 passed, with the known macOS-only `/proc/self/fd`
  signer test deselected.
- Full suite: 509 passed and 46 pre-existing macOS signer portability failures remained
  (`/proc/self/fd` and unavailable `os.memfd_create`).
- Ruff lint and format: passed across `services` and `tests`.
- Strict mypy: passed for all 15 M25B1 source modules.
- `git diff --check`: passed.
