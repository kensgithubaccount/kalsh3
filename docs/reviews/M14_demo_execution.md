# M14 Demo / Paper Execution — Cross-functional review

M14 proves order-state machinery only in `MOCK`, `PAPER`, and the allowlisted Kalshi `DEMO` environment. Production is not an execution mode. No live credential was installed and no network mutation was performed during acceptance.

## Review findings

- **Execution / trader:** create, cancel, amend, and decrease requests share one state machine. Possible-send timeouts become `UNKNOWN_RECONCILIATION_REQUIRED`; they are never blind-retried. Partial fills remain monotonic, and fills racing cancel/amend/decrease acknowledgements are retained.
- **Risk:** the immutable M13 intent hash, short expiry, safety hash, account state, compliance, halt, kills, reconciliation freshness, market/rules state, and explicit subaccount 0 are checked before submission. A durable journal is acquired before the one-use authorization is atomically consumed.
- **Distributed systems:** journal ownership is unique by authorization, client ID, and execution ID. Crash recovery marks every incomplete operation for reconciliation rather than resubmission. PostgreSQL uses SERIALIZABLE transactions, `FOR UPDATE`, singleton capacity locking, unique claims, and conditional authorization consumption. PostgreSQL integration is not verified locally because Docker/PostgreSQL are unavailable; CI provides the service job.
- **Security:** the mutation origin is a non-configurable exact demo allowlist. Production and arbitrary hosts fail before credential use. The redacted `DEMO_WRITE` container cannot represent another credential class, and optional enrollment validates demo account 0 before using distinct encrypted vault names. The package imports no production signer, LLM, or production gateway.
- **Market microstructure:** actual queue observations are labeled `OBSERVED_DEMO_ORDER_QUEUE`; comparisons preserve M11 predictions rather than rewriting them. No demo observations exist yet, so queue calibration remains fixture-only.
- **Finance:** fills and fees use exact `Decimal`, immutable exchange trade IDs deduplicate accounting, and mode-tagged balanced postings prevent MOCK/PAPER/DEMO ledger mixing.
- **SRE / data:** duplicate private-stream events are idempotent. Sequence gaps and disconnects degrade trust and require REST recovery. Reconciliation scopes orders/fills to account 0 and does not treat `NOT_FOUND_YET` as resubmission permission.
- **Product / UX / compliance:** Orders & Trades separates real read-only activity from execution testing, prominently explains demo mock funds, unknown order state, and recovery. Demo activity never changes the global state to `TRADING`; the setup surface is owner-only and disabled until a live demo validator exists. Tests use small deterministic fixtures with no manipulative behavior.

## Acceptance report

- M14 CODE: OFFLINE VERIFIED
- M14 MOCK EXECUTION: MOCK VERIFIED
- M14 PAPER EXECUTION: MOCK VERIFIED
- M14 DEMO TRANSPORT: MOCK VERIFIED
- M14 DEMO HOST ISOLATION: OFFLINE VERIFIED
- M14 DEMO CREDENTIAL ISOLATION: OFFLINE VERIFIED
- M14 ORDER CREATE STATE MACHINE: MOCK VERIFIED
- M14 UNKNOWN SUBMISSION SAFETY: MOCK VERIFIED
- M14 CLIENT_ORDER_ID RECONCILIATION: MOCK VERIFIED
- M14 PARTIAL FILLS: MOCK VERIFIED
- M14 CANCEL: MOCK VERIFIED
- M14 CANCEL/FILL RACE: MOCK VERIFIED
- M14 AMEND: MOCK VERIFIED
- M14 DECREASE: MOCK VERIFIED
- M14 USER_ORDER WS: MOCK VERIFIED
- M14 FILL WS: MOCK VERIFIED
- M14 WS RECONNECT/RECONCILIATION: MOCK VERIFIED
- M14 QUEUE POSITION OBSERVATION: MOCK VERIFIED
- M14 FEE CALIBRATION: MOCK VERIFIED
- M14 SLIPPAGE CALIBRATION: MOCK VERIFIED
- M14 ORDER GROUPS: NOT VERIFIED
- M14 RESTART RECOVERY: MOCK VERIFIED
- M14 SUBMISSION JOURNAL: OFFLINE VERIFIED
- M14 M13 AUTHORIZATION BINDING: OFFLINE VERIFIED
- M14 POSTGRES CONCURRENCY: NOT VERIFIED
- M14 UI: OFFLINE VERIFIED
- M14 LIVE DEMO API: NOT VERIFIED
- M14 LIVE DEMO ORDER: NOT VERIFIED
- M14 PRODUCTION WRITE CREDENTIAL: NONE
- M14 PRODUCTION MUTATION CAPABILITY: NONE
- M14 REAL-MONEY ORDER: NONE
- M14 HUMAN ACCEPTANCE: PENDING

Browser acceptance remains PENDING. Current official documentation revalidation was attempted, but the available web integration returned `401 Unauthorized`; externally supplied current facts were therefore isolated behind adapters and fixture assertions rather than described as live-verified behavior.
