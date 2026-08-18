# M27F -- Live Authenticated Read Acceptance

Date: 2026-08-18
Branch: `feat/m27f-live-authenticated-read-acceptance`
Production mutations: none

## Starting point correction

The branch was initially checked out from a stale `feat/m27c-daily-min-calibration` commit
rather than the M27E merge (`1d6612b9ec4fe2627037351905ca956d77d2836f`) this milestone was
supposed to build on. That commit's tree was identical to the equivalent squash-merged commit
already on `main`, so nothing was lost; the branch was reset to `origin/main` (`1d6612b`)
before any M27F work began.

## Goal

Build the smallest operator-controlled path that can validate the eventual least-privilege
production canary credential (`scopes = {"read", "write::trade"}`, `subaccount = 0`,
`environment = PRODUCTION`) and perform complete authenticated read acceptance -- balance,
open orders, positions, fills, settlements -- against the real Kalshi production account,
without ever installing the write credential, arming production, or calling a mutating
endpoint.

## Reused read boundaries

Nothing about the authority or transport boundary was invented fresh:

- **Candidate authority.** `services.production_execution.enrollment.verify_live_write_credential_authority`
  and `require_live_write_authority` (M27E, unchanged) authenticate as the candidate key
  against `GET /trade-api/v2/api_keys` and enforce the exact server-reported scope set
  `{"read", "write::trade"}` and `subaccount == 0`. M27F calls these functions directly; it
  does not reimplement or relax any part of that check.
- **Account reads.** `services.kalshi_account_gateway.client.KalshiAccountClient` (M25/M21/M22)
  already implements the fixed-origin (`https://external-api.kalshi.com`), redirect-rejecting,
  size- and timeout-bounded, JSON-only GET transport for balance, limits, positions, orders,
  fills, and settlements, with cursor pagination that rejects repeated/malformed cursors and
  never turns a failed page into an empty result. M27F reuses this class as-is for the actual
  reads, rather than building a second HTTP stack.

The only change to reused code is a non-behavioral split inside `KalshiAccountClient`:
`get_balance()`, `get_limits()`, and `get_collection(name)` are now public methods that
`refresh()` composes exactly as before (same paths, same order, same retry/pagination
semantics -- all 50 pre-existing `tests/test_account_gateway.py` cases pass unmodified). This
split exists because `refresh()`'s built-in `verify_exact_read_scope()` requires scopes to be
exactly `{"read"}`, which is correct for the M25B read-only credential but wrong for M27F's
`{"read", "write::trade"}` candidate -- M27F's own (stricter) M27E authority check already
proved the candidate's scopes before any read is attempted, so the read path must not run a
second, incompatible scope check. `client.py`'s signer parameter is also now typed against a
local `Signer` protocol instead of the concrete `RequestSigner` class, so a synthetic test
signer can be injected without touching production wiring.

## Candidate credential handling

The new operator CLI is
`python -m services.supervised_canary.live_read_acceptance`:

```sh
python -m services.supervised_canary.live_read_acceptance \
  --key-id-file /secure/operator/key_id.txt \
  --private-key-fd 3 \
  --output /secure/operator/m27f-live-read-evidence.json \
  3< /secure/operator/key.pem
```

- The key ID (not secret) comes from a small operator-supplied file.
- The private key is read only from the inherited file descriptor via the existing
  `services.kalshi_account_gateway.production_read_credentials.read_private_key_fd` helper
  (unchanged, size-bounded). It never appears in argv, an environment variable, or the shell's
  command line.
- Nothing is installed: `run_live_read_acceptance` never calls
  `ProtectedWriteCredentialStore.install`, never constructs a `SealedCredentialPackage`, and
  the credential/PEM live only in local Python variables for the duration of the process.
- `ProductionWriteCredential.__repr__` and `RequestSigner`'s `repr(field=False)` PEM field
  were already secret-free (M27E); the new `CandidateAuthorityResult`, `EndpointReadResult`,
  and `LiveReadAcceptanceEvidence` dataclasses carry only hashes, counts, classifications, and
  timestamps -- never the PEM, never the raw key ID (only its SHA-256 hash). This is exercised
  by `test_evidence_json_is_secret_free` and the CLI-level PEM-leak tests.
- This milestone does not execute the CLI against a real credential; all coverage uses
  synthetic/local transport fixtures (`FakeAuthorityTransport`, `FakeAccountTransport`,
  `FakeSigner`).

## Authenticated read surface

Exact endpoints, all GET-only, all through the fixed `https://external-api.kalshi.com`
origin:

| Read | Path |
|---|---|
| Candidate authority | `GET /trade-api/v2/api_keys` |
| Balance | `GET /trade-api/v2/portfolio/balance?subaccount=0` |
| Limits | `GET /trade-api/v2/account/limits` |
| Positions | `GET /trade-api/v2/portfolio/positions?subaccount=0[&cursor=...]` |
| Open orders | `GET /trade-api/v2/portfolio/orders?subaccount=0[&cursor=...]` |
| Fills | `GET /trade-api/v2/portfolio/fills?subaccount=0[&cursor=...]` |
| Settlements | `GET /trade-api/v2/portfolio/settlements?subaccount=0[&cursor=...]` |

Pagination (positions/orders/fills/settlements) consumes every page, rejects a repeated or
non-string cursor, and only returns once a page reports an empty/absent cursor -- a mid-
pagination HTTP or schema failure discards the pages already collected rather than returning
a partial list, so a failed read can never present itself as an empty account. Each of the six
reads is attempted and classified independently (`SUCCESS`, `AUTH_FAILURE`, `RATE_LIMITED`,
`UPSTREAM_UNAVAILABLE`, `PAGINATION_FAILURE`, `SCHEMA_OR_HTTP_FAILURE`), so one endpoint's
failure never hides in, or is hidden by, another endpoint's success. `KalshiAccountClient` is
constructed with `max_retries=0` for this acceptance path specifically, so no automatic retry
can mask a failure's real cause.

## Reconciliation

`ReconciliationResult` only reports `PASS` when all of the following hold:

- the candidate authority check passed;
- balance, limits, positions, orders, fills, and settlements each returned `SUCCESS`, with
  the four collections' pagination each fully complete;
- the resulting `AccountSnapshot` (M25 domain model, unchanged) validated cleanly -- e.g. a
  malformed money field or legacy schema shape fails reconciliation even if every individual
  HTTP call returned 200;
- `completed_at - started_at <= 30s` (the M16 `ReadinessSnapshot.missing()` freshness target,
  reused verbatim rather than re-derived); and
- the snapshot's subaccount is exactly 0 -- structurally guaranteed here because every read
  path is hardcoded to `subaccount=0` and `AccountSnapshot.from_payloads` fixes
  `subaccount=0` by construction, not by trusting a response field.

If any required read did not complete, the classification is `BLOCKED` (not a silent pass);
if every read completed but the set is stale or fails snapshot validation, it is `FAIL`. A
missing or partial evidence source is never reported as reconciled.

This milestone does not maintain a local order/position journal to compare against exchange
state, so "no unknown local-vs-exchange order/position state" has no independent local source
to check against in M27F's scope -- it is not claimed as a separate passing gate; only the
authenticated-read completeness and freshness claims above are made.

## Evidence artifact

`LiveReadAcceptanceEvidence.to_json()` (schema `kalsh3.m27f.live-read-acceptance.v1`) contains
only: schema/software version, environment, subaccount, a SHA-256 hash of the key ID,
acquisition/completion timestamps, the candidate-authority classification plus server-reported
scopes/subaccount (never the PEM or raw key ID), and, per read, its classification, item
count, pagination completeness, a canonical SHA-256 hash of the returned payload, timestamps,
and a sanitized failure reason where applicable. No PEM, signing header, or raw account content
is ever written. `test_evidence_json_is_secret_free` and the CLI PEM-leak tests assert this
directly.

## Readiness report

`services.supervised_canary.readiness_report.operator_evidence` and
`render_operator_readiness` gained an optional `live_read_evidence` path (also
`--live-read-evidence` on the existing `readiness_report` CLI). When a fresh, valid M27F
artifact is supplied:

- `CANDIDATE_KEY_AUTHENTICATED_GET` and each of `AUTHENTICATED_PRODUCTION_BALANCE`,
  `AUTHENTICATED_OPEN_ORDERS`, `AUTHENTICATED_POSITIONS`, `AUTHENTICATED_FILLS`,
  `AUTHENTICATED_SETTLEMENTS` flip to `PASS` only for the specific reads that artifact proves
  succeeded;
- `ACCOUNT_RECONCILIATION` flips to `PASS` only when the artifact's own reconciliation
  classification is `PASS` (never on partial or stale evidence);
- `PRODUCTION_WRITE_CREDENTIAL` (`NOT INSTALLED`), `PRODUCTION_ARMED` (`FAIL`/`DISARMED`),
  `REAL_MUTATION` (`NOT TESTED`), and `REAL_SIGNER_VALIDATION` (`BLOCKED_BY_CREDENTIAL`) are
  never touched by this evidence -- authenticated GET acceptance is explicitly not the
  isolated production execution signer runtime with an installed write credential, and the
  report does not conflate the two.

`test_readiness_report_partial_evidence_never_falsely_passes` and
`test_readiness_report_stale_evidence_never_passes_reconciliation` cover the two ways an
almost-complete artifact could otherwise be over-credited.

## Live enrollment gap (unchanged, intentionally)

- `services/production_execution/credentials.py::enrollment_available()` still returns
  `False`.
- `ProtectedWriteCredentialStore.install()` still raises unless `credential.fixture_only` is
  `True` -- confirmed unmodified by this milestone's diff.
- There is still no merged `services/production_execution/enrollment_cli.py`. Real credential
  enrollment remains the next, separately reviewed operator-release milestone after
  authenticated read acceptance is independently accepted.

## Frozen weather / M27D boundary

No file under `services/forecasting/` was touched, and `services/supervised_canary/m27d.py`,
`workflow.py`, and `store.py` were neither imported nor modified -- M27F's evidence path has
no dependency on the M27D one-submission budget or trade-selection policy.

## Verification

- `ruff check .`: pass.
- `ruff format --check .`: pass.
- `mypy` (strict, `services` package): pass, 203 source files.
- `pytest -q` (full suite): 1304 passed, 3 skipped (`KALSH3_TEST_POSTGRES_DSN` not set --
  pre-existing, unrelated to this milestone).
- `git diff --check`: clean.
- Focused M27F suite (`tests/test_m27f_live_read_acceptance.py`, 36 cases): candidate-authority
  adversarial matrix (wrong/duplicate key ID, broad write, extra scope, missing
  `write::trade`, null/wrong subaccount, 401/403/redirect/timeout/malformed/oversized), one
  test per read-failure class per endpoint, pagination/repeated-cursor/incomplete-pagination,
  freshness, secret-handling (PEM absent from evidence/exceptions/CLI output, fd fully
  consumed), and readiness-report gate unlocking (no evidence / partial evidence / stale
  evidence / complete evidence) -- all pass.
- No real credential was used, no authenticated live call was made, and no mutation was
  attempted.

## Safety

- real credential used: NO
- credential installed: NO
- production armed: NO
- authenticated mutation: NO
- real order: NO
- autonomous trading: NO
- frozen weather changed: NO

## Review disposition

**SAFE FOR INDEPENDENT REVIEW**. This does not authorize credential enrollment, arming, or
any production mutation. It establishes the read-acceptance and reconciliation machinery a
future real candidate credential would need to pass before the separate write-credential
enrollment milestone can even be considered.
