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

## Live egress findings - 2026-10-20

A one-shot GitHub-hosted Ubuntu 24.04 diagnostic run executed the merged probe from an
independent cloud provider/network. The probe attempted all four fixed first-party BLS paths.
Every request returned HTTP 403:

- `cpi_release_schedule`: 403
- `monthly_release_list`: 403
- `cpi_home`: 403
- `current_cpi_release`: 403

The report therefore recorded `transport_success_count=0`,
`authority_valid_count=0`, `path_redundancy_ready=false`, and exited 4.

Combined with P11's GCE failure, this demonstrates that merely moving the release-day BLS
request between common cloud-hosted egress providers is not sufficient redundancy. The next
readiness probe must use a genuinely different network route, such as the operator's
residential/home egress, and the future protocol must not make live release-morning BLS
schedule transport a prerequisite for preserving Kalshi or Reuters observations.

This diagnostic result has no scientific-evidence authority and does not freeze P12.

## Home-egress finding - 2026-10-21

The same merged diagnostic probe was then run from the operator's Mac on home/residential
egress. The repository was at canonical main
`58430b3e6bf179a39051620b76cdaa9e1361f3d6`. All four first-party BLS requests again
returned HTTP 403 with 1325-byte bodies:

- `cpi_release_schedule`: 403
- `monthly_release_list`: 403
- `cpi_home`: 403
- `current_cpi_release`: 403

The home report recorded `transport_success_count=0`, `authority_valid_count=0`,
`path_redundancy_ready=false`, and exited 4. It was explicitly diagnostic only with
`scientific_evidence_authorized=false`.

Because both cloud-hosted and residential egress fail under the same Python HTTPS probe,
the evidence no longer supports treating egress location as the only likely blocker. The
common request implementation, HTTP headers, TLS/client fingerprint, or BLS edge policy are
now also plausible causes. The next diagnostic must distinguish request-profile failure from
network-route failure before any further runtime architecture is frozen.

This result is operational-only and contributes zero Reuters-vs-Kalshi directional evidence.


## Request-profile matrix - 2026-10-21

A second home-egress diagnostic compared the same BLS targets under macOS `curl` using
HTTP/1.1 with both its default request profile and a browser-like Safari User-Agent plus
HTML Accept/Accept-Language headers.

All four combinations still returned HTTP 403 with `Server: AkamaiGHost` and a 1325-byte
HTML body:

- CPI schedule HTML + default curl: 403
- CPI schedule HTML + browser-like headers: 403
- BLS calendar ICS + default curl: 403
- BLS calendar ICS + browser-like headers: 403

This rules out a simple Python-specific failure and rules out a repair based only on changing
User-Agent/Accept headers or switching the first-party path to the BLS ICS feed. The common
remaining possibilities include BLS/Akamai policy against generic command-line TLS clients,
client/TLS fingerprinting, cookie/challenge state available only to a full browser, or a
broader edge-policy decision.

The next diagnostic should therefore use a real browser session rather than another synthetic
HTTP-header variant. If a real browser succeeds while command-line clients remain blocked,
P12 should use a separately reviewed browser/manual-attested schedule-authority lane before
freeze instead of weakening automated provenance rules.

This matrix is operational-only and has no scientific-evidence authority.

## Real-browser finding - 2026-10-21

Safari successfully loaded the exact first-party BLS CPI release-calendar page on the same
Mac/home network where Python and curl were rejected by Akamai. The rendered table explicitly
bound:

- reference month: October 2026
- release date: Nov. 10, 2026
- release time: 08:30 AM

Safari also successfully downloaded the official BLS `bls.ics` calendar. This establishes
that the residential route can reach BLS through a full browser and that the operational
failure is not a simple network-route outage.

The screenshot and downloaded ICS are diagnostic observations only. The screenshot is not
accepted as raw source bytes, and the ICS does not encode the CPI reference month. The
reviewed acquisition lane therefore uses a browser-saved copy of the exact already-reviewed
P1 CPI schedule HTML locator, not the screenshot and not a caller-authored transcription.

## Browser/manual schedule acquisition lane

`services/forecasting/cpi_calendar_manual_acquisition.py` introduces a distinct
`MANUAL_BROWSER_ATTESTED_RELEASE_CALENDAR` mode. It reuses the existing P1
`RELEASE_CALENDAR / BLS_CPI_RELEASE_SCHEDULE_HTML` authority and does not impersonate
automated HTTP provenance.

The importer accepts only a local file path plus the target reference month and explicit
operator attestation. It reads exact bytes itself, stamps importer-observed UTC time, binds
the exact SHA-256 and byte count, parses the exact table row for the reference month, and
derives the release instant from the saved HTML. Caller-supplied raw bytes, acquisition
timestamps, and release timestamps are not public inputs.

`scripts/import_cpi_p12_bls_calendar_browser.py` writes a create-once receipt containing
the exact raw bytes (base64), file hash, importer timestamp, parsed reference month/release
instant, P1 authority identities, and manual acquisition identity. The receipt explicitly
states `scientific_evidence_authorized=false` and `p12_frozen=false`.

This is still pre-freeze infrastructure. The resulting receipt gains no test authority until
a later reviewed P12 protocol binds its exact content hash and expected reference month before
the prospective event.

## Revised architecture direction

The preferred P12 design now separates *schedule authority* from release-morning collection:

- acquire and seal BLS schedule authority prospectively before release day through a reviewed
  provenance lane on an egress that can reach BLS;
- require repeated readiness success before the event-specific protocol freezes;
- run release-morning Kalshi market capture independently of BLS schedule transport;
- run Reuters acquisition independently of both BLS and Kalshi transport;
- evaluate completeness only at the preclose/scoring gate;
- allow post-release BLS truth acquisition to retry through a separately reviewed automated or
  manual-attested lane without altering the already-frozen predictor/market evidence.

The existing CPI evidence architecture already keeps manual-browser-attested BLS acquisition
distinct from automated transport. P12 may reuse that design principle, but schedule evidence
needs its own reviewed provenance contract rather than treating caller-authored bytes as
automated first-party acquisition.

## Required gates before P12 may freeze

P12 remains **NOT READY TO FREEZE** until all of these are completed:

- the exact future P12 runtime decouples BLS, Kalshi, and Reuters acquisition stages;
- the actual production runtime passes the BLS redundancy probe on at least two separate days;
- at least one reviewed BLS acquisition path succeeds prospectively, with diagnostics proving
  whether success depends on request profile, browser/manual acquisition, or network route;
- at least one complete unattended scheduled dry run succeeds from each required runtime path;
- 403, timeout, redirect, malformed body, wrong reference month, and wrong release-time fault
  injection all fail closed without suppressing unrelated collectors;
- the secondary-egress acquisition/provenance handoff is reviewed before it gains scientific
  authority;
- event-specific Kalshi identity, siblings, close times, and settlement semantics are frozen
  only after those reliability gates pass.

Until then, this is infrastructure preparation only: promotion authority `NONE`, production
influence `0`, and no trading authority.
