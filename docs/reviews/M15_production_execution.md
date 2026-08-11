# M15 Production Execution Path — Cross-functional security review

M15 implements the production execution architecture offline, chooses a sign-and-send security boundary, and remains permanently DISARMED. No production write credential is installed, no live sender exists, and no production or real-money mutation was executed.

## Review findings

- **Security:** sign-only was rejected because Kalshi's exchange signature does not cover the body. The selected boundary canonicalizes, validates, signs, and gives the same immutable bytes to a fixed transport; it never returns raw signatures. Typed operations prevent arbitrary-signing-oracle, host, path, redirect, query, and authority expansion attacks.
- **Distributed systems:** the journal precedes the offline irreversible-boundary simulation. Authorization, client ID, execution ID, and envelope claims are unique; concurrent ownership is single-use. Timeout, 429, 5xx, oversized response, and restart resolve to reconciliation-required without mutation retry.
- **Risk / CFO:** M13 authorization ID, decision, exact intent, portfolio and reconciliation hashes, TTL, market/rules state, pause state, global halt, compliance, kills, order group, and client uniqueness are revalidated immediately before the boundary. The signer cannot reinterpret price, quantity, correlation, reserve, or financial limits.
- **Trader / finance:** V2 bodies contain fixed-point strings and explicit subaccount/exchange index zero. BUY YES maps to a YES bid; BUY NO maps to a YES ask at the exact complement. Amend requires a newly bound intent; cancellation/decrease remain typed known-order operations.
- **Credential isolation:** production-read, demo-write, and production-write types are not interchangeable. The future fixture-only enrollment validator requires exact `read`+`write` scopes, production account 0, strong authentication, explicit warning, and a sealed result. It is not wired to a route and live enrollment remains unavailable.
- **SRE:** restart always restores DISARMED. The signer has no public port, uses a private network, dedicated user, read-only filesystem, dropped capabilities, no-new-privileges, tmpfs, health check, and resource limits. Current internal-only networking intentionally prevents live egress; future supervised deployment must add narrowly controlled production-host egress.
- **Product / UX:** System separates implementation from activation: credential NOT INSTALLED, signer DISARMED, orders DISABLED, and real-money order NO. There is no ARM button or signer/browser endpoint, and research services do not import the boundary.
- **Compliance:** offline fixtures are the only path that can exercise sign-and-send. The public boundary always rejects. No production key, sender, RFQ, batch create, API-key mutation, or autonomous activation exists.

## Adversarial review

Offline tests reject body, count, price, ticker, side, expiry, group, intent, host, subaccount, exchange-index, path traversal, double encoding, query/path confusion, batch/RFQ authority, stale/reused authorization, concurrent claim, credential-class confusion, malicious environment arming, pause/rules/reconciliation/halt/compliance/kill changes, and browser/research signer access. Synthetic RSA-PSS operations use ephemeral keys held in anonymous memory file descriptors.

## Acceptance report

- M15 CODE: OFFLINE VERIFIED
- M15 SIGNER ARCHITECTURE ADR: OFFLINE VERIFIED
- M15 BODY-BINDING SAFETY: OFFLINE VERIFIED
- M15 PRIVATE-KEY ISOLATION: OFFLINE VERIFIED
- M15 CREDENTIAL CLASS ISOLATION: OFFLINE VERIFIED
- M15 PRODUCTION HOST ALLOWLIST: OFFLINE VERIFIED
- M15 METHOD/PATH ALLOWLIST: OFFLINE VERIFIED
- M15 RSA-PSS SIGNING: MOCK VERIFIED
- M15 REQUEST CANONICALIZATION: OFFLINE VERIFIED
- M15 INTENT HASH BINDING: OFFLINE VERIFIED
- M15 M13 AUTHORIZATION BINDING: MOCK VERIFIED
- M15 SINGLE-USE: OFFLINE VERIFIED
- M15 TIMESTAMP FRESHNESS: OFFLINE VERIFIED
- M15 SUBACCOUNT 0: OFFLINE VERIFIED
- M15 FIXED-POINT V2 TRANSLATION: MOCK VERIFIED
- M15 CREATE PATH: MOCK VERIFIED
- M15 CANCEL PATH: MOCK VERIFIED
- M15 AMEND PATH: MOCK VERIFIED
- M15 DECREASE PATH: MOCK VERIFIED
- M15 UNKNOWN-RESPONSE SAFETY: MOCK VERIFIED
- M15 SUBMISSION JOURNAL: OFFLINE VERIFIED
- M15 RESTART SAFETY: OFFLINE VERIFIED
- M15 CONCURRENCY: OFFLINE VERIFIED
- M15 RATE-LIMIT SAFETY: OFFLINE VERIFIED
- M15 GLOBAL-HALT INTEGRATION: MOCK VERIFIED
- M15 COMPLIANCE INTEGRATION: MOCK VERIFIED
- M15 KILL-STATE INTEGRATION: MOCK VERIFIED
- M15 SIGNER NETWORK ISOLATION: OFFLINE VERIFIED
- M15 CONTAINER HARDENING: OFFLINE VERIFIED
- M15 SECRET REDACTION: OFFLINE VERIFIED
- M15 SECURITY RED TEAM: OFFLINE VERIFIED
- M15 POSTGRES INTEGRATION: NOT VERIFIED
- M15 UI: OFFLINE VERIFIED
- M15 PRODUCTION WRITE CREDENTIAL: NONE
- M15 PRODUCTION STATE: DISARMED
- M15 LIVE PRODUCTION WRITE: NONE
- M15 REAL-MONEY ORDER: NONE
- M15 HUMAN ACCEPTANCE: PENDING

Docker/PostgreSQL runtime integration, browser screenshot review, live signer validation, and human acceptance remain unverified or pending. These limitations do not create an activation path.
