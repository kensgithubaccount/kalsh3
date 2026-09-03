# M27B.3R4.3 — retention capacity policy repair

Status: additive repair ready for independent review; no live smoke or pilot was run under this
policy. Grants no prospective, trading, capital, or execution authority.

## Why 24 GiB was empirically insufficient

The post-merge M27B.3R4.2 live smoke positively exercised the cold-start bootstrap (archive
created, `check_before_scan` succeeded, live public acquisition began and ran cleanly) and then
stopped fail-closed, correctly, at the projection check in `record_scan` -- not from any bootstrap,
WAL, symlink, or retention-integrity defect. A subsequent read-only capacity audit (M27B.3R4.3)
independently recomputed the projection directly from `record_scan`'s own formula and confirmed it.

**Exact observed first-scan growth**: 302,743,552 bytes (`before_scan_bytes=98,304`;
`universe.sqlite` after acquisition `302,792,704` bytes; `observations.sqlite` `49,152` bytes;
`byte_count = 302,841,856`; `observed_delta = byte_count - before_scan_bytes = 302,743,552`).

**Exact code projection**, reproduced from `record_scan` (`services/opportunity_engine/
auditable_retention.py`): `growth_high_water = 302,743,552` (max over the one observed delta);
`remaining_scans = expected_scans(96) - sample_count(1) = 95`; `projected_24h_bytes = byte_count +
growth_high_water * remaining_scans = 302,841,856 + 302,743,552*95 = 29,063,479,296 bytes =
27.067474365234375 GiB`. This is the code-exact figure, independently reproduced by a dedicated
regression test (`tests/test_m27b3r4_3_retention_capacity_policy.py::
TestRetentionEnforcesReviewed28GiBBudget::test_audited_projection_figures_are_reproducible_from_the_supplied_measurements`)
that recomputes it from the same three raw measurements rather than merely asserting a constant.

That exceeds the prior approved 24 GiB (25,769,803,776 byte) budget by 3,293,675,520 bytes
(3.067474365234375 GiB). Nothing about compression, deduplication, or storage-implementation
efficiency is responsible for this -- see below -- the prior budget was simply undersized against
the exchange's actual current scale, and this is the first time that scale was ever empirically
measured against the reviewed 96-scan/900-second-cadence contract.

## Why 28 GiB was chosen

28 GiB (`30,064,771,072` bytes) clears the code-exact 27.067 GiB projection with
`30,064,771,072 - 29,063,479,296 = 1,001,291,776` bytes (≈0.93 GiB) of margin, without being
needlessly large. No larger figure was considered necessary since the projection uses the ledger's
own conservative high-water assumption (every remaining scan grows by at least as much as the
largest scan observed so far), which is already a worst-case bound, not an average-case estimate.

## What did not change and why

- **Compression**: already canonical, lossless (zlib level 9, `_pack_canonical`/`_unpack_canonical`
  in `services/market_universe/archive.py`), already hardcoded `compressed_evidence=True` in the
  only live-write path (`refresh_universe`'s call inside `_run_scan_cycle_unlocked`,
  `structural_measurement_runner.py`). The observed 302.7 MB growth already reflects compressed
  storage; there was no unused compression lever available to pull instead of raising the budget.
- **Cadence**: unchanged at 900 seconds. `docs/reviews/M27B3R3_AUDITABLE_RETENTION.md` names
  "96 scans (15-minute cadence)" as part of "the approved hard bound" for the reviewed 24-hour
  pilot -- a scientific-measurement-resolution decision, not a storage-tuning knob. Changing it to
  fit a budget would reopen that review, which this bounded repair does not attempt.
- **Expected scans**: unchanged at 96, for the same reason.
- **Free-space floor**: unchanged at 8 GiB (`8,589,934,592` bytes) -- a host-capacity safety
  margin, independent of the approved evidence budget by design (`check_before_scan` checks
  `shutil.disk_usage(self.root).free` against it directly; raising the budget number does nothing
  to guarantee the host actually has that much physical free space).
- **Evidence semantics**: no change to archive schema, identity hashing, deduplication behavior,
  descriptor-relative symlink/TOCTOU protections, the single-writer `flock` lease, receipt content-
  addressing, exactly-once duplicate-`scan_run_id` handling, cold-start bootstrap semantics
  (M27B.3R4.2), or WAL-checkpoint stabilization (also M27B.3R4.2). `RetentionPolicy`,
  `AuditableRetentionLedger.check_before_scan`/`record_scan`, and every other line of
  `services/opportunity_engine/auditable_retention.py` and
  `services/opportunity_engine/structural_measurement_runner.py` are byte-for-byte unmodified by
  this repair.

## What changed

Exactly one file: `scripts/run_m27b3_smoke_receipt.py` -- the sole reviewed operator entrypoint
that exists in this repository for the M27B.3 bounded smoke (and the shape any eventual 24-hour
pilot invocation would share, apart from `--max-iterations`). `build_command()` now appends
`--storage-budget-gib 28 --free-space-floor-gib 8 --expected-scans 96` to the fixed, reviewed
command it constructs -- explicit at this one narrow boundary, alongside the pre-existing explicit
`--cadence-seconds 900`. The wrapper's own operator-facing argument parser (`--parent-dir`,
`--run-dir`, `--expected-code-sha`, `--expected-tree`, `--python`) is unchanged; an operator still
cannot influence the budget, floor, scan count, cadence, host, or authentication shape of the child
command in any way.

`services/opportunity_engine/auditable_retention.py`'s `RetentionPolicy.budget_bytes` default
(`DEFAULT_BUDGET_GIB=24`) and `services/opportunity_engine/structural_measurement_runner.py`'s
`--storage-budget-gib` CLI default (also 24) are deliberately **left unchanged**. Per the repair's
own design constraint, a narrow operator binding at the one entrypoint that exists is preferred
over a repository-wide default change; the 24 GiB default remains available, unaffected, to any
other, differently-reviewed caller of the same runner module. No other reviewed entrypoint for the
24-hour pilot currently exists in this repository (only the bounded-smoke wrapper does) -- when one
is built, it must explicitly bind the same 28 GiB/8 GiB/96-scan policy the way this wrapper now
does; that is out of scope for this bounded repair and is not attempted here.

## Host capacity remains a separate operational prerequisite

Raising the approved budget does not, by itself, make the additional disk space exist.
`minimum starting free space = projected_24h_bytes + free_space_floor_bytes`. At the code-exact
27.067 GiB projection: `29,063,479,296 + 8,589,934,592 = 37,653,413,888` bytes ≈ 35.07 GiB; at the
rounder 28 GiB budget ceiling: `30,064,771,072 + 8,589,934,592 = 38,654,705,664` bytes = exactly
**36 GiB**. The smoke host observed ≈33 GiB free at audit time -- short of both figures. **The host
must have at least 36 GiB free before any further bounded smoke or the 24-hour pilot is attempted
under this policy.** This repair does not add disk space and does not check it at run time beyond
the existing, unchanged 8 GiB floor gate (which will correctly fail closed again if attempted
without first securing that space).

## Authority

This is an implementation-only capacity-policy repair. It does not authorize, start, or validate a
live smoke or the 24-hour pilot; it does not add or imply prospective, trading, capital, or
execution authority; `production_influence` remains exactly the int `0` throughout, unchanged.

## Tests

`tests/test_m27b3r4_3_retention_capacity_policy.py` (new): the wrapper's fixed command and process
receipt bind exactly `budget=28 GiB, floor=8 GiB, expected_scans=96` with cadence and every other
prior flag unchanged, and the database-path escape validation is unaffected by the appended flags;
no authenticated/write flag was introduced and the wrapper's own parser still rejects one; the
unmodified retention gate correctly enforces the new value -- a projection that fails under the
prior 24 GiB budget now passes under 28 GiB (reusing the exact same observed growth), a projection
above 28 GiB still fails closed, insufficient filesystem free space still fails closed independent
of the budget value, and growth-high-water semantics (R4.1 gap D) are unchanged at the new budget;
and the audited projection figures are re-derived from the raw measurements via `record_scan`'s own
formula, not merely asserted as constants.

`tests/test_m27b3_smoke_receipt.py::test_command_is_exact_and_database_paths_are_contained` is
updated for the new fixed command shape; every other test in that file, and the full existing
`tests/test_m27b3r3_auditable_retention.py`, `tests/test_m27b3r4_retention_repair.py`,
`tests/test_m27b3r4_2_cold_start_bootstrap.py`, and `tests/test_structural_measurement_runner.py`
suites, are unmodified and continue to pass unchanged, confirming no R3/R4/R4.1/R4.2 regression.
