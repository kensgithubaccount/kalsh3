# M13 Deterministic Risk — Cross-functional review

M13 is a deterministic, read-only financial safety boundary. Its successful result is **RISK CHECK PASSED / PASS_NEXT_GATE**. It is not trade, order, production, signer, or execution approval.

## Review findings

- **Risk / CFO:** immutable policy metadata and database constraints prevent model, learner, request, environment, or dashboard inputs from raising the $300 active-capital, $100 aggregate, $10 market, or $25 event limits. Dynamic active capacity preserves the $700 reserve and shrinks after loss.
- **Trader / Quant:** filled positions, all resting potential fills, fees, proposed full fill, and short-lived reservations enter projected risk. Expected fill probability and forecast quality provide no exposure credit. Related markets aggregate conservatively unless validated payout relationships justify otherwise.
- **Finance:** all financial domain calculations use `Decimal`; the experiment ledger excludes simulation and unexplained primary-account activity. Daily, weekly, monthly, and high-water-mark drawdown states are fee-inclusive and persist review requirements.
- **Security / Compliance:** the package imports no LLM, signer, document-intelligence, mutation, or execution component. Compliance UNKNOWN/HOLD and credential kills fail closed. The owner halt is safety-increasing, authenticated, CSRF-protected, confirmed, audited, durable, and performs no exchange mutation; reset requires strong reauthentication and a reason.
- **Distributed systems:** SQLite immediate transactions, unique client-order IDs, exact-intent hashes, reservations, five-second expiry, durable status, and conditional single-use consumption prevent concurrent cap oversubscription and authorization replay.
- **Post-M13 follow-up:** M14 added the explicit PostgreSQL SERIALIZABLE/row-lock/unique-claim transaction path required by the production architecture. Its static transaction contract is offline verified; PostgreSQL integration remains NOT VERIFIED locally because the runtime is unavailable.
- **Data engineering / SRE:** account reads must be explicitly scoped to subaccount 0. Stale/partial/auth/API failures, page incompleteness, unknown orders/positions, exposure mismatch, sequence gaps, and missing material inputs block new risk. Restart preserves holds, stops, reservations, and consumed/expired authorization state.
- **Product / UX:** Risk & Safety explains reserve, limits, loss stops, reconciliation, kills, compliance, halt, and fixture evaluations without an order-entry surface. Unavailable real state is never displayed as `$0`, and halt copy states that M13 cannot cancel exchange orders.

## Acceptance report

- M13 CODE: OFFLINE VERIFIED
- M13 HARD LIMIT IMMUTABILITY: OFFLINE VERIFIED
- M13 DECIMAL FINANCIAL MATH: OFFLINE VERIFIED
- M13 EXPERIMENT LEDGER: OFFLINE VERIFIED
- M13 PORTFOLIO EXPOSURE: OFFLINE VERIFIED
- M13 RESTING ORDER EXPOSURE: OFFLINE VERIFIED
- M13 MARKET RISK: OFFLINE VERIFIED
- M13 EVENT RISK: OFFLINE VERIFIED
- M13 AGGREGATE RISK: OFFLINE VERIFIED
- M13 PROTECTED RESERVE: OFFLINE VERIFIED
- M13 ACTIVE CAPITAL CAP: OFFLINE VERIFIED
- M13 DAILY LOSS STOP: OFFLINE VERIFIED
- M13 WEEKLY LOSS STOP: OFFLINE VERIFIED
- M13 MONTHLY LOSS STOP: OFFLINE VERIFIED
- M13 EXPERIMENT DRAWDOWN STOP: OFFLINE VERIFIED
- M13 RECONCILIATION: MOCK VERIFIED
- M13 UNKNOWN ORDER SAFETY: OFFLINE VERIFIED
- M13 EXTERNAL ACTIVITY SAFETY: OFFLINE VERIFIED
- M13 GLOBAL HALT: OFFLINE VERIFIED
- M13 COMPLIANCE HOLD: OFFLINE VERIFIED
- M13 FOUR KILL CATEGORIES: OFFLINE VERIFIED
- M13 RISK-REDUCING PATH: OFFLINE VERIFIED
- M13 RISK DECISION: OFFLINE VERIFIED
- M13 SHORT-LIVED AUTHORIZATION: OFFLINE VERIFIED
- M13 SINGLE-USE: OFFLINE VERIFIED
- M13 CONCURRENCY / RESERVATIONS: OFFLINE VERIFIED
- M13 CLIENT ORDER ID UNIQUENESS: OFFLINE VERIFIED
- M13 ORDER GROUP POLICY: MOCK VERIFIED
- M13 REPLAY / TIME SAFETY: OFFLINE VERIFIED
- M13 RESTART PERSISTENCE: OFFLINE VERIFIED
- M13 LARGE-SCALE TEST: OFFLINE VERIFIED
- M13 UI: OFFLINE VERIFIED
- M13 SECURITY ISOLATION: OFFLINE VERIFIED
- M13 REAL ACCOUNT RECONCILIATION: NOT VERIFIED
- M13 PRODUCTION MUTATION CAPABILITY: NONE
- M13 HUMAN ACCEPTANCE: PENDING

Browser screenshot review remains PENDING because browser tooling is unavailable. Live account reconciliation remains NOT VERIFIED because no live read credentials are present. These limitations do not weaken the fail-closed offline gate.
