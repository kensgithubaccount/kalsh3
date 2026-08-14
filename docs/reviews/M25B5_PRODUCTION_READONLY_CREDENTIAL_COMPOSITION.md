# M25B5 — Production Read-Only Credential Composition

## Purpose and architecture

M25B5 implements a production credential lifecycle for one dedicated Kalshi API key that the exchange
itself proves has exactly read-only server scope. It does not enroll or verify a real credential and does
not unlock a production Perps smoke or any execution path.

The shared `ReadEnvironment`, `ExactReadCredential`, and `ExactReadCredentialProvider` contracts now live in
the neutral account-gateway boundary. Perps retains compatible imports, so M25B2/M25B3 APIs do not change.
The production store, verifier, provider, and CLI also live in the account-gateway boundary rather than the
Perps research package. Predictions and Perps can therefore consume the same reviewed production read
credential in later separately reviewed composition work; M25B5 wires neither consumer to it.

At M25B5, the Perps `run_live_smoke()` orchestration rejected production as its first operation, before
provider resolution, signer or transport construction, REST or WebSocket use, and evidence-store mutation.
M25B6 replaces that blanket rejection with a reviewed nominal gate requiring this exact verified provider
backed by exactly `ProductionReadCredentialStore`; generic providers, duck stores, wrappers, and subclasses
cannot unlock production.

The production record is separate from the M25B3 DEMO store, dashboard legacy state, and
`production_execution` write credentials. DEMO enrollment or verification is not a prerequisite and no
DEMO record is migrated or probed.

## Production provenance and server-side scope proof

Verification makes exactly one authenticated request:

```text
GET https://external-api.kalshi.com/trade-api/v2/api_keys
Signed path: /trade-api/v2/api_keys
```

The verifier has a single fixed origin, method, and path; it has no query, redirect, retry, alternate host,
DEMO fallback, mutation method, or key creation/deletion operation. The exact enrolled key ID must appear
once in the returned `api_keys` collection and its `scopes` value must be exactly `['read']`. Harmless extra
metadata on the response or key record is tolerated, but it cannot substitute for or weaken exact key-ID
and scope validation.

Authentication against this fixed endpoint proves production environment provenance. The returned exact
key record separately proves server-side scope. Local `read` metadata, key names or shapes, prior records,
and the GET/HEAD-only `RequestSigner` cannot establish either fact. This separation is defense in depth:

1. Kalshi reports server-side scope exactly `read`.
2. The application credential exposes only `frozenset({'read'})`.
3. `RequestSigner` signs only GET/HEAD.
4. A future collector remains limited to its read-only endpoints.

A syntactically valid scope list other than exactly `['read']` moves the local record to `QUARANTINED`; this
means Kalshi positively returned a syntactically valid but unacceptable/non-exact server-side scope set. It
does not claim that every quarantined response specifically proves write access. The credential cannot
resolve. M25B5 never deletes, rotates, or otherwise modifies the remote key.
Missing or ambiguous keys, absent/empty/malformed scopes, malformed responses, 401/403, redirects, and
transport failures do not establish provenance or scope and leave an enrolled credential unverified.

## Lifecycle, storage, and race protection

The explicit states are:

- `UNENROLLED`: no record exists.
- `ENROLLED_UNVERIFIED`: encrypted enrollment completed, but production provenance and scope are unproven.
- `VERIFIED_PRODUCTION_READONLY`: exact production authentication, key identity, and `read`-only scope were
  proven.
- `DISABLED`: local use is disabled.
- `QUARANTINED`: Kalshi positively returned a syntactically valid but unacceptable/non-exact server-side
  scope set; local use is blocked. This does not necessarily prove write access.

The default store is `${XDG_STATE_HOME:-~/.local/state}/kalsh3/production-exact-read`, outside the
repository and separate from `${XDG_STATE_HOME:-~/.local/state}/kalsh3/perps-exact-read-demo`. It reuses the
reviewed neutral authenticated-encryption primitive. The directory is non-symlink mode 0700; master-key and
encrypted-record files are non-symlink regular mode 0600. Reads use no-follow semantics and bounded sizes;
writes are bounded, fsynced, and atomically replaced. Schema, environment, lifecycle, verification metadata,
fingerprint, key identity, server scopes, permissions, and ciphertext integrity fail closed.

The master key and encrypted record share one OS-user security boundary. This does not claim protection
against the same user, root, a combined backup/snapshot, or offer KMS, HSM, or keychain isolation. It does
prevent plaintext-at-rest storage, casual cross-user access where filesystem controls hold, partial-file
interpretation, and undetected ciphertext modification.

Verification records the exact key ID and keyed credential fingerprint it checked. Immediately before any
verified or quarantined transition, it reloads the record and compares key ID, fingerprint, update time, and
unverified state. Reenrollment or replacement during an in-flight request therefore prevents a stale
response from updating the replacement credential.

An explicit later `enroll()` call may replace any existing record, including a quarantined record, with a
new `ENROLLED_UNVERIFIED` record. This is an intentional human reenrollment action, never an automatic state
transition. `verify()` cannot restore a quarantined credential; the provider continues to reject it.

## Enrollment, composition, and remaining human action

The manual CLI requires explicit `--environment production`, accepts the non-secret key ID normally, and
reads bounded private-key bytes from `--credential-fd` (stdin by default). PEM data is absent from argv,
environment variables, logs, repr, errors, documentation, and plaintext temporary files. Enrollment always
produces `ENROLLED_UNVERIFIED`.

The provider resolves only internally consistent `VERIFIED_PRODUCTION_READONLY` records for the production
environment and returns the existing exact-read credential with application scope exactly `read`. Missing,
unverified, disabled, quarantined, corrupt, mismatched, or legacy/environment-less local state fails before
network use. No production-write type or `services.production_execution` path is imported, inspected,
migrated, or composed.

After independent review and merge, the human action is to create one dedicated production API key manually
with Kalshi server-side scope `read` only, enroll its key ID and PEM once through the reviewed FD input, and
run the explicit production verification command. No DEMO lifecycle is required first. M25B6 later composes
this provider into a tightly bounded, explicitly invoked production read-only Perps smoke.

No real credential was used or enrolled, no network or live smoke was run, and production execution remains
disarmed. No write/trading capability was added, Predictions realtime is unchanged, and
`production_influence` remains exactly zero.

## Exception compatibility

Invalid direct construction of the shared `ExactReadCredential` now raises the neutral
`ReadCredentialError`, not the former Perps-specific `ShadowResearchError`. Preserving the former type would
require the neutral account-gateway contract to depend on Perps, duplicate or wrap the credential class, or
otherwise lose the shared class identity. Current runtime construction sites supply validated stored
credentials and no runtime caller catches `ShadowResearchError` around direct credential construction. The
new behavior is therefore explicit and regression-tested.
