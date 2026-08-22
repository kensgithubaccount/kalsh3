# M27P — Create-Only Canary State Bootstrap Review

Date: 2026-08-22

## Purpose

M27P closes the durable local-state bootstrap gap identified before the first
M27O supervised real-money canary.

M27O requires M16 human-approval state and M13 risk-authorization state to live
in one SQLite database so the final one-shot burn can be crash-atomic. Before
M27P, the reviewed stores supported this architecture, but there was no reviewed
operator boundary for creating that durable production-canary database.

## Added surface

- `services/supervised_canary/m27o_state_bootstrap.py`
- `tests/test_m27o_state_bootstrap.py`

Default durable path:

`$XDG_STATE_HOME/kalsh3/production-canary/m27o-shared.sqlite3`

or, if `XDG_STATE_HOME` is unset:

`~/.local/state/kalsh3/production-canary/m27o-shared.sqlite3`

## Security boundary

M27P is local-only and non-networked.

It has no:

- Kalshi account transport;
- M27O live execution transport;
- private-key or credential input;
- signer access;
- M16 approval issuance;
- M13 authorization issuance;
- M27O operator execution authorization;
- production-arm operation;
- submission-budget burn;
- order mutation path.

The exact required confirmation is:

`INITIALIZE DISARMED ONE-CONTRACT CANARY STATE WITH COMPLIANCE CLEAR`

Actor and reason are also mandatory.

## Required initial state

Before returning PASS, M27P proves:

- complete shared M16/M13 schema;
- production state `DISARMED`;
- real submission count `0`;
- real fill count `0`;
- global halt inactive;
- compliance `CLEAR`;
- `CREDENTIAL`, `DATA`, `PORTFOLIO`, and `STRATEGY` kill states all `NORMAL`;
- weekly review hold clear;
- monthly review hold clear;
- experiment halt hold clear;
- zero canary previews;
- zero M16 approvals;
- zero canary sessions;
- zero M13 risk authorizations;
- zero active risk reservations.

## Create-only filesystem behavior

The target directory is mode `0700`; the state database is mode `0600`.

Existing state fails closed. M27P refuses to bootstrap if the target database
or an associated WAL, SHM, or rollback-journal artifact already exists.

SQLite operates in the existing reviewed WAL model. Bootstrap transactions are
checkpointed before publication. A non-empty WAL or unexpected rollback journal
blocks publication. Empty WAL/SHM bookkeeping is removed once connections are
closed.

The completed self-contained database is published create-only using an atomic
hard link. Concurrent attempts therefore cannot both publish the production
state path.

A successfully published production artifact is never automatically removed by
later verification failure.

## Verification

Parent merged main:

`31e79ea0b985fe762474c3fe632ce6d177dba61e`

Focused verification:

- Ruff: PASS
- M27P tests: `10 passed`
- Existing M27O tests: `41 passed`

Guarded broader regression:

- `648 passed in 28.28s`
- outbound socket connection guard enabled;
- temporary isolated `HOME`;
- temporary isolated `XDG_STATE_HOME`;
- real Kalshi credential environment variables cleared;
- real candidate credential paths hidden.

Evidence:

`~/.kalsh3/evidence/m27p-bootstrap-regression/20260822T221033Z/pytest.txt`

## Production truth

During implementation and verification:

- Kalshi network: NONE
- real production state DB: NOT CREATED
- protected write credential: UNTOUCHED
- candidate credential: UNTOUCHED
- production armed: DISARMED
- M16 approval: NONE
- M13 authorization: NONE
- M27O execution authorization: NONE
- one-order burn: NONE
- mutating Kalshi call: NONE
- order sent: NO
- trade: NO

Creating the actual durable production-canary database remains a separate
operator action. This review does not perform or authorize that action.
