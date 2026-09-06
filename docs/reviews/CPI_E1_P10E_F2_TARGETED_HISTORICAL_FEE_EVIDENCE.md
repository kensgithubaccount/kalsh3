# CPI-E1-P10E-F2 — targeted historical fee evidence acquisition

## Result

F2 independently verified `origin/main` at `2027c82d028d4cd08655e2f099157013952f927a`, tree `cf2ce666c954a6e5894c762a0ae2a3eeeaaba63b`, and searched only for authority capable of binding the two frozen P10E execution timestamps. The exact candidate set was not broadened.

The official Kalshi series-fee-history endpoint returned an empty historical array for both `CPI` and `KXCPI`. Its documentation dates the endpoint to September 21, 2025, so it cannot establish the 2023 regime and does not itself supply an August 2025 record. The official October 1, 2025 fee schedule remains later locator evidence. CFTC rulebook and filing-index searches found no positive effective-date schedule or legally sufficient continuity principle for either timestamp.

No new load-bearing source was found. Existing endpoint evidence remains limited to `CFTC-49335-FINAL` effective 2022-09-22 and `MD-1:25-CV-01283-28-1` dated 2025-05-06, with formula `round_up(0.07 * C * P * (1-P))` and next-cent rounding. Matching formulas do not establish continuity. Minimum/cap, per-order/per-fill behavior, YES/NO applicability, and CPI-specific exceptions remain unresolved.

## Coverage

| Candidate | Classification | Basis |
|---|---|---|
| CPI-23AUG / CPI-23AUG-T0.6 / 2023-09-13T12:25:00Z | UNKNOWN | No positive continuity authority from the 2022 endpoint to the execution date |
| KXCPI-25JUL / KXCPI-25JUL-T0.2 / 2025-08-12T12:29:00Z | UNKNOWN | No August 2025 schedule; October 2025 schedule is post-date only |

The deterministic artifact is `docs/reviews/artifacts/cpi-p10e-f2-fee-authority-recovery/coverage.json`; the bounded search/provenance record is `search.json`. Existing raw authority bytes and hashes are unchanged; no caller-authored text was promoted to canonical source evidence.

No P&L, fee amount, settlement outcome, or trade was calculated or used.

## Classification

**BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE**
