# M19 Final Audit

**Audit date:** 2026-08-11
**Scope:** M0–M18 repository-wide offline audit
**Decision:** M19 offline engineering complete; **NOT APPROVED FOR PRODUCTION ACTIVATION**

## Immutable safety result

| Control | Audited state |
|---|---|
| Production | **DISARMED** |
| Bounded autonomy | **OFF** |
| Production-write credential | **NONE** |
| Live production mutation | **NONE** |
| Real-money order | **NONE** |
| Human production acceptance | **PENDING** |

No credential was requested, created, installed, loaded, or handled. No live mutation was attempted. M13
limits remain unchanged: $1,000 bankroll, $700 protected reserve, $300 active capital, $100 aggregate open
risk, $10 market loss, and $25 related-event risk. M19 adds no activation transition.

## Material findings and corrections

1. **High — production-read redirect credential exposure:** the concrete urllib transport followed redirects
   by default and did not positively validate canonical Trade API paths. It now uses a no-redirect opener,
   rejects URLs, authority confusion, encoded traversal, double slashes, and fragments, requires JSON, and
   detects oversized bodies rather than parsing a silently truncated response.
2. **High — fractional canary persistence used SQLite binary floating point:** the one-contract invariant
   used `CAST(... AS REAL)`. It now persists millionth-contract integer atoms, migrates legacy rows only
   when conversion is exact, rejects corrupt/over-precision/regressing values, and does not count duplicate
   cumulative fill updates twice.
3. **High — public boundary exposed a generic signer-shaped protocol:** although injection was rejected,
   the public M15 boundary still modeled a byte-returning signing interface. The signer slot and protocol
   were removed; the public boundary has no signing oracle or injectable signer field.
4. **Medium — nested floats could enter canonical production bodies:** float rejection only inspected
   top-level values. Recursive validation now rejects floats in mappings, lists, and tuples.
5. **Medium — XML entity-expansion denial of service:** official-feed XML was size-bounded but accepted DTD
   and entity declarations. Both are rejected before parsing.
6. **Medium — password verification trusted stored scrypt parameters:** corrupt/hostile storage could request
   excessive work. Verification now accepts only the canonical bounded parameters and exact salt/hash sizes.
   TOTP verification now rejects malformed/non-ASCII/non-six-digit input without raising.
7. **Medium — owner UI contradicted implemented milestones and showed a dead ARM control:** Risk & Safety now
   accurately describes the implemented-but-disarmed execution architecture and replaces the disabled
   control with an explicit unavailable status.

Regression tests exercise each correction and the complete safety state.

## Audit lenses

### Architecture, security, execution, and risk

Research, forecasting, document/LLM evidence, signals, learning, and opportunity packages do not import the
production signer boundary. M13 remains deterministic and its hard policy is regression-tested. Demo and
production credential/origin/state domains remain separate. The public production boundary cannot accept a
signer, production execution remains an unconditional rejection, restart returns DISARMED/OFF, and unknown
mutations require reconciliation without retry. Durable uniqueness and transactional claims cover risk
authorizations, client order IDs, approvals, canary sessions, and execution journals.

Static review covered subprocess calls (fixed argv/no shell and anonymous descriptors), SQL construction
(parameterized application statements), URL/SSRF controls, XML, password/TOTP/recovery flows, encrypted
credential envelopes, CSRF/session cookies, CSP/HSTS/frame/referrer headers, support/log/metric redaction,
network topology, and encrypted backups. No material offline security finding remains open.

### Finance, quant, data, and models

Money, probability, price, quantity, fees, exposure, limits, and accounting domains use `Decimal`; timeout
and scheduling seconds may use float. YES/NO complements, V2 bid/ask translation, fixed-point requests,
fees/slippage, partial fills, loss stops, reserve protection, related-event concentration, settlement,
queue assumptions, and P&L have deterministic regression coverage. Simulated maker fills remain explicitly
unvalidated rather than certain. Walk-forward/event grouping, point-in-time availability, gaps, feature
vintages, effective sample counts, multiple-testing controls, baseline comparison, and adverse scenarios
are covered. `INDEPENDENT_FUNDAMENTAL` explicitly rejects Kalshi- or external-market-derived features.
Learners can propose bounded research weights only and cannot alter M13 limits or production influence.

### Operations, product, compliance, and capital allocation

Compose and scripts were syntax/static reviewed for private networks, signer isolation, bounded logs,
health checks, restart/resource limits, persistent storage, encrypted backup and network-isolated restore.
Dependency failure, monitoring loss, clock regression, disk/cost pressure, queue saturation, poison work,
API drift, and unknown activity fail closed. Owner surfaces distinguish synthetic, replay, research,
production-read, demo/mock funds, and unavailable real production; they do not claim live evidence, edge,
profit, or readiness. The $700 reserve and all M13 limits remain authoritative. Compliance UNKNOWN/HOLD,
global halt, or kill state cannot be overridden by owner approval.

## Final readiness matrix

| Area | Status | Evidence boundary |
|---|---|---|
| Repository architecture and static isolation | OFFLINE VERIFIED | Code, schema, and adversarial tests |
| Deterministic risk and fixed-point arithmetic | OFFLINE VERIFIED | Decimal/property/load fixtures |
| Historical replay, forecasting, learning, opportunity, simulation | OFFLINE VERIFIED / MOCK VERIFIED | No claim of live profitability |
| Demo execution architecture | MOCK VERIFIED | Live Kalshi Demo acceptance NOT VERIFIED |
| Production sign-and-send architecture | OFFLINE VERIFIED / MOCK VERIFIED | Synthetic keys/sender only |
| Supervised canary and bounded autonomy | OFFLINE VERIFIED | Canary NONE; autonomy OFF |
| Operations hardening | OFFLINE VERIFIED | Live Oracle/Docker/alerts/restore NOT VERIFIED |
| PostgreSQL-marked contract | OFFLINE VERIFIED | Live multi-worker runtime NOT VERIFIED locally |
| Official Kalshi API compatibility | NOT VERIFIED | Official sites unreachable through environment tunnel |
| Production read and reconciliation | NOT VERIFIED | No live read credential installed |
| Browser responsive/screenshot acceptance | NOT VERIFIED | Browser tooling unavailable |
| Bandit/detect-secrets/pip-audit/Trivy/SBOM execution | NOT VERIFIED locally | Tools unavailable; CI configured |
| Production-write credential | NONE | Deliberately absent |
| Real-money behavior | NONE | No production mutation performed |
| Human acceptance | PENDING | Owner/deployment review required |

Current GitHub CI result for this commit is **NOT VERIFIED** until the pushed PR jobs execute. Local Ruff,
formatting, strict mypy, all 341 pytest cases, PostgreSQL-marked contract, shell syntax, YAML parsing,
dependency-lock checking, and credential-pattern review pass. The `make verify` wrapper cannot bootstrap its
build backend because this environment blocks PyPI; its constituent available checks were run directly.
Unavailable tools are not represented as passing.

## Prioritized remaining human actions

### P0 — required before any consideration of activation

1. Review this audit and preserve production DISARMED/autonomy OFF.
2. Deploy the exact reviewed commit on the hardened Oracle target; verify firewall, TLS/Caddy, time sync,
   secret mounts, signer isolation/egress, volumes, resource limits, telemetry, and restart DISARM.
3. Run migrations and real PostgreSQL concurrency/restart/corruption-recovery tests; exercise Redis, NATS,
   archive storage, queue backpressure, disk exhaustion, and monitoring loss.
4. Execute encrypted backup plus isolated restore drill and independently verify reconciliation/journals.
5. Re-fetch and hash current official Kalshi OpenAPI, AsyncAPI, and changelog; review every auth, fixed-point,
   fee, route, lifecycle, sequencing, order-group, rate-limit, and queue-position change.
6. Enroll only a separate production **read** credential and complete subaccount-0 read/reconciliation
   acceptance. Do not enroll a write credential.
7. Complete real Kalshi Demo lifecycle/restart/unknown/fill/cancel accounting acceptance.
8. Run GitHub security/supply-chain jobs and investigate every Bandit, secret, audit, Trivy, and SBOM result.

### P1 — required before a separately authorized first real-money canary

1. Establish current model/source/strategy eligibility from genuine out-of-sample settled evidence.
2. Complete browser desktop/tablet/mobile accessibility and security acceptance.
3. Review live operational costs, alert delivery, incident exercise, API drift, and fresh reconciliation.
4. Conduct an independent security/risk/compliance review of deployment and exact canary candidate.
5. Only after all P0/P1 gates pass, use the protected owner workflow to enroll a separately scoped write
   credential and review one immutable one-contract preview. Enrollment itself must not arm production.
6. Obtain a new, explicit human instruction for that exact canary. Nothing in M19 supplies that approval.

## Production activation checklist — do not execute during M19

- [ ] Reviewed commit deployed with clean CI/security/SBOM results.
- [ ] Hardened runtime, network isolation, exact Kalshi egress, TLS, NTP, monitoring, and restart verified.
- [ ] PostgreSQL concurrency, backup/restore, queues, storage, and reconciliation live verified.
- [ ] Official current API contracts hashed and compatible.
- [ ] Live production-read subaccount 0 fully reconciled; no unknown activity.
- [ ] Live Demo execution acceptance complete.
- [ ] Strategy/model/source evidence eligible; fees and after-cost economics current.
- [ ] Global halt clear; compliance clear; all kills clear; M13 limits unchanged.
- [ ] Owner authenticated with password, TOTP, CSRF, recent session, and explicit warning.
- [ ] Separate production-write credential enrolled directly into isolated signer with exact scopes/account.
- [ ] Exact immutable one-contract preview reviewed; fresh M13 authorization issued after human approval.
- [ ] Separate explicit human instruction received for that exact order.
- [ ] Post-send reconciliation, automatic DISARM, and no-second-order behavior supervised.

Unchecked means **no production mutation**. There is no administrative override.

## Final threat and risk register

| Rank | Residual risk | Current control / required closure |
|---|---|---|
| HIGH | Official Kalshi contract may have drifted | Approval blocked; fetch/hash/review official contracts live |
| HIGH | Runtime signer/network/container isolation unproven | No credential, DISARMED, internal signer network; verify target deployment |
| HIGH | Live account state and external activity unknown | No production read; reconcile subaccount 0 before any approval |
| HIGH | PostgreSQL/queue/restart behavior not live-chaos verified | Offline constraints only; run target concurrency and chaos drills |
| HIGH | Backup/object-store recovery not demonstrated | Encrypted tooling exists; perform and inspect isolated restore |
| HIGH | Strategy profitability/calibration not supported by real evidence | Production influence NONE; gather eligible settled evidence |
| MEDIUM | Security scanners/SBOM/container scan not run locally | CI gates configured; review actual GitHub results |
| MEDIUM | Browser/responsive UX not visually accepted | No activation controls; perform owner screenshot/accessibility review |
| MEDIUM | Alert delivery and monthly cost not observed live | Fail-closed readiness; verify paging and $25/$50 budget behavior |
| LOW | Offline SQLite stores differ from production PostgreSQL | PostgreSQL remains authoritative; retain parity/concurrency tests |

All HIGH items are activation blockers. Credential absence and DISARMED/OFF states limit current capital
exposure to **none**.

## Final conclusion

M0–M19 are complete at their documented offline/mock engineering gates after correction and re-audit.
The repository is suitable for external review and controlled deployment validation, **not live trading**.
Production activation, credential enrollment, autonomy, a canary, and any real-money order remain outside
this milestone and require later live evidence plus separate explicit human authorization.
