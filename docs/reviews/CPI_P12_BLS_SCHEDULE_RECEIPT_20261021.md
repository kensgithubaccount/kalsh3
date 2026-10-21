# CPI P12 BLS Schedule Authority Receipt — 2026-10-21

## Status

**SEALED / DURABLE / BYTE-VERIFIED / RESEARCH-ONLY / P12 NOT FROZEN**

This record preserves the successful browser-attested BLS CPI release-calendar acquisition
completed during P12 reliability preparation.

## Canonical source binding

- source locator: `https://www.bls.gov/schedule/news_release/cpi.htm`
- reference month: `2026-10`
- derived release instant: `2026-11-10T13:30:00Z`
- source bytes: `55389`
- source SHA-256:
  `cece37b1f964eed1cd51215764a55fb26e310da31b441ab9c65758989c9afc17`

The saved BLS HTML table bound October 2026 to Nov. 10, 2026 at 08:30 AM Eastern.
The importer derived the UTC release instant from the exact saved bytes.

## Import receipt

- record type: `CPI-P12-MANUAL-BLS-CALENDAR-RECEIPT-v1`
- importer-observed UTC: `2026-10-21T16:13:23.761887Z`
- evidence id:
  `7b656fd022c75f66d9ff966bca9c60276e9f63846dfbc72baf33fe379628c80d`
- receipt SHA-256:
  `01fc6542b73b0b6d293d91c024d2ab0be7c5ee8928f11f624eb32ac35e50619d`

The receipt remained:

- `research_only=true`
- `production_influence=0`
- `scientific_evidence_authorized=false`
- `p12_frozen=false`

## Durable archive

Both exact artifacts were uploaded to the existing Kalshi evidence bucket and then streamed
back through `gcloud storage cat` for independent byte verification.

Source object:

`gs://total-market-138523-kalshi-evidence/cpi_p12/20261021/cpi_schedule_20261021.html`

Remote SHA-256 reproduced:

`cece37b1f964eed1cd51215764a55fb26e310da31b441ab9c65758989c9afc17`

Receipt object:

`gs://total-market-138523-kalshi-evidence/cpi_p12/20261021/cpi_p12_bls_calendar_receipt_20261021.json`

Remote SHA-256 reproduced:

`01fc6542b73b0b6d293d91c024d2ab0be7c5ee8928f11f624eb32ac35e50619d`

## Authority boundary

This closes the BLS schedule-acquisition transport blocker that caused P11 to fail before
pre-release collection.

It does **not** freeze P12 and does **not** authorize Reuters evidence, Kalshi scoring,
economics, production influence, or trading.

Before P12 may freeze, the exact event-specific protocol must bind this sealed receipt and the
release-morning runtime must decouple BLS schedule authority from Kalshi and Reuters
collection so a BLS transport failure cannot suppress otherwise valid prospective evidence.
