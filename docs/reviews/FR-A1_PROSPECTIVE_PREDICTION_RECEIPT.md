# FR-A1 — Prospective Prediction Receipt Foundation

This is the locked envelope for predictions we make before we know what happens.

FR-A1 composes the canonical frozen `Forecast` and adds a content-addressed,
create-only prospective receipt. The receipt binds the candidate/model,
calibrator, feature schema, code identity, market/event/dependency identity,
timestamps, probabilities, uncertainty, abstention, source/evidence IDs,
point-in-time market reference, horizon, and zero-production-influence status.

The archive accepts an identical publication only idempotently. A different
payload cannot replace an existing receipt. A separate issuer-controlled
publication record records the runtime UTC instant when the receipt entered the
archive. A later outcome handoff must find both exact immutable records and must
occur after that trusted publication instant. Caller-supplied forecast timestamps
are not publication authority. The handoff is chronological only; CPI or any
other settlement authority remains outside FR-A1.

The publication record is issued only through a private capability-gated path.
Its persisted bytes include an issuer MAC verified with the archive's separate,
mode-600 issuer key, so a legitimate publication remains verifiable after a
process restart. The archive/key boundary is trusted infrastructure: arbitrary
host or issuer-key compromise is outside this checkpoint.

Publication uses canonical UTC and must precede the forecast's bound target
resolution instant. If a receipt file exists without its publication file, an
identical retry completes the pair with the current issuer time; it never
backdates recovery.

Historical replay forecasts are rejected when they carry replay time. This
prevents the FR-A1 API from relabeling a historical prediction as prospective.
Each materially different canonical forecast produces a different receipt
identity. There is no inherited prospective history across model identities.

FR-A1 DOES NOT PROVE THE MODEL IS GOOD.

It proves only that future performance can later be measured without rewriting
history, subject to the archive issuer's runtime clock and filesystem trust
model. It does not protect against arbitrary host compromise or clock tampering.

FR-A1 has no scoring, outcome-authority, promotion, ranking, drift, execution,
risk, credential, capital, or production-authorization behavior.
