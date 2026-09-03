# M27B.3 24-hour retention pilot operator entrypoint

Status: additive entrypoint ready for independent review; the pilot has not been run. Grants no
prospective, trading, capital, or execution authority.

## Bounded smoke gate: PASS

The final merged bounded cold-start smoke passed against canonical main
(`10f70f7c7178229caab356f98b6cb606dbd75e80` / `24194f4671ab24eeae5d28f523fdccc32b97b8b1`): wrapper
exit `0`, process status `COMPLETED`, Markets acquisition COMPLETE (119 pages, 118,280 records, 0
malformed), broad Events acquisition COMPLETE (69 pages, 13,734 records, 0 malformed), exact-Event
reconciliation COMPLETE (154 requests / 154 records, 0 malformed, within the reviewed
`MAX_EVENT_RECONCILIATION_REQUESTS = 200` ceiling), structural scan COMPLETE
(`refresh_complete=True`, 7,728 cohorts, 16 leads, 16 observations), retention `complete=true`,
`production_influence=0`. This evidence is not rewritten here; it is the terminal state of that
smoke run, observed and reported once.

## What the pilot is

96 scans at a 900-second cadence: `96 * 900s = 86,400s = 24 hours`, under the exact same reviewed
retention policy the bounded smoke just cleared: budget = 28 GiB, free-space floor = 8 GiB,
expected scans = 96. Its purpose is to observe real, repeated retention growth and auditability
across a full day's worth of scans -- not a claim of predictive edge, after-cost economics,
trading readiness, or execution authority. It is research infrastructure and prospective-validation
evidence only.

## Storage headroom, and why it is not a guarantee

The latest bounded-smoke retention receipt projected `29,366,648,832` bytes against the approved
`30,064,771,072`-byte (28 GiB) budget -- approximately **0.65 GiB of headroom**
(`30,064,771,072 - 29,366,648,832 = 698,122,240` bytes ≈ 0.65 GiB). That projection is derived from
`AuditableRetentionLedger`'s own conservative growth-high-water mechanism: it assumes every
remaining scan grows by at least as much as the largest scan observed *so far*. **This headroom is
not a guarantee that every later scan's actual delta stays below the first scan's projection** -- a
later scan in the real 24-hour pilot could observe a larger delta than any scan seen during the
bounded smoke (a bigger acquisition, more reconciliation growth, a larger WAL/archive footprint at
that particular checkpoint boundary), which would raise the high-water mark and could push a later
scan's projection over budget. **If that happens, the pilot must fail closed on that scan --
exactly as the existing, unmodified `record_scan`/`check_before_scan` budget and free-space-floor
gates already do** -- not silently continue, not be patched around mid-run, and not be treated as a
bug in this entrypoint. This document does not change, weaken, or work around that fail-closed
behavior in any way; it only launches the same code the smoke already exercised, for more
iterations.

## What was implemented

A new, dedicated, standalone operator entrypoint: `scripts/run_m27b3_retention_pilot_receipt.py`.
It does not modify, import from, or repurpose `scripts/run_m27b3_smoke_receipt.py` in any way --
that file is untouched (confirmed by an empty diff) and its own fixed `--max-iterations 1`
one-scan-smoke meaning remains exactly what it was. The two files are structurally parallel by
design (mirrored function names/shapes for auditability) but are independent, self-contained
scripts: a script invoked directly as `python scripts/run_m27b3_retention_pilot_receipt.py` has
only its own directory on `sys.path`, not the repository root (verified empirically), so a
`scripts.run_m27b3_smoke_receipt` import would not resolve at runtime -- the smoke wrapper is
self-contained for the identical reason, and the pilot wrapper follows the same pattern rather than
introducing an import dependency between two operator scripts.

**Fixed pilot child command** (operator cannot override any of it -- the wrapper's own
`argparse` interface accepts only `--parent-dir`, `--run-dir`, `--expected-code-sha`,
`--expected-tree`, `--python`, identical in shape to the smoke wrapper's):

```
<python> -u -m services.opportunity_engine.structural_measurement_runner \
  --archive <run>/universe.sqlite \
  --evidence-db <run>/observations.sqlite \
  --live-public-read \
  --cadence-seconds 900 \
  --max-iterations 96 \
  --source-authority external-api.kalshi.com \
  --storage-budget-gib 28 \
  --free-space-floor-gib 8 \
  --expected-scans 96
```

Every value matches the bounded smoke's own reviewed policy exactly, except `--max-iterations`
(96 instead of the smoke's fixed 1) -- the one deliberate, reviewed difference between the two
entrypoints, matching the 24-hour pilot's actual scientific shape.

**Receipt schema decision.** The existing smoke receipt schema (`kalsh3.m27b3.process-receipt.v2`)
is structurally generic (no field makes a one-scan-specific semantic claim), but reusing its exact
schema-version string for a 96-scan, 24-hour artifact would still leave the receipt's *kind*
ambiguous to a reviewer glancing at it out of context. Rather than weaken or reinterpret the
existing schema, the pilot wrapper uses its own distinct schema version,
`kalsh3.m27b3.pilot-process-receipt.v1`, and an explicit `experiment_kind` field
(`"m27b3_24_hour_retention_pilot"`), while keeping every other field name and the overall receipt
shape identical to the smoke's for consistency and reviewability. Two additional explicit fields --
`cadence_seconds` and `max_iterations` -- are included at the top level (the smoke receipt only
ever exposes these via its embedded `command` list); this is a strict superset, not a
reinterpretation, of the existing shape, added because this checkpoint specifically requires the
receipt to durably bind cadence and iteration count, which the smoke wrapper's receipt shape never
needed to state explicitly since both were always fixed at trivial values (900s, 1).

**Preserved unchanged**, reused directly from the same reviewed patterns as the smoke wrapper:
exact-code-SHA/tree/clean-worktree gating (fails before any child launch); fresh, non-symlinked,
parent-contained run-directory validation; the exact same `ENVIRONMENT_ALLOWLIST` (no credentials,
no account/order/execution-shaped variables); `M27B3_SUPERVISOR_PID`/`M27B3_PARENT_WATCHDOG_FD`
env-var wiring for the child's own unmodified parent-watchdog mechanism
(`structural_measurement_runner._start_parent_watchdog`); atomic, fsynced
STARTING/RUNNING/terminal receipt writes; SIGINT/SIGTERM forwarding and bounded
terminate-then-kill child reaping; truthful `COMPLETED`/`FAILED`/`SIGNALED` terminal
classification (never inferred from file existence); `inspect_receipt`'s
never-silently-completed `INTERRUPTED` classification for a stale `RUNNING` receipt whose
recorded PIDs are gone; full SHA-256 hashing of `stdout.log`/`stderr.log`/`universe.sqlite`/
`observations.sqlite` with an explicit `*_hash_complete` flag; `production_influence` fixed to the
exact int `0`.

**Not changed anywhere in this repair**: `services/market_universe/collect.py`,
`services/market_universe/sync.py`, `services/market_universe/archive.py`, any retention
algorithm, any structural-discovery algorithm, the current lossless-compression behavior, the
current event-reconciliation ceiling (200), or `scripts/run_m27b3_smoke_receipt.py` itself. No
authentication, credential, account-read, order, trading, capital, or execution surface was added
anywhere. No genuine entrypoint blocker requiring a change to any of the above was discovered while
implementing this wrapper.

## What this does not authorize

This is an implementation-only checkpoint. The 24-hour pilot has **not** been started, no live
Kalshi request was made while building or testing this entrypoint, and no other bounded smoke was
run. Starting the pilot remains a separate, explicit operator action requiring independent review
and merge of this branch first. This work establishes no predictive edge, no after-cost economics,
no trading authority, no capital authority, and no execution authority.
