# M27G -- Protected Real Write-Credential Enrollment + Real Signer Validation

Date: 2026-08-18
Branch: `feat/m27g-protected-write-enrollment`
Production mutations: none
Scope: CODE / TEST / REVIEW ONLY -- no live enrollment run, no network call, no order.

## Revision history

1. Original M27G design -- store transaction checked `is_installed()` and then wrote sealed
   artifacts with no cross-process exclusion. **Gemini review: ROLLBACK SAFETY not accepted,
   IMPLEMENTATION_REVIEW_STATUS not safe** -- a cross-process TOCTOU race let two installers
   both observe "not installed" before either published, and a failed installer's blind
   `rollback_failed_install()` could delete a concurrently-succeeding installer's valid
   artifacts. See "Gemini delta repair" below.
2. **This revision**: every write-capable store operation runs inside one `fcntl.flock`
   exclusive lock that spans the entire transaction (state inspection, artifact writes, the
   real-signer self-test, and commit-or-rollback); publication uses true create-only (`O_EXCL`
   on the destination, not temp-file-then-`os.replace`) semantics; an explicit commit marker
   makes "installed" mean "complete and committed," not "some files happen to exist"; and the
   public `rollback_failed_install()` is removed, replaced by a private, lock-scoped rollback
   that can only ever remove the current transaction's own uncommitted artifacts.

## Gemini delta repair: cross-process install/rollback safety (2026-08-18)

**Finding (IMPORTANT):** `_seal_and_write` / `rollback_failed_install` had a cross-process
TOCTOU race. Two installers could both observe "not installed" before either published;
`_atomic_bytes` used `os.replace`, which can silently replace a destination; and a failed
installer's blind `unlink(missing_ok=True)` could delete a concurrently-succeeding
installer's valid installation.

**Root cause:** the transaction was three independent, unsynchronized steps --
`is_installed()` check, sealed-artifact write, and (in the top-level orchestration function)
a *separate* later call to `rollback_failed_install()` after the store method had already
returned. Nothing held any lock across those steps, and nothing prevented a second caller
(same or different process) from running the identical sequence concurrently.

**Fix:**

* `ProtectedWriteCredentialStore.exclusive()` acquires an `fcntl.flock(LOCK_EX)` on a
  dedicated `store.lock` file inside the (0700) store directory, blocking (no timeout, no
  unlocked fallback) until available. `flock` locks are per-*open-file-description*, not
  per-process, so the kernel genuinely arbitrates concurrent callers -- including two threads
  in the same process, which is what makes the new tests able to force real contention without
  subprocesses. The lock is released in `finally` (covering both clean exit and any exception),
  and is also released automatically by the kernel if the holding process crashes.
* `install()` (fixture) and `install_real_credential()` (real) are the *only* two write-capable
  entry points, and both acquire `exclusive()` and hold it for their entire transaction. There
  is no second, unlocked writer path -- every state mutation goes through `_begin_install` /
  `_commit_install` / `_discard_install`, each of which requires a `_StoreLock` token that only
  `exclusive()` can mint.
* `install_real_credential` now owns the *whole* real-install transaction, including the
  caller-supplied real-signer self-test (`self_test` parameter): check -> write (uncommitted)
  -> self-test -> commit-or-discard, all under one lock acquisition. The non-cryptographic
  gates (non-fixture credential, `OperatorReleaseAuthorization` fingerprint match) are checked
  *before* the lock is acquired, since they need no exclusion.
* `_create_only_bytes`/`_create_only_text` replace `_atomic_bytes`/`_atomic_text`: instead of
  writing a temp file and `os.replace`-ing it over the destination (which silently overwrites),
  they `open(path, O_WRONLY|O_CREAT|O_EXCL)` the destination directly -- the kernel atomically
  refuses to create the file if it already exists. This is safe without its own locking
  specifically because it is only ever called from inside the store's exclusive lock.
* A commit marker (`installed.marker`, fixed content, published last) makes `is_installed()`
  require a *complete, committed* set (marker + both sealed artifacts), not merely "some files
  exist." A crash between writing the sealed artifacts and writing the marker leaves state that
  `is_installed()` reports as NOT installed (fail closed); only a later transaction, under the
  same exclusive lock, may clear it and retry -- and it can never be a committed installation,
  because `is_installed()` is checked immediately beforehand and gates every clearing path.
* The public `rollback_failed_install()` is removed. Rollback (`_discard_install`) is now
  private, requires a `_StoreLock`, and is only reachable from inside
  `install_real_credential`'s own lock span, immediately after that same transaction's own
  `_begin_install` -- so it can only ever remove artifacts that transaction itself just wrote,
  and a commit marker is never present at that point (commit is the last step). A failed
  transaction cannot delete a credential that existed before it began.

**Why a commit marker is required, not optional:** two sealed files are always published in
temporal order, and for the real path a self-test that takes real wall-clock time runs
*between* writing them and considering the installation done. Without an explicit "everything,
including the self-test, is done" marker, a reader cannot distinguish "both sealed files exist
because installation finished" from "both sealed files exist because installation crashed
after writing them but before the self-test/commit step" -- exactly the ambiguity M27F/M27G
evidence must never paper over. A plain "both files exist" check (the prior design) could not
express this distinction at all.

No runtime loader path was introduced; `is_installed()`/`exclusive()` remain the only public
surface, and production execution remains unreachable exactly as before.

## Why `enrollment.py` was stale

M27F's live discovery (2026-08-18) proved the least-privilege candidate
(`scopes = {"read","write::trade"}`, `subaccount = 0`) receives `HTTP 401` from
`GET /trade-api/v2/api_keys` when it authenticates as itself -- it cannot prove its own
authority to itself. `services/production_execution/enrollment.py` still contained the
superseded design: `verify_live_write_credential_authority` / `require_live_write_authority`
/ `WriteCredentialServerProof` authenticated *as the candidate* to call that endpoint. M27G
removes that machinery outright rather than leaving it dormant, so it cannot be revived by
accident.

Authority proof now comes from two independently re-validated, already-rendered, secret-free
artifacts the operator supplies:

* a `kalsh3.m27f.candidate-authority.v1` attestation from a *separate* management credential
  (`services/supervised_canary/authority_attestation.py`, unchanged by M27G except its own
  internal delegation -- see below); and
* a **fresh** `kalsh3.m27f.live-read-acceptance.v3` evidence artifact, re-checked for
  freshness *at installation time* -- historical M27F evidence never authorizes installation.

## Layering: one neutral validator, not two copies

`services/supervised_canary` already depends on `services/production_execution.credentials`
for the write-candidate domain constants (`REQUIRED_LIVE_WRITE_SCOPES` /
`REQUIRED_LIVE_WRITE_SUBACCOUNT`). If M27G's `production_execution/enrollment.py` imported
`supervised_canary.authority_attestation.validate_attestation_for_candidate` to re-validate
the same attestation shape, that would create a layering cycle (`production_execution` ->
`supervised_canary` -> `production_execution`).

Fix: the structural attestation validator (`validate_candidate_authority_attestation`), its
scope enum (`KNOWN_KALSHI_API_KEY_SCOPES`), its schema constant, and the shared 30-second
freshness bound (`USER_DATA_FRESHNESS`) moved to a new neutral module,
`services/kalshi_account_gateway/candidate_authority.py`. That package already has zero
dependency on either `production_execution` or `supervised_canary` and is the existing shared
foundation both build on (`auth.py`, `production_read_credentials.py`, `client.py`). Both
`supervised_canary/authority_attestation.py` (`validate_attestation_for_candidate`, now a
one-call thin wrapper) and `production_execution/enrollment.py`
(`validate_authority_attestation_for_installation`) call the same neutral function with their
own `REQUIRED_LIVE_WRITE_SCOPES`/`REQUIRED_LIVE_WRITE_SUBACCOUNT` (identical values, both
sourced from `production_execution.credentials`) rather than keeping subtly different copies.
`production_execution` still does not depend on `supervised_canary` anywhere (`git grep -n
supervised_canary services/production_execution` is empty).

The M27F v3 live-read-evidence validator (`validate_live_read_evidence_for_installation`) is
new in M27G and lives directly in `production_execution/enrollment.py`: it independently
re-validates an already-serialized evidence artifact for a different consumer (installation
gating) than M27F's own generation/reconciliation path, and needs no `supervised_canary`
import beyond the shared `USER_DATA_FRESHNESS` constant (now also sourced from the neutral
module).

## Fresh M27F requirement

`validate_live_read_evidence_for_installation` requires: schema
`kalsh3.m27f.live-read-acceptance.v3`; `environment == "PRODUCTION"`; `key_id_hash` matching
the exact candidate; `candidate_authority.classification == "PASS"` and
`candidate_authority.source == "EXTERNAL_SERVER_ATTESTATION"`; every one of
balance/positions/orders/fills/settlements present with `classification == "SUCCESS"`;
`reconciliation.classification == "PASS"` and `reconciliation.subaccount_binding_verified is
True`; and `0 <= now - completed_at <= 30s`, re-derived from the artifact's own timestamp at
the moment of installation (not merely at the artifact's own creation time). Old schema
versions (`v1`, `v2`, or the unrelated candidate-authority schema) fail the schema check
outright.

## Real installation boundary

`ProtectedWriteCredentialStore.install()` still requires `credential.fixture_only`, so it can
never durably persist a real key, and it remains the always-available path ordinary tests use.
`install_real_credential` is the second, narrowly reviewed entry point, which:

* requires a non-fixture credential (opposite guard from `install()`);
* requires an `OperatorReleaseAuthorization`, a distinct dataclass carrying a candidate
  fingerprint (`sha256(sha256(key_id) : sha256(private_key_pem))`), checked against the exact
  credential being installed -- a stale or swapped-in credential fails closed;
* accepts a caller-supplied `self_test` callable and owns the *entire* real-install
  transaction under one `fcntl.flock` exclusive lock -- state check, sealed writes, the
  self-test, and commit-or-rollback -- never released mid-transaction (see "Gemini delta
  repair" above for why the earlier revision of this was unsafe);
* is called only from `install_production_write_credential`, which is the only place that
  constructs an `OperatorReleaseAuthorization` and the only caller that supplies the real
  `run_real_signer_self_test` as `self_test`.

`is_installed()` gives deterministic already-installed detection -- true only for a complete,
committed installation (commit marker + both sealed artifacts), never a partial/crashed one.
There is no public rollback API: a failed real-install transaction rolls back privately, via
`_discard_install`, only while its own lock is still held, so a key that physically cannot
sign with the real primitive is never left resident, and a failed transaction can never touch
a credential that existed before it began.

## Secret handling

`enrollment_cli.py` accepts `--key-id-file`, `--private-key-fd` (inherited FD only),
`--authority-attestation`, `--live-read-evidence`, `--store-dir`, `--confirm`. The private key
is read via `read_private_key_fd` (reused unmodified from
`kalshi_account_gateway.production_read_credentials`) directly off the integer descriptor --
never argv, an environment variable, or a repository file. `InstallationReceipt`,
`SignerSelfTestResult`, and `EnrollmentOutcome` are hash/classification-only and redact in
`repr()`; the CLI's error path prints only `type(exc).__name__`, never exception detail that
could carry a key ID or PEM fragment.

## Real signer self-test

`services/production_execution/signer_self_test.py` is a new, separate, non-network boundary.
It reuses `security_boundary._rsa_pss_sha256` **unmodified** -- the exact primitive
`SignAndSendBoundary` calls -- so a passing self-test proves the installed key works with the
real production signing runtime, not a reimplementation of it. It signs a fixed,
domain-separated challenge (`kalsh3.m27g.signer-self-test.v1|<key_id_hash>|<timestamp>`) that
can never collide with Kalshi's own `f"{timestamp_ms}{METHOD}{PATH}"` request-signature
message: the challenge always begins with a non-numeric schema string, while a real message
always begins with digits. It derives the public key via `openssl pkey -pubout` (stdin only,
never touches disk with the private key) and verifies the signature locally via
`openssl dgst -verify`. It never imports a transport, never touches `ProductionJournal`, and
never calls `SignAndSendBoundary`.

## Disarmed guarantees (unchanged)

`SignAndSendBoundary.production_execute` and `offline_fixture_execute` are byte-for-byte
unchanged (`git diff services/production_execution/security_boundary.py` is empty).
`production_execute` still unconditionally raises `"production state is DISARMED; live
transport is unreachable"`. No arm CLI was added. `FixedKalshiProductionTransport` /
`ExactByteSender` / order create-cancel-amend-decrease transport are untouched. Neither
`enrollment.py`, `enrollment_cli.py`, nor `signer_self_test.py` references `send_exact`,
`ExactByteSender`, `http.client`, `urllib.request`, or `possibly_sent` (enforced by a
source-scan test).

## `enrollment_available()` after M27G

Remains `False`. This is a deliberate design decision, not an oversight: M27G adds a real
install *code path*, but that path is reachable only by an operator manually invoking
`enrollment_cli.py` with real secrets, a real attestation, and fresh live-read evidence --
none of which exist anywhere in this repository or its runtime. Flipping `enrollment_available()`
to `True` would tell unrelated runtime/UI code "live enrollment is generally reachable right
now," which is false -- no automated path, schedule, request handler, or dashboard action
calls `install_production_write_credential`. The narrower, honest signal that a real install
is architecturally possible (given secrets this repository never holds) is the existence of
`install_real_credential` / `install_production_write_credential` themselves -- an
operator-release capability, not a generally-available one.

`services/supervised_canary/readiness_report.py` is unchanged in this milestone:
`PRODUCTION_WRITE_CREDENTIAL` / `REAL_SIGNER_VALIDATION` still hardcode `NOT INSTALLED` /
`BLOCKED_BY_CREDENTIAL`. No real enrollment run occurs in this milestone, so there is no
receipt to wire into the readiness display yet; speculatively wiring a consumption path for
an artifact shape that has never been produced by a real run would itself be an unreviewed
trust path. That wiring is deferred to whenever an operator actually completes a real
enrollment run in a future authorized session.

## Tests

73 focused M27G tests (`tests/test_m27g_protected_write_enrollment.py`), including 13
dedicated cross-process locking/concurrency tests added for the Gemini delta repair:

* two concurrent real installers (different threads, `threading.Barrier`-synchronized start)
  -- exactly one succeeds, the self-test/transaction never overlaps (`ConcurrencyProbe`
  tracks `max_active`, asserted `== 1`);
* a forced-to-fail installer A and a concurrent installer B, using `threading.Event`s to prove
  B genuinely blocks (has not even entered its transaction) until A's self-test fails and rolls
  back and its lock is released -- then B succeeds and A never touched B's artifacts;
* a successful installer A followed by a second attempt B against the same store -- B is
  rejected "already installed," and A's master key / record / commit marker bytes are proven
  byte-for-byte unchanged afterward (no silent overwrite);
* fixture and real installs racing the same store -- exactly one can ever succeed;
* rollback against a store with a pre-existing valid installation -- the self-test is proven to
  never even run (empty call-tracking list), and the existing installation is untouched;
* a forced failure after the master key is written but before the record -- cleanup is
  complete, and a subsequent transaction recovers cleanly;
* four parametrized partial/corrupt-state scenarios (master-only, master+record without a
  marker, master+record with a corrupted marker, marker alone) -- `is_installed()` reports
  `False` for every one, and each recovers under the lock on the next transaction;
* an exception raised inside `store.exclusive()` -- a subsequent acquisition from another
  thread does not hang;
* lock file permission (0600) and no-secret-content checks.

`tests/test_m27e_live_readiness.py` had its now-obsolete `verify_live_write_credential_authority`
/ `require_live_write_authority` / `WriteCredentialServerProof` / old `enroll_live_write_credential`
tests removed (that API no longer exists, unrelated to this delta); its unrelated M27E coverage
(pipe/memfd signer fallback, fixed transport, readiness-report evidence classes, V2 wire-shape
translation) is untouched. Full M27F suite (`test_m27f_candidate_authority_attestation.py`,
`test_m27f_live_read_acceptance.py`) and M15/M16 production-safety suites pass unchanged. No
external Postgres was required for any of the new concurrency tests (pure in-process threading
against real `fcntl.flock` kernel locks).

## Verification

- `uv run ruff check .` -- clean
- `uv run ruff format --check .` -- clean
- `uv run mypy` (strict) -- clean, 207 source files
- `git diff --check` -- clean
- Full test suite -- pass (see `IMPLEMENTATION_STATUS.md` for the exact count)
- `git diff --stat services/supervised_canary` -- only `authority_attestation.py` (import
  delegation), no behavior change to `live_read_acceptance.py`, `readiness_report.py`,
  `readiness.py`, `workflow.py`, `m27d.py`, or `store.py`
- `git diff services/forecasting` -- empty
