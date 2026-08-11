# ADR 0001: Retain the constrained OpenSSL RSA-PSS adapter for M1

Status: Accepted for M1; revisit when the isolated production signer is built.

## Decision

Retain the current OpenSSL 3 subprocess implementation rather than add Python `cryptography` now.
The environment cannot resolve packages, while `/usr/bin/openssl` is already a pinned container runtime
primitive. The command and algorithm arguments are fixed, no shell is involved, the PEM travels through
an inherited pipe, stderr is never surfaced, and the secret is excluded from `repr`.

## Comparison

Direct `cryptography` would remove process creation, `/proc` and executable-path dependencies, provide
structured exceptions, be more portable across non-Linux Python environments, and align naturally with
a later in-process signer. Both approaches keep PEM bytes in process memory; neither provides hardware
key isolation. OpenSSL adds subprocess/FD/runtime brittleness and less precise errors, but avoids an
unavailable binary wheel and uses a mature audited implementation. RSA-PSS is randomized under both;
deterministic verification tests validate the canonical message and signature rather than signature bytes.

For the eventual isolated signer, prefer `cryptography` (or KMS/HSM) after locked dependency and container
verification. M1's adapter remains read-method-only and is not reusable as a write signer.
