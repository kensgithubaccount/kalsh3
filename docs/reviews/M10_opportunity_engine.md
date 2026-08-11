# M10 After-Cost Opportunity Engine Review

## Acceptance

- YES/NO economics, orderbook normalization, fixed-point/fractional depth, fee-policy fail-closed boundary, historical selection, rounding, taker walk, maker/fill uncertainty, conservative gating, liquidity/decay/correlation, cross-venue research, ranking, replay manifests, 50k/5k fixture and UI: **OFFLINE VERIFIED**.
- Live fee formula/current official examples and live economics: **NOT VERIFIED**. Maker fill probability: **UNVALIDATED**.
- Real forecast evidence: **INSUFFICIENT REAL EVIDENCE**. Production influence: **NONE**. Human acceptance: **PENDING**.

## Cross-functional findings

- **Trader / quant:** YES asks complement NO bids and NO asks complement YES bids. Midpoint is never executable. Both outcomes use point and conservative probabilities; point EV, break-even, costs and conservative EV remain separate. Maker conditional/attempt EV remains unavailable without fill/adverse-selection inputs.
- **Finance:** Fee policies are effective-dated and must be verified; unknown formulas cannot pass. Synthetic coefficients are fixture-only. Theoretical fee, balance rounding and rebates remain separate. Depth capacity never chooses a position size.
- **ML / data science:** Forecast kind and reference overlap are retained, preventing market/Polymarket double counting. Fill, slippage and decay quality are explicit rather than false precision.
- **Data engineering:** Candidates and datasets are content-addressed/frozen. Raw book lineage, fee version, as-of timestamps, learning/source configuration and gap state reproduce candidate generation.
- **Security / SRE:** Opportunity code has no signer, account gateway, risk authorization, mutation, order, sizing or execution API. Stale books, temporal skew, rule mismatch, unknown fee and incomplete depth fail closed.
- **Product / UX / CFO:** Cards separate fair interval, executable price, raw difference, fees, slippage, conservative value, liquidity and age. Ranking is secondary; synthetic/replay/live modes are explicit and no expectancy/profit claim is made.

No M10 object authorizes or describes an approved trade.
