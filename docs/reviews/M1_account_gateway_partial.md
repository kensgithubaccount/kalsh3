# M1 Account Control Center — Code/UX Acceptance Review

## Result

Code and UX are offline verified. Current contracts are externally verified from official documentation
through owner-provided facts; direct in-environment spec fetch remains blocked. Production read and human
acceptance are pending, so M1 is not represented as live-complete and M2 has not started.

## Cross-functional review

- Engineering/data/SRE: complete pagination cannot silently truncate; timeout, auth, rate, server, repeated
  cursor, malformed page, and page-two failures fail closed. Worker and web processes are separate and
  persist attempt/success/status/reason. Docker/restart/restore execution remains external.
- Security/compliance: exact read scope is mandatory. Password scrypt, TOTP, one-use recovery codes, login
  throttling, hashed sessions, CSRF, HTTPS cookies/headers, authenticated credential encryption, audit events,
  and export redaction are tested. Static architecture tests reject external mutation verbs.
- Portfolio/risk/trading/CFO: subaccount 0 and Decimal normalization are explicit. Write-rate limits are
  informational only; M1 cannot spend them. No forecast, opportunity, order, or capital authorization exists.
- Product/design/UX: overview answers connection/trading/cash/value/activity/health first; portfolio uses cards
  and honest empties; system explains read/write budgets; stale data is visibly warned; reports download.
  Responsive CSS covers desktop/tablet/mobile breakpoints, but final browser screenshots require a browser.
- Quant/ML/data science: M1 adds no learned signal and cannot create false backtest evidence.

## Material decisions fixed

Legacy cent-cost settlement fields are rejected, identifiers/raw responses are excluded from support exports,
and OpenSSL signing was retained only after the documented ADR comparison. No material offline finding remains.
