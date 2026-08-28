# CPI-E1-P5A — Manual Official-BLS Acquisition Lane

## Purpose

P5A is a research-only fallback for periods when the reviewed CPI-E1-P4 automated
HTTPS request is rejected by BLS/Akamai.

It does **not** weaken, replace, or reinterpret P4. P4 remains the reviewed
automated transport policy. P5A preserves a permanently different provenance:
`MANUAL_BROWSER_ATTESTED`.

## What P5A proves

A human operator explicitly attests that a local file was saved from one exact
P1-authorized archived BLS CPI HTML locator in a normal browser. The importer then:

- resolves that exact locator through canonical P1 source authority;
- reads one bounded regular local file;
- hashes and binds the exact imported bytes;
- records an importer-observed UTC import instant that the caller cannot supply;
- records the exact manual acquisition mode and attestation policy;
- keeps `research_only = true` and `production_influence = 0`;
- passes the frozen bytes through the existing P3 historical publication parser;
- uses the existing capability-gated P2 issuer to construct conservative historical
  replay availability.

The manual evidence identity is content-addressed and issuer-controlled. Mutation,
reconstruction, wrong-origin locators, missing attestation, empty files, and oversized
files fail closed.

## What P5A does not prove

P5A does **not** prove that Python performed an HTTPS GET, that the file is the exact
wire-level HTTP response body, or that BLS cryptographically authenticated the local
file. The browser-origin statement is a human attestation, not cryptographic HTTP
provenance.

Accordingly, P5A manual evidence must never be relabeled as P4 automated evidence.
It has no HTTP method, HTTP status, response-header, or P4 transport-policy fields.

P5A also grants no G1-G6 gate promotion, settlement truth, modelability, economics,
ranking, trading, risk, capital, credential, or production authority.

## Why this is acceptable for the research checkpoint

P1 already reviewed the exact official BLS archived HTML locator shape. P3 parses the
actual publication/embargo statement from the frozen artifact, and P2 remains the
sole capability-gated issuer of positive publication timing evidence. P5A changes
only how the historical source bytes enter that chain and preserves that distinction
forever in provenance.

This allows empirical parser/PIT smoke testing to continue while P4 live automated
transport remains externally blocked.

## Initial bounded P5A smoke set

Use only these three previously selected official BLS archived releases:

1. `https://www.bls.gov/news.release/archives/cpi_08122025.htm`
   - expected embargo/publication: 2025-08-12 08:30 America/New_York
2. `https://www.bls.gov/news.release/archives/cpi_01132026.htm`
   - expected embargo/publication: 2026-01-13 08:30 America/New_York
3. `https://www.bls.gov/news.release/archives/cpi_02132026.htm`
   - expected embargo/publication: 2026-02-13 08:30 America/New_York

The smoke test is not a corpus build and does not promote any research gate.

## Operator procedure

For each exact locator:

1. Open the exact `https://www.bls.gov/news.release/archives/cpi_MMDDYYYY.htm`
   URL in a normal browser.
2. Save the page/source as a single local HTML file without editing the content.
3. Invoke `attest_import_and_issue_manual_cpi_evidence(...)` with the exact locator,
   local file path, and the exact `OPERATOR_ATTESTATION` string exported by
   `services.forecasting.cpi_manual_acquisition`.
4. Record the resulting manual acquisition evidence ID, raw SHA-256, importer-observed
   UTC instant, parsed publication instant, P2 publication evidence ID, and
   conservative availability values in the P5A smoke receipt.

Do not hand-edit downloaded bytes, substitute mirrors, change locator origin, or
claim P4 automated transport success.

## Exit condition

P5A passes only when all three bounded official releases succeed through:

`manual browser attestation -> exact frozen bytes -> P1 -> P3 -> P2`

with their expected actual publication instants and with manual provenance remaining
explicitly distinct from P4 automated HTTPS evidence.

P4 automated live transport remains independently `EXTERNALLY BLOCKED` until the
reviewed client can receive a successful official BLS response or BLS provides an
approved alternative automated method.
