# CPI P12 — Standalone Kalshi Raw Inventory Checkpoint

Status: **RESEARCH ONLY / NOT PROSPECTIVE / NOT FROZEN / NO TRADES**.

## Authority and motivation

PR #196 preserved an exact browser-attested BLS CPI schedule artifact.
PR #198 introduced a diagnostic showing three stage callables can be
attempted without a failure in one suppressing the others.

Frozen P11 remained terminal and unchanged. Its `capture_market_evidence()`
called `run_preflight()` and therefore depended on a live BLS HTTP request.
The future P12 collector must have no such dependency.

## This checkpoint

`services/forecasting/cpi_p12_kalshi_inventory.py` captures three
first-party, fixed-origin Kalshi public API responses for an explicitly
requested `KXCPI-` event:

1. `/trade-api/v2/series/KXCPI`
2. `/trade-api/v2/events/<event-ticker>`
3. `/trade-api/v2/markets?event_ticker=<event-ticker>&limit=100`

It calls the existing shared `services.market_universe.public_read.get()`
(unauthenticated TLS, no redirects, bounded timeout and response size).
It has **no import or call** into BLS, Reuters, P11 preflight, credentialed
exchange APIs or order placement.

Each successful response has its original raw bytes retained as base64,
a verified SHA-256 and byte count, its exact fixed-host path, HTTP 200
and the transport-observed timestamp. The raw JSON is re-parsed strictly.
Only basic CPI series/event/ticker identity, duplicate market identities,
and complete one-page inventory are checked. **No claim is made that the
target event's exact CPI rules, sibling count, close time, settlement
language, strike comparators or quote evidence are frozen or admissible.**

`scripts/capture_cpi_p12_kalshi_inventory.py` is an operator-triggered,
create-once research CLI. It requires `--research-only` and records a
bounded failure exit instead of fabricating successful evidence.

Example for a **hypothetical, not-yet-authorized** event identifier:

```bash
python scripts/capture_cpi_p12_kalshi_inventory.py \
  --event-ticker KXCPI-26OCT \
  --output /tmp/cpi_p12_inventory_research.json \
  --research-only
```

This example is not a directive to capture anything now; the actual event
identity must be independently verified before event-specific protocol freeze.

The record is explicitly `scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`, `promotion_authority=NONE`,
`production_influence=0`, `p12_frozen=false`. Caller-injected transport
used in unit tests is marked `INJECTED_DIAGNOSTIC_GETTER` and cannot claim
actual network provenance.

## Tests

No-network fault-injection coverage includes response hash/length/path/status
corruption, malformed timestamp/body, missing or duplicated markets,
partial pagination, wrong series/event identity, public transport failure,
and create-once CLI behavior. Any one failure stops this **Kalshi stage**
without invoking a BLS preflight; independent Reuters/BLS processes will
be designed and tested in later checkpoints.

## Still required

- Review actual P12 Kalshi event identity, rules, sibling set, close times,
  precise executable quotes and prospective capture deadline.
- Implement genuine standalone Reuters acquisition with its own raw-source
  provenance and independently frozen search/authority policy, **without**
  inspecting the target forecast before protocol freeze.
- Implement signed/hashed schedule-receipt verification without live BLS
  HTTP, independent OS-scheduled stage triggers, clock/cutoff enforcement,
  terminal failure receipts and all-stage reconciliation at preclose.
- Unattended dry runs and transport fault injections through the actual
  runtime. No frozen P11 code is modified.

No scientific result, backfill, economics or trading authorization follows
from a successful inventory snapshot.
