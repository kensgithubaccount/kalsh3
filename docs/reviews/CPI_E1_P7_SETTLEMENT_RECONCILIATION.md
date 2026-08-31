# CPI-E1-P7 — Exact Kalshi Settlement Reconciliation

## Scope and provenance

- Requested canonical base: `11272005b04c9ccc28f95c7b5d3e9c864ee8404a`.
- Requested canonical tree: `fbabf52064fc5fbdcad78b1f0db345a3b7462598`.
- The base commit object is not available in this clone. The P6 head used for
  this additive branch has exactly the requested tree (`fbabf520…`); `origin/main`
  is pre-P6. This limitation is recorded rather than hidden.
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
| July 2025 | `5b869d4365bc0f58db9814e3da09105f0fd944e4bbf16c39b5511f774a03dc4b` | `0.2` | Not materialized from the saved empirical artifact in this clone |
| December 2025 | `8351af0db99f8b1e338abe1b33cb062a70e61d2b154c0ec26aaed964f52b489e` | `0.3` | Not materialized from the saved empirical artifact in this clone |
| January 2026 | `3b46aebecd5aa2d66f6f8abc38e47381e180a73db6cf87313ecc8eeddebd69f8` | `0.2` | Not materialized from the saved empirical artifact in this clone |

The P6 tests and parser preserve the exact observation ID when the reviewed
P4/P5A artifact is available. P7 does not accept a caller-authored or
reconstructed observation as a substitute.

## Historical-evidence audit and empirical reconciliation

Repository search covered historical market snapshots, archived API responses,
event/market metadata, settlement responses, KXCPI identifiers, rules hashes,
lifecycle/determination artifacts, M26 infrastructure, and archive-backed
evidence. The repository contains generic settlement primitives and M26C
consumers, but no exact immutable historical Kalshi market/rules/finalization
artifact for any target release. No authenticated or trading API was used, and
no external artifact was imported.

| Release | Exact Kalshi market/event/rules evidence | Exchange final | P6-derived result | SettlementRecord |
|---|---|---|---|---|
| July 2025 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| December 2025 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| January 2026 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

Therefore zero empirical SettlementRecords are MATCHED, MISMATCH, or eligible
training labels. Synthetic fixtures prove the code path only and are not
historical claims. Sibling strikes, if later discovered for one release, share
one release dependency and must not be counted as independent evidence events.

## Tests and G3 implication

The focused suite covers exact matching, first-class mismatch, identity/rules
binding, all supported Decimal comparators, wrong period, non-final/disputed
state, contradictory binary values, forged raw hashes, and caller-mutated
determinations. The implementation has no model, economics, probability,
profitability, position-sizing, allocation, execution, credential, or risk
authority. `research_only = true` and `production_influence = 0` remain fixed.

G3 is **UNKNOWN/BLOCKED for the requested historical cohort** because exact
exchange artifacts are absent. This checkpoint does not generalize to all CPI
history or all Kalshi markets. Independent security and architecture review is
required before any later use.
