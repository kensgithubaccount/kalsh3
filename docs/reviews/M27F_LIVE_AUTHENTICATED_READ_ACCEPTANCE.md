# M27F -- Live Authenticated Read Acceptance

Date: 2026-08-18 (live discovery repair: account-level limits removed from narrow-candidate
acceptance)
Branch: `feat/m27f-subaccount-read-reconciliation`
Production mutations: none

## Revision history

1. Original M27F design (candidate signs its own `GET /api_keys`) -- superseded, see "Live
   discovery that forced this revision" below.
2. Candidate-authority attestation split (`feat/m27f-authority-attestation-fix`) -- a separate
   management credential attests to the candidate's authority out of band; still called
   `GET /account/limits` as a mandatory sixth read.
3. **This revision**: with a valid attestation, the real candidate receives `HTTP 403` from
   `GET /account/limits`. That endpoint is account-tier metadata with no `subaccount`
   parameter and is out of scope for this candidate's acceptance contract; M27F now never
   calls it. See "Live discovery: `/account/limits` is out of scope" below.

## Live discovery that forced this revision

A real least-privilege candidate key was generated with `scopes = {"read", "write::trade"}`,
`subaccount = 0`. Live GET-only diagnostics against production established:

| Call | Credential | Result |
|---|---|---|
| `GET /trade-api/v2/api_keys` | broad temporary bootstrap credential | `HTTP 200`; exactly one matching candidate record: `scopes=read,write::trade`, `subaccount=0` |
| `GET /trade-api/v2/portfolio/balance?subaccount=0` | candidate credential | PASS |
| `GET /trade-api/v2/api_keys` | candidate credential | `HTTP 401` |

The original M27F design (this review's earlier revision) had the *candidate* sign its own
`GET /api_keys` call to prove its own scopes/subaccount
(`verify_live_write_credential_authority` / `require_live_write_authority`, reused from
M27E). Against the real least-privilege candidate this produced
`candidate_authority=FAIL`, `reconciliation=BLOCKED`, `reads=0` -- not because the candidate
lacked read/write::trade authority, but because it is not entitled to enumerate account
API-key metadata, even its own. That surface is reserved to a broader account-management
identity. No credential was installed, production remained DISARMED, and no mutation/order
occurred while this was discovered.

The fix is **not** to broaden the candidate's scopes. It is to split candidate-authority proof
from candidate live-read acceptance into two separate operator boundaries.

## Two operator boundaries

### A. Candidate authority attestation (new: `authority_attestation.py`)

A separately authorized *management* credential (a broader, distinct bootstrap key -- never
the candidate's) performs exactly one GET-only call:

```
GET /trade-api/v2/api_keys
```

signed by the management credential, and locates the expected candidate by (non-secret) key
ID. It produces a secret-free `CandidateAuthorityAttestation` artifact
(schema `kalsh3.m27f.candidate-authority.v1`) recording the candidate's server-reported
scopes/subaccount, a unique-match count, and a `PASS`/`FAIL` classification -- never a raw
key ID, private key, signature, header, or account value.

**The candidate's private key is never an input to this module at all.** The generator's
only inputs are the management key ID (file), the management private key (inherited fd,
exactly as M25's `read_private_key_fd` boundary already requires), and the candidate's key
ID (file, non-secret). This keeps the broad management credential and the narrow candidate
credential out of the same process by construction, not by convention -- there is no
parameter through which a candidate private key could flow into
`generate_candidate_authority_attestation` or its CLI.

The transport parameter is `ProductionReadTransport` (reused from M27E's
`production_read_credentials.py`, unmodified) -- a GET-only protocol with no mutating method,
so no write call is even expressible here. Scope parsing reuses
`KNOWN_KALSHI_API_KEY_SCOPES` from `production_execution/enrollment.py` (imported, not
modified) and the exact `REQUIRED_LIVE_WRITE_SCOPES` / `REQUIRED_LIVE_WRITE_SUBACCOUNT`
policy from `production_execution/credentials.py` (imported, not modified).

Operator CLI:

```sh
python -m services.supervised_canary.authority_attestation \
  --management-key-id-file /secure/operator/management_key_id.txt \
  --candidate-key-id-file /secure/operator/candidate_key_id.txt \
  --management-private-key-fd 3 \
  --output /secure/operator/candidate-authority-attestation.json \
  3< /secure/operator/management_key.pem
```

### B. M27F live read acceptance (revised: `live_read_acceptance.py`)

M27F no longer asks the candidate to call `GET /api_keys` at all -- the function that used to
accept an `authority_transport` now has no such parameter (confirmed by
`test_candidate_no_longer_calls_api_keys`, which inspects the function signature). Instead it
receives:

- the candidate key ID (existing local file input, unchanged);
- the candidate private key (existing inherited fd, unchanged);
- the previously generated, secret-free candidate-authority attestation (new: parsed JSON,
  supplied via `--authority-attestation`).

Before any account read is attempted, `validate_attestation_for_candidate` independently
re-checks every field of the attestation -- it never merely trusts the artifact's own
`classification`:

- schema is exactly `kalsh3.m27f.candidate-authority.v1`;
- `environment` is exactly `PRODUCTION`;
- `source.origin`/`source.path` match the fixed production `GET /api_keys` origin/path;
- the attestation's `candidate.key_id_hash` equals `SHA-256(supplied candidate key ID)`;
- `candidate.server_scopes` is exactly `{"read", "write::trade"}`;
- `candidate.server_subaccount` is exactly `0`;
- `candidate.unique_matches` is exactly `1`;
- `classification` is exactly `"PASS"`;
- no field is missing or malformed.

If any check fails, the attestation is classified `FAIL`, **zero** candidate account reads
are attempted, and reconciliation is `BLOCKED` -- fail closed, exactly as the prior
candidate-calls-`/api_keys` design failed closed on an authority rejection. If every check
passes, the candidate performs only the required subaccount-0 portfolio reads (unchanged
`KalshiAccountClient` boundary), with exact `subaccount=0` behavior, existing
pagination/reconciliation rules, existing creation-time `<=30s` freshness, and existing
consumption-time `<=30s` readiness freshness all preserved unmodified.

`CandidateAuthorityResult` now carries `source = "EXTERNAL_SERVER_ATTESTATION"` in the
evidence artifact, so the final M27F evidence clearly distinguishes the attestation-sourced
authority proof from the candidate's own fresh authenticated account reads -- it never
implies the candidate itself successfully accessed `/api_keys`.

## Live discovery: `/account/limits` is out of scope (2026-08-18, this revision)

With a valid attestation now in place (`candidate authority = PASS`, `scopes =
{"read", "write::trade"}`, `subaccount = 0`, temporary bootstrap credential already revoked),
a real M27F run against the real candidate produced:

| Read | Result |
|---|---|
| `GET /trade-api/v2/portfolio/balance?subaccount=0` | `SUCCESS` |
| `GET /trade-api/v2/portfolio/positions?subaccount=0[&cursor=...]` | `SUCCESS`, complete pagination |
| `GET /trade-api/v2/portfolio/orders?subaccount=0[&cursor=...]` | `SUCCESS`, complete pagination |
| `GET /trade-api/v2/portfolio/fills?subaccount=0[&cursor=...]` | `SUCCESS`, complete pagination |
| `GET /trade-api/v2/portfolio/settlements?subaccount=0[&cursor=...]` | `SUCCESS`, complete pagination |
| `GET /trade-api/v2/account/limits` | `SCHEMA_OR_HTTP_FAILURE` (`unexpected upstream status 403`) |

Reconciliation was `BLOCKED` ("one or more required authenticated reads did not complete")
even though every read this candidate actually needs had already succeeded, because the
prior revision made `/account/limits` a mandatory sixth read and routed every read through
`AccountSnapshot.from_payloads`, which itself hard-requires a `limits` payload before it will
even set `subaccount = 0` on the resulting snapshot.

**Root cause**: Kalshi's API draws a real distinction the prior design collapsed.
`balance`/`positions`/`orders`/`fills`/`settlements` are *portfolio* endpoints that accept an
explicit `subaccount` query parameter, including `subaccount=0`. `GET /account/limits` is
documented *account-tier* metadata for the authenticated user/account as a whole -- it has no
`subaccount` parameter at all, and the least-privilege candidate here is not entitled to it.
`LEAST_PRIVILEGE_CANDIDATE_ACCOUNT_LIMITS_GET = HTTP_403` while
`LEAST_PRIVILEGE_CANDIDATE_PORTFOLIO_READS = PASS`. This is not a general authentication
failure for the candidate -- it is one endpoint outside this candidate's need-to-access
surface, and it is not solved by broadening the candidate's scopes.

**Fix, not a broadened candidate**: `live_read_acceptance.py` no longer calls, or has any
code path capable of calling, `GET /account/limits`. The required M27F candidate read set is
now exactly the five portfolio reads in the table above -- never a sixth account-metadata
call. `run_live_read_acceptance` no longer builds an `AccountSnapshot` at all (that model
still requires a `limits` payload by design -- see "Legacy account client preservation"
below); M27F now has its own `ReconciliationResult`, with balance schema proven directly by a
new M27F-local `_validate_balance_schema` (object; `balance`/`portfolio_value`/`updated_ts`
integers, not booleans; optional `balance_breakdown` is an array of objects if present)
without storing any account value in the evidence artifact. Positions/orders/fills/
settlements schema validation (array-of-objects, complete pagination) is unchanged, already
enforced by `KalshiAccountClient._all_pages`.

`ReconciliationResult.limits_succeeded` is removed outright rather than left as a permanently
`False` field for an endpoint that is no longer part of the acceptance contract.
`subaccount_consistent` is renamed `subaccount_binding_verified` and is now derived from (a)
the independently re-validated attestation's `server_subaccount == 0` and (b) every required
portfolio read having succeeded against `KalshiAccountClient`'s structurally fixed
`?subaccount=0` request path -- never from a `subaccount` field inside a portfolio response
body, which does not exist. `ReconciliationResult`'s docstring states this derivation
explicitly so the field is never misread as an echoed server value.

Evidence schema bumped explicitly to `kalsh3.m27f.live-read-acceptance.v3` / software version
`kalsh3.m27f.live-read-acceptance/3` to reflect the removed read and renamed/removed
reconciliation fields -- a clean version bump rather than a silent v2 meaning change.
`readiness_report.py` required **no changes**: it never had a separate account-limits
readiness gate, and its gate-unlocking logic only reads `reconciliation.classification` and
the per-endpoint `reads`, neither of which changed shape in a way that broke it. M27F's
readiness gates remain exactly `CANDIDATE_KEY_AUTHENTICATED_GET`,
`AUTHENTICATED_PRODUCTION_BALANCE`, `AUTHENTICATED_OPEN_ORDERS`, `AUTHENTICATED_POSITIONS`,
`AUTHENTICATED_FILLS`, `AUTHENTICATED_SETTLEMENTS`, `ACCOUNT_RECONCILIATION` -- there is
intentionally no separate account-limits readiness gate.

## Authority attestation lifetime semantics

The M16/M27F 30-second **user-data** freshness rule is deliberately **not** applied to the
authority attestation. That rule bounds how stale a *portfolio snapshot* may be before it is
treated as current account state; an authority attestation describes credential authority,
not account/portfolio data, so the same clock-based bound does not apply to it. Applying it
anyway would require inventing an arbitrary TTL this review explicitly declined to invent.

Instead, the attestation's validity is bound structurally rather than temporally:

- **it remains valid only for the exact key ID hash it names.** `validate_attestation_for_candidate`
  compares `SHA-256(candidate_key_id)` against the artifact's `key_id_hash` exactly; an
  attestation generated for one key ID never validates for a different one
  (`test_validate_rejects_attestation_bound_to_a_different_key_id`).
- **deletion/replacement cannot silently transfer authority.** Kalshi's documented API-key
  surface (`docs.kalshi.com/api-reference/api-keys`) exposes create/generate/get/delete, with
  no documented scope/subaccount *update* operation -- a key's scopes/subaccount cannot
  change in place. If a key is deleted and a new one generated, the new key gets a new key
  ID, so its hash will not match the old attestation, and a fresh attestation must be
  generated for it.
- **the candidate must still authenticate successfully on every M27F run.** The attestation
  only proves the candidate's server-reported *authority* (scopes/subaccount); it never
  substitutes for the candidate's own live signature on each balance/positions/orders/fills/
  settlements call. If the candidate key were revoked after the attestation was generated,
  those live calls would fail with `AUTH_FAILURE` on their own merits, independent of the
  attestation.
- **a malformed or mismatched attestation always fails closed** -- there is no code path that
  treats a missing, malformed, or non-matching field as an implicit pass.

No expiry timestamp is checked or invented on the attestation itself. This is a deliberate
choice, not an omission, for the reasons above; if a future reviewer wants a time-based
attestation TTL, that is a distinct policy decision this milestone intentionally leaves open
rather than picking an arbitrary number.

## Evidence schema: `kalsh3.m27f.candidate-authority.v1`

```json
{
  "schema": "kalsh3.m27f.candidate-authority.v1",
  "software_version": "kalsh3.m27f.candidate-authority/1",
  "environment": "PRODUCTION",
  "observed_at": "2026-08-18T12:00:00+00:00",
  "source": {
    "origin": "https://external-api.kalshi.com",
    "path": "/trade-api/v2/api_keys"
  },
  "classification": "PASS",
  "candidate": {
    "key_id_hash": "<sha256 hex>",
    "server_scopes": ["read", "write::trade"],
    "server_subaccount": 0,
    "unique_matches": 1
  },
  "reason": null
}
```

No raw bootstrap/management key ID, no raw management private key, no raw candidate key ID,
no signature, no header, and no account data ever appears in this artifact --
`test_attestation_json_is_secret_free` and `test_failure_reasons_are_secret_free` assert this
directly, including across the full adversarial matrix.

## Compartmentalization / operator sequence

1. A temporary broad bootstrap credential exists (as it did during live discovery).
2. The reviewed `authority_attestation` CLI performs exactly one `GET /api_keys` with that
   bootstrap credential and locates the candidate.
3. A secret-free candidate authority artifact is written.
4. The broad bootstrap credential is revoked.
5. Future M27F live-read runs use only the authority artifact plus the narrow candidate
   credential -- the broad bootstrap key is never a runtime dependency of ordinary canary
   operation, only of the one-time (or per-rotation) attestation step.

## Reused, unmodified boundaries

- `services.kalshi_account_gateway.production_read_credentials`: `PRODUCTION_ORIGIN`,
  `API_KEYS_PATH`, `ProductionReadTransport`, `ReadSigner`, `UrllibProductionReadTransport`,
  `read_private_key_fd` -- all imported as-is by the new `authority_attestation.py`.
- `services.production_execution.enrollment.KNOWN_KALSHI_API_KEY_SCOPES` and
  `services.production_execution.credentials.{REQUIRED_LIVE_WRITE_SCOPES,
  REQUIRED_LIVE_WRITE_SUBACCOUNT}` -- imported, not modified. `verify_live_write_credential_authority`
  / `require_live_write_authority` (the M27E functions the *candidate* used to call directly)
  are no longer invoked by M27F, but remain unmodified in `enrollment.py` for any future
  caller that legitimately needs a key to attest to its own metadata.
- `services.kalshi_account_gateway.client.KalshiAccountClient` (M25/M21/M22): completely
  unmodified by this revision.
- `git diff -- services/production_execution` and `git diff -- services/forecasting` are both
  empty for this revision -- no production-execution, enrollment, security-boundary, or
  frozen-weather file was touched.

## Legacy account client preservation

`services/kalshi_account_gateway/client.py` (`KalshiAccountClient`, including
`get_limits()`) and `services/kalshi_account_gateway/models.py` (`AccountSnapshot`,
`AccountSnapshot.from_payloads`) are **completely unmodified** by this revision. That older
`KalshiAccountClient.refresh(expected_key_id)` flow -- exact-read-only scope verification,
all six reads including `limits`, and a full `AccountSnapshot` -- is a distinct security
boundary from the M27F narrow `write::trade` candidate and may legitimately need
account-tier limits for its own purposes. Weakening `AccountSnapshot` to make `limits`
optional, or teaching it to accept a five-payload set, would have coupled that separate
boundary's contract to this milestone's narrow candidate; instead M27F now carries its own
`ReconciliationResult` model that never touches `AccountSnapshot`. `tests/test_account_gateway.py`
(the legacy client/snapshot test suite) required zero changes and its full suite passes
unmodified, proving `refresh()`'s own behavior is untouched.

## Tests

- `tests/test_m27f_candidate_authority_attestation.py` (new, 30 cases): unique-match PASS,
  zero/duplicate matches, wrong scopes (missing `write::trade` / broad `write` / extra
  scope), wrong subaccount (null/nonzero), management 401/403, redirect, transport exception,
  five malformed-response shapes, GET-only transport-protocol assertion, exactly-once
  transport call assertion, secret-free artifact and failure-reason assertions, independent
  consumer-side re-validation (including the different-key-id and tampered-classification
  cases), no-time-based-expiry assertion, and CLI secret-handling/argument-surface tests.
- `tests/test_m27f_live_read_acceptance.py` (40 test functions, 86 parametrized cases, up
  from 59): happy path with exactly the five required reads and no `limits_succeeded` field
  anywhere in the evidence; a 17-case invalid-attestation adversarial matrix (missing/
  malformed attestation, wrong schema/environment/source, wrong key-id hash, wrong/broad/
  extra scopes, null/wrong subaccount, zero/duplicate matches, the attestation's own
  classification being `FAIL`, and malformed software-version/observed-at) that all assert
  zero reads and `BLOCKED` reconciliation; a structural regression that the consumer function
  has no `authority_transport` parameter and never requests `/api_keys` through the account
  transport; a fake transport whose `limits` branch always returns `HTTP 403` (matching the
  live evidence) plus explicit assertions that the candidate request sequence never contains
  `/account/limits` or `/api_keys` and that every portfolio path carries `subaccount=0`; a
  balance-schema adversarial matrix (missing field, wrong types, bool-for-integer rejection,
  malformed `balance_breakdown`) plus a secret-free-evidence assertion that raw balance
  figures never appear in the artifact; a 401/pagination-failure matrix covering all five
  required reads individually; a "revoked despite valid old attestation" case (authority
  `PASS`, every read `401`, reconciliation still cannot `PASS`); a regression reproducing the
  exact 2026-08-18 discovery end-to-end (attestation `PASS`, all five portfolio reads `PASS`,
  `/account/limits` call count `= 0`, `reconciliation = PASS`); an exact-30s freshness
  boundary-pass case alongside the pre-existing `>30s` failure case; and the full pre-existing
  CLI/readiness-report/consumption-freshness suite, adapted to the new schema.
- `tests/test_account_gateway.py` (legacy `KalshiAccountClient.refresh()`/`AccountSnapshot`
  tests): unmodified, all pass.
- Full suite: `1384 passed, 3 skipped` (`KALSH3_TEST_POSTGRES_DSN` not set -- pre-existing,
  unrelated).
- `ruff check .`: pass. `ruff format --check .`: pass. `uv run mypy`: pass, 204 source files.
  `git diff --check`: clean. `git diff -- services/production_execution` and
  `git diff -- services/forecasting`: both empty.

No real credential was used, no authenticated live call was made, and no mutation was
attempted while producing this revision.

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
any production mutation. It corrects a real live-discovery gap in the read-acceptance and
reconciliation machinery a future real candidate credential would need to pass before the
separate write-credential enrollment milestone can even be considered.
