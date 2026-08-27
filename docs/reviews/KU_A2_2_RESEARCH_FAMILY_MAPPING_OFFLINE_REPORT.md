# KU-A2.2 Research-Family Mapping & Offline Coverage Report

## Authority boundary

KU-A2.2 is research-only. It adds reviewed structural family interpretation and an offline
descriptive report over canonical KU-A2.1 receipts. It does not add research-readiness,
G1-G7 decisions, forecasts, models, EV/economics, opportunity ranking, capital allocation,
lifecycle promotion, execution authority, credentials, account access, or network acquisition.

A family label is descriptive. `MAPPED` does not mean researchable, ready, official, high
quality, tradeable, or economically attractive.

## Empirical artifact gate

**Status: EMPIRICAL_ARTIFACT_UNAVAILABLE.**

At canonical start `af3424e06b647a31fc3645923dd90185c00a6101`, the complete repository tree contains no
committed whole-exchange KU-A2.1 `SemanticSourceCoverageResult` artifact. The canonical
KU-A2.1 exact-head workflow run `33021832928` exposes only the security/supply-chain SBOM
artifact and no semantic/source coverage result. The CI workflow itself does not upload a
KU-A2.1 whole-exchange result.

Therefore this checkpoint implements and tests deterministic mapping/report machinery but
does not claim empirical family prevalence, rank families, or treat test fixtures as empirical
whole-exchange evidence. No public/live acquisition was performed for KU-A2.2.

## Reviewed taxonomy and rules

The v1 taxonomy is deliberately structural and conservative:

- `BINARY_THRESHOLD`: canonical A2.1 semantic status is `VALID`, product is `BINARY_EVENT`,
  payout is `SIMPLE_BINARY`, a reviewed single-threshold comparator is present, and the
  canonical threshold value is present.
- `BINARY_INTERVAL`: the same binary/valid requirements, with comparator `between` and both
  canonical bounds present.
- `BINARY_PROPOSITION`: the same binary/valid requirements, with no numeric threshold or
  bounds and comparator absent/`none`.
- `SCALAR_OR_PARTIAL`: canonical A2.1 semantic status is `VALID`, product is
  `SCALAR_OR_PARTIAL`, and payout is `SCALAR_OR_PARTIAL`.
- `UNKNOWN_UNMAPPED`: no reviewed rule is proven, including all quarantine outcomes.

The mapping intentionally does not use title text, category names, series labels, settlement
source hostnames, M27B advisory routes, or keyword similarity to grant a family. Settlement
source presence is descriptive only and confers no authority or researchability.

Every A2.1 parsed or quarantine outcome receives exactly one A2.2 mapping. Unknown evidence
is retained explicitly; there are no silent drops.

## Offline report semantics

KU-A2.2 adds only family counts, mapped/unmapped counts, and settlement-source-presence
counts. Lifecycle, semantic status, reason provenance, product, payout, strike, category,
series, recurrence, settlement-source-origin, and unknown/unavailable aggregates are carried
through directly from the canonical KU-A2.1 manifest rather than recomputed under new
semantics.

The A2.2 identities bind the exact A2.1 result, A2.1 manifest, KU-A1 census manifest,
KU-A1 coverage manifest, capture, and source record/quarantine identities. Canonical A1/A2
identities are inputs to A2.2 and are never rewritten.
