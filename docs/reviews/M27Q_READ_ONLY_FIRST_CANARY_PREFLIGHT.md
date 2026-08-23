# M27Q — Read-Only First-Canary Preflight

**Status:** COMPLETE on `main` at `dad5aea9ff93c081e900685ad12dd7b926ad6551`

## Purpose

M27Q closes the last offline composition gap between the reviewed first-canary candidate path and the existing M27I final preflight boundary. It can produce a real M13 risk decision/intent/snapshot triple from a qualifying weather candidate and consume that triple through M27I while remaining read-only.

## What M27Q composes

1. Exact-one M27D qualifying experimental weather-canary selection.
2. Exact persisted M27F evidence bound to the same-sweep transient account-facts bundle.
3. Candidate-specific exposure evidence that must already have succeeded.
4. Immutable inspection of the M27P shared first-canary state.
5. Pure M13 first-canary risk production.
6. Existing M27I final read-only preflight consumption.

The same-sweep M27F bundle exposes only the reduced account facts needed by risk production. The persisted M27F evidence artifact remains the authoritative retained artifact; raw account rows and key material are not retained by the M27Q composition layer.

## What a successful result means

A returned `PREFLIGHT_READY` result means the exact candidate and the exact current evidence set passed the read-only preflight boundary at that instant.

It is evidence only. It is **not** an execution permission.

## Explicit non-authorizations

M27Q does not create, issue, consume, or imply any of the following:

- M13 production authorization;
- M16 supervised-canary approval;
- M27O execution authorization;
- submission-budget burn;
- order submission, cancellation, amendment, or decrease;
- autonomous-trading enablement;
- arming or global-state mutation.

`services/supervised_canary/m27q_preflight_orchestrator.py` intentionally contains no network transport, signer, credential reader, mutable store, approval path, authorization issuance, burn, execution authorization, or order capability.

## Fail-closed bindings

M27Q refuses to proceed when any required identity or state binding cannot be proven, including:

- not exactly one qualifying M27D candidate;
- non-unique candidate/economics binding;
- persisted M27F evidence missing, malformed, or unequal to the exact same-sweep bundle;
- failed M27F reconciliation;
- failed candidate-specific exposure check;
- unsafe or inconsistent M27P shared first-canary state;
- M13 risk failure;
- any existing M27I preflight failure.

## Verification surface

The milestone includes dedicated tests for:

- `tests/test_m27q_preflight_orchestrator.py`
- `tests/test_m27q_risk_preflight.py`
- `tests/test_m27q_state_inspection.py`

The implementation also preserves the previously reviewed M27I and M27O execution boundaries rather than duplicating them.

## Next operator checkpoint

The next permissible live step is a **fresh read-only first-canary preflight run** using current evidence and current account state.

That run may perform only the reads necessary to assemble the exact M27Q inputs and must stop after producing and reviewing the M27Q/M27I preflight artifact. In particular, it must not arm the system, issue an M13 authorization, create an M16 approval, create or consume an M27O execution authorization, burn a submission budget, or send an exchange mutation.

A stale `PREFLIGHT_READY` artifact must never be reused. Any later execution milestone must require its own explicit, separately reviewed authorization boundary and a fresh revalidation of all time-sensitive evidence.

## Functional follow-up boundary

The next code milestone should be a narrow operator runner for M27Q that:

- performs only the required current/public and authenticated GETs;
- uses the installed credential only for the already-reviewed read-only/signer verification paths needed to construct current M27Q evidence;
- calls the existing M27Q orchestrator without changing its policy;
- prints/persists a non-secret review artifact;
- exits before every approval, authorization, burn, mutation, or order path;
- is covered by import/AST capability tests proving that no exchange mutation method is reachable.

That runner should be treated as a new reviewable milestone rather than silently adding network or credential capability to `m27q_preflight_orchestrator.py`.
