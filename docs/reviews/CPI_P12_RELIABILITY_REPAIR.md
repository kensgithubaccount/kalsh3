# CPI P12 Reliability Repair - Pre-Freeze Infrastructure

## Status

This work begins the reliability repair after CPI-E1-P11. It does **not** freeze P12,
select a Kalshi event, authorize Reuters evidence, or authorize trading/economics.

As of 2026-10-20, BLS lists October 2026 CPI for 2026-11-10 at 08:30 ET. That is the
candidate next prospective CPI replication date, but the event-specific protocol remains
unfrozen until the reliability gates below are satisfied.

## P11 failure being repaired

P11 failed closed before any admissible pre-release predictor/market evidence was established.
The scheduled GCE runtime received a transport block from the BLS schedule page during the
11:45Z preflight. The 12:05Z market command invoked the same preflight dependency and failed
for the same reason. At 12:20:01Z the frozen watchdog issued
`INCONCLUSIVE_INSUFFICIENT / MISSED_OR_INCOMPLETE_PRE_RELEASE_COLLECTION` with no backfill.

This is an operational failure, not directional evidence for or against the Reuters-vs-Kalshi
hypothesis.

## Repair principles

1. **No single transport may suppress independent evidence collection.** The future P12 BLS
   schedule check, Kalshi market capture, and Reuters acquisition must run as independent
   pre-release stages. Missing BLS transport may make the final run inconclusive, but it must
   not prevent an otherwise valid Kalshi snapshot or Reuters acquisition from being preserved.
2. **Authority and transport are separate.** HTTP 200 is not enough. A page must positively
   bind the target CPI reference month to the expected release date/time.
3. **Path redundancy is not network redundancy.** Multiple `www.bls.gov` pages protect against
   stale/moved page layouts, but a common egress block can still take all of them down. P12
   must not freeze until live probes succeed from the actual scheduled runtime **and** an
   independently routed secondary egress.
4. **No caller-authored raw bytes become scientific evidence.** This repair probe is diagnostic
   only. Any future cross-egress evidence handoff must have a separately reviewed acquisition
   and provenance boundary before it can be admitted by a prospective protocol.
5. **Fail closed at the gate, not at acquisition.** Collectors should preserve independent
   prospective observations; the preclose/scoring gate decides whether the complete authority
   set is sufficient.
6. **No target Reuters forecast is inspected to tune the protocol.** Reliability work is
   source/transport/schema work only.

## Implemented in this checkpoint

`services/forecasting/cpi_bls_schedule_redundancy.py` adds a research-only bounded probe across
four first-party BLS page families:

- `/schedule/news_release/cpi.htm`
- the release-month `/schedule/YYYY/MM_sched_list.htm`
- `/cpi/`
- `/news.release/cpi.nr0.htm`

Every path is attempted independently. The report preserves HTTP status, observation time,
response size, SHA-256, and semantic authority outcome. A transport failure never
short-circuits the remaining paths. The serialized report intentionally omits raw response
bytes and explicitly stamps `scientific_evidence_authorized=false`.

`scripts/probe_cpi_bls_schedule_redundancy.py` is the operator CLI. It exits 0 only when the
configured number of authority-valid paths is met (default: two); otherwise it exits 4.

Example for the currently scheduled October 2026 CPI release:

```bash
python scripts/probe_cpi_bls_schedule_redundancy.py \
  --reference-month 2026-10 \
  --release-at 2026-11-10T13:30:00Z \
  --collector-label "ENVIRONMENT-NAME" \
  --output /tmp/cpi_bls_probe.json
```

The collector label is diagnostic metadata, not authenticated provenance.

## Required gates before P12 may freeze

P12 remains **NOT READY TO FREEZE** until all of these are completed:

- the exact future P12 runtime decouples BLS, Kalshi, and Reuters acquisition stages;
- the actual production runtime passes the BLS redundancy probe on at least two separate days;
- an independently routed secondary egress passes the same semantic probe;
- at least one complete unattended scheduled dry run succeeds from each required runtime path;
- 403, timeout, redirect, malformed body, wrong reference month, and wrong release-time fault
  injection all fail closed without suppressing unrelated collectors;
- the secondary-egress acquisition/provenance handoff is reviewed before it gains scientific
  authority;
- event-specific Kalshi identity, siblings, close times, and settlement semantics are frozen
  only after those reliability gates pass.

Until then, this is infrastructure preparation only: promotion authority `NONE`, production
influence `0`, and no trading authority.
