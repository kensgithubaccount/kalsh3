# M27F -- Live Authenticated Read Acceptance

Date: 2026-08-18 (candidate-authority attestation split follow-up)
Branch: `feat/m27f-authority-attestation-fix`
Production mutations: none

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
passes, the candidate performs only the existing GET-only balance/limits/positions/orders/
fills/settlements reads (unchanged `KalshiAccountClient` boundary), with exact
`subaccount=0` behavior, existing pagination/reconciliation rules, existing creation-time
`<=30s` account-snapshot freshness, and existing consumption-time `<=30s` readiness
freshness all preserved unmodified.

`CandidateAuthorityResult` now carries `source = "EXTERNAL_SERVER_ATTESTATION"` in the
evidence artifact, so the final M27F evidence clearly distinguishes the attestation-sourced
authority proof from the candidate's own fresh authenticated account reads -- it never
implies the candidate itself successfully accessed `/api_keys`.

Evidence schema bumped to `kalsh3.m27f.live-read-acceptance.v2` / software version
`kalsh3.m27f.live-read-acceptance/2` to reflect the authority-source change; no other
evidence field shape changed, and `readiness_report.py`'s existing gate-unlocking logic
(which only reads `candidate_authority.classification` and the per-endpoint `reads`) needed
no changes.

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
  substitutes for the candidate's own live signature on each balance/limits/positions/
  orders/fills/settlements call. If the candidate key were revoked after the attestation was
  generated, those live calls would fail with `AUTH_FAILURE` on their own merits, independent
  of the attestation.
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

## Tests

- `tests/test_m27f_candidate_authority_attestation.py` (new, 30 cases): unique-match PASS,
  zero/duplicate matches, wrong scopes (missing `write::trade` / broad `write` / extra
  scope), wrong subaccount (null/nonzero), management 401/403, redirect, transport exception,
  five malformed-response shapes, GET-only transport-protocol assertion, exactly-once
  transport call assertion, secret-free artifact and failure-reason assertions, independent
  consumer-side re-validation (including the different-key-id and tampered-classification
  cases), no-time-based-expiry assertion, and CLI secret-handling/argument-surface tests.
- `tests/test_m27f_live_read_acceptance.py` (59 cases): happy path; a 17-case invalid-attestation
  adversarial matrix (missing/malformed attestation, wrong schema/environment/source, wrong
  key-id hash, wrong/broad/extra scopes, null/wrong subaccount, zero/duplicate matches, the
  attestation's own classification being `FAIL`, and malformed software-version/observed-at)
  that all assert zero reads and `BLOCKED` reconciliation; a structural regression test that
  the consumer function has no `authority_transport` parameter and never requests
  `/api_keys` through the account transport; a regression test reproducing the exact live
  discovery (management attestation PASS, simulated candidate 401 on `/api_keys`, M27F still
  passes via the attestation); the full pre-existing per-endpoint failure/pagination/
  freshness/readiness-gate matrix, adapted to the new `authority_attestation` parameter; and
  CLI tests including a new malformed-attestation-file case.
- Full suite: `1357 passed, 3 skipped` (`KALSH3_TEST_POSTGRES_DSN` not set -- pre-existing,
  unrelated).
- `ruff check .`: pass. `ruff format --check .`: pass. `mypy` (strict): pass, 204 source
  files. `git diff --check`: clean.

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
