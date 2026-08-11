# M16 Supervised Production Canary — Cross-functional review

M16 implements the exact one-contract human-approval workflow offline. It does not install a credential, arm production, call M15 transmission, or execute a canary.

## Findings

- **Trader / CFO:** opening quantity must equal `1.00`; immutable preview/approval hashes bind market, BUY YES/NO, exact `$0.xxxx` price, fees, maximum loss, risk, reserve, rules, and client lineage. One unresolved-session uniqueness prevents averaging down, repricing, fallback candidates, or parallel opening orders.
- **Risk / compliance:** human approval is additive and cannot override fresh M13 failure, halt, compliance, kills, unknown activity, rules/candidate/fee changes, exchange pause, stale reconciliation, or missing real-evidence gates.
- **Security:** approval requires recent authenticated owner session, password, TOTP, CSRF, rate-limit clearance, exact confirmation, and a nonsecret proof reference. Approval is 60-second, owner-bound, preview-bound, and atomically single-use.
- **Distributed systems / SRE:** durable uniqueness enforces one unresolved canary. Restart always restores DISARMED; possibly submitted work becomes unknown/reconciliation-required and cannot generate another order.
- **Finance:** all domain amounts use `Decimal`; partial fills retain exact filled and remaining quantities summing to `1.00`. Only `REAL_PRODUCTION` may increment the durable first-50 fill counter; demo/paper fixtures cannot.
- **Data:** a conservative 30-second user-data timestamp gate supplements exchange/trading status, announcements classification, market/rules, API compatibility, live production reads, PostgreSQL and signer-runtime verification.
- **Product / UX:** the dedicated surface says REAL PRODUCTION / REAL MONEY, shows every gate separately, explains credential absence, offers no approval or ARM control, and gives dominant unknown-order safety text.
- **Quant:** acceptance reports distinguish operational lifecycle validation from strategy/forecast performance, which remains `NOT YET KNOWABLE` while unsettled.

## Acceptance report

- M16 CODE: OFFLINE VERIFIED
- M16 CANARY READINESS ENGINE: OFFLINE VERIFIED
- M16 REAL-EVIDENCE GATES: OFFLINE VERIFIED
- M16 M14 LIVE DEMO REQUIREMENT: OFFLINE VERIFIED
- M16 LIVE PRODUCTION READ REQUIREMENT: OFFLINE VERIFIED
- M16 EXCHANGE STATUS GATE: MOCK VERIFIED
- M16 USER-DATA FRESHNESS GATE: MOCK VERIFIED
- M16 API COMPATIBILITY GATE: OFFLINE VERIFIED
- M16 POSTGRESQL LIVE GATE: OFFLINE VERIFIED
- M16 SIGNER RUNTIME GATE: OFFLINE VERIFIED
- M16 ONE-CONTRACT LIMIT: OFFLINE VERIFIED
- M16 ONE-CANARY CONCURRENCY: OFFLINE VERIFIED
- M16 PREVIEW IMMUTABILITY: OFFLINE VERIFIED
- M16 HUMAN REAUTH: MOCK VERIFIED
- M16 TOTP: MOCK VERIFIED
- M16 CSRF: MOCK VERIFIED
- M16 HUMAN APPROVAL: MOCK VERIFIED
- M16 APPROVAL SINGLE-USE: OFFLINE VERIFIED
- M16 APPROVAL EXPIRY: OFFLINE VERIFIED
- M16 FINAL REVALIDATION: OFFLINE VERIFIED
- M16 FRESH M13 AUTHORIZATION: MOCK VERIFIED
- M16 M15 BODY BINDING: MOCK VERIFIED
- M16 PRICE-CHANGE INVALIDATION: OFFLINE VERIFIED
- M16 NO AVERAGING DOWN: OFFLINE VERIFIED
- M16 PARTIAL-FILL HANDLING: OFFLINE VERIFIED
- M16 UNKNOWN-SUBMISSION SAFETY: OFFLINE VERIFIED
- M16 POST-SEND RECONCILIATION: MOCK VERIFIED
- M16 AUTO-DISARM: OFFLINE VERIFIED
- M16 FIRST-50-FILLS COUNTER: OFFLINE VERIFIED
- M16 GLOBAL HALT: MOCK VERIFIED
- M16 COMPLIANCE: MOCK VERIFIED
- M16 UI: OFFLINE VERIFIED
- M16 SECURITY REVIEW: OFFLINE VERIFIED
- M16 LIVE DEMO ACCEPTANCE: NOT VERIFIED
- M16 LIVE PRODUCTION READ: NOT VERIFIED
- M16 PRODUCTION WRITE CREDENTIAL: NONE
- M16 LIVE CANARY: NONE
- M16 REAL-MONEY ORDER: NONE
- M16 HUMAN ACCEPTANCE: PENDING

Live deployment, PostgreSQL concurrency, signer isolation, API compatibility, demo acceptance, production reads, and human acceptance remain genuine live gates rather than fixture-green claims. Production remains DISARMED.
