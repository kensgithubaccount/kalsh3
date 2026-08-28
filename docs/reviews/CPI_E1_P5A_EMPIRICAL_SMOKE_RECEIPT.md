# CPI-E1-P5A — Three-Release Empirical Smoke Receipt

## Status

PASS — bounded empirical smoke only.

This receipt records one operator-executed empirical run of the reviewed
CPI-E1-P5A manual-browser-attested acquisition lane against three real official
BLS archived CPI HTML releases.

It does **not** upgrade P4 automated HTTPS acquisition, which remains externally
blocked by BLS/Akamai in the tested environments. It grants no G1-G6 promotion,
settlement truth, modelability, economics, ranking, execution, risk, capital,
credential, or production authority.

`research_only = true`

`production_influence = 0`

## Exact code under test

- Repository: `kensgithubaccount/kalsh3`
- PR: `#108`
- Candidate head executed: `f8e981e36591db08e7ed2a9772b7f884bab03a99`
- Candidate tree executed: `a5a4bbe4b5978579a9e9cf7ea96a3ecbeaab27fd`
- Acquisition mode: `MANUAL_BROWSER_ATTESTED`

The operator verified the exact detached checkout before execution:

```text
HEAD: f8e981e36591db08e7ed2a9772b7f884bab03a99
TREE: a5a4bbe4b5978579a9e9cf7ea96a3ecbeaab27fd
```

## Human source attestation

The operator explicitly attested:

> I saved these three files directly from the exact BLS URLs above in my normal browser without editing their contents.

This remains a human provenance assertion, not cryptographic proof of the HTTP
origin. P5A preserves that limitation permanently and does not relabel manual
artifacts as P4 automated transport evidence.

## Independent artifact cross-check

The exact uploaded files were independently hashed outside the P5A execution
process. Those SHA-256 values matched the hashes emitted by the exact-head P5A
runtime for all three releases.

The archived documents also contain the expected BLS CPI release headings and
embargo statements parsed below.

## Release 1 — July 2025 CPI / summer EDT

- Exact locator: `https://www.bls.gov/news.release/archives/cpi_08122025.htm`
- Local saved file: `Consumer Price Index News Release - 2025 M07 Results.html`
- Exact byte count independently observed: `1364363`
- SHA-256: `5b869d4365bc0f58db9814e3da09105f0fd944e4bbf16c39b5511f774a03dc4b`
- Importer-observed UTC instant: `2026-08-28T22:33:17.921509+00:00`
- Acquisition evidence ID: `5dc15e24b8196c2bb3718997a5cc00ff57ad777f7e4442c212f60d19c84017be`
- Artifact ID: `fd8a4fdafe7ea67fd8197174f7fe463592ac81026ece3d2f888a67631dd25eb5`
- Parsed actual publication/embargo instant: `2025-08-12T08:30:00-04:00`
- Timing evidence ID: `3314c1d5b3ac34c83b62263edbc141548c8585e0c1b345bc2ce599f99fa74a66`
- Publication evidence ID: `00da4834ba953288337a042a2d61133a19f4508fec95e24e4ccb93213bb410a1`
- Conservative replay available at: `2025-08-12T23:59:59.999999-04:00`
- Availability basis: `RECONSTRUCTED_PRIMARY_SOURCE`
- Availability quality: `CONSERVATIVE_ASSUMPTION`
- Production influence: `0`

Result: PASS. The real archived artifact exercised the summer
`America/New_York` UTC-04:00 interpretation.

## Release 2 — December 2025 CPI / winter EST

- Exact locator: `https://www.bls.gov/news.release/archives/cpi_01132026.htm`
- Local saved file: `Consumer Price Index News Release - 2025 M12 Results.html`
- Exact byte count independently observed: `1379326`
- SHA-256: `8351af0db99f8b1e338abe1b33cb062a70e61d2b154c0ec26aaed964f52b489e`
- Importer-observed UTC instant: `2026-08-28T22:33:18.071965+00:00`
- Acquisition evidence ID: `2f8f7300b553fdb6364aedd858d1672b92fdb05f1865916eee8904a325016846`
- Artifact ID: `911db8be1386eacaebf1138a32351cbe8d4dfbcda942cbae8e09c5f8ad9dad19`
- Parsed actual publication/embargo instant: `2026-01-13T08:30:00-05:00`
- Timing evidence ID: `5bf66ffaa9067432317899fb6a90cde8013ca9ad09b97594b5bacda39e296ccb`
- Publication evidence ID: `0d6032aa22b623af5b8178ee8e0b90d6d354f8ecffeef086037e2458acb97433`
- Conservative replay available at: `2026-01-13T23:59:59.999999-05:00`
- Availability basis: `RECONSTRUCTED_PRIMARY_SOURCE`
- Availability quality: `CONSERVATIVE_ASSUMPTION`
- Production influence: `0`

Result: PASS. The real archived artifact exercised the winter
`America/New_York` UTC-05:00 interpretation.

## Release 3 — January 2026 CPI / rescheduled release

- Exact locator: `https://www.bls.gov/news.release/archives/cpi_02132026.htm`
- Local saved file: `Consumer Price Index News Release - 2026 M01 Results.html`
- Exact byte count independently observed: `1376494`
- SHA-256: `3b46aebecd5aa2d66f6f8abc38e47381e180a73db6cf87313ecc8eeddebd69f8`
- Importer-observed UTC instant: `2026-08-28T22:33:18.219648+00:00`
- Acquisition evidence ID: `cd5684a7d61533b39fb05fbb1e6fbac024093438de4b00e824469b0ef51dc4f3`
- Artifact ID: `058b88e52d330e12e00b07fa9278763a448fc185efbb2672c422ec60e184ed0e`
- Parsed actual publication/embargo instant: `2026-02-13T08:30:00-05:00`
- Timing evidence ID: `4df1cbd4637e37c2de453cb57239e2cce653b15fd9c4ef648b7f51a27d419721`
- Publication evidence ID: `49cd36d6cc68f57b3e155e568339411a2f878df0b61516e9af57261d1a50a33e`
- Conservative replay available at: `2026-02-13T23:59:59.999999-05:00`
- Availability basis: `RECONSTRUCTED_PRIMARY_SOURCE`
- Availability quality: `CONSERVATIVE_ASSUMPTION`
- Production influence: `0`

Result: PASS. The parser used the actual archived February 13 release/embargo
instant rather than silently substituting an earlier scheduled date.

## Empirical checkpoint verdict

All three bounded releases completed the intended P5A chain:

```text
human browser attestation
-> exact saved BLS HTML bytes
-> exact P1-authorized locator
-> MANUAL_BROWSER_ATTESTED acquisition evidence
-> P3 historical publication timing parser
-> capability-gated P2 publication evidence
-> conservative historical replay Availability
```

Observed properties:

1. all three exact uploaded SHA-256 values matched the exact-head P5A runtime;
2. summer EDT and winter EST offsets were parsed correctly;
3. the rescheduled January 2026 CPI release used the real February 13 archived
   embargo boundary;
4. all three outputs remained `RECONSTRUCTED_PRIMARY_SOURCE` with
   `CONSERVATIVE_ASSUMPTION` quality;
5. all three remained `production_influence = 0`;
6. no P4 automated transport success is claimed.

## What this proves

P5A is empirically capable, on this bounded three-release sample, of carrying
real human-attested official BLS archived HTML through the reviewed P1/P3/P2
historical timing chain without erasing the manual provenance distinction.

## What this does not prove

This receipt is not a historical corpus, modelability result, settlement
reconciliation, economics test, forward-performance result, or production
qualification. It does not prove cryptographic browser-origin provenance and it
does not unblock P4 automated HTTP acquisition.

The next repository action after this receipt is fresh exact-head verification
of PR #108. No merge is authorized by this receipt alone.
