# M25B4 — Portable Read-Only Signing Boundary

## Defect and mechanism

The shared `RequestSigner` already transferred its PKCS#8 private key to system OpenSSL through an
anonymous pipe, but told OpenSSL to read that inherited descriptor through `/proc/self/fd/<n>`. Linux
normally mounts procfs there; macOS does not expose `/proc`, so otherwise valid signing failed before
OpenSSL could read the key.

M25B4 retains the anonymous pipe and changes only its descriptor interface to `/dev/fd/<n>`. On macOS,
`/dev/fd` is a native interface and removes the prior `/proc/self/fd` portability failure. On mainstream
Linux, `/dev/fd` commonly resolves to `/proc/self/fd`, so Linux still assumes a normal procfs-backed runtime.
M25B4 therefore supports the project's normal macOS and Linux targets, but does not claim compatibility with
minimal Linux containers or environments lacking procfs. This does not change or weaken the previous working
Linux behavior. The parent starts OpenSSL with only the pipe's read end listed in `pass_fds`, closes its own
read end immediately, writes the complete key through the parent-only write end, and closes that end before
sending the signing message on subprocess stdin. Python creates pipe descriptors close-on-exec by default;
`pass_fds` narrowly makes only the read end available for this child exec while `close_fds=True` closes
unrelated descriptors. The child never inherits a writer, so EOF is deterministic. Starting the reader
before writing also avoids dependence on pipe capacity. All descriptors are closed on success and failure,
and setup or OpenSSL failure is sanitized and fails closed.

The key remains memory-only: it is absent from argv and environment, no PEM or temporary key file is
created, and OpenSSL stderr is not exposed. The request message still uses subprocess stdin, so key and
message have distinct, unambiguous streams. The executable, RSA-PSS/SHA-256 options, digest salt-length
setting, access-key header, timestamp, method normalization, canonical request-path behavior, and query
exclusion are unchanged. RSA-PSS remains protocol-compatible; its randomized salt means signatures need
not be byte-identical across invocations.

## Capability and composition boundaries

`RequestSigner` still rejects every method except GET and HEAD before starting OpenSSL. No order, cancel,
amend, transfer, risk, sizing, routing, signer-service, or other write path was added. The signer contains no
DEMO or PRODUCTION knowledge and remains environment-neutral; exact environment binding remains the
credential-provider/composition layer's responsibility.

M25B3 credential composition remains DEMO-only. M25B5 later adds a separate neutral production read-only
credential lifecycle without changing this signer or unlocking a production smoke. The separate
`services.production_execution` credential and `os.memfd_create` signer were inspected only for comparison
and were not changed. Predictions realtime and dashboard credential behavior retain their existing APIs and
signing semantics.

## Verification and milestone truth

Tests use locally generated ephemeral PKCS#8 keys and local OpenSSL only. They cover GET/HEAD signing,
existing case normalization, write-method rejection before process creation, malformed and invalid key
sanitization, repr redaction, argv/environment exclusion, narrow descriptor inheritance, absence of
temporary-file, `/proc/self/fd`, and `memfd_create` dependencies, exact message semantics, and the unchanged
environment-neutral boundary.

No live smoke or network request was run, no credential was read, enrolled, or used, and no trading/write
capability was enabled. `production_influence` remains exactly zero. A later independently reviewed
milestone may generalize credential composition for a dedicated production read-only credential without
changing this signer primitive.
