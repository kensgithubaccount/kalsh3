# M27B.3R4 — fail-closed retention ledger repair

Status: ready for independent review; no live smoke or pilot was run by this change.

This is a repair of `services/opportunity_engine/auditable_retention.py` and its integration in
`services/opportunity_engine/structural_measurement_runner.py`, addressing 5 confirmed
vulnerabilities found by independent review of the M27B.3R3 design (see
`M27B3R3_AUDITABLE_RETENTION.md`) plus additional hardening. It adds no authentication,
credentials, execution, order, capital, signer, or risk authority; `production_influence`
remains `0` on every receipt, enforced as an exact-integer-zero check, not a truthy check.

## What was actually wrong in R3

1. **Missing evidence was silently skipped.** `_path_stats` did `if not path.is_file(): continue`
   — a deleted or never-created primary evidence database was simply omitted from the byte count
   and the receipt, instead of raising.
2. **State was trusted blindly.** `_state()` read and schema-checked `retention-state.json` but
   never reloaded or validated a single receipt it referenced. A deleted, tampered, or forged
   receipt (or a state file hand-edited to reference one) was invisible to the ledger.
3. **The symlink guard was a tautology.** `auditable_retention.py:138-141` resolved the path
   *before* comparing `path.parent` to `path.parent.resolve()`, so the comparison was always
   `X == X` and never raised, regardless of symlinked ancestry.
4. **The projection used only the latest delta.** `observed_delta = max(0, byte_count - prior)`
   used only the most recent scan's growth. A large scan-1 followed by a tiny scan-2 dropped the
   running 24-hour projection by nearly the entire scan-1 growth, hiding the true worst-case trend
   from the budget gate.
5. **The free-space check ignored the ledger's own growth trend.** `free <= floor` compared only
   the currently observed free space to the hard floor; a scan large enough to cross the floor
   during acquisition was not rejected in advance if free space happened to still read above the
   floor at the moment of the check.

All five were reproduced against `79cb2ea43ad746141aefdd23f8cad9059e6cd27b` and are closed at this
head; see `tests/test_m27b3r4_retention_repair.py`.

## What changed

**Required evidence (gap A).** Every primary evidence path passed to `check_before_scan` and
`record_scan` must exist, be stable, and be a regular file — verified by opening with
`O_NOFOLLOW` and checking `S_ISREG` on the resulting file descriptor, both at preflight and again
at receipt creation (`_primary_stat` / `_hash_primary` in `auditable_retention.py`). A missing or
unstable primary raises `RetentionGateError`; it is never omitted or recorded as zero bytes.
`-wal`/`-shm` sidecars remain optional (`_sidecar_stat` / `_hash_sidecar` return `None` on
`FileNotFoundError`), but if present they are validated exactly like a primary — a sidecar that
exists but is not a stable regular file also fails closed.

**Receipt and state chain validation (gap B).** `_load_and_validate_state` runs on every
`check_before_scan` and every `record_scan` (not only at construction). It: parses and exactly
schema-checks the state JSON; loads every receipt referenced by `state["scans"]`; recomputes each
receipt's `receipt_id` from its canonical JSON and rejects any mismatch; cross-checks
`scan_run_id`, `cumulative_bytes`, and `sample_count` between each state entry and its receipt;
rejects duplicate `scan_run_id` or `receipt_id` values within state; rejects a decreasing
`cumulative_bytes` sequence; and lists the receipts directory to detect orphan receipts (files not
referenced by any state entry, the signature of a crash between receipt and state publication) and
fails closed on them rather than silently adopting or discarding them. Nothing is ever silently
reset — every failure mode raises `RetentionGateError`.

**Path and symlink safety (gap C).** The tautological guard is gone. `_reject_symlink_ancestry`
walks every existing ancestor directory of an evidence path and rejects any that is itself a
symlink; the final path component is rejected by opening with `os.O_RDONLY | os.O_NOFOLLOW`
(`ELOOP` on a symlink), which is race-free against a last-instant swap in a way a separate
`is_symlink()` check on the leaf never is. Hashing (`_hash_open_fd`) reads through the already-open
file descriptor — a path replaced after the open cannot change what bytes are actually hashed —
and after the read completes, `os.stat(path, follow_symlinks=False)` is compared against the
original `fstat` by `(st_dev, st_ino)`; any identity change (a swap, or the path becoming a
symlink) raises `RetentionGateError("... identity changed during hashing ...")`. The evidence path
set is also pinned to the paths approved on the first scan (`state["evidence_paths"]`); a later
call with a redirected path — even a non-symlink substitution — is rejected
(`"redirection is not permitted"`). Ledger writes (`retention-state.json`, `retention-receipts/`,
`retention.lock`) are always derived from the ledger's own `root`, never from caller-supplied
evidence paths, so they cannot be redirected outside the intended retention directory.

**Conservative (high-water) projection (gap D).** Each `record_scan` now persists
`growth_high_water_bytes = max(previous_growth_high_water, observed_delta)` where
`observed_delta = max(0, byte_count - prior_cumulative)`, and the projection is
`byte_count + growth_high_water_bytes * remaining_scans` where
`remaining_scans = max(0, expected_scans - sample_count)`. A later small scan can no longer erase
an earlier large scan's contribution to the projection. `remaining_scans` is clamped to
non-negative at, below, and above `expected_scans`.

**Free-space reservation (gap E).** `check_before_scan` now rejects when
`free_space - growth_high_water_bytes < free_space_floor_bytes` — using the ledger's own known
per-scan growth trend, not only the instantaneous free-space reading — in addition to the existing
projected-budget check. A scan capable of crossing the floor is rejected before acquisition begins,
even when current free space alone still reads above the floor.

**Concurrency, retries, and crash recovery (gap F).** `check_before_scan` acquires an exclusive,
non-blocking `flock` lease on `retention.lock` inside the retention directory (the same
`fcntl.flock` pattern already used by `StructuralMeasurementStore.cycle_lock`); `record_scan`
releases it in a `finally` block regardless of success or failure, and `abort_scan()` releases it
explicitly for a caller that catches a mid-scan exception. `run_forever` in
`structural_measurement_runner.py` now wraps `run_scan_cycle` in `try/except BaseException` and
calls `retention.abort_scan()` before re-raising, so a crash mid-scan releases the reservation
instead of leaking it. A second process (or a second `AuditableRetentionLedger` instance) that
tries to acquire the lease while it is held fails closed with `RetentionGateError` and touches no
evidence; if the holder crashes hard (`os._exit`, no cleanup), the OS releases the `flock`
automatically on process exit, so the next attempt succeeds cleanly. Duplicate `scan_run_id` values
are handled exactly-once: a repeat call with unchanged evidence returns the identical prior receipt
without appending a new `scans` entry or double-counting; a repeat call whose evidence has actually
changed is rejected outright. Receipt publication remains content-addressed and happens before
state publication; state publication remains fsynced and atomic via the existing `_atomic_json`
(mkstemp + fsync + `os.replace` + directory fsync) — unchanged, just reused for a richer schema.

**Strict numeric and policy validation (gap G).** Every persisted numeric field is checked with
`type(value) is int` (which excludes `bool`, since `bool` is a `int` subclass in Python) and a
`>= 0` or `> 0` bound as appropriate; a `float` or numeric string in place of an int is rejected
the same way. `production_influence` must be exactly the int `0`. State and every receipt are
checked against an exact expected key set (`_require_exact_keys`) so an injected/unknown field —
which could otherwise be silently ignored today and trusted by a future reader — is rejected.
State records the policy (`budget_bytes`, `free_space_floor_bytes`, `expected_scans`) it was
created under; on every reopen this is compared to the ledger's currently configured policy, and
any mismatch is rejected as "policy has changed without an explicit reviewed migration" — an
operator who genuinely wants to change the bound must start a fresh retention directory
deliberately, not have it silently reinterpreted.

## Schema break

`SCHEMA_VERSION` moved to `kalsh3.m27b3r4.retention-receipt.v1` and a new
`STATE_SCHEMA_VERSION = kalsh3.m27b3r4.retention-state.v1` was introduced. R3 state/receipts are
therefore incompatible with R4 and will be rejected as "schema is incompatible" on reopen. This is
intentional and safe: no live smoke or pilot was ever run under R3, so no production retention
state exists to migrate.

## Safety and reconstruction boundaries (unchanged from R3, reconfirmed)

- incomplete refreshes append no structural observations or lifecycle events;
- partial source pages remain source evidence and are marked incomplete, never measured;
- page/cursor provenance, market/event identity, semantic inputs, quote timestamps, economics
  inputs, authority, and exact structural observations remain reconstructable through the existing
  archive verifier;
- content hashes bind compressed storage to the original canonical bytes (compression happens
  strictly after the identity hash is taken over canonical UTF-8 JSON; round-trip and independent
  hash reconstruction is covered directly against `_pack_canonical`/`_unpack_canonical`/
  `_hash_bytes` in `tests/test_m27b3r4_retention_repair.py::TestCompressionRoundTrip`);
- receipt publication is atomic and crash-safe; an orphan receipt is detected and fails closed
  rather than being silently treated as evidence;
- the runner remains public-read-only and research-only, with no authentication, execution, order,
  capital, signer, or risk authority imports.

## Tests

`tests/test_m27b3r3_auditable_retention.py` (updated for the gap-A preflight-existence contract —
evidence must exist before `check_before_scan`, not only before `record_scan`) and
`tests/test_m27b3r4_retention_repair.py` (46 new adversarial cases across gaps A-G, concurrency,
compression, and incomplete-refresh accounting) both pass, alongside the unchanged
`tests/test_structural_measurement_runner.py`. Independent review is required before any live
smoke or the 24-hour pilot.
