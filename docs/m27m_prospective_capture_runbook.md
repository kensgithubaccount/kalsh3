# M27M Prospective Capture Operations v1 -- Operator Runbook

**Scope: LOCAL DEVELOPMENT / RESEARCH ONLY.** M27M has no network access, no
Kalshi market or account access, no credential access, no outcome/settlement
ingestion, and no production/risk/execution dependency. Every artifact it
produces is `research_only=true`, `production_influence=0`. Nothing in this
module arms, burns, finalizes, mutates, or orders anything.

M27M does not capture weather evidence itself -- that is M27L's job
(`scripts/capture_m27l_prospective_forecast.py`, frozen). M27M takes the
bundle file M27L already produced and validated, and turns it into a
durable, tamper-evident, exactly-once-per-cycle archive.

## Why this exists

M27L's capture script writes one bundle JSON file per invocation. Nothing
about that file's location or lifecycle stops an operator from:

- losing it,
- accidentally capturing the same forecast reference cycle twice with
  slightly different (but each individually valid) inputs,
- or losing track of which of the 03Z reference cycles in the prospective
  window have actually been captured.

M27M is the archival and accounting layer that closes those gaps, without
ever touching how M27L captures or validates evidence.

## Architecture

```
<archive-root>/
  bundles/<sha256>.json    -- exact validated M27L bundle bytes, verbatim
  receipts/<receipt_id>.json  -- canonical metadata, derived only after
                                  deserialize_prospective_bundle succeeds
  cycles/<reference-cycle>.json  -- the COMMIT POINT, published last
```

- **`bundles/<sha256>.json`** -- the exact bytes of a bundle file that
  passed the frozen M27L `deserialize_prospective_bundle`, named by the
  SHA-256 of those exact bytes. Nothing about this file is re-serialized or
  reformatted; what's archived is byte-identical to what was validated.
- **`receipts/<receipt_id>.json`** -- canonical metadata (target dates,
  per-observation evidence identities/midpoints/model identities, shared
  provenance hashes, `research_only=true`, `production_influence="0"`)
  derived by `derive_receipt` purely from the validated observations and the
  bundle's own hash. `receipt_id` is a deterministic hash of that same
  material, so identical evidence always produces an identical receipt and
  an identical filename.
- **`cycles/<reference-cycle>.json`** -- the single COMMIT POINT. A forecast
  reference cycle (one 03Z UTC reference timestamp) is "accepted" if and
  only if this file exists and independently reverifies. It is always
  published *last*, after the bundle and receipt are already archived.

All three writes are create-only, crash-safe, and symlink-safe: a unique
0600 temp file is written in the target directory (short writes are handled
explicitly), fsynced, and published to its final name via a no-replace hard
link. Byte-identical content already at the destination is treated as
success (idempotent); anything else there -- different content, a symlink,
a non-regular file -- fails closed. Nothing is ever overwritten or deleted.

### Exactly one accepted capture per reference cycle

The cycle filename is `canonical_cycle_key(reference_time)` -- a pure
function of the shared `forecast_reference_time` all three observations in
a bundle agree on. Because cycle publication is create-only:

- **Exact re-registration is idempotent.** Registering byte-identical bundle
  bytes twice succeeds both times and produces identical
  `bundle_sha256`/`receipt_id`/`cycle_key` -- the second call's `*_created`
  flags are all `False`.
- **A different valid bundle for the same reference cycle fails closed.**
  If a cycle file already exists with different content, the second
  registration's cycle-publish step raises `ForecastError` ("...exists with
  conflicting content"). The first accepted capture is never disturbed. Any
  bundle/receipt archived by the rejected attempt is left on disk as a
  **pre-commit orphan** -- never silently deleted, always visible to the
  verifier.
- **A crash between bundle, receipt, and cycle publication is safe to
  resume.** Every step is independently idempotent, so re-running
  registration with the identical bundle bytes picks up wherever it left
  off and completes the commit.

## Operator workflow

```
python scripts/register_m27m_prospective_capture.py \
    --bundle path/to/m27l_bundle.json \
    --archive-root path/to/archive

python scripts/verify_m27m_prospective_collection.py \
    --archive-root path/to/archive
```

`register_m27m_prospective_capture.py` prints a JSON summary
(`cycle_key`, `bundle_sha256`, `receipt_id`, and the three `*_created`
flags) and exits non-zero with an `{"error": ...}` line on stderr if the
bundle is invalid or the reference cycle already has a different accepted
capture.

`verify_m27m_prospective_collection.py` **performs no writes**. It:

1. Independently re-reads and re-hashes every artifact under
   `--archive-root`.
2. Reruns the frozen M27L `deserialize_prospective_bundle` over every
   bundle a cycle file references.
3. Recomputes every receipt field and the receipt identity from scratch
   with `derive_receipt` and compares it byte-for-byte to what's on disk.
4. Validates filename/content identity at every layer (a bundle's filename
   must equal the SHA-256 of its own content; a receipt's filename must
   equal its own recomputed `receipt_id`; a cycle's filename must equal
   `canonical_cycle_key` of its own `cycle_reference_time`).
5. Rejects (as a `problem`, and exits non-zero) anything missing, a
   symlink, a non-regular file, tampered content, or a filename/content
   mismatch.
6. Reports **orphans**: bundles/receipts not referenced by any accepted
   cycle. A self-consistent, valid orphan (e.g. left behind by a rejected
   cherry-pick attempt) does not make the archive `not ok` -- it's
   informational, for the operator to inspect or clean up by hand. An
   orphan whose content is itself tampered or invalid *is* still reported
   as a `problem`.
7. Classifies every expected 03Z reference cycle in the frozen prospective
   window as `CAPTURED` / `PENDING` / `MISSED`, **for reporting only**.

It exits `0` only when `report["ok"]` is `true` (no problems found) --
`PENDING`/`MISSED` cycles never affect the exit code, since those are
expected, non-error states, not integrity failures.

## Coverage classification

`PENDING` / `CAPTURED` / `MISSED` is derived, never invented. The set of
*expected* reference cycles (`expected_reference_cycles` in
`weather_prospective_operations.py`) is computed purely from the frozen
M27C manifest constants `PROSPECTIVE_START`/`PROSPECTIVE_END`
(`weather_prospective.py`), the frozen M27L midpoint set
`SUPPORTED_MIDPOINTS`, and the frozen M27L date mapper `target_local_date`
(`weather_calibration_grib.py`) -- the same function the real bundle
validator itself uses. A 03Z reference day is "expected" exactly when all
three of its frozen midpoints map to a target date inside the frozen
prospective window; nothing about that mapping, or the window itself, is
reimplemented here.

A cycle is:

- **`CAPTURED`** only if its cycle file exists *and* independently
  reverifies in full (steps 1-4 above). A cycle file that exists but fails
  reverification is treated exactly as if it didn't exist.
- **`MISSED`** if, as of the `--as-of` timestamp (default: now), the
  cycle's capture deadline -- the earliest record's `interval_start`, i.e.
  reference time + 9 hours, the same instant the frozen validator itself
  treats as the latest legal collection time -- has passed with no captured
  evidence.
- **`PENDING`** otherwise.

No miss, operator note, or any artifact other than a fully reverified cycle
file is ever allowed to count as evidence of capture. A stray file dropped
into `cycles/` (wrong extension, unparseable JSON, or JSON that merely
*claims* to be captured) is reported as a `problem` and never flips a
cycle's classification to `CAPTURED`.

## What M27M deliberately does not do

- It does not capture weather evidence (M27L's job, frozen and unchanged).
- It does not touch outcomes, settlement, evaluation metrics, or market
  data -- the archived receipt schema has no field for any of them, and
  `deserialize_prospective_bundle`'s own forbidden-field check would reject
  a bundle that tried to smuggle one in anyway.
- It does not decide what to do about a `MISSED` cycle -- that's an
  operator judgment call, informed by, not made by, this module.
- It does not delete, replace, or "clean up" anything. Orphans and problems
  are reported; resolving them (re-registering, or manually removing a
  file) is always an explicit, separate operator action.
