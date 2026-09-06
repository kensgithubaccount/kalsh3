# CPI-E1-P10E-F1 — historical fee authority recovery

## Result

The prior P10E Phase 0 work was recovered exactly from branch
`cpi-e1-p10e-phase0`, head `04631b884399325e140ab6ce1d37aed0c4fd9f39`, tree
`98e0d6e4df9cc80ae817d071f3fc835a011bd0bc`. Its methodology artifact is
`docs/reviews/artifacts/cpi-p10e-phase0-after-cost-spec/spec.json`, digest
`9b26a6b96c1b1dbb41125704953a4bc527c77e6010ee17c4fd28dd5a8a23924a`, and its
exact fee blocker is **BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE**.

The frozen P10E rule produces two disagreement candidates. Both are NO-side
marketable crossing/taker candidates at their P10D sibling cutoffs:

| Event | Sibling | Frozen execution timestamp | Reuters side | Fee coverage |
|---|---|---|---|---|
| CPI-23AUG | CPI-23AUG-T0.6 | 2023-09-13 12:25:00 UTC | NO | UNKNOWN |
| KXCPI-25JUL | KXCPI-25JUL-T0.2 | 2025-08-12 12:29:00 UTC | NO | UNKNOWN |

No settlement timestamp, outcome, or economic result was used to select these
rows. No P&L or fee amount was calculated.

## Authority finding

The existing reviewed P9B package proves two endpoint snapshots:

- `CFTC-49335-FINAL`, effective 2022-09-22, general taker formula
  `round_up(0.07 * C * P * (1-P))`, rounded to the next cent.
- `MD-1:25-CV-01283-28-1`, dated 2025-05-06, with the same formula and
  rounding in a later snapshot.

P9B explicitly refuses to infer continuity between those endpoints. The
2023-09-13 candidate is therefore `UNKNOWN` in
`UNPROVEN_BETWEEN_CFTC_49335_AND_MD_28_1`. The 2025-08-12 candidate is
`UNKNOWN` in `OCT_2025_LOCATOR_ONLY`; the later locator/current fee page does
not positively bind August 2025.

The evidence does not positively establish, for either candidate, minimum or
cap rules, order-level versus fill-level application, exact precision beyond
the endpoint statement, CPI-specific exceptions, or any maker regime. The
strategy requires taker/crossing authority; maker fees and rebates are not
substituted.

The deterministic coverage artifact is
`docs/reviews/artifacts/cpi-p10e-f1-fee-authority-recovery/coverage.json`.
It records both candidates, source identities, raw hashes, provenance, and the
hard completeness result. No new load-bearing source bytes were acquired, so
there are no new source-evidence digests beyond the existing P9B package.

## Classification

**BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE**

The P10E Phase 0 methodology artifact was not updated because fee authority did
not become complete. Strategy rule, cutoffs, roster, sizing, aggregation,
settlement binding, and denominator logic remain unchanged.

