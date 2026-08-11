# M17 Bounded Autonomy — Offline Review

## Scope and decision

M17 implements only a non-active governance architecture for evaluating whether a future, separately
approved bounded-autonomy capability could be considered. `AutonomyState` intentionally has one member:
`OFF`. There is no activation transition, execution adapter, signer dependency, production credential,
environment switch, dashboard control, or network transport.

The M16 supervised canary remains the prerequisite operating mode. Offline fixtures cannot satisfy the
required evidence of a real supervised canary, live reconciliation, live signer isolation, current API
compatibility, PostgreSQL concurrency, production reads, strategy performance, or human governance.

## Safety architecture

- The immutable policy ceiling is one `1.00`-contract order, one concurrent order, and one market.
- Exact human approval remains mandatory; automatic scaling and production activation are rejected.
- Readiness records every absent gate explicitly and still evaluates to `OFF` when every synthetic gate
  is true. Evidence is advisory to future governance, never an authorization.
- Content-addressed readiness snapshots and off-only proposals provide immutable review provenance.
- SQLite and PostgreSQL schemas constrain autonomy to `OFF`, production to `DISARMED`, write credential
  state to `NONE`, and proposal influence to `NONE`.
- Restart recovery overwrites runtime state with `OFF` / `DISARMED`; malicious environment variables are
  not read.
- The package has no production execution, demo execution, signing, HTTP, or credential dependency.
- The owner UI shows every live evidence class as `NOT VERIFIED` and exposes no activation control.

## Cross-functional review

- **Trader/CFO:** policy cannot chase prices, add markets, scale size, or create concurrent orders.
- **Risk/compliance:** evidence and human governance cannot bypass M13, holds, halts, kills, or
  reconciliation.
- **Security:** no signer oracle, private key, transport, activation endpoint, or environment-based arm
  mechanism exists.
- **Distributed systems/SRE:** database constraints and restart recovery preserve `OFF` / `DISARMED`;
  proposals cannot mutate runtime authority.
- **Quant/data:** offline evidence is labeled as such and cannot stand in for live canary or strategy
  evidence.
- **Product/UX:** the screen says `AUTONOMY OFF`, `DISARMED`, credential `NONE`, and production influence
  `NONE`; no ready-to-trade language or activation button exists.

## Acceptance

- M17 code, policy ceiling, readiness engine, immutable snapshots, off-only proposals, persistence,
  restart behavior, malicious-environment resistance, static isolation, migration constraints, and UI:
  **OFFLINE VERIFIED**.
- Live supervised canary, live production reads/reconciliation, live PostgreSQL, live signer isolation,
  current official API compatibility, and strategy evidence: **NOT VERIFIED**.
- Autonomy: **OFF**. Production state: **DISARMED**. Production write credential: **NONE**.
- Live production execution and real-money orders: **NONE**. Human acceptance: **PENDING**.
