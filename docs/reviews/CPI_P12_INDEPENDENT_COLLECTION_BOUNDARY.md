# CPI P12 — Independent Stage Diagnostic Checkpoint

Status: **RESEARCH-ONLY / DIAGNOSTIC CONTRACT ONLY / NO EVENT FREEZE**.

## Why this checkpoint exists

On October 14, P11 failed operationally. `capture_market_evidence()` called
`run_preflight()`, whose live BLS schedule HTTP request returned 403. Both
`record_reuters_pass()` and `record_reuters_nonpass()` also refused to run
without `preflight.json`. Thus one unavailable source suppressed attempts
to preserve independent Kalshi and Reuters observations.

PR #196 already merged the separately attested, SHA-bound BLS calendar
receipt for October 2026, stored and verified in GCS. It remains
research-only and does not make P12 ready to freeze.

## Scope of this change

`services/forecasting/cpi_p12_independent_stages.py` introduces a small
**pure diagnostic control-flow contract**, distinct from frozen P11:

- Three explicitly named lanes: `BLS_SEALED_SCHEDULE`,
  `KALSHI_MARKET`, and `REUTERS_FORECAST`.
- The caller must supply exactly one callable for each lane.
- Every callable is attempted once; a failure in one lane does **not**
  short-circuit the remaining lanes.
- Each attempted stage receives a completion-or-failure diagnostic.
- Arbitrary callback return data and exception messages are discarded.
- Diagnostic records explicitly deny scientific-evidence authority,
  economic-test authority, production influence, P12 freeze and promotion.
- No network I/O, evidence import, exchange API, orders, trading,
  scheduling or time-of-event semantics exist in this module.

This is intentionally **not** a P12 prospective collector. A completed
callback here means only that it returned without an exception. It does
**not** mean that any evidence is valid, timely, admissible, or complete.

## Fail-closed scientific decision boundary

The eventual P12 process architecture must be:

1. Bind the event-specific protocol and previously sealed BLS schedule
   receipt hash **before the prospective event**.
2. Run separately scheduled BLS schedule-receipt verification, Kalshi
   market capture and Reuters evidence acquisition. The BLS lane must
   verify the frozen receipt without depending on live BLS transport
   to begin the Kalshi or Reuters lanes.
3. Make every lane produce independently timestamped, create-once
   provenance-bearing evidence or a bounded failure record, without
   inspecting its peers' success as a prerequisite for acquisition.
4. Only the frozen preclose/scoring gate may reconcile identities,
   cutoffs, provenance, market cohort, Reuters authority and evidence
   completeness. Missing or invalid material yields **INCONCLUSIVE**,
   never implicit success, backfill, or production authority.
5. Keep post-release truth acquisition separate from pre-release
   capture and preserve the originals without amendment.

The eventual scheduler must run stages in independently triggered OS
processes, rather than rely on this in-process diagnostic alone.

## Fault-injection coverage

`tests/test_cpi_p12_independent_stages.py` checks:

- A simulated failure in **each** of the three lanes still attempts all
  other lanes.
- All three can fail independently without claiming a pass.
- Successful callbacks that return forged authority fields cannot
  authorize anything.
- Raw exception strings do not leak into the diagnostic record.
- Missing lanes, wrong key types and noncallables reject the whole
  diagnostic before any stage executes.

## Remaining work before P12 freeze

This checkpoint does **not** close the unattended runtime gate.
Next work must implement independently scheduled, provenance-verified
Kalshi and Reuters acquisition in a new P12-specific runtime, then
prove real source-failure isolation with fault-injected scheduled
dry runs and independent reviewed safety checks.

Do not edit frozen `services/forecasting/cpi_p11_runtime.py`, its
frozen protocol or the P11 terminal artifacts. Promotion authority
remains **NONE**; `production_influence=0`; **P12 NOT FROZEN**.
