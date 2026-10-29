# WN-P1 — Prospective EE metadata one-shot canary (research only)

**Status:** Independent review required; no live run, scheduler, paper strategy, or trading authority. Parent issues #240, #238, #234. The merged historical EE diagnostic (#239) is not point-in-time evidence.

## Frozen intended envelope

- Exact source: `projects/gcp-public-data-weathernext/assets/weathernext_3_0_0_0p05deg`. The inherited reviewed `acquire_metadata` implementation filters the target's 00Z init and forecast hours 1..31, sorts, calls `limit(32)`, then makes **one logical** `getInfo()`. The Earth Engine SDK may perform internal transport retries. There are no forecast pixel reads, `reduceRegion`, GCS ensemble accesses, Kalshi APIs or order calls. Returned metadata and a canonical JSON SHA-256 are NOT the original provider transport bytes.
- Five fixed-standard-time CLI 24-hour windows: Boston/Miami 5–28; Denver 7–30; Los Angeles/Seattle 8–31. First image's six temperature marginal-band names only; **not** a proof of every image's band completeness. The source-reported ingestion-time property is not independent custody proof.
- Manual one-day capture: target must be the CURRENT UTC calendar day and on/after 2026-11-01, the acquisition must start from 08:00:00Z and complete strictly before 09:00:00Z, with nondecreasing host UTC and monotonic clocks. Any source image claiming ingestion later than host observation is rejected. Host clocks themselves are not independently attested.
- Fail-closed custody: `os.mkdir(receipt_root / YYYY-MM-DD)` is an exclusive reservation **before** any source request. Root is a pre-existing, nonsymlink local directory. Claim dir is fsynced. No retry, overwrite, replacement, deletion of a poisoned claim, late backfill or running twice. Exactly one `receipt.json` created with `O_CREAT|O_EXCL`, flushed and fsynced; filesystem failure poisons the slot. The receipt includes complete returned metadata if safely JSON-serializable, source-attempt/failure status and all five city diagnostics.
- The CLI defaults to `PLAN_ONLY_NO_SOURCE_RPC` unless `--execute-readonly` is explicitly supplied after separately reviewing rights and cost. CI and tests inject synthetic source transport, and make no cloud/network call.
- Even on successful metadata completeness the only status is `METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED`. It is not a scientific/provenance receipt for decisions. All statuses expressly set probability, paper and trading authority to false/NONE with zero production influence. This cannot change the Phase 0 gate's empty model registry.

## Terms / remaining source feasibility

Current Google documentation states that live/future WeatherNext forecasts use the separate **GDM Real-Time Weather Forecasting Experimental Data Terms of Use**, whereas historical data more than one hour old uses CC BY 4.0. Third-party restrictions and Earth Engine charges/access tiers may apply; no prospective-rights approval or cost validation has been recorded. Therefore **do not invoke --execute-readonly or schedule this collector** until a human authorizes one specifically scoped future research read after reviewing terms and cost.

Source references:
- https://developers.google.com/weathernext/guides/models
- https://developers.google.com/weathernext/guides/disclaimers
- https://developers.google.com/earth-engine/datasets/catalog/projects_gcp-public-data-weathernext_assets_weathernext_3_0_0_0p05deg

The six bands are hourly **marginal** quantiles, not a joint 24-hour max distribution; Weather Company Kalshi settlement probabilities remain unestablished.

## Exit semantics and review

CLI exit 0 means an UNATTESTED structurally complete, locally timed metadata observation only. Exit 1 means incomplete/late metadata, exit 2 means a failed captured attempt, exit 4 means invalid setup, duplicate slot, or potentially partial/poisoned durability (no rerun). The claimant's filesystem and clocks are not independent source-proof issuers. Never ingest these files into a model/paper/trading-authority path.

Require exact-head CI, separate fresh-detached adversarial review, strict mypy on both new Python files, Ruff, full suite and security before merge. Replay 08:00/09:00 boundaries, clock rollback, fake EE and host times, source ingestion after capture, duplicate hours, 32-image overflow, missing bands, OS/fsync failure and duplicate writers. Verify that any local test fixture cannot be mistaken for authentic source acquisition. Author must not perform a live capture before that review.

Next distinct checkpoints: independent source-time/custody attestation; exact station/pixel and legally permitted 64-member joint distribution or vetted alternative; TWC daily-high probability mapping; Kalshi six-sibling executable economics and prospective immutable shadow ledger. **No trade, paper signal, production or model authority.**
