# M27B.3R4.2 — cold-start bootstrap repair

Status: additive repair ready for independent review; no live smoke or pilot was run.

This closes a bootstrap-ordering defect discovered by the first-ever bounded public-read smoke
attempt against the merged M27B.3R4.1 retention system (`M27B3R4_RETENTION_LEDGER_REPAIR.md`):
`run_forever` called `retention.check_before_scan` -- which R4.1 correctly requires every primary
evidence file to already exist and be stable -- before `run_scan_cycle`/`refresh_universe` ever
created the local universe archive (`universe.sqlite`). On a genuinely fresh run directory (which
the reviewed `scripts/run_m27b3_smoke_receipt.py` wrapper always creates), no prior scan had ever
populated that archive, so the very first live scan was unconditionally rejected before any
acquisition began. `tests/test_m27b3r4_retention_repair.py` and
`tests/test_m27b3r3_auditable_retention.py` always pre-created evidence files in their fixtures, so
this true cold-start path was never exercised until the live smoke attempt.

## What changed

**Cold-start bootstrap (`services/opportunity_engine/auditable_retention.py`).**
`AuditableRetentionLedger.check_before_scan` accepts a new optional keyword-only
`bootstrap_primaries: Mapping[Path, Callable[[Path], None]] | None` parameter. For each primary in
`bootstrap_primaries`, bootstrap is attempted only when both hold:

* the ledger is demonstrably pristine (`_is_pristine_ledger`) -- no `retention-state.json` file
  and no files in `retention-receipts/` at all, checked directly against the filesystem rather
  than derived from an in-memory `state` value, so a state file that fails to parse/validate never
  looks pristine (`_load_and_validate_state` already raises first) and an orphan receipt left by a
  crash with no state file published yet also blocks it, even though
  `_load_and_validate_state`'s own early return for a missing state file does not itself scan the
  receipts directory;
* the primary is genuinely absent (`_primary_is_absent`) -- never true for a symlink, dangling or
  not, or any wrong-typed entry, which must hard-fail through the unchanged `_primary_stat` check
  exactly as before.

The existing free-space floor check already runs first in `check_before_scan` (`growth` is always
`0` for a pristine ledger, so it reduces to "free space already below the floor"), so bootstrap
never writes when the floor is already breached -- no new check was needed for this.

`_bootstrap_primary` creates the new file securely: it rejects a symlinked leaf outright, opens
every parent component relative to an already-held descriptor with `O_DIRECTORY | O_NOFOLLOW` (the
same traversal `_open_parent_chain` already uses for every other evidence access in this module),
then creates the leaf with `O_CREAT | O_EXCL | O_NOFOLLOW` through the held parent descriptor --
rejecting a concurrent creator or a symlink race rather than silently adopting or writing through
either. Only after that secure creation does it hand the real path to the caller's own canonical
schema initializer; an initializer failure (partial or immediate) is wrapped as
`RetentionGateError` and never leaves partial state published. The unchanged
`for primary in primaries: _primary_stat(primary)` preflight loop then validates the result exactly
as it always has -- bootstrap never overrides or shortcuts that check.

Once any state file exists -- even with zero recorded scans, e.g. a crash between
`check_before_scan` succeeding and `record_scan` running -- `_is_pristine_ledger` is `False` and a
missing primary is unconditionally a hard failure again, identical to R4.1.

**Runner wiring (`services/opportunity_engine/structural_measurement_runner.py`).**
`run_forever` passes `bootstrap_primaries={archive_evidence_path: _bootstrap_universe_archive}`.
`_bootstrap_universe_archive` initializes the archive through the exact canonical
`UniverseObservationArchive` constructor -- the same one `refresh_universe` itself uses -- never a
hand-rolled placeholder file. `store.path` (the evidence database) is never included in the
bootstrap mapping: it is always already created eagerly by `StructuralMeasurementStore.__init__`
before retention is ever constructed, so it is never actually absent at this point; if it somehow
were, the unchanged hard-failure path applies to it exactly as before.

**WAL-checkpoint stabilization (also `structural_measurement_runner.py`).** Reproducing the true
cold-start integration test end-to-end surfaced a second, independent issue: a live, actively
written SQLite WAL database's total on-disk footprint (main file plus `-wal`/`-shm` sidecars) is
not actually monotonically non-decreasing when sampled at arbitrary points in its checkpoint
lifecycle, even though the data it holds never shrinks -- SQLite can defer merging WAL frames into
the main file and removing the sidecars until an unpredictable later connection close. Retention's
own growth-can-only-increase check (correctly, from R4.1) rejects a byte-count decrease as a
possible evidence-rollback attack, so this raced intermittently: a receipt recorded with a smaller
measured footprint than the ledger's own captured baseline failed the reload-time monotonicity
check. This was reproduced directly (roughly 1 in 20-50 runs) and is unrelated to whether the
archive was bootstrapped cold-start or already existed -- it is a general property of tracking a
live WAL database as retention-primary evidence that no prior R3/R4/R4.1 test exercised, because
every prior retention test used plain byte-written fixture files, never a real growing SQLite/WAL
database.

The fix does not weaken the monotonicity check (which remains essential against genuine rollback):
`_checkpoint_sqlite_wal` forces `PRAGMA wal_checkpoint(TRUNCATE)` on a primary immediately before
`record_scan` measures it (both archive and evidence store, every scan, not just the first), and
`_bootstrap_universe_archive` checkpoints the archive itself right after creating it, so the
baseline capture inside `check_before_scan` also measures a checkpointed, stable file. A
`gc.collect()` runs before `check_before_scan` too, so any not-yet-finalized connection from an
earlier write in the same scan (this module's and `StructuralMeasurementStore`'s own short-lived
per-call connections) is closed before that baseline measurement -- deliberately *not* accompanied
by a direct file open on either evidence path at that point, since neither path has been validated
by retention yet in that iteration; `gc.collect()` touches no file at all, so it adds no bypass of
the descriptor-relative symlink/TOCTOU protections. `_checkpoint_sqlite_wal` itself is only ever
called against a path retention has already validated moments earlier in the same call (right
before `record_scan`, after `check_before_scan` already ran `_primary_stat` on it this iteration)
or a path this process just securely created itself (`_bootstrap_primary`'s `O_CREAT | O_EXCL`
leaf) -- never against a caller-supplied path that has not yet passed retention's own check. An
earlier version of this fix checkpointed both primaries unconditionally *before*
`check_before_scan`, which both changed the exception surfaced for a pre-existing malformed archive
(`sqlite3.DatabaseError` instead of retention's own `RetentionGateError`/`ArchiveError` path) and,
more importantly, would have opened a caller-supplied path with a raw `sqlite3.connect()` ahead of
any symlink validation; both were caught by the accompanying tests and reverted before this change
proceeded to the quality gates below.

Verified via 150 consecutive true-cold-start runs of a standalone repro script (mocked transport,
tmp-dir archive and evidence store, `expected_scans=1`) with zero failures after the fix, versus
reproducible failures within the first \~20-50 runs before it.

## Safety and reconstruction boundaries (unchanged from R4.1, reconfirmed)

- no authentication, credentials, execution, order, capital, signer, or risk authority is added;
  `production_influence` remains the exact int `0` on every receipt;
- once any state exists, missing evidence is always a hard failure -- bootstrap is reachable only
  for a demonstrably pristine ledger and a genuinely absent primary, never a re-creation or silent
  replacement of anything that ever existed;
- descriptor-relative `O_NOFOLLOW` traversal, the single-writer `flock` lease, exactly-once
  duplicate-`scan_run_id` handling, and crash-safe content-addressed receipt publication are all
  unchanged;
- an incomplete refresh still appends no structural observations or lifecycle events -- retention
  still requires and records evidence accounting for it independently, exactly as in R4.1.

## Tests

`tests/test_m27b3r4_2_cold_start_bootstrap.py` adds: a true cold-start integration test (neither
database pre-created, mocked public transport, one complete scan, a valid reload-verified retention
receipt); missing-archive-after-baseline remains fatal (both after a completed scan and after a
pinned baseline with zero scans, plus an orphan-receipt-without-state-file variant); a
pre-existing malformed archive passes retention's own preflight (it only checks stability/type) but
is rejected by the real scan's own schema validation, and bootstrap never touches an
already-present file; a dangling symlink and a symlink to a real file are both rejected even with
bootstrap offered, and `_bootstrap_primary` itself rejects a symlink directly; the free-space floor
check runs before bootstrap ever attempts a write; two partial/crashed-bootstrap cases -- an
initializer that fails before any write (recovers cleanly on retry through the real initializer's
own existed-but-empty branch) and one that fails after a partial, non-schema write (fails closed on
retry rather than being silently adopted or repaired); concurrent bootstrap cannot both proceed
(the existing exclusive lease already covers this, confirmed specifically for the bootstrap path);
and an incomplete refresh on the bootstrapped first scan still records no structural observations
while retention still accounts for the evidence.

`tests/test_m27b3r3_auditable_retention.py`, `tests/test_m27b3r4_retention_repair.py`, and
`tests/test_structural_measurement_runner.py` are unchanged and continue to pass unmodified,
confirming no R4.1 behavior regressed. Full `pytest` (3431 passed, 3 skipped -- an unrelated
Postgres suite gated on an unset DSN), Ruff lint and format, strict mypy (277 source files, no
issues), Bandit (`-r services -ll -iii`: 0 High-severity findings; the two changed files alone:
zero findings at any severity), `detect-secrets scan`, and `git diff --check` all pass. Independent
review is required before any further live smoke or the 24-hour pilot.
