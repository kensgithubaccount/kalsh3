# M12 Full Dashboard Product and UX Hardening Review

## Acceptance

| Area | Status | Result |
|---|---|---|
| Information architecture | OFFLINE VERIFIED | Main navigation matches the required owner workflow; forecasting and execution simulation live under Advanced. |
| Global state | OFFLINE VERIFIED | Exactly one of the documented states is shown. Without execution capability M12 derives only `LEARNING`, `NEEDS ATTENTION`, or `HALTED`. |
| Overview | OFFLINE VERIFIED | First viewport answers whether trading is possible, account money state, activity, changes, and actionable blockers. Unknown risk is unavailable, never zero. |
| Opportunities | OFFLINE VERIFIED | Ranked research cards retain fair value, interval, executable price, costs, conservative value, liquidity, age, and rejection reasons. |
| Breaking Now | OFFLINE VERIFIED | Verification, corroboration, source class, timing, market reaction, noise, and zero influence remain explicit. |
| Market detail | OFFLINE VERIFIED | Settlement interpretation, versions, forecasts, executable bid/ask, evidence, uncertainty, what changes the forecast, decision, and audit history are separated. |
| Sources / Learning | OFFLINE VERIFIED | Source health, coverage, latency, cost, missing real incremental evidence, governance, weights, and production influence are stated honestly. |
| Portfolio | OFFLINE VERIFIED | Cash/equity use dollar units; unresolved exposure and worst case remain unavailable pending reconciliation. |
| Orders & Trades | OFFLINE VERIFIED | Read-only lifecycle states and reconciliation-required state exist; no mutation control exists. |
| Reports | OFFLINE VERIFIED | Daily, weekly, monthly, and sanitized support report purposes and scheduling state are clear. |
| Risk & Safety | OFFLINE VERIFIED | Immutable limits and blockers are shown; dangerous controls are disabled with accessible explanations. No authorization is performed. |
| System / Advanced | OFFLINE VERIFIED | Release, API/spec, data, continuity, workers, budgets, and advanced research surfaces are organized without raw JSON in primary views. |
| Accessibility | OFFLINE VERIFIED | Skip link, landmarks, one H1, current-page navigation, focus styles, touch targets, reduced-motion handling, and disabled-control descriptions have deterministic tests. |
| Responsive behavior | OFFLINE VERIFIED | Fluid typography, bounded grids, mobile single-column layouts, content wrapping, and navigation overflow guards have deterministic CSS tests. |
| Security regression | OFFLINE VERIFIED | Authentication, CSRF, secure cookies, CSP/HSTS/clickjacking/referrer headers, escaping, downloads, and non-mutation architecture tests pass. |
| Desktop/tablet/mobile screenshot review | PENDING | Browser and Playwright executables are unavailable in this environment; HTML/CSS tests are not represented as visual QA. |
| Human acceptance | PENDING | Owner product review remains required. |
| Production writes | OFF | Dashboard contains no order, signer, arm, cancel, amend, or risk-authorization path. |

## Cross-functional review

- **Product:** The owner sees a single system state and can answer “can it trade?” immediately. Research pages never imply approval or profitability.
- **UX:** Primary pages use plain language, consistent metrics/cards, meaningful empty/stale/error states, responsive layout, and secondary disclosure for advanced provenance.
- **Accessibility:** Keyboard focus, skip navigation, semantic landmarks, active navigation, touch targets, contrast-oriented colors, and reduced-motion behavior are implemented and fixture-tested.
- **Trader / finance:** Executable prices remain distinct from forecasts, money is labeled in dollars, and unavailable exposure is never rendered as `$0`. No recommended size appears.
- **Data / ML:** Synthetic, replay, real research, and unavailable evidence states remain separate. No fixture result is promoted as real evidence.
- **Security / SRE:** The private security boundary remains intact. Unknown routes return a real 404; stale state remains prominent; no dangerous control is live.
- **CFO:** Cost, capacity, drawdown, reserve, and evidence limitations remain visible without implying realized ROI.

Material offline findings were fixed. Visual and owner acceptance remain pending rather than being inferred from template tests.
