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

`KalshiHistoricalAcquisitionEvidence` is the positive-authority boundary. Its
only reviewed live constructor is a bounded unauthenticated HTTPS GET to
`external-api.kalshi.com`, with exact GET method, status 200, path, selectors,
raw bytes, hash, and transport-observed UTC acquisition time. Redirects,
credentials, cookies, arbitrary source identities, and caller-supplied raw
responses are rejected. Eight durable fixtures under
`services/forecasting/fixtures/cpi_p7_public/` are fixed hash-allowlisted copies
of those exact response bodies.

The architecture guard scans `services/**/*.py`, not only forecasting, and
allows the four guarded private symbols only in this exact owner. It also has
regression fixtures proving an unauthorized production module under `services/`
(including aliased imports) is detected.

The final authority-chain repair keeps the issuance seam owned by this exact
reviewed module; `tests/test_cpi_settlement_architecture.py` fails if its
private capability, transport-response type, issuer, or completeness helper is
referenced by another forecasting production module. The transport captures
stdlib's parsed definite response length before reading, rejects declared
oversize responses before reading, requires exact byte completeness, and
translates `IncompleteRead`/HTTP exceptions to fail-closed errors. Close-
delimited responses remain explicitly supported only when stdlib reports no
definite length.

The durable eighth fixture is the exact unauthenticated GET response from
`https://assets.kalshi.com/contract_terms/CPI.pdf`, acquired
`2026-08-31T17:33:02Z`, SHA-256
`2317b1d8e823082b409f6ff3415fb135804d9682681f9f92f640b3681b29a872`, stored as
`services/forecasting/fixtures/cpi_p7_public/CPI-contract-terms.pdf`. Its
content-addressed repository policy identity is
`283343eb473846880398f681842523a2c51a18af4ab4079cf999e0f7a9911b8a` (the
full `KXCPIReviewedSemanticPolicy` contents, including the terms hash and all
normalized mappings). This identity is repository-derived, not exchange-issued.

`CPIHistoricalSemanticEvidence` rebuilds the `ContractSpecification` from the
validated market, event, series, and exact official terms acquisitions. The
historical market rules must themselves contain the CPI and single-decimal
language; the event and series must agree on the BLS settlement source; and the
historical KXCPI series must point to the exact terms URL. The terms artifact
supplies the source-explicit mapping for CPI-U, seasonally adjusted
month-over-month percent change, one-decimal treatment, BLS authority,
first-sentence report convention, and no post-expiration revision. It does not
literally state “U.S. city average” or “all items”; those are the
repository-reviewed normalization to the exact canonical P6 domain. The
complete normalization is content-addressed in the immutable
`KXCPIReviewedSemanticPolicy` record `KXCPI_SEMANTIC_POLICY`, which
also binds the exact terms URL/hash, basket, geography, unit, finality, payout,
and correction mappings. Market-specific historical rules remain primary for
comparator, threshold, and reference month. Callers cannot supply a
comparator, threshold, reference period, semantic hash, or rules identity to
the reconciliation path. `KalshiFinalizedEvidence` consumes only the validated
market acquisition and requires explicit historical `status=finalized`,
non-null `settlement_ts`, binary result, and exact YES/$1 or NO/$0 consistency.
The rules version is repository-derived and content-addressed as
`historical-market-rules-v1:<full historical market rules SHA-256>`; it is not
Kalshi-issued. All acquisition and settlement evidence remains research-only.

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
2026-08-31 UTC; exact response bytes are durably retained as seven minimal
frozen fixtures under `services/forecasting/fixtures/cpi_p7_public/`. No
authenticated or trading API was used. Retrieval time is distinct from each
historical `settlement_ts`.

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
The local exact-byte acquisition timestamps were cutoff
`2026-08-31T19:52:58Z`, market-list `19:53:19Z`, July market
`19:54:20Z`, December market `19:54:21Z`, January market `19:54:23Z`, July
event `19:54:24Z`, December event `19:54:28Z`, January event `19:54:44Z`, and
series `19:55:19Z`.

| Release | Exact Kalshi market/event/rules evidence | Exchange final | P6-derived result | SettlementRecord |
|---|---|---|---|---|
| July 2025 | `KXCPI-25JUL-T0.1`; `KXCPI-25JUL`; rules `e5c8ef3fded5aa6ff7fdc11be4a4d0436669a33e9f3ae083294c2c40de727243`; derived version `historical-market-rules-v1:e5c8ef3fded5aa6ff7fdc11be4a4d0436669a33e9f3ae083294c2c40de727243`; semantic `679f866bc0a965cd5cbff5480e49f1db9f7086a86a4218862653807ef11d7976` | `finalized`; YES; `$1.0000`; expiration `0.2`; `2025-08-12T13:09:49.950641Z` | YES (`0.2 > 0.1`) | MATCHED; eligible `True` |
| December 2025 | `KXCPI-25DEC-T0.2`; `KXCPI-25DEC`; rules `01e1d4cde33d117ce4723bb681c4ef09aab57f7d43a42909fb788a2e58b3bc23`; derived version `historical-market-rules-v1:01e1d4cde33d117ce4723bb681c4ef09aab57f7d43a42909fb788a2e58b3bc23`; semantic `887a7640c9fdb06d82e639fd9dce092e414d4b05ba7a1916c7e81f4429fefb99` | `finalized`; YES; `$1.0000`; expiration `0.3%`; `2026-01-13T16:48:15.222981Z` | YES (`0.3 > 0.2`) | MATCHED; eligible `True` |
| January 2026 | `KXCPI-26JAN-T0.1`; `KXCPI-26JAN`; rules `6c8f57e912985c25aa45b43592d22f377eb09d0273b45128d58ac91dc164193a`; derived version `historical-market-rules-v1:6c8f57e912985c25aa45b43592d22f377eb09d0273b45128d58ac91dc164193a`; semantic `5306fef3d70331a75a991e62bd0eed2fe9fe27475a695384a6eccb3bac6b4e06` | `finalized`; YES; `$1.0000`; expiration `0.2`; `2026-02-13T15:08:40.596381Z` | YES (`0.2 > 0.1`) | MATCHED; eligible `True` |

The three selected markets produce empirical MATCHED SettlementRecords and
eligible training labels. The API exposes explicit `status: finalized` and
non-null `settlement_ts`; P7 accepts that terminal status rather than inferring
finality from result presence. The API exposes no separate rules-version field;
the exact historical rules identity is therefore the content-addressed market
rules SHA listed above, which remains an independent-review limitation.
Sibling strikes share one release dependency and must not be counted as
independent evidence events.

## Codex Security findings repaired

The reviewed acquisition boundary now reconstructs one canonical URL from the
endpoint role and expected selector before the GET and during every later
validation. It rejects explicit ports (including `:443`), userinfo, queries,
fragments, alternate paths, percent-encoded alternatives, and role/selector
mismatches. Historical market, event, series, and contract-terms paths are
validated exactly.

Authority-bearing JSON uses duplicate-key rejection at every object depth and
rejects non-standard constants. Payload selection is role-specific: market,
event, and series responses require their exact reviewed wrapper shape, with
the documented event `markets` list, and conflicting wrappers or top-level
authoritative fields fail closed. These repairs address the two Codex Security
medium findings from the preceding exact head; this receipt records the repair
but does not itself constitute the independent security re-review.

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
