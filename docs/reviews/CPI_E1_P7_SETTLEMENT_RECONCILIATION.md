# CPI-E1-P7 — Exact Kalshi Settlement Reconciliation

## Scope and provenance

- Requested canonical base: `11272005b04c9ccc28f95c7b5d3e9c864ee8404a`.
- Requested canonical tree: `fbabf52064fc5fbdcad78b1f0db345a3b7462598`.
- `origin/main` was fetched and verified as exactly the requested base commit
  and tree. The prior P7 branch did not descend from it and was preserved as
  `cpi-e1-p7-settlement-reconciliation-scratch`; this branch replays P7 as a
  new additive commit from the canonical base.
- Branch: `cpi-e1-p7-settlement-reconciliation`.
- P7 is an offline, read-only evidence boundary. It imports no execution,
  signer, credential, risk, order, or production service.

## Implementation boundary

`services/forecasting/cpi_settlement_reconciliation.py` reuses the canonical
`Comparator`, `ContractSpecification`, `SemanticStatus`, `PayoutModel`,
`ExchangeDetermination`, `DeterminationState`, `SettlementRecord`, and
`ReconciliationStatus` primitives. It does not add a settlement-label type.

The semantic gate requires exact KXCPI family identity, ticker/event/series,
rules version and market-rules hash, semantic hash, valid simple-binary model,
CPI-U / U.S. city average / all-items / SA MoM domain, exact comparator and
threshold unit, one-decimal initial-release rounding, authoritative source,
and explicit revision/correction policy. Missing, conflicting, ambiguous, core,
YoY, wrong-month, unsupported, or non-valid inputs fail closed.

`KalshiFinalizedEvidence` derives its determination from raw UTF-8 JSON and
recomputes the SHA-256 hash at validation time. It binds source identity,
market/event/series, rules identity, semantic hash, result, binary dollar value,
determination/finalization timestamps, lifecycle flags, and acquisition time.
`YES` is exactly `$1`; `NO` is exactly `$0`. Caller mutation, forged hashes,
non-final/disputed/conflicting/superseded-without-latest-authority evidence,
and malformed values fail closed.

## P6 bindings

| Release | BLS artifact SHA-256 | P6 value | P6 observation identity |
|---|---|---:|---|
| July 2025 | `5b869d4365bc0f58db9814e3da09105f0fd944e4bbf16c39b5511f774a03dc4b` | `0.2` | `74b5c6f504d448ac475a5598e50a0602b249368acd26b90642c066ecd96f4c65` |
| December 2025 | `8351af0db99f8b1e338abe1b33cb062a70e61d2b154c0ec26aaed964f52b489e` | `0.3` | `9cbc587c2fe7a8664e9a9546ad6a672e7914719cadc62a9cf03025affc4be0af` |
| January 2026 | `3b46aebecd5aa2d66f6f8abc38e47381e180a73db6cf87313ecc8eeddebd69f8` | `0.2` | `6b566274e63c5c6d65f11ab193c0275b30264cdeae428bb24a442dde0bfdbbda` |

The P6 tests and parser preserve the exact observation ID when the reviewed
P4/P5A artifact is available. P7 does not accept a caller-authored or
reconstructed observation as a substitute.

## Historical-evidence audit and empirical reconciliation

Repository search covered historical market snapshots, archived API responses,
event/market metadata, settlement responses, KXCPI identifiers, rules hashes,
lifecycle/determination artifacts, M26 infrastructure, and archive-backed
evidence. The repository contains generic settlement primitives and M26C
consumers. Public unauthenticated GET acquisition was then performed on
2026-08-31 UTC; exact response bytes were retained in local scratch storage
only. No authenticated or trading API was used.

Endpoints used: `/historical/cutoff`,
`/historical/markets?series_ticker=KXCPI&limit=1000`, three
`/historical/markets/{ticker}` requests, three `/events/{event_ticker}`
requests, and `/series/KXCPI`. All returned HTTP 200. The market-list cursor
was empty after 474 markets. The cutoff reported
`market_settled_ts=2026-07-02T00:00:00Z`.

Raw response SHA-256: cutoff
`bb66b1a68c9636fbb64c23e09f4b932a8766a5d9f40acf4f6f0e372548052222`; market
list `1f0de2b979f10aa3ff378b7b27b1cc34f4729ddf02f0c45c87935fdbb10df998`;
July market `5531efbd8268f779f5db2bb10b158e772878da9819be497022d6cd4d1b758ae7`;
December market `0b8142a93ed9b739e3f685366c7167371e1607228497f206bbd1bfec506bfcfc`;
January market `de5164c582f534a608fad0771a369417146281902a01ef1fd031b3ea6f3f2d79`;
July event `b63394b1e12c7750d40277662f3309187601328ec73b413d4a4366bd7e992770`;
December event `7ba1eb6e3971520916dcf7a890fd7c7be81d07eaea2941fb19f7f0c1e6effb6b`;
January event `7b7947dc42f6ea7e1b0dd4ec7917e97672775a3e21d7417bc0a60f567d9c7ea8`;
series `f5c410bc20a280d5fc14e33d1b028777a3a88aa6a955938eeee03cc481866e60`.

| Release | Exact Kalshi market/event/rules evidence | Exchange final | P6-derived result | SettlementRecord |
|---|---|---|---|---|
| July 2025 | `KXCPI-25JUL-T0.1`; `KXCPI-25JUL`; rules `e5c8ef3fded5aa6ff7fdc11be4a4d0436669a33e9f3ae083294c2c40de727243` | `finalized`; YES; `$1.0000`; `2025-08-12T13:09:49.950641Z` | YES (`0.2 > 0.1`) | MATCHED; eligible `True` |
| December 2025 | `KXCPI-25DEC-T0.2`; `KXCPI-25DEC`; rules `01e1d4cde33d117ce4723bb681c4ef09aab57f7d43a42909fb788a2e58b3bc23` | `finalized`; YES; `$1.0000`; `2026-01-13T16:48:15.222981Z` | YES (`0.3 > 0.2`) | MATCHED; eligible `True` |
| January 2026 | `KXCPI-26JAN-T0.1`; `KXCPI-26JAN`; rules `6c8f57e912985c25aa45b43592d22f377eb09d0273b45128d58ac91dc164193a` | `finalized`; YES; `$1.0000`; `2026-02-13T15:08:40.596381Z` | YES (`0.2 > 0.1`) | MATCHED; eligible `True` |

The three selected markets produce empirical MATCHED SettlementRecords and
eligible training labels. The API exposes explicit `status: finalized` and
non-null `settlement_ts`; P7 accepts that terminal status rather than inferring
finality from result presence. The API exposes no separate rules-version field;
the exact historical rules identity is therefore the content-addressed market
rules SHA listed above, which remains an independent-review limitation.
Sibling strikes share one release dependency and must not be counted as
independent evidence events.

## Tests and G3 implication

The focused suite covers exact matching, first-class mismatch, identity/rules
binding, all supported Decimal comparators, wrong period, non-final/disputed
state, contradictory binary values, forged raw hashes, and caller-mutated
determinations. The implementation has no model, economics, probability,
profitability, position-sizing, allocation, execution, credential, or risk
authority. `research_only = true` and `production_influence = 0` remain fixed.

G3 is **PASS only for these three exact selected markets**. This checkpoint does
not generalize to all CPI history or all Kalshi markets. Independent security
and architecture review is required before any later use.
