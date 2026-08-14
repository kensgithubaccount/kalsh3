# M25B3 — Environment-Proven Exact-Read Credential Boundary

## Security model found

The existing dashboard vault encrypted credential JSON with a reviewed dependency-free
encrypt-then-MAC primitive: scrypt derives separate AES-256-CTR and HMAC-SHA256 keys, system OpenSSL
performs encryption, and HMAC authenticates the complete versioned envelope. The dashboard database record
did not contain environment provenance, and its setup flow was coupled to account reconciliation and
dashboard state. It therefore could not be reused as an M25B3 provider record.

`RequestSigner` accepts an in-memory unencrypted PKCS#8 PEM, hides it from repr, permits only GET/HEAD,
and passes it to OpenSSL through an inherited pipe. Its `/proc/self/fd` path is Linux-specific. The separate
`production_execution` credential and signer use a production-write type and Linux `memfd_create`; M25B3
does not import, call, migrate, inspect, or modify that domain.

M25B3 extracts only the existing authenticated-encryption primitive into `services.neutral_security`, with
the dashboard module retaining its prior public import. The Perps credential store is otherwise separate.

## Store and lifecycle

The default store is `${XDG_STATE_HOME:-~/.local/state}/kalsh3/perps-exact-read-demo`, outside the
repository. The directory must be a non-symlink mode-0700 directory. Its random 256-bit master-key file and
encrypted credential record must be non-symlink regular mode-0600 files. Reads use no-follow semantics and
bounded sizes; writes use mode-0600 temporary files, `fsync`, and atomic replacement. Truncation, envelope
tampering, schema drift, unknown fields, malformed keys, incorrect modes, symlinks, scope drift, and
environment-less legacy records fail closed. No automatic migration exists.

Credential bytes are not stored as plaintext, and the encrypted record has authenticated integrity
protection. Filesystem ownership and permissions provide the primary local access boundary. The local master
key is stored separately as a file, but `master.key` and `credential.enc` are in the same user-state directory
under the same OS-user security boundary. An attacker who can read both files can recover the credential.
This design does not protect against compromise of the same OS user, root, or a backup or snapshot that
captures both files. It does protect against accidental plaintext disclosure, partial single-file disclosure,
unauthorized different-user access where filesystem controls hold, and ciphertext tampering. This matches
the repository's existing dashboard-vault precedent; no KMS-, HSM-, or keychain-level protection is claimed.

The lifecycle is:

- `UNENROLLED`: no record exists.
- `ENROLLED_UNVERIFIED`: DEMO was explicitly selected and encrypted enrollment completed offline.
- `VERIFIED_DEMO`: the exact enrolled credential authenticated one fixed DEMO GET verification request.
- `DISABLED` or `QUARANTINED`: the provider refuses the credential.

Records include a fixed schema/version, explicit DEMO environment, key ID, keyed non-revealing credential
fingerprint, app-local `read` scope, state, verification target/method/time, and creation/update times. Secret
fields and provider/store objects are repr-redacted. Python cannot promise secure zeroization; the design
instead minimizes plaintext lifetime and copies.

## Environment proof and privilege truth

Environment proof means the credential successfully authenticated exactly
`GET https://external-api.demo.kalshi.co/trade-api/v2/margin/enabled`. The verifier uses the existing
GET/HEAD-only signer and GET-only HTTP interface, disables retry, rejects redirects, never probes production,
and updates the record only after an exact valid response. HTTP 200 with `{"enabled": false}` proves DEMO
authentication provenance while truthfully reporting that Perps entitlement is false. M25B2 still returns
NO_GO for false entitlement.

This does not claim the Kalshi key is server-side read-only. M21/M22 scope conclusions remain distinct: the
M25B2/M25B3 application boundary exposes only fixed GET/read behavior, while server-side privileges are not
inferred from this endpoint. The local provider returns `scopes == frozenset({"read"})` as an application
capability restriction, not an exchange-side privilege claim.

## Enrollment and composition

The manual enrollment CLI accepts DEMO only, an explicit non-secret key ID, and bounded secret bytes from a
file descriptor (stdin by default). It has no PEM argv option, secret environment-variable input, key-file
search, migration, or networking. Enrollment always produces `ENROLLED_UNVERIFIED`.

The normal smoke CLI resolves a `VERIFIED_DEMO` provider before any public or authenticated network work.
Missing, unverified, disabled, quarantined, corrupt, mismatched, or legacy credentials produce a sanitized
nonzero blocker with zero network. A verified DEMO provider may reach the unchanged M25B2 smoke. Production
is refused before credential loading or network access; confirmation cannot unlock it and no override exists.
The injectable lower-level `run_live_smoke()` remains test-oriented and expects its caller to supply a valid
provider; the normal CLI owns the zero-network pre-resolution ordering.

## Milestone truth and next action

All M25B3 verification used fake signers, fake HTTP responses, temporary encrypted stores, and zero network.
No credential was enrolled or verified, and no live smoke was run. Production remains blocked. No write,
trading, strategy, learning, risk, routing, signer-service, or production-execution capability was added.
`production_influence` remains exactly zero and Predictions realtime is unchanged. The known macOS
`/proc/self/fd` and `os.memfd_create` portability failures remain separate.

After independent review and merge, the precise human action is to enroll a DEMO credential, verify DEMO
provenance through the reviewed GET-only verifier, and then explicitly run one tightly bounded DEMO smoke.
