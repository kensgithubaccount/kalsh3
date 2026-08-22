# M27O — Protected One-Contract Live Canary

Status: implementation in progress
Base: `78cd5596d19dd3f0675bd9843035128a4eded7af`

## Goal

Permit exactly one supervised, real-money, one-contract Kalshi opening-order attempt only after
all previously reviewed evidence and human-approval gates are simultaneously current and bound
to the exact same candidate, price, fee ceiling, body bytes, risk authorization, account state,
and rules identity.

M27O is not bounded autonomy. It is the bridge from a fully reviewed supervised canary to a
single real submission attempt. The generic M15 production boundary remains DISARMED.

## Non-negotiable invariants

1. Exactly one opening contract (`1.00`), subaccount `0`.
2. CREATE only. No cancel, amend, decrease, transfer, or block-trade surface in M27O v1.
3. Exact candidate, market, outcome side, limit price, fee ceiling, loss ceiling,
   `client_order_id`, rules identity, reconciliation identity, and canonical body hash are
   immutable from preview through send.
4. A serialized M27I artifact must independently validate and be `PREFLIGHT_READY` at the
   instant it is consumed.
5. M16 human approval must be step-up-authenticated, exact-preview-bound, `ISSUED`, unexpired,
   and atomically consumed once.
6. M13 authorization must be `ISSUED`, unexpired, exact-intent/portfolio-bound, and atomically
   consumed once. Its historical `production_execution_authorized` field remains `False`:
   M13 authorizes the next risk gate, not a live send. The exact M27O human-approved release is
   the distinct authority for the one supervised canary.
7. M16 and M13 must use the same dedicated SQLite runtime database for the M27O canary.
   Independent WAL databases are rejected rather than falsely described as crash-atomic.
8. The M16 `production_submission_counter` is burned before any network mutation is attempted.
   A local crash after the burn but before send still consumes the only v1 attempt; safety wins
   over liveness.
9. The durable canary session is created as `SUBMISSION_PENDING` with
   `possibly_submitted=1` in that same atomic transaction. Any restart therefore requires
   reconciliation and never assumes the order was definitely not sent.
10. The durable production journal must claim the exact envelope before network send.
11. The real write credential is decrypted only inside `services.production_execution`, while
    holding the protected credential-store lock. Plaintext credential material never enters
    `services.supervised_canary`, never returns to the caller, and is never logged or serialized.
12. Only the fixed Kalshi production origin and exact create-order path are reachable.
13. Any timeout, transport exception, oversized/ambiguous response, 429, 5xx, or process restart
    after the send boundary is treated as `UNKNOWN_RECONCILIATION_REQUIRED`; never retry the
    mutation automatically.
14. After one attempt, the canary remains DISARMED and must reconcile before any terminal
    success/failure classification. No second opening order is possible in M27O v1.

## Architecture

### Phase A — pure release binding (implemented)

`services.supervised_canary.m27o.prepare_one_contract_release` consumes no store and performs no
network operation. It independently binds:

- M27I serialized `PREFLIGHT_READY` artifact and content hash;
- immutable M16 `HumanCanaryPreview`;
- M16 `HumanCanaryApproval`;
- exact M15 `ProductionRequestEnvelope` bytes/hash;
- fresh M13 `RiskAuthorization`.

It emits a short-lived `OneContractCanaryRelease`. Its expiry is the minimum of every upstream
expiry. Any mismatch fails before a credential or mutation boundary can be reached.

### Phase B — durable atomic one-shot consumption (implemented)

`services.supervised_canary.m27o.commit_atomic_release` requires M16 and M13 to be initialized on
one shared SQLite database. In one `BEGIN IMMEDIATE` transaction it independently re-checks the
exact durable preview, approval, M13 authorization/reservation, global halt, compliance, all
four kill states, durable loss holds, unresolved-canary state, and global submission budget.

It then performs all of the following or none of them:

1. consumes the exact M16 approval;
2. consumes the exact M13 authorization;
3. releases the exact M13 risk reservation;
4. burns the one-real-submission budget;
5. opens exactly one `SUBMISSION_PENDING` canary session with `possibly_submitted=1`;
6. writes secret-free M16 and M13 audit events.

The transaction is intentionally conservative. After it commits, a crash before or after the
future network call is treated as possibly sent and requires reconciliation. The one-order
budget is never restored automatically.

This phase still has no credential access, signing, network transport, or order submission.

### Phase C — high-trust sign/send (next)

A new narrow module inside `services.production_execution` will be the only live M27O credential
consumer. It will:

- accept only a valid Phase-B atomic commit plus the exact release/envelope;
- hold `ProtectedWriteCredentialStore.exclusive()`;
- decode the committed real credential only inside that scope;
- independently bind the credential fingerprint/key hash to fresh M27H evidence;
- reuse the existing RSA-PSS signer primitive;
- accept only CREATE / POST `/trade-api/v2/portfolio/events/orders`;
- require quantity `1.00` and the exact release-bound body hash;
- claim the `ProductionJournal` before signing/sending;
- instantiate the fixed Kalshi production transport internally;
- clear signature headers immediately;
- normalize every possibly-sent outcome to mandatory reconciliation.

The generic `SignAndSendBoundary.production_execute()` remains permanently DISARMED. M27O does
not turn it into a general production API.

## Human authorization

No code commit, installed credential, preflight, or M27O release is itself real-money approval.
The actual first live canary still requires a fresh, explicit operator authorization for the
exact candidate after a current M27I preflight. The final human approval remains candidate- and
price-specific and expires in at most 60 seconds.

## Current implementation state

Implemented:

- Branch isolated from merged main.
- Pure M27O release-binding module.
- Shortest-upstream-expiry semantics.
- Exact one-contract / exact create-order binding.
- M27I serialized hash/state validation.
- Human approval, fee/loss, candidate, rules, reconciliation, and production-read binding.
- Exact M15 envelope/body/client-order binding.
- Fresh M13 authorization binding.
- Shared-database requirement for true M16/M13 atomicity.
- Atomic approval + M13 + risk-reservation + one-real-submission-budget consumption.
- Durable `SUBMISSION_PENDING` / `possibly_submitted=1` recovery posture.
- Re-check of global halt, compliance, kill states, loss holds, unresolved canary, and budget
  inside the atomic transaction.
- Adversarial tests for tampering, staleness, drift, replay, separate-database rejection,
  safety-state rollback, and concurrent one-winner behavior.
- No network, credential, signer, arm, or order path in Phases A/B.

Not yet implemented:

- high-trust installed-credential execution module;
- real transport reachability;
- post-send live reconciliation wiring;
- final independent security review and full-suite regression pass.

Until those are implemented and reviewed, production remains DISARMED and no real order is
reachable.
